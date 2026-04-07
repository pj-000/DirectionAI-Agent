"""PPT Generation API - merged into DeerFlow backend."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from deerflow.directionai.runtime_paths import get_directionai_data_dir

from .models.schemas import OutlinePlan, SlideLayout
from .agents.orchestrator import OrchestratorAgent
from .agents.ppt_evaluator import (
    PPTEvaluationRequest,
    evaluate_ppt_content,
    stream_evaluate_ppt_content,
)
from .tools.pptx_skill import get_preview_runtime_diagnostics, read_pptx

# ─── Output paths ────────────────────────────────────────────────────────────
_DEER_FLOW_HOME = get_directionai_data_dir()
OUTPUT_ROOT = _DEER_FLOW_HOME / "outputs"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


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
        min_s, max_s = int(range_match.group(1)), int(range_match.group(2))
        return tuple(sorted((min_s, max_s)))
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


class PPTGenerationRequest:
    def __init__(
        self,
        topic: str,
        model_provider: str = "minmax",
        output_language: str = "中文",
        target_audience: str = "general",
        style: str = "",
        enable_web_search: bool = False,
        image_mode: str = "generate",
        min_slides: int = 6,
        max_slides: int = 10,
        debug_layout: bool = False,
        language: str | None = None,
        audience: str | None = None,
        slides: str | None = None,
        page_limit: int | None = None,
        use_rag: bool | None = None,
        course: str | None = None,
        units: list[str] | None = None,
        lessons: list[str] | None = None,
        knowledge_points: list[str] | None = None,
        constraint: str | None = None,
        content: str | None = None,
    ):
        self.topic = topic
        self.model_provider = model_provider
        self.output_language = (language or output_language or "中文").strip() or "中文"
        self.target_audience = (audience or target_audience or "general").strip() or "general"
        self.style = "" if (style or "").lower() == "auto" else style
        self.enable_web_search = bool(use_rag) if use_rag is not None else enable_web_search
        self.image_mode = image_mode
        self.min_slides = min_slides
        self.max_slides = max_slides
        if page_limit:
            self.min_slides = self.max_slides = page_limit
        elif slides:
            self.min_slides, self.max_slides = parse_slide_range(slides, (self.min_slides, self.max_slides))
        if self.max_slides < self.min_slides:
            self.min_slides, self.max_slides = self.max_slides, self.min_slides
        self.topic = _derive_topic(
            topic=self.topic,
            course=course,
            constraint=constraint,
            units=units or [],
            lessons=lessons or [],
            knowledge_points=knowledge_points or [],
        )
        if not self.topic:
            raise ValueError("topic 不能为空")
        self.debug_layout = debug_layout
        self.content = content or constraint


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
        preview_images = self.preview_images
        if preview_images is None:
            preview_images = _collect_preview_image_urls(Path(self.output_filename).name, self.output_path)
        return {
            "status": "success",
            "markdown_content": self.markdown_content,
            "pptx_file_name": self.output_filename,
            "pptx_file_path": self.output_path,
            "display_url": f"/pptagentapi/download_ppt/{self.output_filename}",
            "download_url": f"/pptagentapi/download_ppt/{self.output_filename}",
            "preview_images": preview_images,
            "preview_warning": self.preview_warning,
            "biz_id": self.biz_id,
        }


def _preview_dir_for_artifact(output_filename: str, output_path: str | None = None) -> Path:
    stem = Path(output_filename).stem
    base_dir = Path(output_path).resolve().parent if output_path else OUTPUT_ROOT
    return (base_dir / "slides_preview" / stem).resolve()


def _collect_preview_image_urls(output_filename: str, output_path: str | None = None) -> list[str]:
    preview_dir = _preview_dir_for_artifact(output_filename, output_path)
    if not preview_dir.exists() or not preview_dir.is_dir():
        return []
    images = sorted(
        (path for path in preview_dir.iterdir() if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}),
        key=lambda p: int(re.search(r"(\d+)", p.name).group(1)) if re.search(r"(\d+)", p.name) else 999,
    )
    return [f"/pptagentapi/preview_ppt/{output_filename}/{path.name}" for path in images]


def _build_preview_warning(output_filename: str, output_path: str | None, preview_images: list[str], *, visual_qa_enabled: bool) -> str:
    if preview_images:
        return ""
    if not visual_qa_enabled:
        return "当前环境未开启视觉 QA（QWEN_API_KEY 或 QWEN_BASE_URL 未配置），当前流程会跳过缩略图渲染，请先使用「导出为PPT」查看文件。"
    runtime = get_preview_runtime_diagnostics()
    missing: list[str] = []
    if not runtime.get("soffice_found"):
        missing.append("未找到 soffice（LibreOffice）")
    if not runtime.get("pdftoppm_found"):
        missing.append("未找到 pdftoppm")
    if missing:
        return "当前未生成缩略图预览：" + "；".join(missing) + "。请安装缺失依赖后重试，或先使用「导出为PPT」查看文件。"
    return "当前未生成缩略图预览：请查看后端日志。"


def _build_preview_markdown(outline: OutlinePlan, research_results: list[dict | None] | None = None, completed_slides: int = 0) -> str:
    research_results = research_results or []
    lines = [f"# {outline.title}", "", f"- 主题：{outline.topic}", f"- 总页数：{len(outline.slides)}", ""]
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


def _serialize_sse(event: str | None = None, data: Any | None = None, comment: str | None = None) -> str:
    if comment is not None:
        return f": {comment}\n\n"
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


SENSITIVE_THINKING_PATTERNS = (
    "豆包", "tavily", "搜图", "生图", "降级逻辑", "image_mode", "image_source",
    "你是", "system", "只输出", "输出要求", "输出json格式", "json 格式",
    "不要 markdown", "不要解释", "必须遵守", "设计规则", "评估维度",
    "layout 只能是", "layout只", "第 0 页必须是 cover", "第0页必须是cover",
    "第 1 页必须是 toc", "第1页必须是toc", "最后一页必须是 closing",
    "最后一页必须是closing", "image_prompt", "visual_mode", "slide_index",
    "ppt 基本信息", "页面列表", "补充修正要求", "用户要求我", "让我分析需求",
    "根据硬约束", "layout_wide", "hero-cover", "cover layout",
    "rounded_rectangle", "opacity 属性", "pres.layout", "编写代码", "代码：",
    "letslide", "const ", "slide.", "pres.", "addslide", "addshape",
    "addtext", "for(let", "foreach(", "function(", "=>",
    "the user wants me", "this is page", "cover slide", "visual theme",
    "title font", "body font", "font size", "positioned at", "starting at",
    "below that", "i'm creating", "i am creating", "i'm placing", "i am placing",
    "rounded rectangle", "off-white color", "视觉母题", "布局类型",
    "无图片模式", "标题字体", "正文字体", "主色", "辅色", "点缀色",
    "禁止使用addimage", "用 shapes 实现", "visual motif",
    # Layout/code output leaking from planner
    "key elements to include", "color palette", "left column", "right column",
    "vertical accent", "accent strip", "near leftedge", "golden accent",
    "limit point", "function curve", "coordinate plane", "epsilon-delta",
    "ε-δ", "primary:", "secondary:", "accent:", "layout instruction",
    "key requirements", "generated image available at", "let me write the code",
    "i should:", "for a two-column layout", "for a two column layout",
    "让我分析这个需求", "分析需求：", "构建一个", "用矩形绘制", "用圆形表示",
    "右侧浅色", "左侧深色", "底部极细", "右上角抽象",
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
    rule_patterns = (
        r"`?layout`?\s*只能是", r"第\s*0\s*页(?:[^。！？!?；;\n]{0,40})必须是\s*cover",
        r"第\s*1\s*页(?:[^。！？!?；;\n]{0,40})必须是\s*toc",
        r"最后一页(?:[^。！？!?；;\n]{0,40})必须是\s*closing",
        r"第\s*\d+\s*页(?:[^。！？!?；;\n]{0,40})必须是\s*(?:cover|toc|closing)",
        r"根据\s*规则", r"规则\s*[：:]", r"cover/toc/closing", r"content/two_column",
        r"只允许\s*`?auto`?", r"必须填写一句英文视觉描述", r"只输出\s*json",
        r"输出\s*json\s*格式", r"第\s*\d+\s*页\s*[:：]\s*(?:cover|toc|closing)",
        r"总共\s*\d+\s*页", r"让我规划一下结构", r"\btheme\s*:", r"\bgoal\s*:",
        r"\bmain colors?\s*:", r"\bfonts?\s*:", r"\blayout\s*:", r"\bimage asset\s*:",
        r"\bgenerated_image\b", r"\bcontent page with\b", r"\baddimage\b",
        # Layout description patterns (code-like instructions from planner)
        r"让我分析", r"分析需求", r"分析这个需求", r"构建一个",
        r"\d+\.\s*[A-Z][a-z].*(?:strip|column|row|区域|卡片|布局)",
        r"(?:width|height|x:|y:|位置|坐标).*\d", r"(?:#[0-9A-Fa-f]{3,6}|dark navy|light gray|golden)",
        r"ε-δ|epsilon-delta|epsilon/delta",
        r"左侧.*右侧|右侧.*左侧|顶部.*底部|底部.*顶部",
        r"(?:用|通过).*(?:绘制|构建|生成|表示)",
        r"(?:渐变|装饰|光晕|渐近线|母题)",
    )
    return any(re.search(p, normalized) for p in rule_patterns)


def _looks_like_internal_logic(text: str) -> bool:
    code_signals = (
        r"(?:^|[\s{(])let\s+[a-z_]", r"(?:^|[\s{(])const\s+[a-z_]",
        r"(?:^|[\s{(])for\s*\(", r"(?:^|[\s{(])if\s*\(",
        r"[a-z_]+\.[a-z_]+\s*\(",
        r"\b(?:x|y|w|h|size|opacity|rotate|fill|color)\s*:",
    )
    if any(re.search(p, text) for p in code_signals):
        return True
    punctuation_density = sum(text.count(token) for token in ("{", "}", ";", "=>"))
    return punctuation_density >= 3


def _sanitize_thinking_text(text: Any) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""
    cleaned_lines = [line for line in raw.splitlines() if not _is_sensitive_thinking_text(line.strip())]
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
        return [cleaned] if cleaned else []


async def _yield_stream_item(item: dict[str, Any], sanitizer: _ThinkingStreamSanitizer):
    event = item.get("event")
    data = item.get("data")
    if event == "thinking_safe_chunk" and isinstance(data, str):
        for chunk in data:
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


def generate_ppt_bundle(req: PPTGenerationRequest, emit: Callable[[str, Any], None] | None = None) -> GenerationArtifacts:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    biz_id = f"ppt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    output_filename = f"{slugify(req.topic)}_{biz_id}.pptx"
    output_path = str((OUTPUT_ROOT / output_filename).resolve())
    raw_reasoning_enabled = {"value": False}
    current_node = {"value": "初始化生成任务"}

    def emit_event(ev: str, d: Any) -> None:
        if emit:
            emit(ev, d)

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
        start_step(current_step, "规划PPT结构", "正在拆解主题，先安排封面、目录和正文的整体顺序，再细化每一页要讲什么。")
        outline = orchestrator.planner.plan_outline(
            req.topic,
            min_slides=req.min_slides,
            max_slides=req.max_slides,
            style=req.style,
            audience=req.target_audience,
            language=req.output_language,
            content_requirements=req.content or "",
        )
        end_step(current_step, "规划PPT结构")
        outline_topics = " / ".join(slide.topic for slide in outline.slides if slide.topic)
        if outline_topics:
            emit_safe_thinking(f"这份 PPT 目前会按这些页面往下展开：{outline_topics}")
        body_count = max(len(outline.slides) - 3, 0)
        if body_count:
            emit_safe_thinking(f"结构上会先用封面和目录起势，中间用 {body_count} 页正文展开，最后再用总结页收束。")

        total_steps = len(outline.slides) + 3
        if req.enable_web_search:
            total_steps += 1
        if req.image_mode != "off":
            total_steps += 1

        emit_event("progress", {"step": current_step, "total": total_steps, "message": f"大纲规划完成，共 {len(outline.slides)} 页。"})
        emit_event("preview", {"markdown_content": _build_preview_markdown(outline, completed_slides=0), "completed_slides": 0, "total_slides": len(outline.slides), "current_title": ""})

        research_results: list[dict | None] = []
        image_paths: list[str | None] = []
        current_step += 1

        if req.enable_web_search:
            start_step(current_step, "补充联网资料")
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
            emit_event("progress", {"step": current_step, "total": total_steps, "message": "联网资料已完成，开始准备视觉素材。"})
            emit_event("preview", {"markdown_content": _build_preview_markdown(outline, research_results, completed_slides=0), "completed_slides": 0, "total_slides": len(outline.slides), "current_title": ""})
            current_step += 1

        if req.image_mode != "off":
            start_step(current_step, "准备页面配图", "正在为需要主视觉的页面找合适素材，顺手把整套风格往一个方向收。")
            image_paths = orchestrator._fetch_assets(outline, req.output_language)
            fetched_pages = sum(1 for item in image_paths if item)
            emit_safe_thinking(f"图片素材准备得差不多了，已有 {fetched_pages} 页拿到可用配图。")
            end_step(current_step, "准备页面配图")
            emit_event("progress", {"step": current_step, "total": total_steps, "message": "图片素材已准备完成。"})
            current_step += 1

        start_step(current_step, "确定视觉主题")
        theme = orchestrator.planner.decide_visual_theme(outline, style=req.style, audience=req.target_audience, language=req.output_language)
        consistency_brief = orchestrator.planner._build_consistency_brief(theme)
        motif = theme.get("motif_description", "")
        if motif:
            emit_safe_thinking(f"这套 PPT 的整体视觉方向先定成了：{motif}")
        palette = theme.get("palette") or theme.get("color_palette") or []
        if isinstance(palette, list) and palette:
            emit_safe_thinking("会主要围绕这组颜色展开：" + " / ".join(str(item) for item in palette[:4]))
        end_step(current_step, "确定视觉主题")
        emit_event("progress", {"step": current_step, "total": total_steps, "message": "视觉主题已确定，开始逐页生成。"})
        current_step += 1

        slide_codes: list[str] = []
        prev_summary_lines: list[str] = []

        for index, slide in enumerate(outline.slides):
            research = research_results[index] if index < len(research_results) else None
            image_path = image_paths[index] if index < len(image_paths) else None
            node = f"生成第{index + 1}页：{slide.topic}"
            summary = f"正在整理「{slide.topic}」这一页的内容。"
            objective = (getattr(slide, "objective", "") or "").strip()
            if objective:
                summary += f"这一页会重点讲清：{objective}。"
            if research and (research.get("bullet_points") or []):
                summary += "会把查到的关键信息压缩成更容易理解的表达。"
            if image_path:
                summary += "配图会跟着这一页的重点走，避免喧宾夺主。"
            start_step(current_step, node, summary)
            try:
                code = orchestrator.planner.plan_slide(
                    slide=slide, theme=theme, research=research, image_path=image_path,
                    prev_slides_summary="\n".join(prev_summary_lines[-5:]),
                    consistency_brief=consistency_brief,
                    content_requirements=req.content or "",
                )
            finally:
                raw_reasoning_enabled["value"] = False
            emit_safe_thinking("这一页的内容方向已经定下来了，继续往后生成。")
            slide_codes.append(code)
            prev_summary_lines.append(f"第{slide.slide_index}页 [{slide.layout.value}] {slide.topic} | 标题区稳定、装饰锚点固定、卡片语言一致")
            end_step(current_step, node)
            emit_event("preview", {"markdown_content": _build_preview_markdown(outline, research_results=research_results, completed_slides=index + 1), "completed_slides": index + 1, "total_slides": len(outline.slides), "current_title": slide.topic})
            emit_event("progress", {"step": current_step, "total": total_steps, "message": f"第 {index + 1} 页已生成：{slide.topic}"})
            current_step += 1

        start_step(current_step, "组装与校验PPT", "正在把所有页面组装起来，再做一轮文字和版面的检查。")
        result_path = orchestrator.planner.assemble_pptx(slide_codes, output_path, theme)
        content_issues = orchestrator._content_qa(result_path, outline)
        if content_issues:
            emit_safe_thinking(f"检查时发现 {len(content_issues)} 处页面内容还需要修一下，正在自动处理。")
            result_path, slide_codes, theme = orchestrator._fix_content_issues(content_issues, slide_codes, theme, outline, research_results, image_paths, output_path)
        result_path = orchestrator._qa_loop(result_path, slide_codes, theme, outline, research_results, image_paths)
        final_markdown = read_pptx(result_path).strip() or _build_preview_markdown(outline, research_results=research_results, completed_slides=len(outline.slides))
        preview_images = _collect_preview_image_urls(Path(result_path).name, result_path)
        preview_warning = _build_preview_warning(Path(result_path).name, result_path, preview_images, visual_qa_enabled=orchestrator.evaluator.enabled)
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
