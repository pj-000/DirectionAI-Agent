"""PPT Generation API Router."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

router = APIRouter(prefix="/pptagentapi", tags=["ppt"])

# ─── Output paths ────────────────────────────────────────────────────────────
_DEER_FLOW_HOME = os.environ.get("DEER_FLOW_HOME", ".deer-flow")
_OUTPUT_ROOT = Path(_DEER_FLOW_HOME) / "outputs"
_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def _serialize_sse(event: str | None, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    lines = [f"event: {event}"] if event else []
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


# ─── SSE streaming ────────────────────────────────────────────────────────────
@router.get("/stream_ppt")
async def stream_ppt(request: Request) -> StreamingResponse:
    """Stream PPT generation progress via SSE (GET with query params)."""
    from deerflow.directionai.ppt.api import (
        PPTGenerationRequest as OrigRequest,
        _ThinkingStreamSanitizer,
        _yield_stream_item,
        generate_ppt_bundle,
    )

    # Parse query params
    params = dict(request.query_params)
    topic = params.pop("topic", None) or params.pop("course", None)
    if not topic:
        raise HTTPException(status_code=400, detail="topic 或 course 参数不能为空")

    try:
        orig_req = OrigRequest(
            topic=topic,
            model_provider=params.get("model_provider", "minmax"),
            output_language=params.get("output_language", "中文"),
            target_audience=params.get("target_audience", "general"),
            style=params.get("style", ""),
            enable_web_search=params.get("enable_web_search", "") in ("true", "1", "yes"),
            image_mode=params.get("image_mode", "generate"),
            min_slides=int(params.get("min_slides", 6)),
            max_slides=int(params.get("max_slides", 10)),
            page_limit=int(params["page_limit"]) if "page_limit" in params else None,
            debug_layout=params.get("debug_layout", "") in ("true", "1", "yes"),
            content=params.get("content") or params.get("constraint"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"参数错误: {exc}")

    event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()

    def emit(event: str, data: Any) -> None:
        event_queue.put({"event": event, "data": data})

    def worker() -> None:
        try:
            artifacts = generate_ppt_bundle(orig_req, emit=emit)
            emit("done", artifacts.to_response())
        except Exception as exc:
            emit("error", {"detail": str(exc)})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    async def event_stream():
        sanitizer = _ThinkingStreamSanitizer()
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.to_thread(event_queue.get, timeout=15)
            except queue.Empty:
                if not thread.is_alive():
                    yield _serialize_sse("error", {"detail": "工作线程意外退出"})
                    break
                yield "data: \u6bcf15\u79d2\u8fde\u63a5\u4fdd\u6d3b\n\n"
                continue

            async for payload in _yield_stream_item(item, sanitizer):
                yield payload
            if item.get("event") in {"done", "error"}:
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Download ─────────────────────────────────────────────────────────────────
@router.get("/download_ppt/{filename}")
def download_ppt(filename: str) -> FileResponse:
    """Download a generated PPTX file."""
    safe_name = Path(filename).name
    target = (_OUTPUT_ROOT / safe_name).resolve()

    if target.parent != _OUTPUT_ROOT:
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=str(target),
        filename=safe_name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


# ─── Preview images ───────────────────────────────────────────────────────────
@router.get("/preview_ppt/{filename}/{image_name}")
def preview_ppt_image(filename: str, image_name: str) -> FileResponse:
    """Serve a preview thumbnail image."""
    safe_name = Path(filename).name
    safe_image = Path(image_name).name
    stem = safe_name.replace(".pptx", "")
    preview_dir = _OUTPUT_ROOT / "slides_preview" / stem
    target = (preview_dir / safe_image).resolve()

    if not str(target).startswith(str(preview_dir)):
        raise HTTPException(status_code=404, detail="文件不存在")
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    suffix = target.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"
    return FileResponse(path=str(target), media_type=media_type)
