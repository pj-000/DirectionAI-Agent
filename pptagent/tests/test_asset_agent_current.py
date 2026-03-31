import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, patch

from agents.asset_agent import AssetAgent
from models.schemas import SlideOutline, SlideLayout


def _make_outline_slide(visual_mode: str = "auto") -> SlideOutline:
    return SlideOutline.model_validate(
        {
            "slide_index": 2,
            "layout": SlideLayout.CONTENT,
            "topic": "机械原理",
            "objective": "解释机构特征",
            "image_prompt": "mechanical linkage diagram",
            "visual_mode": visual_mode,
        }
    )


def test_asset_agent_skips_fetch_for_js_diagram(tmp_path):
    agent = AssetAgent(image_source="auto")
    slide = _make_outline_slide("js_diagram")

    with patch.object(agent, "_try_search", new_callable=AsyncMock) as mock_search:
        with patch.object(agent, "_try_generate", new_callable=AsyncMock) as mock_generate:
            result = asyncio.run(agent._fetch_for_slide(slide, tmp_path))

    assert result is None
    mock_search.assert_not_called()
    mock_generate.assert_not_called()


def test_asset_agent_prefers_generate_for_generated_image_mode(tmp_path):
    agent = AssetAgent(image_source="auto")
    slide = _make_outline_slide("generated_image")

    with patch.object(agent, "_try_search", new_callable=AsyncMock, return_value="/tmp/search.jpg") as mock_search:
        with patch.object(agent, "_try_generate", new_callable=AsyncMock, return_value="/tmp/generated.png") as mock_generate:
            result = asyncio.run(agent._fetch_for_slide(slide, tmp_path))

    assert result == "/tmp/generated.png"
    mock_generate.assert_awaited_once()
    mock_search.assert_not_called()
