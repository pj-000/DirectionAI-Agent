"""DirectionAI Education Tools - PPT Generation."""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

DEFAULT_PPTAGENT_URL = "http://pptagent:8000"


class GeneratePPTToolInput(BaseModel):
    """Input schema for the generate_ppt tool."""

    topic: str = Field(description="PPT 的主题，这是最重要的参数，定义了整个演示文稿的核心内容。")
    output_language: str = Field(default="中文", description="PPT 内容语言，例如：'中文'、'英文'。")
    target_audience: str = Field(default="通用受众", description="目标受众，例如：'大学生'、'企业老板'、'技术团队'、'通用受众'。")
    style: str = Field(default="", description="视觉风格偏好，留空则自动决定。可选：'商务'、'学术'、'简约'、'科技感'等。")
    min_slides: int = Field(default=6, ge=2, le=20, description="最少幻灯片页数，范围 2-20。")
    max_slides: int = Field(default=10, ge=2, le=20, description="最多幻灯片页数，范围 2-20。")
    model_provider: Literal["minmax", "claude"] = Field(default="minmax", description="使用的模型：'minmax'（MiniMax M2.7）或 'claude'（Claude Sonnet）。")
    image_mode: Literal["generate", "search", "auto", "off"] = Field(default="generate", description="图片模式：'generate'（AI生图）、'search'（仅搜图）、'auto'（先搜后生）、'off'（无图）。")
    enable_web_search: bool = Field(default=False, description="是否联网搜索补充资料。")
    content: str = Field(
        default="",
        description="补充给 PPT 生成器的详细内容。用户上传文档时，应把从文档中提炼出的摘要、章节结构、关键事实、表格要点、受众要求、页数约束等放在这里，而不是只传主题。",
    )


async def _generate_ppt_func(
    topic: str,
    output_language: str = "中文",
    target_audience: str = "通用受众",
    style: str = "",
    min_slides: int = 6,
    max_slides: int = 10,
    model_provider: str = "minmax",
    image_mode: str = "generate",
    enable_web_search: bool = False,
    content: str = "",
) -> str:
    topic = topic.strip()
    min_slides = max(2, min(20, min_slides))
    max_slides = max(2, min(20, max_slides))
    if max_slides < min_slides:
        min_slides, max_slides = max_slides, min_slides

    payload = {
        "topic": topic,
        "model_provider": model_provider,
        "output_language": output_language,
        "target_audience": target_audience,
        "style": style,
        "enable_web_search": enable_web_search,
        "image_mode": image_mode,
        "min_slides": min_slides,
        "max_slides": max_slides,
        "debug_layout": False,
    }
    if content:
        payload["content"] = content

    task_id = str(uuid.uuid4())[:8]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{DEFAULT_PPTAGENT_URL}/stream_ppt",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if not resp.is_success:
                error_detail = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
                return f"错误：无法启动 PPT 生成任务 (pptagent 返回 {error_detail})。请确认 pptagent 服务已启动。"
    except httpx.ConnectError:
        return f"错误：无法连接到 pptagent 服务 ({DEFAULT_PPTAGENT_URL})。请确认 PPT 生成服务已启动。"
    except httpx.TimeoutException:
        return "错误：连接 pptagent 服务超时。请稍后重试。"
    except Exception as exc:
        logger.error("Failed to call pptagent: %s", exc)
        return f"错误：启动 PPT 生成任务时发生错误: {exc}"

    params = (
        f"task={task_id}&topic={topic}&min_slides={min_slides}"
        f"&max_slides={max_slides}&model_provider={model_provider}"
        f"&output_language={output_language}&target_audience={target_audience}"
    )
    model_label = "MiniMax M2.7" if model_provider == "minmax" else "Claude Sonnet"

    return (
        f"✅ PPT 生成任务已启动！\n\n"
        f"**主题**: {topic}\n"
        f"**页数**: {min_slides}-{max_slides} 页\n"
        f"**语言**: {output_language}\n"
        f"**受众**: {target_audience}\n"
        f"**模型**: {model_label}\n\n"
        f"📊 **查看生成进度**: 请访问 [PPT 生成页面](/workspace/ppt?{params}) 查看实时进度和预览。\n\n"
        f"生成过程：规划大纲 → 补充资料（可选）→ 确定视觉主题 → 逐页生成 → 质量评估。"
    )


generate_ppt_tool = StructuredTool.from_function(
    name="generate_ppt",
    description="生成 PowerPoint 演示文稿。当用户想要创建、生成 PPT 或演示文稿时使用此工具。若用户上传了 PDF、Word、PPT、Excel 等文档，应先读取并总结文档内容，再把结构化摘要和关键要求放入 content 后调用此工具。该工具会启动异步生成流程，前端会在工具卡片中实时展示规划、生成和最终预览。",
    func=_generate_ppt_func,
    args_schema=GeneratePPTToolInput,
    return_direct=True,
)


# ─── Lesson Plan Tool ───────────────────────────────────────────────────────


class GenerateLessonPlanInput(BaseModel):
    topic: str = Field(description="教案主题，核心知识点或课程名称。")
    output_language: str = Field(default="中文", description="教案语言。")
    target_audience: str = Field(default="学生", description="授课对象，例如：'中学生'、'大学生'。")
    course: str = Field(default="", description="所属课程名称，例如：'高中数学'。")
    units: str = Field(default="", description="需要覆盖的单元名称，逗号分隔。")
    lessons: str = Field(default="", description="具体课时安排，逗号分隔。")
    knowledge_points: str = Field(default="", description="需要覆盖的知识点，逗号分隔。")
    enable_web_search: bool = Field(default=False, description="是否联网搜索参考资料。")
    content: str = Field(default="", description="附加要求或特殊说明。")


async def _generate_lesson_plan_func(**kwargs) -> str:
    topic = kwargs.get("topic", "").strip()
    if not topic:
        return "错误：教案主题不能为空。请提供要生成的教案主题。"

    return (
        f"✅ 教案生成任务已启动！\n\n"
        f"**主题**: {topic}\n"
        f"**课程**: {kwargs.get('course', '未指定')}\n"
        f"**语言**: {kwargs.get('output_language', '中文')}\n"
        f"**受众**: {kwargs.get('target_audience', '学生')}\n\n"
        f"📋 教案将包含：\n"
        f"- 教学目标\n"
        f"- 教学内容大纲\n"
        f"- 教学活动设计\n"
        f"- 课堂练习与评估\n\n"
        f"⚠️ 教案生成功能正在集成中，请稍后使用。"
    )


generate_lesson_plan_tool = StructuredTool.from_function(
    name="generate_lesson_plan",
    description="生成教案和教学设计方案。当用户想要创建教案、备课材料或课程设计时使用此工具。",
    func=_generate_lesson_plan_func,
    args_schema=GenerateLessonPlanInput,
)


# ─── Exam Tool ──────────────────────────────────────────────────────────────


class GenerateExamInput(BaseModel):
    topic: str = Field(description="出题主题，科目或知识点的核心内容。")
    output_language: str = Field(default="中文", description="试题语言。")
    difficulty: Literal["简单", "中等", "困难"] = Field(default="中等", description="难度等级：'简单'、'中等'、'困难'。")
    question_types: str = Field(default="选择题,简答题", description="题型，逗号分隔。可选：'选择题'、'填空题'、'简答题'、'论述题'、'判断题'。")
    num_questions: int = Field(default=10, ge=1, le=100, description="总题量。")
    enable_web_search: bool = Field(default=False, description="是否联网搜索相关资料。")
    content: str = Field(default="", description="具体章节，重点知识、特殊要求。")


async def _generate_exam_func(**kwargs) -> str:
    topic = kwargs.get("topic", "").strip()
    if not topic:
        return "错误：出题主题不能为空。请提供要生成的试题主题。"

    types_list = [t.strip() for t in kwargs.get("question_types", "选择题,简答题").split(",") if t.strip()]

    return (
        f"✅ 试题生成任务已启动！\n\n"
        f"**主题**: {topic}\n"
        f"**难度**: {kwargs.get('difficulty', '中等')}\n"
        f"**题量**: {kwargs.get('num_questions', 10)} 题\n"
        f"**题型**: {', '.join(types_list)}\n"
        f"**语言**: {kwargs.get('output_language', '中文')}\n\n"
        f"📝 生成的试题将包含：\n"
        f"- 客观题（选择、填空、判断）\n"
        f"- 主观题（简答、论述）\n"
        f"- 参考答案与评分标准\n\n"
        f"⚠️ 试题生成功能正在集成中，请稍后使用。"
    )


generate_exam_tool = StructuredTool.from_function(
    name="generate_exam",
    description="生成考试试卷和试题。当用户想要出题、创建考试卷或练习题时使用此工具。",
    func=_generate_exam_func,
    args_schema=GenerateExamInput,
)


# ─── Evaluate PPT Tool ──────────────────────────────────────────────────────


class EvaluatePPTInput(BaseModel):
    course: str = Field(description="PPT 对应的课程或主题。")
    ppt_content: str = Field(description="PPT 的 Markdown 内容。")
    evaluation_metrics: str = Field(default="指令遵循,内容相关性,事实准确性,领域专业性,清晰易懂", description="评估维度，逗号分隔。")
    constraint: str = Field(default="", description="附加评估约束或要求。")
    page_limit: int = Field(default=0, description="期望的页数。")
    lang: Literal["zh", "en"] = Field(default="zh", description="评估语言：'zh' 或 'en'。")


async def _evaluate_ppt_func(**kwargs) -> str:
    ppt_content = kwargs.get("ppt_content", "").strip()
    if not ppt_content:
        return "错误：PPT 内容不能为空。请提供要评估的 PPT 内容。"

    return (
        f"✅ PPT 质量评估已启动！\n\n"
        f"**课程**: {kwargs.get('course', '')}\n"
        f"**评估维度**: {kwargs.get('evaluation_metrics', '')}\n"
        f"**语言**: {'中文' if kwargs.get('lang', 'zh') == 'zh' else '英文'}\n\n"
        f"📊 评估维度包括：\n"
        f"- 指令遵循与任务完成\n"
        f"- 内容相关性与范围控制\n"
        f"- 基础事实准确性\n"
        f"- 领域知识专业性\n"
        f"- 清晰易懂与表达启发\n\n"
        f"⚠️ PPT 评估功能正在集成中，请稍后使用。"
    )


evaluate_ppt_tool = StructuredTool.from_function(
    name="evaluate_ppt",
    description="评估已生成的 PPT 质量。对 PPT 进行多维度质量评估。",
    func=_evaluate_ppt_func,
    args_schema=EvaluatePPTInput,
)
