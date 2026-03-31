import pytest
import os
import sys
import json
import asyncio
import time
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch, AsyncMock
from pydantic import ValidationError
from models.schemas import (
    TextElement, SlideSpec, SlideLayout, PresentationPlan,
    LayoutValidationError, SLIDE_WIDTH, SLIDE_HEIGHT,
)
from agents.researcher import ResearchAgent


# ─── image_placeholder 校验 ───


class TestImagePlaceholderValidation:

    def test_image_placeholder_requires_query_or_prompt(self):
        """image_placeholder 必须提供 unsplash_query 或 dalle_prompt"""
        with pytest.raises(ValidationError, match="image_placeholder.*至少提供"):
            TextElement(
                type="image_placeholder", content="测试图片",
                x=0.5, y=1.5, width=5.0, height=4.0, font_size=14,
                color="#000000",
            )

    def test_image_placeholder_with_unsplash_query_passes(self):
        """提供 unsplash_query 时校验通过"""
        elem = TextElement(
            type="image_placeholder", content="太阳能板",
            x=0.5, y=1.5, width=5.0, height=4.0, font_size=14,
            color="#000000", unsplash_query="solar panel",
        )
        assert elem.unsplash_query == "solar panel"

    def test_image_placeholder_with_dalle_prompt_passes(self):
        """提供 dalle_prompt 时校验通过"""
        elem = TextElement(
            type="image_placeholder", content="太阳能板",
            x=0.5, y=1.5, width=5.0, height=4.0, font_size=14,
            color="#000000", dalle_prompt="A solar panel on rooftop",
        )
        assert elem.dalle_prompt == "A solar panel on rooftop"

    def test_image_placeholder_with_both_passes(self):
        """同时提供两者也通过"""
        elem = TextElement(
            type="image_placeholder", content="太阳能板",
            x=0.5, y=1.5, width=5.0, height=4.0, font_size=14,
            color="#000000",
            unsplash_query="solar panel",
            dalle_prompt="A solar panel on rooftop",
        )
        assert elem.unsplash_query and elem.dalle_prompt

    def test_local_image_path_default_none(self):
        """local_image_path 初始为 None"""
        elem = TextElement(
            type="image_placeholder", content="测试",
            x=0.5, y=1.5, width=5.0, height=4.0, font_size=14,
            color="#000000", unsplash_query="test",
        )
        assert elem.local_image_path is None

    def test_non_image_element_no_query_required(self):
        """非 image_placeholder 元素不要求 query"""
        elem = TextElement(
            type="body", content="正文内容",
            x=0.5, y=1.5, width=12.0, height=5.0, font_size=18,
            color="#333333",
        )
        assert elem.unsplash_query is None
        assert elem.dalle_prompt is None


# ─── ResearchAgent 测试 ───


class TestResearchAgent:

    def _make_slide(self, layout, topic="测试主题"):
        return SlideSpec(
            slide_index=0, layout=layout, topic=topic,
            elements=[
                TextElement(
                    type="title", content=topic,
                    x=0.5, y=0.3, width=12.0, height=0.9,
                    font_size=32, bold=True, color="#1F3864",
                ),
            ],
        )

    def test_skip_cover(self):
        """cover 页面返回 None"""
        with patch("agents.researcher.AsyncOpenAI"):
            with patch("agents.researcher.Path") as mock_path:
                mock_path.return_value.read_text.return_value = "mock {language}"
                agent = ResearchAgent()
        slide = self._make_slide(SlideLayout.COVER)
        result = asyncio.run(agent.research_slide(slide))
        assert result is None

    def test_skip_closing(self):
        """closing 页面返回 None"""
        with patch("agents.researcher.AsyncOpenAI"):
            with patch("agents.researcher.Path") as mock_path:
                mock_path.return_value.read_text.return_value = "mock {language}"
                agent = ResearchAgent()
        slide = self._make_slide(SlideLayout.CLOSING)
        result = asyncio.run(agent.research_slide(slide))
        assert result is None

    def test_default_content_on_failure(self):
        """研究失败时返回默认内容结构"""
        with patch("agents.researcher.AsyncOpenAI") as mock_cls:
            with patch("agents.researcher.Path") as mock_path:
                mock_path.return_value.read_text.return_value = "mock {language}"
                agent = ResearchAgent()
                agent.client = AsyncMock()
                agent.client.chat.completions.create.side_effect = Exception("API error")

        slide = self._make_slide(SlideLayout.CONTENT)
        result = asyncio.run(agent.research_slide(slide))
        assert result is not None
        assert "topic" in result
        assert "bullet_points" in result
        assert isinstance(result["bullet_points"], list)

    def test_research_all_length_matches_slides(self):
        """research_all 返回长度与 slides 一致"""
        with patch("agents.researcher.AsyncOpenAI"):
            with patch("agents.researcher.Path") as mock_path:
                mock_path.return_value.read_text.return_value = "mock {language}"
                agent = ResearchAgent()

        slides = [
            self._make_slide(SlideLayout.COVER, "封面"),
            self._make_slide(SlideLayout.CONTENT, "内容1"),
            self._make_slide(SlideLayout.CONTENT, "内容2"),
            self._make_slide(SlideLayout.CLOSING, "结尾"),
        ]

        # Mock API 返回
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "topic": "test", "summary": "test", "bullet_points": ["a", "b"]
        })

        agent.client = AsyncMock()
        agent.client.chat.completions.create.return_value = mock_response

        results = asyncio.run(agent.research_all(slides))
        assert len(results) == len(slides)
        assert results[0] is None  # cover
        assert results[3] is None  # closing
        assert results[1] is not None  # content
