"""Utilities for normalizing document-heavy PPT input."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from deerflow.directionai.ppt.tools.document_summarizer import DocumentSummary, summarize_document

logger = logging.getLogger(__name__)

_SUMMARY_TRIGGER_CHARS = 12_000
_DOCUMENT_HINT_TRIGGER_CHARS = 4_000
_PRE_SUMMARY_TARGET_CHARS = 24_000
_FALLBACK_TARGET_CHARS = 12_000
_MAX_DEBUG_PREVIEW_CHARS = 2_000


@dataclass
class PreparedPPTContent:
    planner_content: str
    raw_content: str
    normalization: str
    summary: dict[str, Any] | None
    debug: dict[str, Any]


def prepare_ppt_content(
    topic: str,
    content: str,
    *,
    model_provider: str,
) -> PreparedPPTContent:
    cleaned = str(content or "").strip()
    debug: dict[str, Any] = {
        "topic": topic,
        "raw_char_count": len(cleaned),
        "used_document_summary": False,
    }
    if not cleaned:
        debug["planner_char_count"] = 0
        return PreparedPPTContent(
            planner_content="",
            raw_content="",
            normalization="empty",
            summary=None,
            debug=debug,
        )

    structured_summary = _try_parse_document_summary(cleaned)
    if structured_summary is not None:
        planner_content = structured_summary.to_planner_context().strip()
        debug.update(
            {
                "used_document_summary": True,
                "planner_char_count": len(planner_content),
                "summary_sections": len(structured_summary.sections),
                "summary_suggested_total_slides": structured_summary.ppt_generation_hints.suggested_total_slides,
            }
        )
        return PreparedPPTContent(
            planner_content=planner_content,
            raw_content=cleaned,
            normalization="document_summary_json",
            summary=structured_summary.model_dump(mode="json"),
            debug=debug,
        )

    if not _should_summarize(cleaned):
        debug["planner_char_count"] = len(cleaned)
        return PreparedPPTContent(
            planner_content=cleaned,
            raw_content=cleaned,
            normalization="raw",
            summary=None,
            debug=debug,
        )

    compressed = _compress_document_text(cleaned, target_chars=_PRE_SUMMARY_TARGET_CHARS)
    debug["pre_summary_char_count"] = len(compressed)

    try:
        summary = summarize_document(
            compressed,
            source_file=topic or None,
            model_provider=model_provider,
            max_input_chars=_PRE_SUMMARY_TARGET_CHARS,
        )
        planner_content = summary.to_planner_context().strip()
        debug.update(
            {
                "used_document_summary": True,
                "planner_char_count": len(planner_content),
                "summary_sections": len(summary.sections),
                "summary_suggested_total_slides": summary.ppt_generation_hints.suggested_total_slides,
            }
        )
        return PreparedPPTContent(
            planner_content=planner_content,
            raw_content=cleaned,
            normalization="llm_document_summary",
            summary=summary.model_dump(mode="json"),
            debug=debug,
        )
    except Exception as exc:
        logger.warning("Failed to summarize long PPT content for topic %r: %s", topic, exc)
        fallback = _build_fallback_digest(cleaned, target_chars=_FALLBACK_TARGET_CHARS)
        debug.update(
            {
                "planner_char_count": len(fallback),
                "summary_error": str(exc),
            }
        )
        return PreparedPPTContent(
            planner_content=fallback,
            raw_content=cleaned,
            normalization="fallback_digest",
            summary=None,
            debug=debug,
        )


def build_task_payload(
    *,
    topic: str,
    output_language: str,
    target_audience: str,
    style: str,
    min_slides: int,
    max_slides: int,
    model_provider: str,
    image_mode: str,
    enable_web_search: bool,
    content: str,
) -> dict[str, Any]:
    prepared = prepare_ppt_content(topic, content, model_provider=model_provider)
    payload = {
        "topic": topic,
        "min_slides": min_slides,
        "max_slides": max_slides,
        "output_language": output_language,
        "target_audience": target_audience,
        "model_provider": model_provider,
        "image_mode": image_mode,
        "enable_web_search": enable_web_search,
        "style": style,
        "content": prepared.planner_content,
        "content_debug": {
            **prepared.debug,
            "normalization": prepared.normalization,
            "raw_preview": prepared.raw_content[:_MAX_DEBUG_PREVIEW_CHARS],
            "planner_preview": prepared.planner_content[:_MAX_DEBUG_PREVIEW_CHARS],
        },
    }
    if prepared.summary is not None:
        payload["document_summary"] = prepared.summary
    return payload


def _try_parse_document_summary(text: str) -> DocumentSummary | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    required_fields = {"document_title", "document_summary", "sections"}
    if not required_fields.issubset(data):
        return None
    try:
        return DocumentSummary.model_validate(data)
    except Exception:
        return None


def _should_summarize(text: str) -> bool:
    if len(text) >= _SUMMARY_TRIGGER_CHARS:
        return True
    if len(text) < _DOCUMENT_HINT_TRIGGER_CHARS:
        return False
    return _looks_like_extracted_document(text)


def _looks_like_extracted_document(text: str) -> bool:
    normalized = text.lower()
    if "<!-- slide number:" in normalized:
        return True
    markdown_table_lines = sum(1 for line in text.splitlines() if line.strip().startswith("|"))
    headings = sum(1 for line in text.splitlines() if re.match(r"^\s{0,3}(#|第.+[章节部分]|chapter\s+\d+)", line, re.IGNORECASE))
    bullet_lines = sum(1 for line in text.splitlines() if re.match(r"^\s*[-*•]\s+", line))
    return markdown_table_lines >= 4 or headings >= 3 or bullet_lines >= 8


def _compress_document_text(text: str, *, target_chars: int) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    selected: list[str] = []
    current_len = 0
    heading_budget = 0
    bullet_budget = 0
    paragraph_budget = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if selected and selected[-1] != "":
                selected.append("")
                current_len += 1
            continue

        keep = False
        if _is_heading_line(stripped):
            keep = heading_budget < 40
            heading_budget += 1 if keep else 0
        elif stripped.startswith("|"):
            keep = True
        elif re.match(r"^\s*[-*•]\s+", stripped):
            keep = bullet_budget < 120
            bullet_budget += 1 if keep else 0
        else:
            keep = paragraph_budget < 120 and len(stripped) >= 24
            paragraph_budget += 1 if keep else 0

        if not keep:
            continue

        selected.append(stripped)
        current_len += len(stripped) + 1
        if current_len >= target_chars:
            break

    compressed = "\n".join(selected).strip()
    if len(compressed) > target_chars:
        compressed = compressed[:target_chars].rstrip()
    return compressed or text[:target_chars].rstrip()


def _build_fallback_digest(text: str, *, target_chars: int) -> str:
    compressed = _compress_document_text(text, target_chars=target_chars)
    if len(compressed) >= min(4_000, target_chars // 2):
        return compressed

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    digest_parts: list[str] = []
    current_len = 0
    for part in paragraphs:
        snippet = part[:600].strip()
        if not snippet:
            continue
        digest_parts.append(snippet)
        current_len += len(snippet) + 2
        if current_len >= target_chars:
            break
    digest = "\n\n".join(digest_parts).strip()
    return digest[:target_chars].rstrip() or text[:target_chars].rstrip()


def _is_heading_line(text: str) -> bool:
    return bool(
        re.match(r"^\s{0,3}#{1,6}\s+", text)
        or re.match(r"^第[\d一二三四五六七八九十百]+[章节部分篇]", text)
        or re.match(r"^chapter\s+\d+", text, re.IGNORECASE)
    )

