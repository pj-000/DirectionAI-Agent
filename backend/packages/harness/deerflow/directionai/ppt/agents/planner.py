import json
import re
import os
import sys
import logging
from pathlib import Path
from typing import Callable
from openai import OpenAI
from ..tools.pptx_skill import PROJECT_ROOT, run_js, skill_paths, assert_skill_present
from ..tools.openai_compat import build_chat_completion_kwargs, stream_chat_completion_text
from ..models.schemas import OutlinePlan, SlideOutline, SlideLayout, SlideEvalResult, VisualMode, resolve_visual_mode
from .. import config

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
SUPPORTED_STYLES = (
    "auto", "executive", "ocean", "minimal", "coral",
    "terracotta", "teal", "forest", "berry", "cherry",
)
SUPPORTED_AUDIENCES = ("general", "boss", "investor", "student", "customer", "technical")

AUDIENCE_ALIASES = {
    "": "general",
    "general": "general",
    "default": "general",
    "public": "general",
    "大众": "general",
    "通用": "general",
    "普通": "general",
    "boss": "boss",
    "leader": "boss",
    "leaders": "boss",
    "manager": "boss",
    "management": "boss",
    "executive": "boss",
    "ceo": "boss",
    "老板": "boss",
    "领导": "boss",
    "管理层": "boss",
    "投资人": "investor",
    "投资者": "investor",
    "vc": "investor",
    "investor": "investor",
    "student": "student",
    "students": "student",
    "learner": "student",
    "learners": "student",
    "college student": "student",
    "university student": "student",
    "学员": "student",
    "学生": "student",
    "大学生": "student",
    "高校学生": "student",
    "customer": "customer",
    "customers": "customer",
    "client": "customer",
    "clients": "customer",
    "user": "customer",
    "users": "customer",
    "客户": "customer",
    "用户": "customer",
    "消费者": "customer",
    "technical": "technical",
    "tech": "technical",
    "engineer": "technical",
    "engineers": "technical",
    "developer": "technical",
    "developers": "technical",
    "技术": "technical",
    "工程师": "technical",
    "开发者": "technical",
}

AUDIENCE_PROFILES = {
    "general": "信息密度中等，先讲背景再讲重点，少用术语，兼顾概念解释与关键结论。",
    "boss": "结论先行，高信息压缩，比细节更重视决策点、ROI、风险、资源投入与下一步建议。",
    "investor": "突出市场空间、增长逻辑、竞争优势、商业化路径、关键指标与风险回报，语气克制专业。",
    "student": "循序渐进，术语要解释，例子要具体，信息密度偏低到中等，强调学习路径与核心概念。",
    "customer": "以用户价值、应用场景、收益、差异化和可信度为中心，少讲内部机制，避免堆砌术语。",
    "technical": "允许更高信息密度，强调原理、架构、实现方式、性能取舍、限制条件与技术准确性。",
}


def normalize_audience(audience: str | None) -> str:
    return (audience or "").strip() or "general"


def suggest_audience_label(audience: str | None) -> str | None:
    raw = normalize_audience(audience)
    normalized = AUDIENCE_ALIASES.get(raw.lower())
    if normalized:
        return normalized
    return raw.lower() if raw.lower() in AUDIENCE_PROFILES else None


def suggest_style_label(style: str | None) -> str | None:
    raw = (style or "").strip()
    if not raw or raw.lower() == "auto":
        return None
    lowered = raw.lower()
    return lowered if lowered in SUPPORTED_STYLES else raw


SHAPE_VALUE_MAP = {
    "rect": "rect",
    "rectangle": "rect",
    "square": "rect",
    "oval": "ellipse",
    "ellipse": "ellipse",
    "circle": "ellipse",
    "line": "line",
    "roundrect": "roundRect",
    "roundedrect": "roundRect",
    "roundedrectangle": "roundRect",
    "rounded_rectangle": "roundRect",
    "chevron": "chevron",
    "diamond": "diamond",
    "triangle": "triangle",
    "hexagon": "hexagon",
    "arc": "arc",
    "pie": "pie",
    "cloud": "cloud",
    "pentagon": "pentagon",
    "rightarrow": "rightArrow",
    "right_arrow": "rightArrow",
    "arrow": "rightArrow",
    "star5": "star5",
    "star5point": "star5",
    "star_5_point": "star5",
}


# 本地 vendored skill 路径
_SKILL = skill_paths()


class PlannerAgent:
    """
    读取本地 Anthropic PPTX skill 文档作为 system prompt，
    让 GLM 生成 PptxGenJS 代码，通过 pptx_skill.run_js() 执行。
    """

    def __init__(
        self,
        model_provider: str = "minmax",
        thinking_callback: Callable[[str], None] | None = None,
    ):
        assert_skill_present()
        provider_settings = config.get_llm_provider_settings(model_provider)
        env_key = provider_settings["api_key"]
        self.model_provider = provider_settings["provider"]
        self.api_key = provider_settings["api_key"]
        self.base_url = provider_settings["base_url"]
        self.model = provider_settings["model"]
        def mask_key(value: str) -> str:
            if not value:
                return "<empty>"
            if len(value) > 16:
                return f"{value[:12]}...{value[-6:]}"
            return value
        print(
            "[Planner] LLM config: "
            f"provider={self.model_provider} | "
            f"base_url={self.base_url} | "
            f"model={self.model} | "
            f"api_key={mask_key(self.api_key)}"
        )
        print(
            "[Planner] Env check: "
            f"os.getenv(PLANNER_API_KEY)={mask_key(env_key)} | "
            f"selected_api_key={mask_key(self.api_key)} | "
            f"same={'yes' if env_key == self.api_key else 'no'}"
        )
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        self.last_reasoning = ""
        self.thinking_callback = thinking_callback

        # 直接读取本地 vendored skill 文档
        self._skill_md = Path(_SKILL["skill_md"]).read_text(encoding="utf-8")
        self._pptxgenjs_md = Path(_SKILL["pptxgenjs_md"]).read_text(encoding="utf-8")
        self._local_rules_md = Path(_SKILL["local_rules_md"]).read_text(encoding="utf-8")
        self._user_template = (PROJECT_ROOT / "prompts" / "planner_user.txt").read_text(encoding="utf-8")

    def _handle_reasoning_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self.last_reasoning += chunk
        if self.thinking_callback:
            self.thinking_callback(chunk)

    def _build_system_prompt(self) -> str:
        """
        system prompt = 角色指令 + 官方 SKILL.md Before Starting + Design Ideas + Typography + Avoid + 完整 pptxgenjs.md
        """
        # 从 SKILL.md 中提取 "Before Starting"（在 Design Ideas 下）到 QA 之前的内容
        skill = self._skill_md
        start_marker = "### Before Starting"
        end_marker = "## QA"
        start_idx = skill.find(start_marker)
        end_idx = skill.find(end_marker)
        if start_idx >= 0 and end_idx > start_idx:
            design_section = skill[start_idx:end_idx].strip()
        elif start_idx >= 0:
            design_section = skill[start_idx:].strip()
        else:
            # fallback: Design Ideas 之后
            idx = skill.find("## Design Ideas")
            design_section = skill[idx:].strip() if idx >= 0 else skill

        return f"""你是一位顶级 PPT 视觉设计工程师。你使用 PptxGenJS（Node.js）生成精美的演示文稿。

以下是来自 Anthropic 官方 PPTX Design Skill 的设计规范，你必须严格遵守：

---
{design_section}
---

以下是本地增强的生成规则（硬约束 + 视觉设计原则 + 布局词汇表），你也必须严格遵守：

---
{self._local_rules_md}
---

以下是 PptxGenJS 的完整 API 教程，你生成的代码必须严格遵循这些用法和注意事项：

---
{self._pptxgenjs_md}
---

## 你的输出格式

输出一段完整的 Node.js 代码，用 <code> 标签包裹。代码要求：

1. `const pptxgen = require("pptxgenjs");` 开头
2. 使用 `pres.layout = "LAYOUT_WIDE";`（13.33" × 7.5"）
3. 最后调用 `pres.writeFile({{ fileName: "OUTPUT_PATH" }});`
4. 只使用 pptxgenjs，不要其他 npm 包
5. 严格遵循上面所有设计规则，包括字体配对、字号规范、间距、以及 Avoid 清单
6. 严格遵循上面 PptxGenJS Tutorial 中的所有 API 用法和 Common Pitfalls
"""

    def _build_outline_system_prompt(self) -> str:
        return """你是一位 PPT 内容架构师，负责先规划页级大纲，再交给后续模块做研究和设计。

你的任务是输出一个严格的 JSON，对应如下结构：
{
  "title": "整份 PPT 标题",
  "topic": "用户主题",
  "slides": [
    {
      "slide_index": 0,
      "layout": "cover",
      "topic": "本页主题",
      "objective": "本页想传达的目标",
      "image_prompt": "",
      "visual_mode": "auto"
    }
  ]
}

规则：
1. 只输出 JSON，不要 markdown，不要解释
2. `layout` 只能是：cover、toc、content、two_column、closing
3. 第 0 页必须是 cover，第 1 页必须是 toc，最后一页必须是 closing
4. 中间页需要围绕主题形成清晰叙事，页间不要重复
5. `topic` 要具体到适合单页研究和展开
6. `objective` 用一句话说明本页任务
7. 幻灯片总页数必须落在用户要求范围内
8. `image_prompt`：content/two_column 页必须填写一句英文视觉描述（15-40词），描述具体画面主体、场景、氛围，适合图片搜索或 AI 生图；cover/toc/closing 页留空字符串 ""
9. `visual_mode`：只允许 `auto`、`js_diagram`、`generated_image`
10. 对 content/two_column 页，你必须判断这页主视觉更适合哪种方式：
   - `js_diagram`：适合精确关系、结构原理、流程、图表、坐标系、机构示意
   - `generated_image`：适合场景感、整体形态、概念画面、复杂主视觉插图
   - `auto`：两者都可以，由后续模块自行决定
11. cover/toc/closing 页默认使用 `auto`
12. 优先按“表达目标”而不是学科名来判断：
   - 如果重点是精确关系、步骤逻辑、结构说明、定量对比，优先 `js_diagram`
   - 如果重点是场景感、整体形态、感性认知、复杂主视觉，优先 `generated_image`
"""

    @staticmethod
    def _contains_cjk(text: str | None) -> bool:
        if not text:
            return False
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    def _should_use_cjk_safe_fonts(self, outline: OutlinePlan, language: str) -> bool:
        if self._contains_cjk(language):
            return True
        if self._contains_cjk(outline.title) or self._contains_cjk(outline.topic):
            return True
        return any(
            self._contains_cjk(slide.topic) or self._contains_cjk(slide.objective)
            for slide in outline.slides
        )

    def _preferred_cjk_font(self) -> str:
        if sys.platform == "darwin":
            return "PingFang SC"
        return "Microsoft YaHei"

    def _stabilize_theme(self, theme: dict, outline: OutlinePlan, language: str) -> dict:
        normalized = dict(theme or {})
        if self._should_use_cjk_safe_fonts(outline, language):
            cjk_font = self._preferred_cjk_font()
            normalized["header_font"] = cjk_font
            normalized["body_font"] = cjk_font
            normalized["font_strategy_note"] = (
                f"本套中文 PPT 统一使用 {cjk_font}，避免导出评审图时出现英文字体对中文 fallback 不稳定。"
            )
        return normalized

    def _build_consistency_brief(self, theme: dict) -> str:
        lines = [
            "- 全部正文页共享同一套背景骨架：背景明暗关系、主色装饰位置、标题区位置、卡片语言必须连续一致。",
            "- 不要每页重新设计一套背景。正文页只允许在同一骨架里变换内容编排，例如单栏、双栏、卡片、图表区的组合。",
            "- 装饰锚点固定：如果使用左侧色带、右上水印、底部分隔条，这些元素的位置和透明度在整份 PPT 中应基本不变。",
            "- 标题系统固定：标题框起始位置、字号层级、标题与正文的垂直间距保持稳定，不要忽左忽右。",
            "- 卡片系统固定：圆角半径、描边粗细、阴影强弱、填充明度尽量统一。",
            "- 封面和结尾页要成对呼应；正文页之间追求家族感，而不是逐页换皮肤。",
        ]
        if theme.get("motif_description"):
            lines.insert(0, f"- 当前视觉母题：{theme.get('motif_description')}")
        note = theme.get("font_strategy_note")
        if note:
            lines.append(f"- 字体策略：{note}")
        return "\n".join(lines)

    def _build_outline_user_prompt(
        self,
        topic: str,
        min_slides: int = 6,
        max_slides: int = 10,
        style: str = "",
        audience: str = "general",
        language: str = "中文",
        content_requirements: str = "",
    ) -> str:
        audience = normalize_audience(audience)
        audience_ref = suggest_audience_label(audience) or "无"
        style = (style or "").strip()
        style_ref = suggest_style_label(style) or "无"
        style_text = style or "未指定（请根据主题与受众自动决定艺术方向）"
        prompt = (
            f"主题：{topic}\n"
            f"语言：{language}\n"
            f"风格原文：{style_text}\n"
            f"风格参考标签：{style_ref}\n"
            "如果风格原文未指定，表示用户把 art direction 交给你决定，"
            "不要机械套用固定风格枚举。\n"
            f"受众原文：{audience}\n"
            f"受众参考标签：{audience_ref}\n"
            "请优先理解并保留用户输入的原始风格和受众描述，参考标签仅用于辅助，不得覆盖原意。\n"
            f"受众适配提示：{self._build_audience_profile(audience)}\n"
            f"页数范围：{min_slides}-{max_slides}\n\n"
            "请先规划一份页级大纲，让后续 Research Agent 能逐页研究。"
        )
        extra = (content_requirements or "").strip()
        if extra:
            prompt += (
                "\n\n补充内容要求：\n"
                f"{extra}\n"
                "上面这段是用户明确希望 PPT 展示的重点内容、章节安排或特殊要求。"
                "规划页级大纲时必须优先吸收，不能忽略。"
            )
        return prompt

    def _build_user_prompt(self, topic: str, min_slides: int = 6, max_slides: int = 10,
                           style: str = "", audience: str = "general",
                           language: str = "中文", research_context: str = "",
                           outline_context: str = "", image_context: str = "") -> str:
        audience = normalize_audience(audience)
        style = (style or "").strip()
        audience_profile = self._build_audience_profile(audience)
        style_text = style or "未指定（请根据主题与受众自动决定艺术方向）"
        return (self._user_template
                .replace("{topic}", topic)
                .replace("{slide_width}", str(config.SLIDE_WIDTH_INCH))
                .replace("{slide_height}", str(config.SLIDE_HEIGHT_INCH))
                .replace("{language}", language)
                .replace("{style}", style_text)
                .replace("{audience}", audience)
                .replace("{audience_profile}", audience_profile)
                .replace("{style_reference}", suggest_style_label(style) or "无")
                .replace("{audience_reference}", suggest_audience_label(audience) or "无")
                .replace("{outline_context}", outline_context.strip() or "无显式页级大纲，请自行规划结构。")
                .replace("{research_context}", research_context.strip() or "无额外研究资料，请基于常识与准确性完成。")
                .replace("{image_context}", image_context.strip() or "无可用图片。")
                .replace("{min_slides}", str(min_slides))
                .replace("{max_slides}", str(max_slides)))

    def plan_outline(
        self,
        topic: str,
        min_slides: int = 6,
        max_slides: int = 10,
        style: str = "",
        audience: str = "general",
        language: str = "中文",
        content_requirements: str = "",
    ) -> OutlinePlan:
        print(f"[Planner] 开始规划页级大纲：{topic}")
        system_prompt = self._build_outline_system_prompt()
        user_prompt = self._build_outline_user_prompt(
            topic,
            min_slides=min_slides,
            max_slides=max_slides,
            style=style,
            audience=audience,
            language=language,
            content_requirements=content_requirements,
        )

        last_raw = ""
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[Planner] 大纲规划第 {attempt}/{MAX_RETRIES} 次尝试...")
            self.last_reasoning = ""
            raw_content, reasoning_text = stream_chat_completion_text(
                self.client,
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                on_reasoning_chunk=self._handle_reasoning_chunk,
                **build_chat_completion_kwargs(self.model),
            )
            self.last_reasoning = reasoning_text
            last_raw = raw_content

            try:
                data = self._extract_json(last_raw)
                outline = self._parse_outline_plan(data, topic)
                print(f"[Planner] 大纲规划完成，共 {len(outline.slides)} 页")
                return outline
            except Exception as e:
                err_msg = str(e)
                logger.warning(f"[Planner] 大纲规划第 {attempt} 次失败: {err_msg[:200]}")
                print(f"[Planner] 大纲规划第 {attempt} 次失败: {err_msg[:200]}")

        raise RuntimeError(f"页级大纲规划失败，最后响应前500字：{last_raw[:500]}")

    # ------------------------------------------------------------------ #
    #  逐页生成                                                             #
    # ------------------------------------------------------------------ #

    def decide_visual_theme(
        self,
        outline: OutlinePlan,
        style: str = "",
        audience: str = "general",
        language: str = "中文",
    ) -> dict:
        """
        一次 LLM 调用，确定整份 PPT 的视觉母题。
        返回 dict，包含 primary_color / secondary_color / accent_color /
        header_font / body_font / motif_description / pres_init_code。
        """
        style = (style or "").strip()
        style_ref = suggest_style_label(style) or "无"
        auto_style = not style or style.lower() == "auto"
        system = (
            "你是 PPT 视觉设计师。根据主题、受众和风格偏好输出整份 PPT 的视觉母题 JSON。\n"
            "只输出 JSON，不要解释。格式：\n"
            "{\n"
            '  "primary_color": "1E2761",\n'
            '  "secondary_color": "CADCFC",\n'
            '  "accent_color": "FFFFFF",\n'
            '  "header_font": "Georgia",\n'
            '  "body_font": "Calibri",\n'
            '  "motif_description": "左侧深色色带 + 圆形图标 + 卡片内容区",\n'
            '  "pres_init_code": "pres.layout = \\"LAYOUT_WIDE\\";"\n'
            "}\n\n"
            "## 设计规则（必须遵守）：\n"
            "1. **配色要与主题高度匹配**：不默认蓝色，配色应该让人一眼看出是为这个主题设计的\n"
            "2. **主色占比 60-70%**：primary_color 应占据视觉主导地位，secondary 和 accent 为辅\n"
            "3. **深浅对比**：primary_color 明度要低（深色），accent_color 要高对比\n"
            "4. **三明治结构**：封面和结尾页用深色背景，内容页用浅色（或全程深色营造高端感）\n"
            "5. **字体配对**：从以下配对中选择或自行搭配有个性的组合（不用 Arial）：\n"
            "   - Georgia + Calibri\n"
            "   - Arial Black + Arial\n"
            "   - Calibri + Calibri Light\n"
            "   - Cambria + Calibri\n"
            "   - Trebuchet MS + Calibri\n"
            "   - Impact + Arial\n"
            "   - Palatino + Garamond\n"
            "   - Consolas + Calibri\n"
            "6. **视觉母题贯穿**：选一个重复元素（圆形图标/色带/边框/卡片）在每页出现\n\n"
            "7. `motif_description` 必须是一行字符串，不要包含真实换行\n"
            "8. `pres_init_code` 必须是单行字符串，内部双引号需要正确转义\n\n"
            "如果用户没有指定风格或写的是 auto，你必须先自行完成 art direction："
            "结合主题、受众、页面结构判断最匹配的色彩、字体和视觉母题，"
            "不要机械套用固定 ocean/coral 等枚举。"
        )
        slide_topics = "\n".join(
            f"- 第{s.slide_index}页 [{s.layout.value}] {s.topic}"
            for s in outline.slides
        )
        if auto_style:
            style_block = (
                "风格偏好：未指定（请根据主题与受众自动决定）\n"
                "要求：先做 art direction，再输出主题 JSON。"
            )
        else:
            style_block = (
                f"风格偏好原文：{style}\n"
                f"风格参考标签：{style_ref}\n"
                "说明：这是软偏好（preference），不是硬枚举约束。"
                "请尽量吸收这个偏好，但最终仍要以主题、受众和内容结构为准。"
            )
        user = (
            f"主题：{outline.title}\n"
            f"受众：{audience}\n"
            f"语言：{language}\n\n"
            f"{style_block}\n\n"
            f"页面列表：\n{slide_topics}\n\n"
            "请选择与主题高度匹配的配色和视觉母题，避免审美惯性。"
        )
        retry_instruction = ""
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                attempt_user = user
                if retry_instruction:
                    attempt_user = f"{user}\n\n补充修正要求：{retry_instruction}"
                self.last_reasoning = ""
                raw_content, reasoning_text = stream_chat_completion_text(
                    self.client,
                    model=self.model,
                    max_tokens=1024,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": attempt_user}],
                    on_reasoning_chunk=self._handle_reasoning_chunk,
                    **build_chat_completion_kwargs(self.model),
                )
                self.last_reasoning = reasoning_text
                if not raw_content or not raw_content.strip():
                    raise ValueError("LLM 返回空内容")
                data = self._extract_json(raw_content)
                if isinstance(data, dict) and "primary_color" in data:
                    data = self._stabilize_theme(data, outline, language)
                    print(f"[Planner] 视觉母题：{data.get('motif_description', '')}")
                    return data
                raise ValueError("返回结果不是预期的主题 JSON 对象")
            except Exception as e:
                last_error = e
                retry_instruction = (
                    "请只返回一个单行 JSON 对象；"
                    "所有字符串字段都必须合法转义；"
                    "`motif_description` 不要出现换行；"
                    "`pres_init_code` 示例：pres.layout = \\\"LAYOUT_WIDE\\\";"
                )
                logger.warning(f"[Planner] decide_visual_theme 第 {attempt} 次失败: {e}")

        if last_error is not None:
            logger.warning(f"[Planner] decide_visual_theme 失败，使用默认: {last_error}")
        return self._stabilize_theme({
            "primary_color": "1F3864",
            "secondary_color": "2E75B6",
            "accent_color": "FFFFFF",
            "header_font": "Arial Black",
            "body_font": "Calibri",
            "motif_description": "深色封面 + 浅色内容页 + 左侧色带装饰",
            "pres_init_code": 'pres.layout = "LAYOUT_WIDE";',
        }, outline, language)

    def plan_slide(
        self,
        slide: SlideOutline,
        theme: dict,
        research: dict | None,
        image_path: str | None,
        prev_slides_summary: str = "",
        revision_feedback: SlideEvalResult | None = None,
        consistency_brief: str = "",
        content_requirements: str = "",
    ) -> str:
        """
        为单页生成 PptxGenJS 代码片段（不含 require / pres 初始化 / writeFile）。
        返回形如 `{ let slide = pres.addSlide(); ... }` 的代码块字符串。
        """
        SKIP = {SlideLayout.COVER, SlideLayout.TOC, SlideLayout.CLOSING}

        system = self._build_slide_system_prompt()

        last_raw = ""
        retry_feedback: list[str] = []
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                user = self._build_slide_user_prompt(
                    slide=slide,
                    theme=theme,
                    research=research,
                    image_path=image_path,
                    prev_slides_summary=prev_slides_summary,
                    revision_feedback=revision_feedback,
                    retry_feedback=retry_feedback,
                    consistency_brief=consistency_brief,
                    content_requirements=content_requirements,
                )
                self.last_reasoning = ""
                raw_content, reasoning_text = stream_chat_completion_text(
                    self.client,
                    model=self.model,
                    max_tokens=config.MAX_TOKENS_PLANNER,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    on_reasoning_chunk=self._handle_reasoning_chunk,
                    **build_chat_completion_kwargs(self.model),
                )
                self.last_reasoning = reasoning_text
                last_raw = raw_content
                code = self._extract_code(last_raw)
                code = self._sanitize_generated_code(code)
                self._validate_generated_slide_code(code, image_path=image_path)
                code = code.strip()
                if not code.startswith("{"):
                    code = "{\n" + code + "\n}"
                print(f"[Planner] 第 {slide.slide_index} 页生成成功（{len(code)} 字符）")
                return code
            except Exception as e:
                err_msg = str(e)
                print(f"[Planner] 第 {slide.slide_index} 页第 {attempt} 次失败: {err_msg[:150]}")
                logger.warning(f"[Planner] 第{slide.slide_index}页第{attempt}次失败: {e}")
                retry_feedback = [err_msg[:300]]

        raise RuntimeError(f"第{slide.slide_index}页生成失败，最后响应：{last_raw[:300]}")

    def _build_slide_user_prompt(
        self,
        slide: SlideOutline,
        theme: dict,
        research: dict | None,
        image_path: str | None,
        prev_slides_summary: str = "",
        revision_feedback: SlideEvalResult | None = None,
        retry_feedback: list[str] | None = None,
        consistency_brief: str = "",
        content_requirements: str = "",
    ) -> str:
        effective_visual_mode = resolve_visual_mode(slide)
        parts = [
            f"## 整份 PPT 视觉母题",
            f"- 主色：#{theme.get('primary_color', '1F3864')}",
            f"- 辅色：#{theme.get('secondary_color', '2E75B6')}",
            f"- 点缀色：#{theme.get('accent_color', 'FFFFFF')}",
            f"- 标题字体：{theme.get('header_font', 'Arial Black')}",
            f"- 正文字体：{theme.get('body_font', 'Calibri')}",
            f"- 视觉母题：{theme.get('motif_description', '')}",
            "",
            f"## 本页信息",
            f"- 页码：第 {slide.slide_index} 页",
            f"- 布局类型：{slide.layout.value}",
            f"- 页面主题：{slide.topic}",
            f"- 页面目标：{slide.objective}",
            f"- 主视觉方式：{effective_visual_mode.value}",
        ]

        if slide.image_prompt:
            parts.append(f"- 图片描述：{slide.image_prompt}")

        if research and slide.layout not in {SlideLayout.COVER, SlideLayout.TOC, SlideLayout.CLOSING}:
            summary = research.get("summary", "")
            bullets = research.get("bullet_points", [])
            key_data = research.get("key_data", [])
            if summary:
                parts.append(f"\n## 研究摘要\n{summary}")
            if bullets:
                parts.append("\n## 要点（必须完整呈现）")
                for b in bullets:
                    parts.append(f"- {b}")
            if key_data:
                parts.append("\n## 核心数据（用 stat-callout 大字展示）")
                for d in key_data:
                    parts.append(f"- {d}")

        if slide.visual_mode == VisualMode.AUTO and effective_visual_mode != VisualMode.AUTO:
            parts.append(
                "\n## 主视觉推断\n"
                f"大纲原始 visual_mode=auto；系统根据本页主题与目标推断，更适合 `{effective_visual_mode.value}`。"
            )

        if effective_visual_mode == VisualMode.JS_DIAGRAM:
            parts.append(
                "\n## 主视觉执行策略\n"
                "本页主视觉已决策为 `js_diagram`。请优先使用 addChart / addShape / addText 构建结构化示意图，"
                "不要把主视觉交给生成图片。"
            )
        elif effective_visual_mode == VisualMode.GENERATED_IMAGE:
            parts.append(
                "\n## 主视觉执行策略\n"
                "本页主视觉已决策为 `generated_image`。如果有可用图片资产，优先把该图片作为主视觉；"
                "如果图片暂时不可用，再退回 addChart / addShape / addText 做结构化示意。"
            )
        else:
            parts.append(
                "\n## 主视觉执行策略\n"
                "本页主视觉方式为 `auto`。请根据可用图片资产和内容表达需要，自主判断用图片还是用 addChart / addShape / addText。"
            )

        if image_path:
            parts.append(
                "\n## 图片资产\n"
                f"本页只有一个可用本地图片资产：{image_path}\n"
                "如果你决定使用图片，必须且只能使用这个本地路径的 addImage({ path: \"...\" })，"
                "禁止使用任何远程 URL、data URI、preencoded 占位图或临时 SVG。"
            )
        else:
            parts.append(
                "\n## 无图片模式（硬约束）\n"
                "本页没有可用图片资产。禁止 addImage，禁止任何远程图片 URL、base64/data URI、preencoded 占位图、临时 PNG/SVG。\n"
                "你仍然需要自己做“插图感”视觉元素，但必须使用 addChart / addShape / addText 组合出稳定图表或示意图。\n"
                "优先参考第 9 页那种正常视觉：图表、卡片、公式框、色块分区，而不是伪图片。"
            )

        extra = (content_requirements or "").strip()
        if extra:
            parts.append(
                "\n## 用户补充内容要求\n"
                f"{extra}\n"
                "如果这些要求与页面主题相关，必须在本页内容组织、标题措辞、信息层级里有所体现。"
            )

        if prev_slides_summary:
            parts.append(f"\n## 已生成页布局摘要（避免重复）\n{prev_slides_summary}")

        if consistency_brief:
            parts.append(f"\n## 跨页一致性（必须严格遵守）\n{consistency_brief}")

        if revision_feedback:
            parts.append(f"\n## 上一版问题（必须修复）")
            for issue in revision_feedback.issues:
                parts.append(f"- 问题：{issue}")
            for sug in revision_feedback.suggestions:
                parts.append(f"- 建议：{sug}")

        if retry_feedback:
            parts.append("\n## 上一轮代码硬错误（必须避免重犯）")
            for item in retry_feedback:
                parts.append(f"- {item}")

        parts.append(
            "\n## 输出要求\n"
            "只输出本页的代码片段，用 <code> 标签包裹。\n"
            "代码以 `{` 开头，以 `}` 结尾，内部第一行是 `let slide = pres.addSlide();`。\n"
            "不要包含 require、new pptxgen()、pres.layout、writeFile。\n"
            "严格遵守视觉母题的配色和字体，保持与其他页一致。\n"
            "布局仍然由你自主规划，不要套死模板；但装饰元素必须服从安全区规则，不能压住标题或正文。\n"
            "如果正文里需要出现英文引号或单引号，请在 JS 字符串中写成 `\\u0022` / `\\u0027`，不要输出裸引号。\n"
            "所有视觉元素必须是稳定可渲染的：本地图片、图表、形状、文本。禁止伪造图片路径和远程图片。\n\n"
            "## 画布尺寸（必须遵守）\n"
            "画布为 LAYOUT_WIDE：13.33\" × 7.5\"（英寸）。\n"
            "- 背景色块/形状必须覆盖整个画布（x:0, y:0, w:13.33, h:7.5）\n"
            "- 内容区域使用 0.5\" 外边距（x 从 0.5 开始，最大宽度 12.33\"）\n"
            "- 不要把所有元素挤在画布中间一小块区域\n"
            "- 封面/结尾页的标题和装饰必须铺满全屏，不能只占中间一小块\n\n"
            "## 装饰安全区（必须遵守）\n"
            "- 左侧深色竖条如果出现，默认贴左边或距离左边不超过 0.25\"，宽度保持在 0.08\"-0.18\"，不要伸进正文区\n"
            "- 左上角数学水印如果出现，必须是浅色低对比装饰，并放在标题上方或左上角留白区，不能与标题文本重叠\n"
            "- 标题框必须避开左侧装饰和水印；当使用左侧竖条时，正文页标题建议从 x>=0.7\" 开始\n"
            "- 装饰元素优先让位于内容，可减少、缩小或移除装饰，不能为了装饰牺牲可读性"
        )
        return "\n".join(parts)

    def plan_all_slides(
        self,
        outline: OutlinePlan,
        research_results: list[dict | None] | None,
        image_paths: list[str | None] | None,
        style: str = "",
        audience: str = "general",
        language: str = "中文",
        content_requirements: str = "",
    ) -> tuple[list[str], dict]:
        """
        逐页生成代码片段。
        返回 (slide_codes, theme)，slide_codes 与 outline.slides 一一对应。
        """
        research_results = research_results or [None] * len(outline.slides)
        image_paths = image_paths or [None] * len(outline.slides)

        print("[Planner] 确定视觉母题...")
        theme = self.decide_visual_theme(outline, style=style, audience=audience, language=language)
        consistency_brief = self._build_consistency_brief(theme)

        slide_codes: list[str] = []
        prev_summary_lines: list[str] = []

        for i, slide in enumerate(outline.slides):
            print(f"[Planner] 生成第 {slide.slide_index} 页（{slide.layout.value}: {slide.topic}）...")
            research = research_results[i] if i < len(research_results) else None
            img = image_paths[i] if i < len(image_paths) else None
            prev_summary = "\n".join(prev_summary_lines[-5:])  # 最近5页

            code = self.plan_slide(
                slide,
                theme,
                research,
                img,
                prev_summary,
                consistency_brief=consistency_brief,
                content_requirements=content_requirements,
            )
            slide_codes.append(code)
            prev_summary_lines.append(
                f"第{slide.slide_index}页 [{slide.layout.value}] {slide.topic} | 统一骨架："
                f"标题区稳定、装饰锚点固定、卡片语言一致"
            )

        print(f"[Planner] 逐页生成完成，共 {len(slide_codes)} 页")
        return slide_codes, theme

    def assemble_pptx(
        self,
        slide_codes: list[str],
        output_path: str,
        theme: dict,
    ) -> str:
        """
        把所有页代码片段组装成完整 JS，执行生成 .pptx。
        """
        output_path = os.path.abspath(output_path)
        safe_path = output_path.replace("\\", "/")

        header_font = theme.get("header_font", "Arial Black")
        body_font = theme.get("body_font", "Calibri")

        lines = [
            'const pptxgen = require("pptxgenjs");',
            "let pres = new pptxgen();",
            'pres.layout = "LAYOUT_WIDE";',
            f'pres.theme = {{ headFontFace: "{header_font}", bodyFontFace: "{body_font}", lang: "zh-CN" }};',
            f'pres.title = "Presentation";',
            f'// 视觉母题：{theme.get("motif_description", "")}',
            f'// 主色：#{theme.get("primary_color", "")}  辅色：#{theme.get("secondary_color", "")}  点缀：#{theme.get("accent_color", "")}',
            f'// 字体：{header_font} / {body_font}',
            "",
        ]

        for i, code in enumerate(slide_codes):
            lines.append(f"// ===== 第 {i} 页 =====")
            lines.append(code)
            lines.append("")

        lines.append(f'pres.writeFile({{ fileName: "{safe_path}" }});')

        full_code = "\n".join(lines)
        full_code = self._sanitize_generated_code(full_code)
        full_code = self._enforce_theme_fonts(full_code, theme)
        self._export_generated_js(slide_codes, full_code, output_path)

        total_chars = sum(len(c) for c in slide_codes)
        print(f"[Planner] 组装完成（{len(slide_codes)} 页，{total_chars} 字符），执行生成 PPTX...")
        run_js(full_code, output_path)
        print(f"[Planner] PPTX 生成成功: {output_path}")
        return output_path

    @staticmethod
    def _generated_js_dir(output_path: str) -> Path:
        output_file = Path(output_path)
        return output_file.parent / f"{output_file.stem}_generated_js"

    def _export_generated_js(self, slide_codes: list[str], full_code: str, output_path: str) -> None:
        export_dir = self._generated_js_dir(output_path)
        export_dir.mkdir(parents=True, exist_ok=True)

        for stale in export_dir.glob("slide_*.js"):
            stale.unlink()

        (export_dir / "presentation.js").write_text(full_code, encoding="utf-8")
        for index, code in enumerate(slide_codes):
            filename = export_dir / f"slide_{index:02d}.js"
            filename.write_text(code.rstrip() + "\n", encoding="utf-8")

        print(f"[Planner] 已导出每页 JS: {export_dir}")

    def _build_slide_system_prompt(self) -> str:
        """单页生成的 system prompt：设计规范 + API 教程，要求只输出片段。"""
        skill = self._skill_md
        start_idx = skill.find("### Before Starting")
        end_idx = skill.find("## QA")
        if start_idx >= 0 and end_idx > start_idx:
            design_section = skill[start_idx:end_idx].strip()
        elif start_idx >= 0:
            design_section = skill[start_idx:].strip()
        else:
            idx = skill.find("## Design Ideas")
            design_section = skill[idx:].strip() if idx >= 0 else skill

        return f"""你是一位顶级 PPT 视觉设计工程师，使用 PptxGenJS（Node.js）生成单页幻灯片代码片段。

以下是来自 Anthropic 官方 PPTX Design Skill 的设计规范，你必须严格遵守：

---
{design_section}
---

以下是本地增强的生成规则（硬约束 + 视觉设计原则 + 布局词汇表），你也必须严格遵守：

---
{self._local_rules_md}
---

以下是 PptxGenJS 的完整 API 教程，你生成的代码必须严格遵循这些用法和注意事项：

---
{self._pptxgenjs_md}
---

## 你的输出格式

输出单页代码片段，用 <code> 标签包裹。要求：
1. 代码以 `{{` 开头，以 `}}` 结尾
2. 内部第一行必须是 `let slide = pres.addSlide();`
3. 不要包含 `require`、`new pptxgen()`、`pres.layout`、`writeFile`
4. 严格遵守用户提供的视觉母题配色和字体
5. 正文里若需要出现英文引号或单引号，必须在 JS 字符串中写成 `\\u0022` / `\\u0027`
6. 你可以自由规划布局，但装饰元素必须服从安全区：左侧色带不要侵入正文区，左上角水印不得与标题重叠
7. 严格遵循上面所有设计规则和 PptxGenJS API 用法
"""

    def plan(self, topic: str, output_path: str = None,
             min_slides: int = 6, max_slides: int = 10,
             style: str = "", audience: str = "general",
             language: str = "中文", research_context: str = "",
             content_requirements: str = "",
             outline: OutlinePlan | None = None,
             research_results: list[dict | None] | None = None,
             image_paths: list[str | None] | None = None) -> tuple[str, list[str], dict]:
        """
        逐页生成 PptxGenJS 代码并执行，直接产出 .pptx 文件。

        Returns:
            (output_path, slide_codes, theme)
        """
        if output_path is None:
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            output_path = os.path.join(config.OUTPUT_DIR, "output.pptx")

        output_path = os.path.abspath(output_path)

        if outline is None:
            raise ValueError("plan() 需要传入 outline，请先调用 plan_outline()")

        slide_codes, theme = self.plan_all_slides(
            outline,
            research_results=research_results,
            image_paths=image_paths,
            style=style,
            audience=audience,
            language=language,
            content_requirements=content_requirements,
        )
        result_path = self.assemble_pptx(slide_codes, output_path, theme)
        return result_path, slide_codes, theme

    def enrich_image_prompts(
        self,
        outline: OutlinePlan,
        research_results: list[dict | None],
    ) -> OutlinePlan:
        """
        用 research 结果重新生成每页的 image_prompt。
        单次批量 LLM 调用，返回更新后的 OutlinePlan。
        cover/toc/closing 页保持空。
        """
        SKIP = {SlideLayout.COVER, SlideLayout.TOC, SlideLayout.CLOSING}

        # 构造输入：只包含需要生成 image_prompt 的页
        items = []
        for slide, result in zip(outline.slides, research_results or []):
            if slide.layout in SKIP:
                continue
            summary = (result or {}).get("summary", "")
            bullets = (result or {}).get("bullet_points", [])
            items.append({
                "slide_index": slide.slide_index,
                "topic": slide.topic,
                "summary": summary,
                "bullets": bullets[:3],  # 只取前3条，控制 token
            })

        if not items:
            return outline

        print(f"[Planner] 基于 research 重写 image_prompt（{len(items)} 页）...")
        system = (
            "你是图片描述专家。根据每页的研究摘要，为每页输出一句15-40词的英文图片搜索描述，"
            "描述具体画面主体、场景、氛围，适合图片搜索或 AI 生图。\n"
            "只输出 JSON 数组，格式：[{\"slide_index\": 0, \"image_prompt\": \"...\"}]"
        )
        user = "以下是各页研究内容，请为每页生成 image_prompt：\n" + json.dumps(items, ensure_ascii=False)

        try:
            self.last_reasoning = ""
            raw, reasoning_text = stream_chat_completion_text(
                self.client,
                model=self.model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                on_reasoning_chunk=self._handle_reasoning_chunk,
                **build_chat_completion_kwargs(self.model),
            )
            self.last_reasoning = reasoning_text
            enriched = self._extract_json(raw)
            if not isinstance(enriched, list):
                raise ValueError("期望 JSON 数组")

            prompt_map = {item["slide_index"]: item.get("image_prompt", "") for item in enriched}

            updated_slides = []
            for slide in outline.slides:
                if slide.slide_index in prompt_map and prompt_map[slide.slide_index]:
                    updated_slides.append(slide.model_copy(update={"image_prompt": prompt_map[slide.slide_index]}))
                else:
                    updated_slides.append(slide)

            print(f"[Planner] image_prompt 已基于 research 更新，覆盖 {len(prompt_map)} 页")
            return outline.model_copy(update={"slides": updated_slides})

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[Planner] enrich_image_prompts 失败，保留原始 image_prompt: {e}")
            return outline

    def outline_to_research_slides(self, outline: OutlinePlan) -> list:
        return [
            self._make_research_slide(slide)
            for slide in outline.slides
        ]

    def _build_audience_profile(self, audience: str) -> str:
        reference = suggest_audience_label(audience)
        lines = [
            "- 必须优先理解并保留用户输入的原始受众描述，不能粗暴压缩成固定桶。",
            "- `style` 主要控制视觉风格；如果 `style` 与 `audience` 有冲突，优先保证内容表达适配受众。",
            f"- 原始受众描述：{audience}",
            f"- 可选参考标签：{reference or '无'}",
        ]
        if reference:
            lines.append(f"- 可参考的适配策略：{AUDIENCE_PROFILES[reference]}")
        else:
            lines.append("- 如果原始描述不属于常见标签，请根据字面含义自行判断术语深度、语气和信息密度。")
        return "\n".join(lines)

    def _build_outline_context(self, outline: OutlinePlan | None) -> str:
        if not outline:
            return ""
        lines = [
            "以下是已经确认的页级大纲，请严格按此结构组织内容：",
        ]
        for slide in outline.slides:
            lines.append(
                f"- 第 {slide.slide_index} 页 | layout={slide.layout.value} | topic={slide.topic} | objective={slide.objective} | visual_mode={slide.visual_mode.value}"
            )
        return "\n".join(lines)

    def _build_research_context(
        self,
        outline: OutlinePlan | None,
        research_results: list[dict | None] | None,
    ) -> str:
        if not outline or not research_results:
            return ""

        lines = ["以下是逐页 ResearchAgent 结果，请充分吸收为对应页面的事实依据和表达素材："]
        for slide, result in zip(outline.slides, research_results):
            if not result:
                continue
            summary = result.get("summary") or slide.topic
            bullet_points = result.get("bullet_points") or []
            key_data = result.get("key_data") or []
            lines.append(f"- 第 {slide.slide_index} 页 {slide.topic}")
            lines.append(f"  - 摘要：{summary}")
            for point in bullet_points:
                lines.append(f"  - 要点：{point}")
            for kd in key_data:
                lines.append(f"  - 核心数据（适合大字展示）：{kd}")
        return "\n".join(lines)

    def _build_image_context(
        self,
        outline: OutlinePlan | None,
        image_paths: list[str | None] | None,
    ) -> str:
        if not outline or not image_paths:
            return ""

        lines = [
            "以下是 AssetAgent 为每页准备的本地图片路径，生成代码时用 addImage({ path: '...' }) 插入：",
            "图片建议位置：x=7.0, y=1.2, w=5.8, h=5.6（英寸），适合双栏右侧。",
            "如果某页布局不适合双栏，可自行调整坐标，但必须使用提供的图片路径。",
        ]
        has_image = False
        for slide, path in zip(outline.slides, image_paths):
            if path:
                lines.append(f"- 第 {slide.slide_index} 页（{slide.topic}）：{path}")
                has_image = True

        if not has_image:
            return ""
        return "\n".join(lines)

    def _parse_outline_plan(self, data: dict, topic: str) -> OutlinePlan:
        slides = data.get("slides")
        if not isinstance(slides, list):
            raise ValueError("大纲 JSON 缺少 slides 数组")

        normalized_slides = []
        for idx, slide in enumerate(slides):
            if not isinstance(slide, dict):
                raise ValueError("slides 内元素必须是对象")
            normalized_slides.append(
                {
                    "slide_index": slide.get("slide_index", idx),
                    "layout": slide.get("layout", "content"),
                    "topic": slide.get("topic") or f"{topic} - 第{idx + 1}页",
                    "objective": slide.get("objective", ""),
                    "image_prompt": slide.get("image_prompt") or None,
                    "visual_mode": slide.get("visual_mode") or "auto",
                }
            )

        normalized = {
            "title": data.get("title") or topic,
            "topic": data.get("topic") or topic,
            "slides": normalized_slides,
        }
        outline = OutlinePlan.model_validate(normalized)
        outline = self._normalize_outline_structure(outline)
        self._validate_outline_structure(outline)
        return outline

    def _normalize_outline_structure(self, outline: OutlinePlan) -> OutlinePlan:
        slides = list(outline.slides)
        if not slides:
            return outline

        if len(slides) < 4:
            raise ValueError("PPT 至少需要 4 页，才能满足 cover / toc / content / closing 结构")

        slides[0] = slides[0].model_copy(
            update={
                "slide_index": 0,
                "layout": SlideLayout.COVER,
                "topic": slides[0].topic or outline.title,
                "objective": slides[0].objective or "概括整份 PPT 主题并建立开场氛围",
                "image_prompt": None,
                "visual_mode": VisualMode.AUTO,
            }
        )

        toc_topic = slides[1].topic
        if not toc_topic or toc_topic == outline.topic:
            toc_topic = "目录"
        slides[1] = slides[1].model_copy(
            update={
                "slide_index": 1,
                "layout": SlideLayout.TOC,
                "topic": toc_topic,
                "objective": slides[1].objective or "概览整份 PPT 的章节结构",
                "image_prompt": None,
                "visual_mode": VisualMode.AUTO,
            }
        )

        last_index = len(slides) - 1
        closing_topic = slides[-1].topic
        if not closing_topic or closing_topic == outline.topic:
            closing_topic = "总结与展望"
        slides[-1] = slides[-1].model_copy(
            update={
                "slide_index": last_index,
                "layout": SlideLayout.CLOSING,
                "topic": closing_topic,
                "objective": slides[-1].objective or "总结核心信息并完成收束",
                "image_prompt": None,
                "visual_mode": VisualMode.AUTO,
            }
        )

        middle = slides[2:-1]
        if middle and not any(item.layout in {SlideLayout.CONTENT, SlideLayout.TWO_COLUMN} for item in middle):
            first_middle = middle[0]
            slides[2] = first_middle.model_copy(
                update={
                    "slide_index": 2,
                    "layout": SlideLayout.CONTENT,
                    "objective": first_middle.objective or "展开主题的第一个核心知识点",
                }
            )

        for idx in range(2, last_index):
            slides[idx] = slides[idx].model_copy(update={"slide_index": idx})

        return outline.model_copy(update={"slides": slides})

    def _validate_outline_structure(self, outline: OutlinePlan) -> None:
        if not outline.slides:
            raise ValueError("大纲不能为空")
        first = outline.slides[0].layout.value
        second = outline.slides[1].layout.value if len(outline.slides) > 1 else None
        last = outline.slides[-1].layout.value
        if first != "cover":
            raise ValueError("第 0 页必须是 cover")
        if second != "toc":
            raise ValueError("第 1 页必须是 toc")
        if last != "closing":
            raise ValueError("最后一页必须是 closing")
        for idx, slide in enumerate(outline.slides):
            if slide.slide_index != idx:
                raise ValueError("slide_index 必须从 0 开始连续递增")
        middle_layouts = [s.layout for s in outline.slides[2:-1]]
        if middle_layouts and not any(layout in {SlideLayout.CONTENT, SlideLayout.TWO_COLUMN} for layout in middle_layouts):
            raise ValueError("中间页至少需要一个 content 或 two_column 布局")

    def _make_research_slide(self, slide: SlideOutline):
        from ..models.schemas import SlideSpec, TextElement

        return SlideSpec(
            slide_index=slide.slide_index,
            layout=slide.layout,
            topic=slide.topic,
            elements=[
                TextElement(
                    type="title",
                    content=slide.topic,
                    x=0.5,
                    y=0.3,
                    width=12.0,
                    height=0.9,
                    font_size=32,
                    bold=True,
                    color="#1F3864",
                )
            ],
        )

    def _fix_js_quotes(self, code: str) -> str:
        """修复 JS 字符串中的内嵌引号，避免中英文混排文本打断字符串字面量。"""
        return self._escape_problematic_js_string_quotes(code)

    def _extract_code(self, raw: str) -> str:
        """从 LLM 响应中提取 <code>...</code> 或 ```javascript...``` 中的代码。"""
        m = re.search(r"<code>(.*?)</code>", raw, re.DOTALL)
        if m:
            return m.group(1).strip()

        m = re.search(r"```(?:javascript|js)\s*(.*?)```", raw, re.DOTALL)
        if m:
            return m.group(1).strip()

        m = re.search(r"```\s*(.*?)```", raw, re.DOTALL)
        if m:
            return m.group(1).strip()

        raise ValueError("LLM 响应中未找到 <code> 或 ```javascript 代码块")

    def _extract_json(self, raw: str):
        cleaned = str(raw or "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        candidates: list[str] = []
        if cleaned:
            candidates.append(cleaned)

        extracted = self._extract_outer_json_blob(cleaned)
        if extracted and extracted not in candidates:
            candidates.append(extracted)

        sanitized_cleaned = self._sanitize_json_strings(cleaned)
        if sanitized_cleaned and sanitized_cleaned not in candidates:
            candidates.append(sanitized_cleaned)

        sanitized_extracted = self._extract_outer_json_blob(sanitized_cleaned)
        if sanitized_extracted and sanitized_extracted not in candidates:
            candidates.append(sanitized_extracted)

        repaired_candidates = [
            self._repair_missing_json_commas(sanitized_cleaned),
            self._repair_missing_json_commas(sanitized_extracted),
            self._close_truncated_json(sanitized_extracted),
            self._close_truncated_json(self._repair_missing_json_commas(sanitized_extracted)),
            self._close_truncated_json(self._repair_missing_json_commas(sanitized_cleaned)),
        ]
        for repaired in repaired_candidates:
            if repaired and repaired not in candidates:
                candidates.append(repaired)

        for candidate in candidates:
            try:
                return json.loads(candidate, strict=False)
            except json.JSONDecodeError:
                continue

        preview = (extracted or cleaned)[:200]
        raise ValueError(f"无法解析 JSON，原始内容前200字：{preview}")

    @staticmethod
    def _extract_outer_json_blob(text: str) -> str:
        positions = [(text.find("{"), "{"), (text.find("["), "[")]
        positions = [(pos, ch) for pos, ch in positions if pos >= 0]
        if not positions:
            return text

        start, opener = min(positions, key=lambda item: item[0])
        closing_map = {"{": "}", "[": "]"}
        stack = [closing_map[opener]]
        in_string = False
        escape = False

        for i in range(start + 1, len(text)):
            ch = text[i]

            if escape:
                escape = False
                continue

            if ch == "\\":
                escape = True
                continue

            if ch == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if ch in closing_map:
                stack.append(closing_map[ch])
                continue

            if stack and ch == stack[-1]:
                stack.pop()
                if not stack:
                    return text[start:i + 1]

        return text[start:]

    @staticmethod
    def _sanitize_json_strings(text: str) -> str:
        quote_escape_map = {
            "\u201c": "\\u201c",
            "\u201d": "\\u201d",
            "\u2018": "\\u2018",
            "\u2019": "\\u2019",
            "\u300c": "\\u300c",
            "\u300d": "\\u300d",
            "\u300e": "\\u300e",
            "\u300f": "\\u300f",
        }

        result: list[str] = []
        in_string = False
        i = 0

        while i < len(text):
            ch = text[i]
            next_ch = text[i + 1] if i + 1 < len(text) else ""

            if ch == "\\" and in_string and next_ch:
                result.extend((ch, next_ch))
                i += 2
                continue

            if not in_string:
                result.append(ch)
                if ch == '"':
                    in_string = True
                i += 1
                continue

            if ch in quote_escape_map:
                result.append(quote_escape_map[ch])
                i += 1
                continue

            if ch == "\n":
                result.append("\\n")
                i += 1
                continue

            if ch == "\r":
                result.append("\\r")
                i += 1
                continue

            if ch == "\t":
                result.append("\\t")
                i += 1
                continue

            if ch == '"':
                if PlannerAgent._is_probable_json_string_end(text, i):
                    in_string = False
                    result.append(ch)
                else:
                    result.append("\\u0022")
                i += 1
                continue

            result.append(ch)
            i += 1

        sanitized = "".join(result)
        sanitized = re.sub(r",(\s*[}\]])", r"\1", sanitized)
        return sanitized

    @staticmethod
    def _consume_json_string(text: str, start: int) -> int:
        i = start + 1
        escape = False

        while i < len(text):
            ch = text[i]
            if escape:
                escape = False
                i += 1
                continue
            if ch == "\\":
                escape = True
                i += 1
                continue
            if ch == '"':
                return i + 1
            i += 1

        return len(text)

    @staticmethod
    def _consume_json_literal(text: str, start: int) -> int:
        i = start
        while i < len(text) and text[i] in "-+0123456789.eE":
            i += 1
        return i

    @staticmethod
    def _repair_missing_json_commas(text: str) -> str:
        if not text:
            return text

        result: list[str] = []
        stack: list[dict] = []
        i = 0

        def current():
            return stack[-1] if stack else None

        def maybe_insert_comma(next_char: str) -> None:
            ctx = current()
            if not ctx or ctx["state"] != "expect_comma_or_end":
                return
            if next_char.isspace() or next_char in ",}]":
                return
            result.append(",")
            ctx["state"] = "expect_key_or_end" if ctx["type"] == "object" else "expect_value_or_end"

        def mark_value_consumed() -> None:
            ctx = current()
            if not ctx:
                return
            if ctx["type"] == "object" and ctx["state"] == "expect_value":
                ctx["state"] = "expect_comma_or_end"
            elif ctx["type"] == "array" and ctx["state"] == "expect_value_or_end":
                ctx["state"] = "expect_comma_or_end"

        while i < len(text):
            ch = text[i]

            if ch.isspace():
                result.append(ch)
                i += 1
                continue

            maybe_insert_comma(ch)
            ctx = current()

            if ch == "{":
                was_value = bool(
                    ctx and (
                        (ctx["type"] == "object" and ctx["state"] == "expect_value")
                        or (ctx["type"] == "array" and ctx["state"] == "expect_value_or_end")
                    )
                )
                result.append(ch)
                stack.append({"type": "object", "state": "expect_key_or_end", "was_value": was_value})
                i += 1
                continue

            if ch == "[":
                was_value = bool(
                    ctx and (
                        (ctx["type"] == "object" and ctx["state"] == "expect_value")
                        or (ctx["type"] == "array" and ctx["state"] == "expect_value_or_end")
                    )
                )
                result.append(ch)
                stack.append({"type": "array", "state": "expect_value_or_end", "was_value": was_value})
                i += 1
                continue

            if ch == "}":
                result.append(ch)
                if stack and stack[-1]["type"] == "object":
                    popped = stack.pop()
                    if popped.get("was_value"):
                        mark_value_consumed()
                i += 1
                continue

            if ch == "]":
                result.append(ch)
                if stack and stack[-1]["type"] == "array":
                    popped = stack.pop()
                    if popped.get("was_value"):
                        mark_value_consumed()
                i += 1
                continue

            if ch == ",":
                result.append(ch)
                if ctx:
                    ctx["state"] = "expect_key_or_end" if ctx["type"] == "object" else "expect_value_or_end"
                i += 1
                continue

            if ch == ":":
                result.append(ch)
                if ctx and ctx["type"] == "object" and ctx["state"] == "expect_colon":
                    ctx["state"] = "expect_value"
                i += 1
                continue

            if ch == '"':
                end = PlannerAgent._consume_json_string(text, i)
                result.append(text[i:end])
                if ctx:
                    if ctx["type"] == "object":
                        if ctx["state"] == "expect_key_or_end":
                            ctx["state"] = "expect_colon"
                        elif ctx["state"] == "expect_value":
                            ctx["state"] = "expect_comma_or_end"
                    elif ctx["type"] == "array" and ctx["state"] == "expect_value_or_end":
                        ctx["state"] = "expect_comma_or_end"
                i = end
                continue

            literal_match = None
            for literal in ("true", "false", "null"):
                if text.startswith(literal, i):
                    literal_match = literal
                    break
            if literal_match:
                result.append(literal_match)
                mark_value_consumed()
                i += len(literal_match)
                continue

            if ch in "-0123456789":
                end = PlannerAgent._consume_json_literal(text, i)
                result.append(text[i:end])
                mark_value_consumed()
                i = end
                continue

            result.append(ch)
            i += 1

        repaired = "".join(result)
        repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
        return repaired

    @staticmethod
    def _close_truncated_json(text: str) -> str:
        if not text:
            return text

        result: list[str] = []
        closers: list[str] = []
        in_string = False
        escape = False
        closing_map = {"{": "}", "[": "]"}

        for ch in text:
            result.append(ch)

            if escape:
                escape = False
                continue

            if ch == "\\":
                escape = True
                continue

            if ch == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if ch in closing_map:
                closers.append(closing_map[ch])
            elif closers and ch == closers[-1]:
                closers.pop()

        if in_string:
            result.append('"')

        while closers:
            result.append(closers.pop())

        return "".join(result)

    @staticmethod
    def _is_probable_json_string_end(text: str, quote_index: int) -> bool:
        j = quote_index + 1
        while j < len(text) and text[j].isspace():
            j += 1
        return j >= len(text) or text[j] in "\",:}]"

    def _build_error_feedback(self, err_msg: str) -> list[str]:
        feedback = [err_msg[:500]]
        if "Missing/Invalid shape parameter" in err_msg:
            feedback.append(
                "修正所有 `addShape()` 的第一个参数：请使用合法形状值，例如 "
                "`\"rect\"`、`\"ellipse\"`、`\"line\"`、`\"roundRect\"`，"
                "或等价的 `pres.shapes.RECTANGLE/OVAL/LINE/ROUNDED_RECTANGLE`。"
            )
        return feedback

    def _validate_generated_slide_code(self, code: str, image_path: str | None) -> None:
        """
        约束图片使用策略：
        - 无图片资产时，禁止 addImage / 远程 URL / preencoded 占位图
        - 有图片资产时，只允许引用该本地图片，不允许远程 URL 或伪图片
        """
        forbidden_markers = [
            "https://",
            "http://",
            "data:image",
            "images.unsplash.com",
            "preencoded.png",
            ".svg",
        ]
        lowered = code.lower()

        if image_path is None:
            if "addimage(" in lowered:
                raise ValueError("无图片模式下禁止使用 addImage；请改用 addChart/addShape/addText 做正常插图。")
            for marker in forbidden_markers:
                if marker in lowered:
                    raise ValueError(f"无图片模式下禁止引用图片资源：{marker}")
            return

        for marker in forbidden_markers:
            if marker in lowered:
                raise ValueError(f"检测到非法图片引用：{marker}。有图片资产时只能使用提供的本地图片路径。")

        path_literals = re.findall(r'addImage\s*\(\s*\{[^}]*?\bpath\s*:\s*["\']([^"\']+)["\']', code, re.DOTALL)
        invalid = [p for p in path_literals if p != image_path]
        if invalid:
            raise ValueError(f"检测到未授权图片路径：{invalid[0]}")

    def _sanitize_generated_code(self, code: str) -> str:
        def shape_literal(token: str) -> str | None:
            normalized = re.sub(r"[^A-Za-z0-9]+", "", token).lower()
            value = SHAPE_VALUE_MAP.get(normalized)
            return f'"{value}"' if value else None

        def replace_shape_member(match: re.Match) -> str:
            token = match.group(1)
            return shape_literal(token) or match.group(0)

        def replace_addshape_string(match: re.Match) -> str:
            prefix, quote, token, suffix = match.groups()
            return f"{prefix}{shape_literal(token) or f'{quote}{token}{quote}'}{suffix}"

        code = re.sub(
            r"\b[\w$]+\.(?:ShapeType|shapes)\.([A-Za-z0-9_]+)\b",
            replace_shape_member,
            code,
        )
        code = re.sub(
            r"(addShape\(\s*)([\"'])([A-Za-z0-9_]+)\2(\s*,)",
            replace_addshape_string,
            code,
        )
        code = self._strip_stray_js_bareword_lines(code)
        return self._escape_problematic_js_string_quotes(code)

    @staticmethod
    def _strip_stray_js_bareword_lines(code: str) -> str:
        lines = code.splitlines()
        if not lines:
            return code

        result: list[str] = []
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]{0,15}", stripped or ""):
                result.append(line)
                continue

            prev_nonempty = ""
            for prev_index in range(index - 1, -1, -1):
                candidate = lines[prev_index].strip()
                if candidate:
                    prev_nonempty = candidate
                    break

            next_nonempty = ""
            for next_index in range(index + 1, len(lines)):
                candidate = lines[next_index].strip()
                if candidate:
                    next_nonempty = candidate
                    break

            if prev_nonempty in {"{", ""} and (
                next_nonempty == "{"
                or next_nonempty.startswith("let slide = pres.addSlide(")
            ):
                continue

            result.append(line)

        sanitized = "\n".join(result)
        if code.endswith("\n"):
            sanitized += "\n"
        return sanitized

    def _enforce_theme_fonts(self, code: str, theme: dict) -> str:
        target_font = theme.get("body_font") or theme.get("header_font")
        if not target_font:
            return code

        def replace_fontface(match: re.Match) -> str:
            prefix, quote, _font_name, suffix = match.groups()
            return f"{prefix}{quote}{target_font}{suffix}"

        code = re.sub(
            r'(\bfontFace\s*:\s*)(["\'])([^"\']+)(\2)',
            replace_fontface,
            code,
        )
        return code

    def _is_probable_js_string_end(self, code: str, quote_index: int) -> bool:
        """
        判断字符串中的当前引号是否更像字面量结束，而不是正文里的引用符号。

        这里故意保守：只有在后面紧跟明显的 JS 分隔符时才视为闭合，
        其余情况统一转义，优先保证生成代码可执行。
        """
        j = quote_index + 1
        while j < len(code) and code[j].isspace():
            j += 1

        if j >= len(code):
            return True

        if code.startswith("//", j) or code.startswith("/*", j):
            return True

        return code[j] in ",:;)}]+-*/%?&|<=>"

    @staticmethod
    def _looks_like_missing_js_comma_after_string(code: str, quote_index: int) -> bool:
        j = quote_index + 1
        saw_newline = False

        while j < len(code) and code[j].isspace():
            if code[j] in "\r\n":
                saw_newline = True
            j += 1

        if not saw_newline or j >= len(code):
            return False

        if not (code[j].isalpha() or code[j] in "_$"):
            return False

        k = j + 1
        while k < len(code) and (code[k].isalnum() or code[k] in "_$"):
            k += 1
        while k < len(code) and code[k].isspace():
            k += 1

        return k < len(code) and code[k] == ":"

    def _escape_problematic_js_string_quotes(self, code: str) -> str:
        """
        逐字符扫描 JS 代码，修复字符串字面量中的问题引号。

        主要处理两类问题：
        1. 中文/日文弯引号直接出现在字符串里，统一转成 \\uXXXX
        2. 字符串内部裸露的 ASCII 单/双引号，若看起来不像字符串结束，则转成 \\u0027 / \\u0022
        """
        quote_escape_map = {
            "\u201c": "\\u201c",
            "\u201d": "\\u201d",
            "\u2018": "\\u2018",
            "\u2019": "\\u2019",
            "\u300c": "\\u300c",
            "\u300d": "\\u300d",
            "\u300e": "\\u300e",
            "\u300f": "\\u300f",
        }

        result: list[str] = []
        state = "normal"
        quote_char = None
        i = 0

        while i < len(code):
            ch = code[i]
            next_ch = code[i + 1] if i + 1 < len(code) else ""

            if state == "normal":
                if ch == "/" and next_ch == "/":
                    result.extend((ch, next_ch))
                    state = "line_comment"
                    i += 2
                    continue
                if ch == "/" and next_ch == "*":
                    result.extend((ch, next_ch))
                    state = "block_comment"
                    i += 2
                    continue
                if ch in ('"', "'"):
                    result.append(ch)
                    state = "string"
                    quote_char = ch
                    i += 1
                    continue
                if ch == "`":
                    result.append(ch)
                    state = "template"
                    i += 1
                    continue

                result.append(ch)
                i += 1
                continue

            if state == "line_comment":
                result.append(ch)
                i += 1
                if ch == "\n":
                    state = "normal"
                continue

            if state == "block_comment":
                if ch == "*" and next_ch == "/":
                    result.extend((ch, next_ch))
                    state = "normal"
                    i += 2
                    continue
                result.append(ch)
                i += 1
                continue

            if state == "template":
                if ch == "\\" and next_ch:
                    result.extend((ch, next_ch))
                    i += 2
                    continue
                result.append(ch)
                i += 1
                if ch == "`":
                    state = "normal"
                continue

            if ch == "\\" and next_ch:
                result.extend((ch, next_ch))
                i += 2
                continue

            if ch in quote_escape_map:
                result.append(quote_escape_map[ch])
                i += 1
                continue

            if ch == quote_char:
                if self._is_probable_js_string_end(code, i):
                    result.append(ch)
                    state = "normal"
                    quote_char = None
                elif self._looks_like_missing_js_comma_after_string(code, i):
                    result.append(ch)
                    result.append(",")
                    state = "normal"
                    quote_char = None
                else:
                    result.append("\\u0022" if quote_char == '"' else "\\u0027")
                i += 1
                continue

            result.append(ch)
            i += 1

        return "".join(result)

    def _inject_output_path(self, code: str, output_path: str) -> str:
        """确保代码中的 writeFile 使用正确的 output_path。"""
        safe_path = output_path.replace("\\", "/")

        if "writeFile" in code or "writeToFile" in code:
            code = re.sub(
                r'fileName\s*:\s*["\'][^"\']*["\']',
                f'fileName: "{safe_path}"',
                code
            )
            return code

        code += f'\npres.writeFile({{ fileName: "{safe_path}" }});\n'
        return code
