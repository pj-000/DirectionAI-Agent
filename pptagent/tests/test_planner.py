import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
from agents.planner import (
    PlannerAgent,
    normalize_audience,
    suggest_audience_label,
    suggest_style_label,
)
from models.schemas import OutlinePlan, SlideOutline, SlideLayout, VisualMode, resolve_visual_mode


@pytest.fixture
def mock_planner():
    """返回一个 mock 掉 API 和文件读取的 PlannerAgent"""
    with patch("agents.planner.OpenAI"):
        with patch("agents.planner.assert_skill_present"):
            with patch("agents.planner.Path") as mock_path:
                mock_path.return_value.read_text.return_value = "mock prompt"
                planner = PlannerAgent()
                planner.client = MagicMock()
                planner._skill_md = "## Design Ideas\n- test"
                planner._pptxgenjs_md = "# PptxGenJS Tutorial\n- test"
                planner._user_template = (
                    "topic={topic} lang={language} style={style} style_ref={style_reference} "
                    "audience={audience} audience_ref={audience_reference} "
                    "profile={audience_profile} outline={outline_context} "
                    "research={research_context} "
                    "min={min_slides} max={max_slides}"
                )
                yield planner


def test_extract_code_tag(mock_planner):
    raw = "Here is the code:\n<code>\nconst pptxgen = require('pptxgenjs');\n</code>\nDone."
    code = mock_planner._extract_code(raw)
    assert "pptxgenjs" in code


def test_extract_javascript_block(mock_planner):
    raw = "```javascript\nconst pptxgen = require('pptxgenjs');\n```"
    code = mock_planner._extract_code(raw)
    assert "pptxgenjs" in code


def test_extract_generic_block(mock_planner):
    raw = "```\nconst pptxgen = require('pptxgenjs');\n```"
    code = mock_planner._extract_code(raw)
    assert "pptxgenjs" in code


def test_no_code_raises(mock_planner):
    with pytest.raises(ValueError, match="未找到"):
        mock_planner._extract_code("这里没有代码")


def test_inject_output_path_existing_writefile(mock_planner):
    code = 'pres.writeFile({ fileName: "old.pptx" });'
    new_code = mock_planner._inject_output_path(code, "/tmp/new.pptx")
    assert 'fileName: "/tmp/new.pptx"' in new_code
    assert "old.pptx" not in new_code


def test_inject_output_path_append_writefile(mock_planner):
    code = 'const pptxgen = require("pptxgenjs");\nlet pres = new pptxgen();'
    new_code = mock_planner._inject_output_path(code, "/tmp/new.pptx")
    assert 'pres.writeFile({ fileName: "/tmp/new.pptx" });' in new_code


def test_build_user_prompt_includes_style(mock_planner):
    prompt = mock_planner._build_user_prompt(
        "测试主题",
        6,
        8,
        language="English",
        style="minimal",
        audience="投资人",
        outline_context="第0页 cover",
        research_context="主题摘要：行业增长快\n- 要点A",
    )
    assert "style=minimal" in prompt
    assert "style_ref=minimal" in prompt
    assert "topic=测试主题" in prompt
    assert "lang=English" in prompt
    assert "audience=投资人" in prompt
    assert "audience_ref=investor" in prompt
    assert "市场空间" in prompt
    assert "outline=第0页 cover" in prompt
    assert "research=主题摘要：行业增长快\n- 要点A" in prompt


def test_build_user_prompt_uses_auto_art_direction_when_style_blank(mock_planner):
    prompt = mock_planner._build_user_prompt(
        "测试主题",
        6,
        8,
        language="中文",
        style="",
        audience="general",
    )
    assert "style=未指定（请根据主题与受众自动决定艺术方向）" in prompt
    assert "style_ref=无" in prompt


def test_extract_json_handles_code_fence(mock_planner):
    data = mock_planner._extract_json('```json\n{"title":"测试"}\n```')
    assert data["title"] == "测试"


def test_extract_json_repairs_missing_comma_between_slide_objects(mock_planner):
    raw = """{
  "title": "高等数学",
  "topic": "高等数学",
  "slides": [
    {
      "slide_index": 0,
      "layout": "cover",
      "topic": "封面",
      "objective": "课程导入",
      "image_prompt": ""
    }
    {
      "slide_index": 1,
      "layout": "toc",
      "topic": "目录",
      "objective": "建立结构",
      "image_prompt": ""
    }
  ]
}"""

    data = mock_planner._extract_json(raw)

    assert len(data["slides"]) == 2
    assert data["slides"][1]["layout"] == "toc"


def test_extract_json_repairs_missing_comma_between_properties(mock_planner):
    raw = """{
  "title": "高等数学",
  "topic": "高等数学",
  "slides": [
    {
      "slide_index": 2,
      "layout": "content",
      "topic": "反函数"
      "objective": "解释一一对应与求法"
      "image_prompt": "A mathematics classroom chalkboard with inverse function graph"
    }
  ]
}"""

    data = mock_planner._extract_json(raw)

    assert data["slides"][0]["topic"] == "反函数"
    assert data["slides"][0]["objective"] == "解释一一对应与求法"


def test_extract_json_escapes_inline_quotes_in_math_strings(mock_planner):
    raw = """{
  "title": "高等数学",
  "topic": "高等数学",
  "slides": [
    {
      "slide_index": 2,
      "layout": "content",
      "topic": "反函数 "f(x)" 的概念",
      "objective": "说明 "一一对应" 是存在反函数的关键条件",
      "image_prompt": "A clean textbook page with inverse function graph and annotations"
    }
  ]
}"""

    data = mock_planner._extract_json(raw)

    assert data["slides"][0]["topic"] == '反函数 "f(x)" 的概念'
    assert data["slides"][0]["objective"] == '说明 "一一对应" 是存在反函数的关键条件'


def test_extract_json_escapes_raw_newlines_inside_strings(mock_planner):
    raw = """{
  "title": "高等数学",
  "topic": "高等数学",
  "slides": [
    {
      "slide_index": 2,
      "layout": "content",
      "topic": "基本初等函数",
      "objective": "先给出定义
再说明分类",
      "image_prompt": "A clean calculus notebook with labeled elementary function graphs"
    }
  ]
}"""

    data = mock_planner._extract_json(raw)

    assert data["slides"][0]["objective"] == "先给出定义\n再说明分类"


def test_extract_json_handles_theme_json_with_multiline_text_and_inner_quotes(mock_planner):
    raw = """{
  "primary_color": "1B4332",
  "secondary_color": "D8F3DC",
  "accent_color": "F4A261",
  "header_font": "Cambria",
  "body_font": "Calibri",
  "motif_description": "左侧深森林绿竖向色带（宽约22%）
中部米白卡片承载公式与定义
底部橙色细线用于强调关键结论",
  "pres_init_code": "pres.layout = "LAYOUT_WIDE";"
}"""

    data = mock_planner._extract_json(raw)

    assert data["primary_color"] == "1B4332"
    assert "中部米白卡片" in data["motif_description"]
    assert data["pres_init_code"] == 'pres.layout = "LAYOUT_WIDE";'


def test_sanitize_generated_code_fixes_shape_aliases(mock_planner):
    code = (
        'slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: 1, h: 1 });\n'
        'slide.addShape("RECTANGLE", { x: 1, y: 1, w: 1, h: 1 });'
    )
    sanitized = mock_planner._sanitize_generated_code(code)
    assert 'addShape("rect"' in sanitized


def test_sanitize_generated_code_escapes_mixed_script_inline_quotes(mock_planner):
    code = (
        'slide.addText("用"任意小的 ε"约束输出精度，用"足够小的 δ"控制输入范围", '
        '{ x: 0.5, y: 1, w: 6, h: 1 });'
    )

    sanitized = mock_planner._sanitize_generated_code(code)

    assert '用\\u0022任意小的 ε\\u0022约束输出精度，用\\u0022足够小的 δ\\u0022控制输入范围' in sanitized


def test_sanitize_generated_code_keeps_ternary_string_literals(mock_planner):
    code = 'fill: { color: i === 1 ? "E8C94F" : "1B3A2D" },'

    sanitized = mock_planner._sanitize_generated_code(code)

    assert sanitized == code


def test_sanitize_generated_code_repairs_missing_comma_after_string_property(mock_planner):
    code = (
        'slide.addShape("rect", {\n'
        '  x: 1,\n'
        '  fill: { color: "E67E22"\n'
        '  rotate: -35\n'
        '});'
    )

    sanitized = mock_planner._sanitize_generated_code(code)

    assert 'fill: { color: "E67E22",' in sanitized
    assert '\\u0022' not in sanitized


def test_sanitize_generated_code_removes_stray_bareword_line_between_blocks(mock_planner):
    code = '{\non\n{\n  let slide = pres.addSlide();\n}\n}'

    sanitized = mock_planner._sanitize_generated_code(code)

    assert "\non\n" not in sanitized
    assert "{\n{\n  let slide = pres.addSlide();" in sanitized


def test_export_generated_js_writes_per_slide_files(tmp_path):
    output_path = str(tmp_path / "demo.pptx")
    planner = object.__new__(PlannerAgent)

    planner._export_generated_js(
        slide_codes=["{ let slide = pres.addSlide(); }", "{ let slide = pres.addSlide(); slide.addText('x'); }"],
        full_code='const pptxgen = require("pptxgenjs");',
        output_path=output_path,
    )

    export_dir = tmp_path / "demo_generated_js"
    assert (export_dir / "presentation.js").exists()
    assert (export_dir / "slide_00.js").read_text(encoding="utf-8").startswith("{ let slide")
    assert (export_dir / "slide_01.js").exists()


def test_validate_generated_slide_code_rejects_addimage_without_asset(mock_planner):
    code = 'slide.addImage({ path: "https://images.unsplash.com/photo-1" });'

    with pytest.raises(ValueError, match="无图片模式下禁止使用 addImage"):
        mock_planner._validate_generated_slide_code(code, image_path=None)


def test_validate_generated_slide_code_rejects_placeholder_image_without_asset(mock_planner):
    code = 'slide.addText("x", { x: 1, y: 1, w: 1, h: 1 }); // preencoded.png'

    with pytest.raises(ValueError, match="禁止引用图片资源"):
        mock_planner._validate_generated_slide_code(code, image_path=None)


def test_validate_generated_slide_code_accepts_only_authorized_local_asset(mock_planner):
    code = 'slide.addImage({ path: "/tmp/real-image.png", x: 1, y: 1, w: 2, h: 2 });'

    mock_planner._validate_generated_slide_code(code, image_path="/tmp/real-image.png")


def test_validate_generated_slide_code_rejects_unapproved_asset_path(mock_planner):
    code = 'slide.addImage({ path: "/tmp/other-image.png", x: 1, y: 1, w: 2, h: 2 });'

    with pytest.raises(ValueError, match="未授权图片路径"):
        mock_planner._validate_generated_slide_code(code, image_path="/tmp/real-image.png")


def test_build_slide_user_prompt_forbids_images_when_no_asset(mock_planner):
    slide = SlideOutline.model_validate(
        {
            "slide_index": 2,
            "layout": SlideLayout.CONTENT,
            "topic": "定积分",
            "objective": "解释面积意义",
            "image_prompt": "riemann sum chart",
        }
    )

    prompt = mock_planner._build_slide_user_prompt(
        slide=slide,
        theme={"primary_color": "1F3864", "secondary_color": "2E75B6", "accent_color": "FFFFFF"},
        research=None,
        image_path=None,
    )

    assert "禁止 addImage" in prompt
    assert "优先参考第 9 页那种正常视觉" in prompt


def test_build_slide_user_prompt_respects_js_diagram_visual_mode(mock_planner):
    slide = SlideOutline.model_validate(
        {
            "slide_index": 2,
            "layout": SlideLayout.CONTENT,
            "topic": "连杆机构运动",
            "objective": "解释构件关系",
            "visual_mode": "js_diagram",
        }
    )

    prompt = mock_planner._build_slide_user_prompt(
        slide=slide,
        theme={"primary_color": "1F3864", "secondary_color": "2E75B6", "accent_color": "FFFFFF"},
        research=None,
        image_path=None,
    )

    assert "主视觉方式：js_diagram" in prompt
    assert "优先使用 addChart / addShape / addText" in prompt


def test_build_slide_user_prompt_keeps_layout_freedom_with_safe_zone_rules(mock_planner):
    slide = SlideOutline.model_validate(
        {
            "slide_index": 3,
            "layout": SlideLayout.CONTENT,
            "topic": "微积分基本定理",
            "objective": "解释定理结构",
        }
    )

    prompt = mock_planner._build_slide_user_prompt(
        slide=slide,
        theme={"primary_color": "1F3864", "secondary_color": "2E75B6", "accent_color": "FFFFFF"},
        research=None,
        image_path=None,
    )

    assert "布局仍然由你自主规划" in prompt
    assert "装饰安全区" in prompt
    assert "左上角数学水印" in prompt


def test_build_slide_user_prompt_includes_cross_slide_consistency_brief(mock_planner):
    slide = SlideOutline.model_validate(
        {
            "slide_index": 4,
            "layout": SlideLayout.CONTENT,
            "topic": "双曲函数图像特征",
            "objective": "统一背景骨架下解释图像性质",
        }
    )

    prompt = mock_planner._build_slide_user_prompt(
        slide=slide,
        theme={"primary_color": "1F3864", "secondary_color": "2E75B6", "accent_color": "FFFFFF"},
        research=None,
        image_path=None,
        consistency_brief="- 全部正文页共享同一套背景骨架",
    )

    assert "跨页一致性" in prompt
    assert "共享同一套背景骨架" in prompt


def test_parse_outline_plan_validates_structure(mock_planner):
    outline = mock_planner._parse_outline_plan(
        {
            "title": "人工智能",
            "topic": "人工智能",
            "slides": [
                {"slide_index": 0, "layout": "cover", "topic": "人工智能概览", "objective": "开场"},
                {"slide_index": 1, "layout": "toc", "topic": "目录", "objective": "建立结构"},
                {"slide_index": 2, "layout": "content", "topic": "核心技术", "objective": "解释技术"},
                {"slide_index": 3, "layout": "closing", "topic": "总结", "objective": "收尾"},
            ],
        },
        "人工智能",
    )
    assert isinstance(outline, OutlinePlan)
    assert outline.slides[2].topic == "核心技术"


def test_parse_outline_plan_keeps_visual_mode(mock_planner):
    outline = mock_planner._parse_outline_plan(
        {
            "title": "机械原理",
            "topic": "机械原理",
            "slides": [
                {"slide_index": 0, "layout": "cover", "topic": "封面", "visual_mode": "auto"},
                {"slide_index": 1, "layout": "toc", "topic": "目录", "visual_mode": "auto"},
                {"slide_index": 2, "layout": "content", "topic": "连杆机构", "visual_mode": "js_diagram"},
                {"slide_index": 3, "layout": "closing", "topic": "总结", "visual_mode": "auto"},
            ],
        },
        "机械原理",
    )

    assert outline.slides[2].visual_mode == VisualMode.JS_DIAGRAM


def test_resolve_visual_mode_infers_js_diagram_from_structural_topic():
    slide = SlideOutline.model_validate(
        {
            "slide_index": 2,
            "layout": "content",
            "topic": "系统架构与数据流程",
            "objective": "解释模块关系与处理步骤",
            "visual_mode": "auto",
        }
    )

    assert resolve_visual_mode(slide) == VisualMode.JS_DIAGRAM


def test_resolve_visual_mode_infers_generated_image_from_scene_prompt():
    slide = SlideOutline.model_validate(
        {
            "slide_index": 2,
            "layout": "content",
            "topic": "智慧工厂应用场景",
            "objective": "建立整体感性认知",
            "image_prompt": "cinematic factory workshop scene with robotic arms",
            "visual_mode": "auto",
        }
    )

    assert resolve_visual_mode(slide) == VisualMode.GENERATED_IMAGE


def test_outline_to_research_slides(mock_planner):
    outline = OutlinePlan.model_validate(
        {
            "title": "人工智能",
            "topic": "人工智能",
            "slides": [
                {"slide_index": 0, "layout": "cover", "topic": "封面"},
                {"slide_index": 1, "layout": "toc", "topic": "目录"},
                {"slide_index": 2, "layout": "content", "topic": "应用场景"},
                {"slide_index": 3, "layout": "closing", "topic": "结束"},
            ],
        }
    )
    slides = mock_planner.outline_to_research_slides(outline)
    assert len(slides) == 4
    assert slides[2].topic == "应用场景"
    assert slides[2].elements[0].type == "title"


def test_stabilize_theme_uses_cjk_safe_fonts_for_chinese_deck(mock_planner):
    outline = OutlinePlan.model_validate(
        {
            "title": "高等数学",
            "topic": "高等数学",
            "slides": [
                {"slide_index": 0, "layout": "cover", "topic": "封面"},
                {"slide_index": 1, "layout": "toc", "topic": "目录"},
                {"slide_index": 2, "layout": "content", "topic": "双曲函数"},
                {"slide_index": 3, "layout": "closing", "topic": "总结"},
            ],
        }
    )

    theme = mock_planner._stabilize_theme(
        {"header_font": "Arial Black", "body_font": "Calibri"},
        outline,
        language="中文",
    )

    assert theme["header_font"] in {"PingFang SC", "Microsoft YaHei"}
    assert theme["body_font"] in {"PingFang SC", "Microsoft YaHei"}
    assert "font_strategy_note" in theme


def test_enforce_theme_fonts_rewrites_explicit_fontface_literals(mock_planner):
    code = (
        'slide.addText("标题", { fontFace: "Arial Black", bold: true });\n'
        'slide.addText("正文", { fontFace: "Calibri", color: "333333" });'
    )

    normalized = mock_planner._enforce_theme_fonts(code, {"body_font": "PingFang SC"})

    assert normalized.count('fontFace: "PingFang SC"') == 2
    assert 'fontFace: "PingFang SC""' not in normalized
    assert "Arial Black" not in normalized
    assert "Calibri" not in normalized


def test_normalize_audience_aliases():
    assert normalize_audience("大学生") == "大学生"
    assert suggest_audience_label("大学生") == "student"
    assert suggest_audience_label("老板") == "boss"
    assert suggest_audience_label("投资人") == "investor"
    assert suggest_audience_label("unknown-audience") is None


def test_suggest_style_label_supports_known_freeform_and_auto():
    assert suggest_style_label("minimal") == "minimal"
    assert suggest_style_label("Future Editorial") == "Future Editorial"
    assert suggest_style_label("auto") is None
    assert suggest_style_label("") is None


def test_plan_with_real_api():
    if not os.getenv("GLM_API_KEY"):
        pytest.skip("未设置 GLM_API_KEY，跳过真实 API 测试")

    try:
        planner = PlannerAgent()
        outline = planner.plan_outline("量子计算入门")
        output_path, _, _ = planner.plan("量子计算入门", outline=outline)
    except Exception as exc:
        pytest.skip(f"真实 API 当前不可用，跳过: {exc}")

    assert isinstance(output_path, str)
    assert output_path.endswith(".pptx")
    assert os.path.exists(output_path)

    print(f"\n生成文件: {output_path}")
