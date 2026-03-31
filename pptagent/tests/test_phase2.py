import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydantic import ValidationError
from models.schemas import (
    TextElement, SlideSpec, SlideLayout, PresentationPlan,
    LayoutValidationError, SLIDE_WIDTH, SLIDE_HEIGHT,
)


# ─── ElementSpec 坐标校验 ───


class TestElementBoundary:

    def test_valid_coordinates_pass(self):
        elem = TextElement(
            content="测试", type="title",
            x=0.5, y=0.3, width=12.333, height=0.9,
            font_size=32, bold=True, color="#1F3864", align="left",
        )
        assert elem.x == 0.5

    def test_right_edge_overflow_raises(self):
        with pytest.raises(ValidationError, match="右边界越界"):
            TextElement(content="测试", type="title",
                        x=10.0, y=0.0, width=5.0, height=1.0,
                        font_size=32, color="#000000")

    def test_bottom_edge_overflow_raises(self):
        with pytest.raises(ValidationError, match="下边界越界"):
            TextElement(content="测试", type="title",
                        x=0.0, y=5.0, width=5.0, height=4.0,
                        font_size=32, color="#000000")

    def test_exact_boundary_passes(self):
        elem = TextElement(content="测试", type="title",
                           x=0.0, y=0.0, width=SLIDE_WIDTH, height=SLIDE_HEIGHT,
                           font_size=32, color="#000000")
        assert elem.x + elem.width == SLIDE_WIDTH

    def test_within_tolerance_passes(self):
        elem = TextElement(content="测试", type="title",
                           x=0.0, y=0.0, width=13.34, height=7.5,
                           font_size=32, color="#000000")
        assert elem is not None

    def test_negative_x_raises(self):
        with pytest.raises(ValidationError, match="非负"):
            TextElement(content="测试", type="title",
                        x=-1.0, y=0.0, width=5.0, height=1.0,
                        font_size=32, color="#000000")

    def test_zero_width_raises(self):
        with pytest.raises(ValidationError, match="正数"):
            TextElement(content="测试", type="title",
                        x=0.0, y=0.0, width=0, height=1.0,
                        font_size=32, color="#000000")


# ─── SlideSpec title 元素校验 ───


class TestSlideSpecValidation:

    def test_no_title_element_raises(self):
        with pytest.raises(ValidationError, match="缺少 type='title'"):
            SlideSpec(
                slide_index=0, layout=SlideLayout.CONTENT, topic="测试",
                elements=[
                    TextElement(content="正文", type="body",
                                x=0.5, y=1.5, width=12.0, height=5.0,
                                font_size=18, color="#333333")
                ],
            )

    def test_with_title_element_passes(self):
        slide = SlideSpec(
            slide_index=0, layout=SlideLayout.CONTENT, topic="测试",
            elements=[
                TextElement(content="标题", type="title",
                            x=0.5, y=0.3, width=12.0, height=0.9,
                            font_size=32, bold=True, color="#1F3864"),
                TextElement(content="正文", type="body",
                            x=0.5, y=1.5, width=12.0, height=5.0,
                            font_size=18, color="#333333"),
            ],
        )
        assert len(slide.elements) == 2


# ─── PresentationPlan 页数边界 ───


class TestPresentationPlanValidation:

    def _make_slide(self, index, layout=SlideLayout.CONTENT):
        return SlideSpec(
            slide_index=index, layout=layout, topic=f"页 {index}",
            elements=[
                TextElement(content=f"标题 {index}", type="title",
                            x=0.5, y=0.3, width=12.0, height=0.9,
                            font_size=32, bold=True, color="#1F3864")
            ],
        )

    def test_too_few_slides_raises(self):
        with pytest.raises(ValidationError, match="至少需要 2 页"):
            PresentationPlan(title="测试", topic="测试",
                             slides=[self._make_slide(0)])

    def test_too_many_slides_raises(self):
        with pytest.raises(ValidationError, match="最多 20 页"):
            PresentationPlan(title="测试", topic="测试",
                             slides=[self._make_slide(i) for i in range(21)])

    def test_valid_slide_count_passes(self):
        plan = PresentationPlan(title="测试", topic="测试",
                                slides=[self._make_slide(i) for i in range(5)])
        assert len(plan.slides) == 5


# ─── LayoutValidationError ───


class TestLayoutValidationError:

    def test_carries_errors_and_raw_json(self):
        errors = ["右边界越界", "缺少 title 元素"]
        raw = '{"bad": "json"}'
        exc = LayoutValidationError(errors=errors, raw_json=raw)
        assert exc.errors == errors
        assert exc.raw_json == raw
        assert "2 个错误" in str(exc)

    def test_empty_raw_json(self):
        exc = LayoutValidationError(errors=["错误1"])
        assert exc.raw_json == ""
