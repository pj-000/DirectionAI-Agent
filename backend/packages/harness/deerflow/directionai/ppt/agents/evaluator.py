"""
agents/evaluator.py

用 qwen-vl 对生成的 PPT 每页做视觉评分。
图片 base64 编码后发给多模态模型，返回 SlideEvalResult 列表。
如果 QWEN_API_KEY 未配置，evaluate_all() 直接返回空列表，不影响主流程。
"""
import base64
import json
import logging
import re
from pathlib import Path

from openai import OpenAI

from .. import config
from ..models.schemas import OutlinePlan, SlideEvalResult

logger = logging.getLogger(__name__)


class EvaluatorAgent:
    def __init__(self):
        self.enabled = bool(config.QWEN_API_KEY and config.QWEN_BASE_URL)
        if self.enabled:
            self.client = OpenAI(
                api_key=config.QWEN_API_KEY,
                base_url=config.QWEN_BASE_URL,
            )
        else:
            self.client = None
            print("[Evaluator] QWEN_API_KEY 未配置，视觉 QA 已禁用")

    def evaluate_all(
        self,
        image_paths: list[str],
        outline: OutlinePlan,
    ) -> list[SlideEvalResult]:
        """
        逐页视觉评分。image_paths 与 outline.slides 一一对应（长度可能不等，取 min）。
        返回 SlideEvalResult 列表。若单页评分失败，会回落为低分结果，避免 QA 静默跳过。
        """
        if not self.enabled:
            return []

        results = []
        n = min(len(image_paths), len(outline.slides))
        for i in range(n):
            img_path = image_paths[i]
            slide = outline.slides[i]
            if not img_path or not Path(img_path).exists():
                continue
            try:
                result = self._evaluate_slide(img_path, slide.slide_index, slide.topic, slide.layout.value)
                results.append(result)
                print(
                    f"[Evaluator] 第 {slide.slide_index} 页评分: "
                    f"layout={result.layout_score:.1f} content={result.content_score:.1f} "
                    f"design={result.design_score:.1f} overall={result.overall:.1f}"
                )
            except Exception as e:
                logger.warning(f"[Evaluator] 第 {slide.slide_index} 页评分失败: {e}")
                result = self._build_failed_result(slide.slide_index, str(e))
                results.append(result)
                print(
                    f"[Evaluator] 第 {slide.slide_index} 页评分失败，按低分处理: "
                    f"overall={result.overall:.1f}"
                )

        if results:
            avg = sum(r.overall for r in results) / len(results)
            low = [r for r in results if r.overall < config.EVAL_SCORE_THRESHOLD]
            print(f"[Evaluator] 评分完成，平均分 {avg:.2f}，{len(low)} 页低于阈值 {config.EVAL_SCORE_THRESHOLD}")

        return results

    def _evaluate_slide(
        self,
        image_path: str,
        slide_index: int,
        topic: str,
        layout: str,
    ) -> SlideEvalResult:
        """单页评分：base64 编码图片，发给 qwen-vl，解析 JSON 结果。"""
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        ext = Path(image_path).suffix.lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"

        system = (
            "你是 PPT 视觉质量评审专家。\n\n"
            "**关键心态：假设一定有问题。你的任务是找出它们。**\n"
            "第一次渲染几乎不可能完美。如果你没发现任何问题，说明你看得不够仔细。\n"
            "即使是小问题也要列出来。\n\n"
            "对幻灯片图片进行评分，只输出 JSON，不要解释。\n"
            "输出务必简短：最多 3 条 issues、最多 2 条 suggestions；每条尽量控制在 18-32 个字。\n"
            "不要写长句，不要在字符串里继续解释原因，不要输出多余字段。\n"
            "格式：\n"
            "{\n"
            '  "layout_score": 4.0,\n'
            '  "content_score": 3.5,\n'
            '  "design_score": 4.0,\n'
            '  "issues": ["问题1", "问题2"],\n'
            '  "suggestions": ["建议1", "建议2"]\n'
            "}\n\n"
            "评分标准（1-5分）：\n"
            "layout_score 重点检查：\n"
            "- 元素重叠（文字穿过形状、线条穿过文字、元素堆叠）\n"
            "- 文字溢出或被裁切（超出文本框/幻灯片边界）\n"
            "- 装饰线为单行文字设计但标题换行了\n"
            "- 来源引用或页脚与上方内容碰撞\n"
            "- 元素间距过小（< 0.3\"）或卡片/区块几乎贴在一起\n"
            "- 间距不均匀（一处大片空白，另一处拥挤）\n"
            "- 幻灯片边缘边距不足（< 0.5\"）\n"
            "- 列或相似元素未对齐\n"
            "- 文本框过窄导致过度换行\n\n"
            "content_score 重点检查：\n"
            "- 标题是否清晰可读\n"
            "- 要点是否充实（不是只有几个词）\n"
            "- 是否有占位符残留文字\n"
            "- 信息密度是否适当\n\n"
            "design_score 重点检查：\n"
            "- 低对比度文字（如浅灰文字在浅色背景上）\n"
            "- 低对比度图标（如深色图标在深色背景上没有对比圆圈）\n"
            "- 是否有视觉重心（不是纯文字堆砌）\n"
            "- 配色是否协调\n"
            "- 是否有标题下方的装饰横线（AI 生成的典型反模式）\n\n"
            "**重要：issues 必须列出所有发现的问题，包括小问题。如果 issues 为空，重新检查。**"
        )

        user_text = (
            f"这是第 {slide_index} 页幻灯片，主题：{topic}，布局类型：{layout}。\n"
            "请对这张幻灯片图片进行视觉质量评分。"
        )

        response = self.client.chat.completions.create(
            model=config.QWEN_VL_MODEL,
            max_tokens=640,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                        {"type": "text", "text": user_text},
                    ],
                },
            ],
        )

        raw = response.choices[0].message.content
        data = self._parse_json(raw)

        layout_score = self._coerce_score(data.get("layout_score"), 3.0)
        content_score = self._coerce_score(data.get("content_score"), 3.0)
        design_score = self._coerce_score(data.get("design_score"), 3.0)
        overall = layout_score * 0.4 + content_score * 0.3 + design_score * 0.3
        issues = self._coerce_string_list(data.get("issues"))
        suggestions = self._coerce_string_list(data.get("suggestions"))

        return SlideEvalResult(
            slide_index=slide_index,
            layout_score=layout_score,
            content_score=content_score,
            design_score=design_score,
            overall=round(overall, 2),
            issues=issues,
            suggestions=suggestions,
        )

    @staticmethod
    def _parse_json(raw: str) -> dict:
        cleaned = str(raw or "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

        candidates: list[str] = []
        if cleaned:
            candidates.append(cleaned)

        extracted = EvaluatorAgent._extract_outer_json_object(cleaned)
        if extracted and extracted not in candidates:
            candidates.append(extracted)

        sanitized = EvaluatorAgent._sanitize_json_strings(extracted or cleaned)
        if sanitized and sanitized not in candidates:
            candidates.append(sanitized)

        repaired = EvaluatorAgent._repair_missing_json_commas(sanitized)
        if repaired and repaired not in candidates:
            candidates.append(repaired)

        for candidate in candidates:
            try:
                data = json.loads(candidate, strict=False)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue

        partial = EvaluatorAgent._extract_partial_result(cleaned)
        if partial:
            return partial

        raise ValueError(f"无法解析评分 JSON，原始内容前200字：{cleaned[:200]}")

    @staticmethod
    def _extract_partial_result(text: str) -> dict:
        def parse_score(field: str):
            match = re.search(rf'"{field}"\s*:\s*(-?\d+(?:\.\d+)?)', text)
            return float(match.group(1)) if match else None

        def extract_array_items(field: str) -> list[str]:
            field_match = re.search(rf'"{field}"\s*:\s*\[', text)
            if not field_match:
                return []

            start = field_match.end()
            end_candidates = []
            next_field = re.search(r',?\s*"(?:suggestions|issues|layout_score|content_score|design_score)"\s*:', text[start:])
            if next_field:
                end_candidates.append(start + next_field.start())
            close_bracket = text.find("]", start)
            if close_bracket >= 0:
                end_candidates.append(close_bracket)
            segment = text[start:min(end_candidates)] if end_candidates else text[start:]

            items: list[str] = []
            i = 0
            while i < len(segment):
                if segment[i] != '"':
                    i += 1
                    continue
                j = i + 1
                escape = False
                buf: list[str] = []
                while j < len(segment):
                    ch = segment[j]
                    if escape:
                        buf.append(ch)
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        items.append("".join(buf))
                        i = j + 1
                        break
                    else:
                        buf.append(ch)
                    j += 1
                else:
                    break
            return [item.strip() for item in items if item.strip()]

        layout_score = parse_score("layout_score")
        content_score = parse_score("content_score")
        design_score = parse_score("design_score")

        if layout_score is None and content_score is None and design_score is None:
            return {}

        issues = extract_array_items("issues")
        suggestions = extract_array_items("suggestions")

        partial = {
            "layout_score": layout_score if layout_score is not None else 3.0,
            "content_score": content_score if content_score is not None else 3.0,
            "design_score": design_score if design_score is not None else 3.0,
            "issues": issues,
            "suggestions": suggestions,
        }
        return partial

    @staticmethod
    def _extract_outer_json_object(text: str) -> str:
        positions = [(text.find("{"), "{"), (text.find("["), "[")]
        positions = [(pos, ch) for pos, ch in positions if pos >= 0]
        if not positions:
            return text

        start, opener = min(positions, key=lambda item: item[0])
        closing_map = {"{": "}", "[": "]"}
        stack = [closing_map[opener]]
        in_string = False
        escape = False

        for i in range(start + 1, len(text)):
            ch = text[i]

            if escape:
                escape = False
                continue

            if ch == "\\":
                escape = True
                continue

            if ch == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if ch in closing_map:
                stack.append(closing_map[ch])
                continue

            if stack and ch == stack[-1]:
                stack.pop()
                if not stack:
                    return text[start:i + 1]

        return text[start:]

    @staticmethod
    def _sanitize_json_strings(text: str) -> str:
        quote_escape_map = {
            "\u201c": "\\u201c",
            "\u201d": "\\u201d",
            "\u2018": "\\u2018",
            "\u2019": "\\u2019",
            "\u300c": "\\u300c",
            "\u300d": "\\u300d",
            "\u300e": "\\u300e",
            "\u300f": "\\u300f",
        }

        result: list[str] = []
        in_string = False
        i = 0

        while i < len(text):
            ch = text[i]
            next_ch = text[i + 1] if i + 1 < len(text) else ""

            if ch == "\\" and in_string and next_ch:
                result.extend((ch, next_ch))
                i += 2
                continue

            if not in_string:
                result.append(ch)
                if ch == '"':
                    in_string = True
                i += 1
                continue

            if ch in quote_escape_map:
                result.append(quote_escape_map[ch])
                i += 1
                continue

            if ch == "\n":
                result.append("\\n")
                i += 1
                continue

            if ch == "\r":
                result.append("\\r")
                i += 1
                continue

            if ch == "\t":
                result.append("\\t")
                i += 1
                continue

            if ch == '"':
                if EvaluatorAgent._is_probable_json_string_end(text, i):
                    in_string = False
                    result.append(ch)
                else:
                    result.append("\\u0022")
                i += 1
                continue

            result.append(ch)
            i += 1

        sanitized = "".join(result)
        sanitized = re.sub(r",(\s*[}\]])", r"\1", sanitized)
        return sanitized

    @staticmethod
    def _consume_json_string(text: str, start: int) -> int:
        i = start + 1
        escape = False

        while i < len(text):
            ch = text[i]
            if escape:
                escape = False
                i += 1
                continue
            if ch == "\\":
                escape = True
                i += 1
                continue
            if ch == '"':
                return i + 1
            i += 1

        return len(text)

    @staticmethod
    def _consume_json_literal(text: str, start: int) -> int:
        i = start
        while i < len(text) and text[i] in "-+0123456789.eE":
            i += 1
        return i

    @staticmethod
    def _repair_missing_json_commas(text: str) -> str:
        if not text:
            return text

        result: list[str] = []
        stack: list[dict] = []
        i = 0

        def current():
            return stack[-1] if stack else None

        def maybe_insert_comma(next_char: str) -> None:
            ctx = current()
            if not ctx or ctx["state"] != "expect_comma_or_end":
                return
            if next_char.isspace() or next_char in ",}]":
                return
            result.append(",")
            ctx["state"] = "expect_key_or_end" if ctx["type"] == "object" else "expect_value_or_end"

        def mark_value_consumed() -> None:
            ctx = current()
            if not ctx:
                return
            if ctx["type"] == "object" and ctx["state"] == "expect_value":
                ctx["state"] = "expect_comma_or_end"
            elif ctx["type"] == "array" and ctx["state"] == "expect_value_or_end":
                ctx["state"] = "expect_comma_or_end"

        while i < len(text):
            ch = text[i]

            if ch.isspace():
                result.append(ch)
                i += 1
                continue

            maybe_insert_comma(ch)
            ctx = current()

            if ch == "{":
                was_value = bool(
                    ctx and (
                        (ctx["type"] == "object" and ctx["state"] == "expect_value")
                        or (ctx["type"] == "array" and ctx["state"] == "expect_value_or_end")
                    )
                )
                result.append(ch)
                stack.append({"type": "object", "state": "expect_key_or_end", "was_value": was_value})
                i += 1
                continue

            if ch == "[":
                was_value = bool(
                    ctx and (
                        (ctx["type"] == "object" and ctx["state"] == "expect_value")
                        or (ctx["type"] == "array" and ctx["state"] == "expect_value_or_end")
                    )
                )
                result.append(ch)
                stack.append({"type": "array", "state": "expect_value_or_end", "was_value": was_value})
                i += 1
                continue

            if ch == "}":
                result.append(ch)
                if stack and stack[-1]["type"] == "object":
                    popped = stack.pop()
                    if popped.get("was_value"):
                        mark_value_consumed()
                i += 1
                continue

            if ch == "]":
                result.append(ch)
                if stack and stack[-1]["type"] == "array":
                    popped = stack.pop()
                    if popped.get("was_value"):
                        mark_value_consumed()
                i += 1
                continue

            if ch == ",":
                result.append(ch)
                if ctx:
                    ctx["state"] = "expect_key_or_end" if ctx["type"] == "object" else "expect_value_or_end"
                i += 1
                continue

            if ch == ":":
                result.append(ch)
                if ctx and ctx["type"] == "object" and ctx["state"] == "expect_colon":
                    ctx["state"] = "expect_value"
                i += 1
                continue

            if ch == '"':
                end = EvaluatorAgent._consume_json_string(text, i)
                result.append(text[i:end])
                if ctx:
                    if ctx["type"] == "object":
                        if ctx["state"] == "expect_key_or_end":
                            ctx["state"] = "expect_colon"
                        elif ctx["state"] == "expect_value":
                            ctx["state"] = "expect_comma_or_end"
                    elif ctx["type"] == "array" and ctx["state"] == "expect_value_or_end":
                        ctx["state"] = "expect_comma_or_end"
                i = end
                continue

            literal_match = None
            for literal in ("true", "false", "null"):
                if text.startswith(literal, i):
                    literal_match = literal
                    break
            if literal_match:
                result.append(literal_match)
                mark_value_consumed()
                i += len(literal_match)
                continue

            if ch in "-0123456789":
                end = EvaluatorAgent._consume_json_literal(text, i)
                result.append(text[i:end])
                mark_value_consumed()
                i = end
                continue

            result.append(ch)
            i += 1

        repaired = "".join(result)
        repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
        return repaired

    @staticmethod
    def _is_probable_json_string_end(text: str, quote_index: int) -> bool:
        j = quote_index + 1
        while j < len(text) and text[j].isspace():
            j += 1
        return j >= len(text) or text[j] in "\",:}]"

    @staticmethod
    def _coerce_score(value, default: float) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if match:
                return float(match.group(0))
        return float(default)

    @staticmethod
    def _coerce_string_list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()]

    @staticmethod
    def _build_failed_result(slide_index: int, error: str) -> SlideEvalResult:
        return SlideEvalResult(
            slide_index=slide_index,
            layout_score=1.5,
            content_score=1.5,
            design_score=1.5,
            overall=1.5,
            issues=[f"视觉评分失败：{error[:180]}"],
            suggestions=[
                "重新检查本页排版与文本复杂度，避免过密内容和难以辨识的标注。",
                "保留清晰标题层级，减少可能引发评分模型误判的复杂引号或杂乱说明。",
            ],
        )
