import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.evaluator import EvaluatorAgent
from models.schemas import OutlinePlan


def _make_outline():
    return OutlinePlan.model_validate(
        {
            "title": "高等数学",
            "topic": "高等数学",
            "slides": [
                {"slide_index": 0, "layout": "content", "topic": "极限"},
                {"slide_index": 1, "layout": "closing", "topic": "总结"},
            ],
        }
    )


def test_parse_json_repairs_unescaped_quotes_inside_string_values():
    raw = """```json
{
  "layout_score": 2.5,
  "content_score": 3.0,
  "design_score": 2.0,
  "issues": ["标题中出现"极限"但层级不清", "正文过密"],
  "suggestions": ["将"极限"作为强调词单独处理"]
}
```"""

    data = EvaluatorAgent._parse_json(raw)

    assert data["layout_score"] == 2.5
    assert data["issues"][0] == '标题中出现"极限"但层级不清'
    assert data["suggestions"][0] == '将"极限"作为强调词单独处理'


def test_parse_json_repairs_missing_commas_between_properties():
    raw = """{
  "layout_score": 3.8
  "content_score": 4.5
  "design_score": 3.2,
  "issues": ["卡片间距略不一致"]
  "suggestions": ["统一卡片垂直间距"]
}"""

    data = EvaluatorAgent._parse_json(raw)

    assert data["layout_score"] == 3.8
    assert data["content_score"] == 4.5
    assert data["suggestions"][0] == "统一卡片垂直间距"


def test_parse_json_escapes_raw_newlines_inside_issue_strings():
    raw = """{
  "layout_score": 3.2,
  "content_score": 3.8,
  "design_score": 2.5,
  "issues": ["底部说明文字过密
导致第二行与页脚距离不足"],
  "suggestions": ["增加底部留白"]
}"""

    data = EvaluatorAgent._parse_json(raw)

    assert data["issues"][0] == "底部说明文字过密\n导致第二行与页脚距离不足"


def test_parse_json_salvages_scores_from_truncated_response():
    raw = """{
  "layout_score": 4.2,
  "content_score": 4.0,
  "design_score": 3.8,
  "issues": [
    "六张卡片水平间距不一致",
    "卡片左侧竖条宽度不统一"""

    data = EvaluatorAgent._parse_json(raw)

    assert data["layout_score"] == 4.2
    assert data["content_score"] == 4.0
    assert data["design_score"] == 3.8
    assert data["issues"] == ["六张卡片水平间距不一致"]


def test_evaluate_all_converts_failures_into_low_score_feedback(tmp_path):
    image_path = tmp_path / "slide-1.jpg"
    image_path.write_bytes(b"fake")

    agent = EvaluatorAgent.__new__(EvaluatorAgent)
    agent.enabled = True
    agent.client = None

    def raise_error(*args, **kwargs):
        raise ValueError("bad json")

    agent._evaluate_slide = raise_error

    results = agent.evaluate_all([str(image_path)], _make_outline())

    assert len(results) == 1
    assert results[0].slide_index == 0
    assert results[0].overall == 1.5
    assert results[0].issues[0].startswith("视觉评分失败：")
