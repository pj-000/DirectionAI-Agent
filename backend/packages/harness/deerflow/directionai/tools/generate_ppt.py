"""DirectionAI Education Tools - PPT Generation (internal)."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from deerflow.directionai.runtime_paths import get_directionai_data_dir
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PPT_TASK_ROOT = (get_directionai_data_dir() / "ppt_tasks").resolve()
_PPT_TASK_ROOT.mkdir(parents=True, exist_ok=True)


class GeneratePPTToolInput(BaseModel):
    """Input schema for the generate_ppt tool."""

    topic: str = Field(description="PPT 的主题，这是最重要的参数，定义了整个演示文稿的核心内容。")
    output_language: str = Field(default="中文", description="PPT 内容语言，例如：'中文'、'英文'。")
    target_audience: str = Field(default="通用受众", description="目标受众，例如：'大学生'、'企业老板'、'技术团队'、'通用受众'。")
    style: str = Field(default="", description="视觉风格偏好，留空则自动决定。可选：'商务'、'学术'、'简约'、'科技感'等。")
    min_slides: int = Field(default=6, ge=4, le=20, description="最少幻灯片页数，范围 4-20。")
    max_slides: int = Field(default=10, ge=4, le=20, description="最多幻灯片页数，范围 4-20。")
    model_provider: Literal["minmax", "claude"] = Field(default="minmax", description="使用的模型：'minmax'（MiniMax M2.7）或 'claude'（Claude Sonnet）。")
    image_mode: Literal["generate", "search", "auto", "off"] = Field(default="generate", description="图片模式：'generate'（AI生图）、'search'（仅搜图）、'auto'（先搜后生）、'off'（无图）。")
    enable_web_search: bool = Field(default=False, description="是否联网搜索补充资料。")
    content: str = Field(
        default="",
        description="补充给 PPT 生成器的详细内容。用户上传文档时，应把从文档中提炼出的摘要、主题章节、关键事实、表格要点、受众要求、页数约束等放在这里，而不是只传主题。除非用户明确要求先出大纲，否则不要在这里预先写死最终每一页的分页方案，分页规划应由 generate_ppt 自己完成。",
    )


def _generate_ppt_func(
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
    content = content.strip()
    min_slides = max(4, min(20, min_slides))
    max_slides = max(4, min(20, max_slides))
    if max_slides < min_slides:
        min_slides, max_slides = max_slides, min_slides

    # The streaming UI currently connects via GET query params.
    # Keep document-derived context, but cap it to avoid oversized SSE URLs.
    if len(content) > 6000:
        content = content[:6000].rstrip() + "\n\n[文档摘要因流式传输长度限制被截断]"

    task_id = f"ppttask_{uuid.uuid4().hex}"
    ppt_params = {
        "topic": topic,
        "min_slides": min_slides,
        "max_slides": max_slides,
        "output_language": output_language,
        "target_audience": target_audience,
        "model_provider": model_provider,
        "image_mode": image_mode,
        "enable_web_search": enable_web_search,
        "style": style,
        "content": content,
    }
    (_PPT_TASK_ROOT / f"{task_id}.json").write_text(
        json.dumps(ppt_params, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    model_label = "MiniMax M2.7" if model_provider == "minmax" else "Claude Sonnet"
    task_page_url = (
        f"/workspace/ppt?task_id={task_id}"
        f"&topic={quote(topic)}"
        f"&min_slides={min_slides}"
        f"&max_slides={max_slides}"
    )

    # Return marker + placeholder. Frontend SSE streaming renders the live timeline.
    # When SSE "done" arrives, PPTStreamingInline replaces the placeholder with download link.
    return (
        f"__PPTGEN_START__{json.dumps({'task_id': task_id}, ensure_ascii=False)}__PPTGEN_END__\n\n"
        f"✅ PPT 生成任务已启动！\n\n"
        f"**主题**: {topic}\n"
        f"**页数**: {min_slides}-{max_slides} 页\n"
        f"**语言**: {output_language}\n"
        f"**受众**: {target_audience}\n"
        f"**模型**: {model_label}\n\n"
        f"**任务页面**: {task_page_url}\n\n"
        f"📊 正在生成中，下方将实时展示进度...\n\n"
        f"生成过程：规划大纲 → 确定视觉主题 → 逐页生成 → 质量评估。"
    )


generate_ppt_tool = StructuredTool.from_function(
    name="generate_ppt",
    description="生成 PowerPoint 演示文稿。仅当用户明确想要的最终产物是新的 PPT，或明确要求修改/重生成现有 PPT 时使用此工具。若用户上传了 PDF、Word、PPT、Excel 等文档，应先读取并总结文档内容，再把结构化摘要和关键要求放入 content 后调用此工具。如果用户只是基于现有文件索要总结、讲稿、翻译、说明、问答或其他文本结果，不要调用此工具，应直接基于文件内容完成该文本任务。该工具会启动异步生成流程，前端会在工具卡片中实时展示规划、生成和最终预览。",
    func=_generate_ppt_func,
    args_schema=GeneratePPTToolInput,
    return_direct=True,
)


# ─── Lesson Plan Tool ────────────────────────────────────────────────────────


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


def _generate_lesson_plan_func(**kwargs) -> str:
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


# ─── Exam Tool ───────────────────────────────────────────────────────────────


class GenerateExamInput(BaseModel):
    topic: str = Field(description="出题主题，科目或知识点的核心内容。")
    output_language: str = Field(default="中文", description="试题语言。")
    difficulty: Literal["简单", "中等", "困难"] = Field(default="中等", description="难度等级：'简单'、'中等'、'困难'。")
    question_types: str = Field(default="选择题,简答题", description="题型，逗号分隔。可选：'选择题'、'填空题'、'简答题'、'论述题'、'判断题'。")
    num_questions: int = Field(default=10, ge=1, le=100, description="总题量。")
    enable_web_search: bool = Field(default=False, description="是否联网搜索相关资料。")
    content: str = Field(default="", description="具体章节，重点知识、特殊要求。")


def _generate_exam_func(**kwargs) -> str:
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


# ─── Evaluate PPT Tool ───────────────────────────────────────────────────────


class EvaluatePPTInput(BaseModel):
    course: str = Field(description="PPT 对应的课程或主题。")
    ppt_content: str = Field(description="PPT 的 Markdown 内容。")
    evaluation_metrics: str = Field(default="指令遵循,内容相关性,事实准确性,领域专业性,清晰易懂", description="评估维度，逗号分隔。")
    constraint: str = Field(default="", description="附加评估约束或要求。")
    page_limit: int = Field(default=0, description="期望的页数。")
    lang: Literal["zh", "en"] = Field(default="zh", description="评估语言：'zh' 或 'en'。")


def _evaluate_ppt_func(**kwargs) -> str:
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
