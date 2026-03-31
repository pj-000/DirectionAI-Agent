import uuid
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Literal
from enum import Enum

# 幻灯片边界常量
SLIDE_WIDTH = 13.333
SLIDE_HEIGHT = 7.5
BOUNDARY_TOLERANCE = 0.01


class LayoutValidationError(Exception):
    """布局校验失败异常，携带详细错误列表和原始内容"""

    def __init__(self, errors: List[str], raw_json: str = ""):
        self.errors = errors
        self.raw_json = raw_json
        msg = f"布局校验失败（{len(errors)} 个错误）:\n" + "\n".join(f"  - {e}" for e in errors)
        super().__init__(msg)


class SlideLayout(str, Enum):
    COVER = "cover"
    TOC = "toc"
    CONTENT = "content"
    TWO_COLUMN = "two_column"
    CLOSING = "closing"


class VisualMode(str, Enum):
    AUTO = "auto"
    JS_DIAGRAM = "js_diagram"
    GENERATED_IMAGE = "generated_image"


# Phase 4 支持的元素类型
ELEMENT_TYPES = {
    "title", "subtitle", "body", "image_placeholder",
    "shape", "callout",
}


class TextElement(BaseModel):
    """单个元素：文本、形状、图片占位、callout。"""
    content: str = ""
    x: float
    y: float
    width: float
    height: float
    font_size: int = 18
    bold: bool = False
    color: str = "#000000"
    align: Literal["left", "center", "right"] = "left"
    type: str = "body"

    # Phase 3: 图片
    unsplash_query: Optional[str] = None
    dalle_prompt: Optional[str] = None
    local_image_path: Optional[str] = None

    # Phase 4: 形状
    shape_type: Optional[str] = None       # rect / circle / line
    fill_color: Optional[str] = None       # 形状填充色
    corner_radius: Optional[float] = None  # 圆角半径（英寸），仅 rect

    @field_validator("color")
    @classmethod
    def validate_color(cls, v: str) -> str:
        if not v.startswith("#") or len(v) != 7:
            raise ValueError(f"颜色必须是 7 位十六进制格式，如 #1F3864，收到：{v}")
        return v

    @field_validator("fill_color")
    @classmethod
    def validate_fill_color(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and (not v.startswith("#") or len(v) != 7):
            raise ValueError(f"fill_color 格式错误：{v}")
        return v

    @field_validator("x", "y")
    @classmethod
    def validate_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("坐标必须为非负数")
        return v

    @field_validator("width", "height")
    @classmethod
    def validate_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("尺寸必须为正数")
        return v

    @model_validator(mode="after")
    def validate_boundary_and_constraints(self):
        right_edge = self.x + self.width
        bottom_edge = self.y + self.height
        if right_edge > SLIDE_WIDTH + BOUNDARY_TOLERANCE:
            raise ValueError(
                f"元素右边界越界：x({self.x}) + width({self.width}) = {right_edge:.3f}，"
                f"超出幻灯片宽度 {SLIDE_WIDTH}"
            )
        if bottom_edge > SLIDE_HEIGHT + BOUNDARY_TOLERANCE:
            raise ValueError(
                f"元素下边界越界：y({self.y}) + height({self.height}) = {bottom_edge:.3f}，"
                f"超出幻灯片高度 {SLIDE_HEIGHT}"
            )
        if self.type == "image_placeholder":
            if not self.unsplash_query and not self.dalle_prompt:
                raise ValueError(
                    "image_placeholder 元素必须至少提供 unsplash_query 或 dalle_prompt 之一"
                )
        if self.type == "shape":
            if not self.shape_type:
                raise ValueError("shape 元素必须指定 shape_type（rect / circle / line）")
            if not self.fill_color:
                raise ValueError("shape 元素必须指定 fill_color")
        return self


class SlideSpec(BaseModel):
    """单页幻灯片的完整规格"""
    slide_index: int
    layout: SlideLayout
    topic: str
    background_color: str = "#FFFFFF"
    elements: List[TextElement] = []
    speaker_notes: Optional[str] = None

    @field_validator("background_color")
    @classmethod
    def validate_bg_color(cls, v: str) -> str:
        if not v.startswith("#") or len(v) != 7:
            raise ValueError(f"背景色格式错误：{v}")
        return v

    @model_validator(mode="after")
    def validate_has_title(self):
        """每页至少有一个 type='title' 的元素（纯形状页除外）"""
        text_types = {"title", "subtitle", "body", "callout"}
        has_text = any(elem.type in text_types for elem in self.elements)
        has_title = any(elem.type == "title" for elem in self.elements)
        if has_text and not has_title and len(self.elements) > 0:
            raise ValueError(
                f"第 {self.slide_index} 页缺少 type='title' 的元素"
            )
        return self


class SlideOutline(BaseModel):
    """单页大纲：用于规划与研究，不包含最终渲染元素。"""
    slide_index: int
    layout: SlideLayout
    topic: str
    objective: str = ""
    image_prompt: Optional[str] = None
    visual_mode: VisualMode = VisualMode.AUTO


_JS_DIAGRAM_HINTS = (
    "关系", "结构", "原理", "流程", "步骤", "对比", "架构", "框图", "示意", "曲线", "函数", "坐标",
    "公式", "定理", "机构", "传动", "系统", "组成", "分析", "矩阵", "层级", "拓扑", "时序",
    "relationship", "structure", "principle", "process", "workflow", "comparison", "architecture",
    "diagram", "schematic", "curve", "function", "coordinate", "equation", "theorem", "mechanism",
    "transmission", "system", "component", "analysis", "matrix", "hierarchy", "topology", "timeline",
    "chart", "plot", "graph",
)

_GENERATED_IMAGE_HINTS = (
    "场景", "画面", "氛围", "形态", "外观", "实物", "人物", "工厂", "车间", "产品", "设备", "历史场景",
    "应用场景", "概念图", "封面主视觉", "scene", "atmosphere", "appearance", "photo", "realistic",
    "cinematic", "render", "rendering", "product", "device", "factory", "workshop", "portrait",
    "landscape", "concept art", "hero image", "illustration",
)


def resolve_visual_mode(slide: SlideOutline) -> VisualMode:
    """
    优先尊重大纲里模型显式给出的 visual_mode。
    当其为 auto 时，再根据 topic / objective / image_prompt 做轻量泛化推断。
    """
    if slide.visual_mode != VisualMode.AUTO:
        return slide.visual_mode

    if slide.layout not in {SlideLayout.CONTENT, SlideLayout.TWO_COLUMN}:
        return VisualMode.AUTO

    text = " ".join(
        part.strip().lower()
        for part in (slide.topic or "", slide.objective or "", slide.image_prompt or "")
        if part and part.strip()
    )
    if not text:
        return VisualMode.AUTO

    js_score = sum(1 for hint in _JS_DIAGRAM_HINTS if hint.lower() in text)
    generated_score = sum(1 for hint in _GENERATED_IMAGE_HINTS if hint.lower() in text)

    if js_score >= generated_score + 1 and js_score > 0:
        return VisualMode.JS_DIAGRAM
    if generated_score >= js_score + 1 and generated_score > 0:
        return VisualMode.GENERATED_IMAGE
    return VisualMode.AUTO


def _generate_short_id() -> str:
    return uuid.uuid4().hex[:8]


class PresentationPlan(BaseModel):
    """完整 PPT 规划"""
    title: str
    topic: str
    slide_width: float = 13.333
    slide_height: float = 7.5
    theme_color: str = "#1F3864"
    accent_color: str = "#2E75B6"
    font_family: str = "Microsoft YaHei"
    slides: List[SlideSpec] = []
    job_id: str = Field(default_factory=_generate_short_id)

    @field_validator("slides")
    @classmethod
    def validate_slide_count(cls, v: List[SlideSpec]) -> List[SlideSpec]:
        if len(v) < 2:
            raise ValueError("PPT 至少需要 2 页")
        if len(v) > 20:
            raise ValueError("PPT 最多 20 页")
        return v


class OutlinePlan(BaseModel):
    """PPT 页级大纲，用于 Planner 与 Research 之间的中间态。"""
    title: str
    topic: str
    slides: List[SlideOutline] = []
    job_id: str = Field(default_factory=_generate_short_id)

    @field_validator("slides")
    @classmethod
    def validate_slide_count(cls, v: List[SlideOutline]) -> List[SlideOutline]:
        if len(v) < 2:
            raise ValueError("PPT 至少需要 2 页")
        if len(v) > 20:
            raise ValueError("PPT 最多 20 页")
        return v


class SlideEvalResult(BaseModel):
    """单页视觉评分结果。"""
    slide_index: int
    layout_score: float   # 布局合理性 1-5
    content_score: float  # 内容完整性 1-5
    design_score: float   # 视觉质量 1-5
    overall: float        # 加权平均
    issues: List[str] = []
    suggestions: List[str] = []
