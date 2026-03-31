from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import api


@pytest.fixture
def client():
    return TestClient(api.app)


def test_request_defaults_and_compatibility():
    req = api.PPTGenerationRequest.model_validate(
        {
            "course": "高等数学",
            "constraint": "双曲函数与反函数",
            "use_rag": True,
            "page_limit": 8,
        }
    )

    assert req.topic == "高等数学 - 双曲函数与反函数"
    assert req.enable_web_search is True
    assert req.image_mode == "generate"
    assert req.min_slides == 8
    assert req.max_slides == 8


def test_generate_ppt_route_returns_expected_payload(client, monkeypatch, tmp_path):
    output_path = tmp_path / "demo.pptx"
    output_path.write_bytes(b"pptx")
    captured = {}

    def fake_generate(req, emit=None):
        captured["req"] = req
        return api.GenerationArtifacts(
            output_path=str(output_path),
            output_filename=output_path.name,
            markdown_content="# Demo",
            total_slides=6,
            biz_id="ppt_123",
        )

    monkeypatch.setattr(api, "generate_ppt_bundle", fake_generate)

    response = client.post(
        "/generate_ppt",
        json={
            "topic": "AI 智能体",
            "output_language": "英文",
            "target_audience": "投资人",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["download_url"] == "/download_ppt/demo.pptx"
    assert body["preview_warning"] == ""
    assert captured["req"].enable_web_search is False
    assert captured["req"].image_mode == "generate"
    assert captured["req"].style == ""


def test_stream_ppt_route_emits_sse_events(client, monkeypatch, tmp_path):
    output_path = tmp_path / "stream-demo.pptx"
    output_path.write_bytes(b"pptx")

    def fake_generate(req, emit=None):
        emit("thinking_start", {"step": 1, "node": "规划PPT结构"})
        emit("thinking_chunk", "正在规划。")
        emit("thinking_end", {"step": 1, "node": "规划PPT结构"})
        emit(
            "preview",
            {
                "markdown_content": "# Preview",
                "completed_slides": 1,
                "total_slides": 6,
                "current_title": "封面",
            },
        )
        return api.GenerationArtifacts(
            output_path=str(output_path),
            output_filename=output_path.name,
            markdown_content="# Final",
            total_slides=6,
            biz_id="ppt_456",
        )

    monkeypatch.setattr(api, "generate_ppt_bundle", fake_generate)

    with client.stream("POST", "/stream_ppt", json={"topic": "流式测试"}) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: thinking_start" in body
    assert "event: preview" in body
    assert "event: done" in body


def test_build_preview_warning_reports_visual_qa_disabled(tmp_path):
    output_path = tmp_path / "demo.pptx"
    output_path.write_bytes(b"pptx")

    warning = api._build_preview_warning(
        output_path.name,
        str(output_path),
        [],
        visual_qa_enabled=False,
    )

    assert "未开启视觉 QA" in warning
