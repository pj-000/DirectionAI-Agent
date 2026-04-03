from __future__ import annotations

import json
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI
from pydantic import BaseModel, Field

from .. import config
from ..tools.openai_compat import build_chat_completion_kwargs, stream_chat_completion_text


class PPTEvaluationRequest(BaseModel):
    course: str = Field(..., description="课程名称或 PPT 主题")
    units: str = Field(default="", description="补充单元信息")
    lessons: str = Field(default="", description="补充课时信息")
    constraint: str = Field(default="", description="附加要求")
    page_limit: int = Field(default=0, ge=0, le=50, description="PPT 页数")
    ppt_content: str = Field(..., description="PPT 文本内容或 Markdown")
    evaluation_metrics: list[str] = Field(default_factory=list, description="评估维度")
    model_type: str = Field(default="QWen", description="兼容旧前端字段")
    lang: str = Field(default="zh", description="评估语言")


@dataclass
class PPTEvaluationArtifacts:
    course: str
    units: str
    lessons: str
    constraint: str
    page_limit: int
    evaluation_metrics: list[str]
    evaluation_score: str
    principle_descriptions: list[str]
    evaluator: str
    lang: str

    def to_response(self) -> dict[str, Any]:
        return {
            "course": self.course,
            "units": self.units,
            "lessons": self.lessons,
            "constraint": self.constraint,
            "page_limit": self.page_limit,
            "evaluation_metrics": self.evaluation_metrics,
            "evaluation_score": self.evaluation_score,
            "principle_descriptions": self.principle_descriptions,
            "evaluator": self.evaluator,
            "lang": self.lang,
        }


DEFAULT_PPT_EVAL_METRICS = [
    "1 指令遵循与任务完成",
    "3 内容相关性与范围控制",
    "5 基础事实准确性",
    "6 领域知识专业性",
    "10 清晰易懂与表达启发",
]

LEGACY_PPT_METRIC_ALIASES = {
    "1.1 指令遵循与任务完成": "1 指令遵循与任务完成",
    "1.3 内容相关性与范围控制": "3 内容相关性与范围控制",
    "2.1 基础事实准确性": "5 基础事实准确性",
    "2.2 领域知识专业性": "6 领域知识专业性",
    "3.1 清晰易懂与表达启发": "10 清晰易懂与表达启发",
}

DEFAULT_PRINCIPLE_DESCRIPTIONS = {
    "1 指令遵循与任务完成": "是否完全理解并执行了用户的指令？是否完成了指定的核心任务？输出的格式是否符合要求？",
    "3 内容相关性与范围控制": "输出内容是否紧密围绕指定的知识点、主题或问题？是否控制在要求的难度、范围或学科领域内？",
    "5 基础事实准确性": "涉及的概念定义、公式、日期、专有名词、代码语法、法律条文等客观信息是否准确无误？",
    "6 领域知识专业性": "在特定学科领域的知识运用是否不仅正确，而且体现了适当的专业深度和行业标准？",
    "10 清晰易懂与表达启发": "解释、说明、反馈是否清晰、简洁、易于目标学习者理解？表达方式是否具有启发性？",
}

BASE_DIR = Path(__file__).resolve().parents[2]
PPT_PRINCIPLES_DIR = BASE_DIR / "re_evoagentx" / "evo_modules" / "principles" / "ppt"
PPT_PRINCIPLES_PATHS = {
    "zh": PPT_PRINCIPLES_DIR / "principles_zh_whiten.json",
    "en": PPT_PRINCIPLES_DIR / "principles_en_whiten.json",
}
_PRINCIPLES_CACHE: dict[str, dict[str, Any]] = {}

EVAL_NODE_LABELS = ["读取PPT内容", "按维度评分", "整理评估报告"]


def load_principles(lang: str) -> dict[str, Any]:
    normalized_lang = "en" if str(lang).lower().startswith("en") else "zh"
    cached = _PRINCIPLES_CACHE.get(normalized_lang)
    if cached is not None:
        return cached

    path = PPT_PRINCIPLES_PATHS[normalized_lang]
    if not path.exists():
        _PRINCIPLES_CACHE[normalized_lang] = {}
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    _PRINCIPLES_CACHE[normalized_lang] = data if isinstance(data, dict) else {}
    return _PRINCIPLES_CACHE[normalized_lang]


def normalize_metric(metric: str) -> str:
    cleaned = str(metric or "").strip()
    if not cleaned:
        return cleaned
    return LEGACY_PPT_METRIC_ALIASES.get(cleaned, cleaned)


def normalize_metrics(metrics: list[str]) -> list[str]:
    normalized = [normalize_metric(metric) for metric in metrics if str(metric or "").strip()]
    return normalized or list(DEFAULT_PPT_EVAL_METRICS)


def get_metric_definition(metric: str, lang: str) -> dict[str, Any]:
    principles = load_principles(lang)
    normalized_metric = normalize_metric(metric)
    principle = principles.get(normalized_metric)
    return principle if isinstance(principle, dict) else {}


def build_principle_descriptions(metrics: list[str], lang: str) -> list[str]:
    descriptions: list[str] = []
    for metric in metrics:
        principle = get_metric_definition(metric, lang)
        descriptions.append(principle.get("description") or DEFAULT_PRINCIPLE_DESCRIPTIONS.get(metric, metric))
    return descriptions


def strip_metric_label(metric: str) -> str:
    return re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", str(metric or "")).strip() or str(metric or "").strip()


def build_evaluation_prompt(req: PPTEvaluationRequest, metrics: list[str]) -> str:
    metric_lines = []
    for item in metrics:
        principle = get_metric_definition(item, req.lang)
        description = principle.get("description") or DEFAULT_PRINCIPLE_DESCRIPTIONS.get(item, strip_metric_label(item))
        levels = principle.get("levels")
        line = f"- {item}: {description}"
        if isinstance(levels, list) and levels:
            line += "\n  评分标准：\n  " + "\n  ".join(str(level) for level in levels)
        metric_lines.append(line)

    return f"""你是一位严格的 PPT 教学内容评估专家。

请根据给定 PPT 文本内容，对每个评估维度打 1-10 分，并给出简短但具体的原因与优化建议。

输出要求：
1. 只输出 JSON，不要 markdown，不要解释
2. JSON 格式必须是：
{{
  "detailed_scores": [
    {{
      "principle": "1.1 指令遵循与任务完成",
      "score": 8,
      "reason": "......",
      "optimization_suggestion": "......"
    }}
  ]
}}
3. `detailed_scores` 的顺序必须与输入评估维度顺序一致
4. `score` 必须是 1-10 的整数
5. `reason` 和 `optimization_suggestion` 要具体、简洁，避免空话
6. 如果 PPT 内容明显缺失、偏题、事实不足，要直接扣分，不要默认高分

PPT 基本信息：
- 主题：{req.course}
- 单元：{req.units or "无"}
- 课时：{req.lessons or "无"}
- 补充要求：{req.constraint or "无"}
- 页数：{req.page_limit or 0}
- 语言：{req.lang}

评估维度：
{chr(10).join(metric_lines)}

PPT 内容：
{req.ppt_content}
"""


def extract_json_block(raw: str) -> dict[str, Any]:
    cleaned = str(raw or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    candidates = [cleaned] if cleaned else []
    if "{" in cleaned and "}" in cleaned:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            fragment = cleaned[start:end + 1]
            if fragment not in candidates:
                candidates.append(fragment)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue

    raise ValueError(f"无法解析评估 JSON，原始内容前 200 字：{cleaned[:200]}")


def normalize_scores(data: dict[str, Any], metrics: list[str]) -> dict[str, Any]:
    raw_scores = data.get("detailed_scores")
    scores = raw_scores if isinstance(raw_scores, list) else []

    normalized: list[dict[str, Any]] = []
    for index, metric in enumerate(metrics):
        item = scores[index] if index < len(scores) and isinstance(scores[index], dict) else {}
        score = item.get("score", 6)
        try:
            score_int = int(round(float(score)))
        except Exception:
            score_int = 6
        score_int = max(1, min(10, score_int))

        normalized.append({
            "principle": str(item.get("principle") or metric),
            "score": score_int,
            "reason": str(item.get("reason") or "已完成基础评估，但理由输出不足。"),
            "optimization_suggestion": str(
                item.get("optimization_suggestion") or "建议继续补充更具体的内容与结构优化建议。"
            ),
        })

    return {"detailed_scores": normalized}


def wrap_evaluation_score(data: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"


def evaluate_ppt_content(
    req: PPTEvaluationRequest,
    thinking_callback: Callable[[str], None] | None = None,
) -> tuple[PPTEvaluationArtifacts, str]:
    metrics = normalize_metrics(req.evaluation_metrics)
    prompt = build_evaluation_prompt(req, metrics)

    client = OpenAI(
        api_key=config.PLANNER_API_KEY,
        base_url=config.PLANNER_BASE_URL,
    )
    raw, reasoning_text = stream_chat_completion_text(
        client,
        model=config.PLANNER_MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是严格的 PPT 教学内容评估专家，只输出 JSON。",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.2,
        max_tokens=2200,
        on_reasoning_chunk=thinking_callback,
        **build_chat_completion_kwargs(config.PLANNER_MODEL),
    )
    parsed = normalize_scores(extract_json_block(raw), metrics)

    return (
        PPTEvaluationArtifacts(
            course=req.course,
            units=req.units,
            lessons=req.lessons,
            constraint=req.constraint,
            page_limit=req.page_limit,
            evaluation_metrics=metrics,
            evaluation_score=wrap_evaluation_score(parsed),
            principle_descriptions=build_principle_descriptions(metrics, req.lang),
            evaluator=config.PLANNER_MODEL,
            lang=req.lang,
        ),
        reasoning_text,
    )


def stream_evaluate_ppt_content(req: PPTEvaluationRequest) -> queue.Queue:
    event_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def emit(event: str, data: Any) -> None:
        payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        event_queue.put({"event": event, "data": payload})

    def worker() -> None:
        try:
            emit("thinking_start", {"step": 1, "node": EVAL_NODE_LABELS[0]})
            emit("thinking_chunk", "正在读取 PPT 文本内容、页数要求和目标受众。")
            emit("thinking_end", {})
            emit("progress", {"step": 1, "total": 3, "message": "正在调度下一步任务：按维度评分..."})

            emit("thinking_start", {"step": 2, "node": EVAL_NODE_LABELS[1]})
            emit("thinking_chunk", "正在结合评估维度，对任务完成度、相关性、准确性、专业性和表达效果进行评分。")
            artifacts, reasoning_text = evaluate_ppt_content(
                req,
                thinking_callback=lambda chunk: emit("thinking_chunk", chunk),
            )
            emit("thinking_end", {})
            emit("progress", {"step": 2, "total": 3, "message": "正在调度下一步任务：整理评估报告..."})

            emit("thinking_start", {"step": 3, "node": EVAL_NODE_LABELS[2]})
            emit("thinking_chunk", "正在整理评分结果、评分标准说明和优化建议。")
            emit("thinking_end", {})
            emit("progress", {"step": 3, "total": 3, "message": "评估内容已全部生成，正在整合处理..."})
            emit("done", artifacts.to_response())
        except Exception as exc:
            emit("error", {"detail": str(exc)})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    emit("progress", {"step": 0, "total": 3, "message": "正在启动 PPT 评估..."})
    return event_queue
