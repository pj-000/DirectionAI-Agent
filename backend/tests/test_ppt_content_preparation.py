from __future__ import annotations

import json
import re

from deerflow.directionai.tools.generate_ppt import _generate_ppt_func
from deerflow.directionai.tools.ppt_content_preparation import prepare_ppt_content


def test_prepare_ppt_content_uses_existing_document_summary_json() -> None:
    content = json.dumps(
        {
            "document_title": "量子计算导论",
            "document_summary": "介绍量子计算的核心概念与应用前景。",
            "sections": [
                {
                    "section_title": "基本概念",
                    "section_summary": "解释量子比特与叠加态。",
                    "key_points": ["量子比特可以同时表示多个状态。"],
                    "suggested_slide_count": 2,
                }
            ],
            "metadata": {"language": "zh-CN"},
            "ppt_generation_hints": {
                "suggested_total_slides": 6,
                "audience": "student",
                "style_preference": "academic",
                "recommended_visual_elements": ["diagrams"],
                "content_focus": "knowledge_sharing",
            },
        },
        ensure_ascii=False,
    )

    prepared = prepare_ppt_content("量子计算导论", content, model_provider="minmax")

    assert prepared.normalization == "document_summary_json"
    assert prepared.summary is not None
    assert "## 1. 基本概念" in prepared.planner_content
    assert "## PPT 生成建议" in prepared.planner_content
    assert prepared.debug["used_document_summary"] is True


def test_prepare_ppt_content_falls_back_when_summary_fails(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(
        "deerflow.directionai.tools.ppt_content_preparation.summarize_document",
        _boom,
    )

    long_markdown = "\n".join(
        [
            "# 第一章 背景",
            *[
                f"- 要点 {idx}: 这是用于测试长文档压缩与回退摘要流程的内容，包含较长描述和多个层次。"
                for idx in range(250)
            ],
        ]
    )

    prepared = prepare_ppt_content("长文档测试", long_markdown, model_provider="minmax")

    assert prepared.normalization == "fallback_digest"
    assert prepared.summary is None
    assert prepared.debug["summary_error"] == "summary failed"
    assert prepared.planner_content
    assert len(prepared.planner_content) <= 12_000


def test_generate_ppt_writes_prepared_task_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "deerflow.directionai.tools.generate_ppt._PPT_TASK_ROOT",
        tmp_path,
    )

    content = json.dumps(
        {
            "document_title": "AI 教学应用",
            "document_summary": "总结 AI 在课堂中的典型使用方式。",
            "sections": [
                {
                    "section_title": "应用场景",
                    "section_summary": "覆盖备课、课堂互动和作业反馈。",
                    "key_points": ["AI 可以辅助教师进行差异化备课。"],
                    "suggested_slide_count": 2,
                }
            ],
            "metadata": {"language": "zh-CN"},
            "ppt_generation_hints": {
                "suggested_total_slides": 5,
                "audience": "student",
                "style_preference": "informative",
                "recommended_visual_elements": ["comparison"],
                "content_focus": "training",
            },
        },
        ensure_ascii=False,
    )

    response = _generate_ppt_func(topic="AI 教学应用", content=content)
    match = re.search(r"__PPTGEN_START__(.+?)__PPTGEN_END__", response, re.S)
    assert match is not None
    task_id = json.loads(match.group(1))["task_id"]

    payload = json.loads((tmp_path / f"{task_id}.json").read_text(encoding="utf-8"))
    assert payload["content_debug"]["normalization"] == "document_summary_json"
    assert payload["document_summary"]["document_title"] == "AI 教学应用"
    assert "## PPT 生成建议" in payload["content"]

