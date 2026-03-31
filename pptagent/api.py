from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

import config
from agents.orchestrator import OrchestratorAgent
from agents.ppt_evaluator import (
    PPTEvaluationRequest,
    evaluate_ppt_content,
    stream_evaluate_ppt_content,
)
from models.schemas import OutlinePlan, SlideLayout
from tools.pptx_skill import get_preview_runtime_diagnostics, read_pptx

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = (BASE_DIR / config.OUTPUT_DIR).resolve()
FRONTEND_INDEX = (BASE_DIR / "static" / "index.html").resolve()


def slugify(text: str) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff]", "_", text)
    name = re.sub(r"_+", "_", name).strip("_")
    return name[:50] or "output"


def parse_slide_range(slides_raw: str, default: tuple[int, int] = (6, 10)) -> tuple[int, int]:
    text = (slides_raw or "").strip()
    if not text:
        return default

    range_match = re.match(r"^(\d+)\s*[-~～]\s*(\d+)$", text)
    if range_match:
        min_slides, max_slides = int(range_match.group(1)), int(range_match.group(2))
        return tuple(sorted((min_slides, max_slides)))

    if text.isdigit():
        value = int(text)
        return value, value

    return default


def _derive_topic(
    topic: str | None,
    course: str | None,
    constraint: str | None,
    units: list[str],
    lessons: list[str],
    knowledge_points: list[str],
) -> str:
    direct = (topic or "").strip()
    if direct:
        return direct

    parts: list[str] = []

    course_text = (course or "").strip()
    if course_text and course_text != "不限制":
        parts.append(course_text)

    if lessons:
        parts.append("、".join(item.strip() for item in lessons if item and item.strip())[:80])
    elif units:
        parts.append("、".join(item.strip() for item in units if item and item.strip())[:80])
    elif knowledge_points:
        parts.append("、".join(item.strip() for item in knowledge_points if item and item.strip())[:80])

    constraint_text = (constraint or "").strip()
    if constraint_text:
        parts.append(constraint_text[:120])

    cleaned = [part for part in parts if part]
    return " - ".join(cleaned[:2]).strip()


class PPTGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    topic: str | None = Field(default=None, description="PPT 主题")
    model_provider: Literal["minmax", "claude"] = Field(default="minmax", description="模型供应商")
    output_language: str = Field(default="中文", description="输出语言")
    target_audience: str = Field(default="general", description="目标受众")

    style: str = Field(default="", description="PPT 风格，为空时自动决定")
    enable_web_search: bool = Field(default=False, description="是否启用联网检索")
    image_mode: Literal["generate", "search", "auto", "off"] = Field(
        default="generate",
        description="图片模式：generate=豆包生图，search=仅搜图，auto=先搜图后生图，off=关闭图片",
    )
    min_slides: int = Field(default=6, ge=2, le=20, description="最少页数")
    max_slides: int = Field(default=10, ge=2, le=20, description="最多页数")
    debug_layout: bool = Field(default=False, description="是否输出调试布局")

    # Compatibility fields for existing frontend payloads.
    language: str | None = None
    audience: str | None = None
    slides: str | None = None
    page_limit: int | None = Field(default=None, ge=2, le=20)
    use_rag: bool | None = None
    course: str | None = None
    units: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    knowledge_points: list[str] = Field(default_factory=list)
    constraint: str | None = None
    content: str | None = None

    @model_validator(mode="after")
    def normalize(self) -> "PPTGenerationRequest":
        if self.language and self.output_language == "中文":
            self.output_language = self.language
        if self.audience and self.target_audience == "general":
            self.target_audience = self.audience
        if self.use_rag is not None:
            self.enable_web_search = bool(self.use_rag)

        if self.page_limit:
            self.min_slides = self.page_limit
            self.max_slides = self.page_limit
        elif self.slides:
            self.min_slides, self.max_slides = parse_slide_range(
                self.slides,
                default=(self.min_slides, self.max_slides),
            )

        if self.max_slides < self.min_slides:
            self.min_slides, self.max_slides = self.max_slides, self.min_slides

        self.topic = _derive_topic(
            topic=self.topic,
            course=self.course,
            constraint=self.constraint,
            units=self.units,
            lessons=self.lessons,
            knowledge_points=self.knowledge_points,
        )
        if not self.topic:
            raise ValueError("topic 不能为空")

        self.output_language = (self.output_language or "中文").strip() or "中文"
        self.target_audience = (self.target_audience or "general").strip() or "general"

        if (self.style or "").lower() == "auto":
            self.style = ""

        return self


@dataclass
class GenerationArtifacts:
    output_path: str
    output_filename: str
    markdown_content: str
    total_slides: int
    biz_id: str
    preview_images: list[str] | None = None
    preview_warning: str = ""

    def to_response(self) -> dict[str, Any]:
        download_url = f"/download_ppt/{self.output_filename}"
        preview_images = self.preview_images
        if preview_images is None:
            preview_images = _collect_preview_image_urls(self.output_filename, self.output_path)
        return {
            "status": "success",
            "markdown_content": self.markdown_content,
            "pptx_file_name": self.output_filename,
            "pptx_file_path": self.output_path,
            "display_url": download_url,
            "download_url": download_url,
            "preview_images": preview_images,
            "preview_warning": self.preview_warning,
            "biz_id": self.biz_id,
        }


def _preview_dir_for_artifact(output_filename: str, output_path: str | None = None) -> Path:
    stem = Path(output_filename).stem
    base_dir = Path(output_path).resolve().parent if output_path else OUTPUT_ROOT
    return (base_dir / "slides_preview" / stem).resolve()


def _preview_image_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)(?=\.[^.]+$)", path.name)
    if not match:
        return (10**9, path.name)
    return (int(match.group(1)), path.name)


def _collect_preview_image_urls(output_filename: str, output_path: str | None = None) -> list[str]:
    preview_dir = _preview_dir_for_artifact(output_filename, output_path)
    if not preview_dir.exists() or not preview_dir.is_dir():
        return []

    images = sorted(
        (
            path for path in preview_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ),
        key=_preview_image_sort_key,
    )
    return [f"/preview_ppt/{output_filename}/{path.name}" for path in images]


def _build_preview_warning(
    output_filename: str,
    output_path: str | None,
    preview_images: list[str],
    *,
    visual_qa_enabled: bool,
) -> str:
    if preview_images:
        return ""

    if not visual_qa_enabled:
        return (
            "当前环境未开启视觉 QA（QWEN_API_KEY 或 QWEN_BASE_URL 未配置），"
            "当前流程会跳过缩略图渲染，请先使用“导出为PPT”查看文件。"
        )

    runtime = get_preview_runtime_diagnostics()
    missing: list[str] = []
    if not runtime.get("soffice_found"):
        missing.append("未找到 soffice（LibreOffice）")
    if not runtime.get("pdftoppm_found"):
        missing.append("未找到 pdftoppm")

    if missing:
        return (
            "当前未生成缩略图预览：" + "；".join(missing) +
            "。请安装缺失依赖后重试，或先使用“导出为PPT”查看文件。"
        )

    preview_dir = _preview_dir_for_artifact(output_filename, output_path)
    if preview_dir.exists() and preview_dir.is_dir():
        return (
            "当前未生成缩略图预览：已创建 slides_preview 目录但没有图片文件，"
            "通常是 soffice 转 PDF 或 pdftoppm 转图失败，请查看后端日志中的 [PptxSkill] 记录。"
        )

    return (
        "当前未生成缩略图预览：PPT 文件已生成，但后台没有产出 slides_preview 图片。"
        "请检查后端日志中的 [PptxSkill] 记录，以及 outputs/slides_preview 目录写权限。"
    )


def _build_slide_summary(slide, research: dict | None, image_path: str | None) -> str:
    summary_parts = [
        f"布局：{slide.layout.value}",
        f"目标：{slide.objective or slide.topic}",
    ]

    bullet_points = (research or {}).get("bullet_points") or []
    if bullet_points:
        summary_parts.append(f"研究要点：{len(bullet_points)} 条")
    if image_path:
        summary_parts.append("已提供本地配图")
    elif slide.layout in {SlideLayout.CONTENT, SlideLayout.TWO_COLUMN}:
        summary_parts.append("本页将以图形或文字视觉为主")

    return "；".join(summary_parts) + "。"


def _build_page_thinking_summary(slide, research: dict | None, image_path: str | None) -> str:
    parts = [f"正在整理“{slide.topic}”这一页的内容。"]

    objective = (getattr(slide, "objective", "") or "").strip()
    if objective:
        parts.append(f"这一页会重点讲清：{objective}。")

    bullets = (research or {}).get("bullet_points") or []
    if bullets:
        parts.append("会把查到的关键信息压缩成更容易理解的表达。")

    if image_path:
        parts.append("配图会跟着这一页的重点走，避免喧宾夺主。")

    return "".join(parts)


def _build_preview_markdown(
    outline: OutlinePlan,
    research_results: list[dict | None] | None = None,
    completed_slides: int = 0,
) -> str:
    research_results = research_results or []
    lines = [
        f"# {outline.title}",
        "",
        f"- 主题：{outline.topic}",
        f"- 总页数：{len(outline.slides)}",
        "",
    ]

    for index, slide in enumerate(outline.slides):
        status = "已生成" if index < completed_slides else "待生成"
        lines.append(f"## 第{index + 1}页 · {slide.topic} [{status}]")
        lines.append(f"- 布局：{slide.layout.value}")
        if slide.objective:
            lines.append(f"- 页面目标：{slide.objective}")

        research = research_results[index] if index < len(research_results) else None
        bullet_points = (research or {}).get("bullet_points") or []
        if bullet_points and index < completed_slides:
            for bullet in bullet_points[:4]:
                lines.append(f"- {bullet}")

        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _serialize_sse(
    *,
    event: str | None = None,
    data: Any | None = None,
    comment: str | None = None,
) -> str:
    if comment is not None:
        return f": {comment}\n\n"

    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def _iter_thinking_chunks(text: Any, chunk_size: int = 48) -> list[str]:
    raw = str(text or "")
    if not raw.strip():
        return []

    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[str] = []

    for paragraph in normalized.split("\n"):
        if not paragraph:
            chunks.append("\n")
            continue

        buffer = ""
        for char in paragraph:
            buffer += char
            if len(buffer) >= chunk_size:
                chunks.append(buffer)
                buffer = ""

        if buffer:
            chunks.append(buffer)
        chunks.append("\n")

    while chunks and chunks[-1] == "\n":
        chunks.pop()

    return [chunk for chunk in chunks if chunk]


SENSITIVE_THINKING_PATTERNS = (
    "豆包",
    "tavily",
    "搜图",
    "生图",
    "降级逻辑",
    "image_mode",
    "image_source",
    "你是",
    "system",
    "只输出",
    "输出要求",
    "输出json格式",
    "json 格式",
    "不要 markdown",
    "不要解释",
    "必须遵守",
    "设计规则",
    "评估维度",
    "layout 只能是",
    "layout只",
    "第 0 页必须是 cover",
    "第0页必须是cover",
    "第 1 页必须是 toc",
    "第1页必须是toc",
    "最后一页必须是 closing",
    "最后一页必须是closing",
    "image_prompt",
    "visual_mode",
    "slide_index",
    "ppt 基本信息",
    "页面列表",
    "补充修正要求",
    "用户要求我",
    "让我分析需求",
    "根据硬约束",
    "layout_wide",
    "hero-cover",
    "cover layout",
    "rounded_rectangle",
    "opacity 属性",
    "pres.layout",
    "编写代码",
    "代码：",
    "letslide",
    "const ",
    "slide.",
    "pres.",
    "addslide",
    "addshape",
    "addtext",
    "for(let",
    "foreach(",
    "function(",
    "=>",
    "the user wants me to",
    "this is page",
    "cover slide",
    "visual theme",
    "title font",
    "body font",
    "font size",
    "positioned at",
    "starting at",
    "below that",
    "i'm creating",
    "i am creating",
    "i'm placing",
    "i am placing",
    "rounded rectangle",
    "off-white color",
    "视觉母题",
    "布局类型",
    "无图片模式",
    "标题字体",
    "正文字体",
    "主色",
    "辅色",
    "点缀色",
    "禁止使用addimage",
    "用 shapes 实现",
    "visual motif",
)


def _is_sensitive_thinking_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    if not normalized:
        return False
    if normalized.startswith("```") or normalized.endswith("```"):
        return True
    if _looks_like_internal_logic(normalized):
        return True
    if any(pattern in normalized for pattern in SENSITIVE_THINKING_PATTERNS):
        return True

    rule_like_patterns = (
        r"`?layout`?\s*只能是",
        r"第\s*0\s*页(?:[^。！？!?；;\n]{0,40})必须是\s*cover",
        r"第\s*1\s*页(?:[^。！？!?；;\n]{0,40})必须是\s*toc",
        r"最后一页(?:[^。！？!?；;\n]{0,40})必须是\s*closing",
        r"第\s*\d+\s*页(?:[^。！？!?；;\n]{0,40})必须是\s*(?:cover|toc|closing)",
        r"根据\s*规则",
        r"规则\s*[：:]",
        r"cover/toc/closing",
        r"content/two_column",
        r"只允许\s*`?auto`?",
        r"必须填写一句英文视觉描述",
        r"只输出\s*json",
        r"输出\s*json\s*格式",
        r"第\s*\d+\s*页\s*[:：]\s*(?:cover|toc|closing)",
        r"总共\s*\d+\s*页",
        r"让我规划一下结构",
        r"\btheme\s*:",
        r"\bgoal\s*:",
        r"\bmain colors?\s*:",
        r"\bfonts?\s*:",
        r"\blayout\s*:",
        r"\bimage asset\s*:",
        r"\bgenerated_image\b",
        r"\bcontent page with\b",
        r"\baddimage\b",
    )
    return any(re.search(pattern, normalized) for pattern in rule_like_patterns)


def _looks_like_internal_logic(text: str) -> bool:
    code_signals = (
        r"(?:^|[\s{(])let\s+[a-z_]",
        r"(?:^|[\s{(])const\s+[a-z_]",
        r"(?:^|[\s{(])for\s*\(",
        r"(?:^|[\s{(])if\s*\(",
        r"[a-z_]+\.[a-z_]+\s*\(",
        r"\b(?:x|y|w|h|size|opacity|rotate|fill|color)\s*:",
    )
    if any(re.search(pattern, text) for pattern in code_signals):
        return True
    punctuation_density = sum(text.count(token) for token in ("{", "}", ";", "=>"))
    return punctuation_density >= 3


def _sanitize_thinking_text(text: Any) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""

    cleaned_lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and _is_sensitive_thinking_text(stripped):
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


class _ThinkingStreamSanitizer:
    def __init__(self) -> None:
        self.pending = ""
        self.block_current_line = False
        self.sentence_endings = "。！？!?；;"

    def reset(self) -> None:
        self.pending = ""
        self.block_current_line = False

    def _try_emit_pending(self) -> str | None:
        if self.block_current_line:
            self.pending = ""
            self.block_current_line = False
            return None
        cleaned = _sanitize_thinking_text(self.pending)
        self.pending = ""
        self.block_current_line = False
        return cleaned or None

    def feed(self, text: Any) -> list[str]:
        outputs: list[str] = []
        for char in str(text or ""):
            self.pending += char

            line_probe = self.pending.strip()
            if line_probe and _is_sensitive_thinking_text(line_probe):
                self.block_current_line = True

            if char == "\n":
                cleaned = self._try_emit_pending()
                if cleaned:
                    outputs.append(cleaned)
                continue

            if char in self.sentence_endings:
                cleaned = self._try_emit_pending()
                if cleaned:
                    outputs.append(cleaned)

        return outputs

    def flush(self) -> list[str]:
        cleaned = self._try_emit_pending()
        if not cleaned:
            return []
        return [cleaned]


async def _yield_stream_item(item: dict[str, Any], sanitizer: _ThinkingStreamSanitizer):
    event = item.get("event")
    data = item.get("data")

    if event == "thinking_safe_chunk" and isinstance(data, str):
        for chunk in _iter_thinking_chunks(data, chunk_size=3):
            yield _serialize_sse(event="thinking_chunk", data=chunk)
            await asyncio.sleep(0.02)
        return

    if event == "thinking_chunk" and isinstance(data, str):
        for chunk in sanitizer.feed(data):
            yield _serialize_sse(event=event, data=chunk)
            await asyncio.sleep(0.02)
        return

    if event == "thinking_start":
        sanitizer.reset()
    elif event == "thinking_end":
        for chunk in sanitizer.flush():
            yield _serialize_sse(event="thinking_chunk", data=chunk)
            await asyncio.sleep(0.02)

    yield _serialize_sse(event=event, data=data)


def generate_ppt_bundle(
    req: PPTGenerationRequest,
    emit: Callable[[str, Any], None] | None = None,
) -> GenerationArtifacts:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    output_filename = f"{slugify(req.topic)}.pptx"
    output_path = str((OUTPUT_ROOT / output_filename).resolve())
    biz_id = f"ppt_{int(time.time() * 1000)}"
    raw_reasoning_enabled = {"value": False}
    current_node = {"value": "初始化生成任务"}

    def emit_event(event: str, data: Any) -> None:
        if emit:
            emit(event, data)

    def emit_reasoning(text: str) -> None:
        if raw_reasoning_enabled["value"] and text:
            emit_event("thinking_chunk", text)

    def emit_safe_thinking(text: str) -> None:
        if text:
            emit_event("thinking_safe_chunk", text)

    image_source = "generate" if req.image_mode == "off" else req.image_mode
    orchestrator = OrchestratorAgent(
        debug_layout=req.debug_layout,
        no_research=not req.enable_web_search,
        no_images=req.image_mode == "off",
        image_source=image_source,
        model_provider=req.model_provider,
        thinking_callback=emit_reasoning,
    )

    def start_step(step: int, node: str, summary: str | None = None) -> None:
        current_node["value"] = node
        emit_event("thinking_start", {"step": step, "node": node})
        if summary:
            emit_safe_thinking(summary)

    def end_step(step: int, node: str) -> None:
        emit_event("thinking_end", {"step": step, "node": node})

    try:
        emit_event("progress", {"step": 0, "total": 0, "message": "正在启动 PPT 生成任务..."})

        current_step = 1
        start_step(
            current_step,
            "规划PPT结构",
            "正在拆解主题，先安排封面、目录和正文的整体顺序，再细化每一页要讲什么。",
        )
        outline = orchestrator.planner.plan_outline(
            req.topic,
            min_slides=req.min_slides,
            max_slides=req.max_slides,
            style=req.style,
            audience=req.target_audience,
            language=req.output_language,
            content_requirements=req.content or req.constraint or "",
        )
        end_step(current_step, "规划PPT结构")
        outline_topics = " / ".join(slide.topic for slide in outline.slides if slide.topic)
        if outline_topics:
            emit_safe_thinking(f"这份 PPT 目前会按这些页面往下展开：{outline_topics}")
        body_slide_count = max(len(outline.slides) - 3, 0)
        if body_slide_count:
            emit_safe_thinking(f"结构上会先用封面和目录起势，中间用 {body_slide_count} 页正文展开，最后再用总结页收束。")

        total_steps = len(outline.slides) + 3
        if req.enable_web_search:
            total_steps += 1
        if req.image_mode != "off":
            total_steps += 1

        emit_event(
            "progress",
            {
                "step": current_step,
                "total": total_steps,
                "message": f"大纲规划完成，共 {len(outline.slides)} 页。",
            },
        )
        emit_event(
            "preview",
            {
                "markdown_content": _build_preview_markdown(outline, completed_slides=0),
                "completed_slides": 0,
                "total_slides": len(outline.slides),
                "current_title": "",
            },
        )

        research_results: list[dict | None] = []
        image_paths: list[str | None] = []
        current_step += 1

        if req.enable_web_search:
            start_step(
                current_step,
                "补充联网资料",
            )
            research_results = orchestrator._research_outline(outline, req.output_language)
            outline = orchestrator.planner.enrich_image_prompts(outline, research_results)
            researched_pages = sum(1 for item in research_results if item and item.get("bullet_points"))
            emit_safe_thinking(f"资料补充完成，已经为 {researched_pages} 页补到了可用信息。")
            sample_points = []
            for item in research_results:
                bullets = (item or {}).get("bullet_points") or []
                if bullets:
                    sample_points.extend(bullets[:2])
                if len(sample_points) >= 3:
                    break
            if sample_points:
                emit_safe_thinking("先整理出几条能直接用在页里的信息：" + "；".join(sample_points[:3]))
            end_step(current_step, "补充联网资料")
            emit_event(
                "progress",
                {
                    "step": current_step,
                    "total": total_steps,
                    "message": "联网资料已完成，开始准备视觉素材。",
                },
            )
            emit_event(
                "preview",
                {
                    "markdown_content": _build_preview_markdown(outline, research_results, completed_slides=0),
                    "completed_slides": 0,
                    "total_slides": len(outline.slides),
                    "current_title": "",
                },
            )
            current_step += 1

        if req.image_mode != "off":
            start_step(
                current_step,
                "准备页面配图",
                "正在为需要主视觉的页面找合适素材，顺手把整套风格往一个方向收。",
            )
            image_paths = orchestrator._fetch_assets(outline, req.output_language)
            fetched_pages = sum(1 for item in image_paths if item)
            emit_safe_thinking(f"图片素材准备得差不多了，已有 {fetched_pages} 页拿到可用配图。")
            if fetched_pages:
                emit_safe_thinking("需要主视觉的页面已经有图可用，后面会继续统一风格。")
            end_step(current_step, "准备页面配图")
            emit_event(
                "progress",
                {
                    "step": current_step,
                    "total": total_steps,
                    "message": "图片素材已准备完成。",
                },
            )
            current_step += 1

        start_step(
            current_step,
            "确定视觉主题",
        )
        theme = orchestrator.planner.decide_visual_theme(
            outline,
            style=req.style,
            audience=req.target_audience,
            language=req.output_language,
        )
        consistency_brief = orchestrator.planner._build_consistency_brief(theme)
        motif = theme.get("motif_description", "")
        if motif:
            emit_safe_thinking(f"这套 PPT 的整体视觉方向先定成了：{motif}")
        palette = theme.get("palette") or theme.get("color_palette") or []
        if isinstance(palette, list) and palette:
            emit_safe_thinking("会主要围绕这组颜色展开：" + " / ".join(str(item) for item in palette[:4]))
        end_step(current_step, "确定视觉主题")
        emit_event(
            "progress",
            {
                "step": current_step,
                "total": total_steps,
                "message": "视觉主题已确定，开始逐页生成。",
            },
        )
        current_step += 1

        slide_codes: list[str] = []
        prev_summary_lines: list[str] = []

        for index, slide in enumerate(outline.slides):
            research = research_results[index] if index < len(research_results) else None
            image_path = image_paths[index] if index < len(image_paths) else None
            node = f"生成第{index + 1}页：{slide.topic}"
            start_step(current_step, node, _build_page_thinking_summary(slide, research, image_path))
            layout_name = str(getattr(getattr(slide, "layout", None), "value", getattr(slide, "layout", "")) or "").lower()
            raw_reasoning_enabled["value"] = layout_name in {"content", "two_column"}
            if research:
                bullets = (research or {}).get("bullet_points") or []
                if bullets:
                    emit_safe_thinking("这一页会先抓这几个重点：" + "；".join(str(item) for item in bullets[:3]))
            if image_path:
                emit_safe_thinking("这页已经配好图片，排版时会把图文主次拉开。")
            try:
                code = orchestrator.planner.plan_slide(
                    slide=slide,
                    theme=theme,
                    research=research,
                    image_path=image_path,
                    prev_slides_summary="\n".join(prev_summary_lines[-5:]),
                    consistency_brief=consistency_brief,
                    content_requirements=req.content or req.constraint or "",
                )
            finally:
                raw_reasoning_enabled["value"] = False
            emit_safe_thinking("这一页的内容方向已经定下来了，继续往后生成。")
            slide_codes.append(code)
            prev_summary_lines.append(
                f"第{slide.slide_index}页 [{slide.layout.value}] {slide.topic} | 标题区稳定、装饰锚点固定、卡片语言一致"
            )
            end_step(current_step, node)
            emit_event(
                "preview",
                {
                    "markdown_content": _build_preview_markdown(
                        outline,
                        research_results=research_results,
                        completed_slides=index + 1,
                    ),
                    "completed_slides": index + 1,
                    "total_slides": len(outline.slides),
                    "current_title": slide.topic,
                },
            )
            emit_event(
                "progress",
                {
                    "step": current_step,
                    "total": total_steps,
                    "message": f"第 {index + 1} 页已生成：{slide.topic}",
                },
            )
            current_step += 1

        start_step(
            current_step,
            "组装与校验PPT",
            "正在把所有页面组装起来，再做一轮文字和版面的检查。",
        )
        result_path = orchestrator.planner.assemble_pptx(slide_codes, output_path, theme)
        content_issues = orchestrator._content_qa(result_path, outline)
        if content_issues:
            emit_safe_thinking(f"检查时发现 {len(content_issues)} 处页面内容还需要修一下，正在自动处理。")
            result_path, slide_codes, theme = orchestrator._fix_content_issues(
                content_issues,
                slide_codes,
                theme,
                outline,
                research_results,
                image_paths,
                output_path,
            )
        result_path = orchestrator._qa_loop(
            result_path,
            slide_codes,
            theme,
            outline,
            research_results,
            image_paths,
        )
        final_markdown = read_pptx(result_path).strip() or _build_preview_markdown(
            outline,
            research_results=research_results,
            completed_slides=len(outline.slides),
        )
        preview_images = _collect_preview_image_urls(Path(result_path).name, result_path)
        preview_warning = _build_preview_warning(
            Path(result_path).name,
            result_path,
            preview_images,
            visual_qa_enabled=orchestrator.evaluator.enabled,
        )
        end_step(current_step, "组装与校验PPT")

        return GenerationArtifacts(
            output_path=result_path,
            output_filename=Path(result_path).name,
            markdown_content=final_markdown,
            total_slides=len(outline.slides),
            biz_id=biz_id,
            preview_images=preview_images,
            preview_warning=preview_warning,
        )
    except Exception as exc:
        step_name = current_node["value"]
        raise RuntimeError(f"{step_name} 失败: {exc}") from exc


app = FastAPI(title="PPTAgent FastAPI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index() -> FileResponse:
    if not FRONTEND_INDEX.exists():
        raise HTTPException(status_code=404, detail="前端页面不存在")
    return FileResponse(str(FRONTEND_INDEX))


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/generate_ppt")
def generate_ppt_route(req: PPTGenerationRequest) -> dict[str, Any]:
    try:
        artifacts = generate_ppt_bundle(req)
        return artifacts.to_response()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/evaluate/ppt")
def evaluate_ppt_route(req: PPTEvaluationRequest) -> dict[str, Any]:
    try:
        return evaluate_ppt_content(req)[0].to_response()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/stream_ppt")
async def stream_ppt_route(req: PPTGenerationRequest, request: Request) -> StreamingResponse:
    event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()

    def emit(event: str, data: Any) -> None:
        event_queue.put({"event": event, "data": data})

    def worker() -> None:
        try:
            artifacts = generate_ppt_bundle(req, emit=emit)
            emit("done", artifacts.to_response())
        except Exception as exc:
            emit("error", {"detail": str(exc)})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    async def event_stream():
        sanitizer = _ThinkingStreamSanitizer()
        while True:
            if await request.is_disconnected():
                break

            try:
                item = await asyncio.to_thread(event_queue.get, timeout=15)
            except queue.Empty:
                if not thread.is_alive():
                    yield _serialize_sse(event="error", data={"detail": "工作线程意外退出"})
                    break
                yield _serialize_sse(comment="keepalive")
                continue

            async for payload in _yield_stream_item(item, sanitizer):
                yield payload
            if item.get("event") in {"done", "error"}:
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/stream_evaluate/ppt")
async def stream_evaluate_ppt_route(req: PPTEvaluationRequest, request: Request) -> StreamingResponse:
    event_queue = stream_evaluate_ppt_content(req)

    async def event_stream():
        sanitizer = _ThinkingStreamSanitizer()
        while True:
            if await request.is_disconnected():
                break

            try:
                item = await asyncio.to_thread(event_queue.get, timeout=15)
            except queue.Empty:
                yield _serialize_sse(comment="keepalive")
                continue

            async for payload in _yield_stream_item(item, sanitizer):
                yield payload
            if item.get("event") in {"done", "error"}:
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/download_ppt/{filename}")
def download_ppt_route(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    target = (OUTPUT_ROOT / safe_name).resolve()

    if target.parent != OUTPUT_ROOT:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=str(target),
        filename=safe_name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@app.get("/preview_ppt/{filename}/{image_name}")
def preview_ppt_image_route(filename: str, image_name: str) -> FileResponse:
    safe_name = Path(filename).name
    safe_image = Path(image_name).name
    preview_dir = _preview_dir_for_artifact(safe_name)
    target = (preview_dir / safe_image).resolve()

    if target.parent != preview_dir:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    suffix = target.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"
    return FileResponse(path=str(target), media_type=media_type)
