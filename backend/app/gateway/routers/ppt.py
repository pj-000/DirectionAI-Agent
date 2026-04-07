"""PPT Generation API Router."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any

from deerflow.directionai.runtime_paths import get_directionai_data_dir
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

router = APIRouter(prefix="/pptagentapi", tags=["ppt"])

# ─── Output paths ────────────────────────────────────────────────────────────
_DEER_FLOW_HOME = get_directionai_data_dir()
_OUTPUT_ROOT = (_DEER_FLOW_HOME / "outputs").resolve()
_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
_TASK_ROOT = (_DEER_FLOW_HOME / "ppt_tasks").resolve()
_TASK_ROOT.mkdir(parents=True, exist_ok=True)
_TASK_RUNTIME_TTL_SECONDS = 60 * 60
_TASK_RUNTIME_LOCK = threading.Lock()
_TASK_RUNTIMES: dict[str, "_PPTTaskRuntime"] = {}


class _PPTTaskRuntime:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.history: list[dict[str, Any]] = []
        self.subscribers: set["queue.Queue[dict[str, Any]]"] = set()
        self.thread: threading.Thread | None = None
        self.terminal_event: str | None = None
        self.updated_at = time.time()
        self.lock = threading.Lock()

    def publish(self, event: str, data: Any) -> None:
        item = {"event": event, "data": data}
        with self.lock:
            self.history.append(item)
            self.updated_at = time.time()
            if event in {"done", "error"}:
                self.terminal_event = event
            subscribers = list(self.subscribers)

        for subscriber in subscribers:
            subscriber.put(item)

    def subscribe(self) -> tuple["queue.Queue[dict[str, Any]]", list[dict[str, Any]], str | None]:
        subscriber: "queue.Queue[dict[str, Any]]" = queue.Queue()
        with self.lock:
            self.subscribers.add(subscriber)
            self.updated_at = time.time()
            return subscriber, list(self.history), self.terminal_event

    def unsubscribe(self, subscriber: "queue.Queue[dict[str, Any]]") -> None:
        with self.lock:
            self.subscribers.discard(subscriber)
            self.updated_at = time.time()

    def is_terminal(self) -> bool:
        with self.lock:
            return self.terminal_event is not None


def _prune_task_runtimes() -> None:
    cutoff = time.time() - _TASK_RUNTIME_TTL_SECONDS
    with _TASK_RUNTIME_LOCK:
        stale_task_ids: list[str] = []
        for task_id, runtime in _TASK_RUNTIMES.items():
            with runtime.lock:
                has_subscribers = bool(runtime.subscribers)
                is_terminal = runtime.terminal_event is not None
                is_alive = runtime.thread is not None and runtime.thread.is_alive()
                updated_at = runtime.updated_at
            if is_terminal and not has_subscribers and not is_alive and updated_at < cutoff:
                stale_task_ids.append(task_id)

        for task_id in stale_task_ids:
            _TASK_RUNTIMES.pop(task_id, None)


def _serialize_sse(event: str | None, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    lines = [f"event: {event}"] if event else []
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def _load_task_payload(task_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        raise HTTPException(status_code=400, detail="非法 task_id")

    task_path = (_TASK_ROOT / f"{task_id}.json").resolve()
    if task_path.parent != _TASK_ROOT or not task_path.exists():
        raise HTTPException(status_code=404, detail="PPT 任务不存在")

    try:
        payload = json.loads(task_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法读取 PPT 任务: {exc}") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="PPT 任务格式无效")
    return payload


def _build_generation_request(params: dict[str, Any]):
    from deerflow.directionai.ppt.api import PPTGenerationRequest as OrigRequest

    topic = params.get("topic") or params.get("course")
    if not topic:
        raise HTTPException(status_code=400, detail="topic 或 course 参数不能为空")

    try:
        return OrigRequest(
            topic=str(topic),
            model_provider=str(params.get("model_provider", "minmax")),
            output_language=str(params.get("output_language", "中文")),
            target_audience=str(params.get("target_audience", "general")),
            style=str(params.get("style", "")),
            enable_web_search=params.get("enable_web_search") in (True, "true", "1", "yes"),
            image_mode=str(params.get("image_mode", "generate")),
            min_slides=max(4, int(params.get("min_slides", 6))),
            max_slides=max(4, int(params.get("max_slides", 10))),
            page_limit=int(params["page_limit"]) if params.get("page_limit") not in (None, "") else None,
            debug_layout=params.get("debug_layout") in (True, "true", "1", "yes"),
            content=params.get("content") or params.get("constraint"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"参数错误: {exc}") from exc


def _start_generation_worker(orig_req, emit):
    from deerflow.directionai.ppt.api import generate_ppt_bundle

    def worker() -> None:
        try:
            artifacts = generate_ppt_bundle(orig_req, emit=emit)
            emit("done", artifacts.to_response())
        except Exception as exc:
            emit("error", {"detail": str(exc)})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _get_or_start_task_runtime(task_id: str, orig_req) -> _PPTTaskRuntime:
    _prune_task_runtimes()

    with _TASK_RUNTIME_LOCK:
        runtime = _TASK_RUNTIMES.get(task_id)
        if runtime is None:
            runtime = _PPTTaskRuntime(task_id)
            _TASK_RUNTIMES[task_id] = runtime

        with runtime.lock:
            needs_worker = runtime.terminal_event is None and (
                runtime.thread is None or not runtime.thread.is_alive()
            )
            if needs_worker:
                runtime.thread = _start_generation_worker(orig_req, runtime.publish)
                runtime.updated_at = time.time()

        return runtime


def _stream_queue_with_thread(
    request: Request,
    event_queue: "queue.Queue[dict[str, Any]]",
    thread: threading.Thread | None = None,
):
    from deerflow.directionai.ppt.api import _ThinkingStreamSanitizer, _yield_stream_item

    async def event_stream():
        sanitizer = _ThinkingStreamSanitizer()
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.to_thread(event_queue.get, timeout=15)
            except queue.Empty:
                if thread is not None and not thread.is_alive():
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


def _build_runtime_snapshot(history: list[dict[str, Any]]) -> dict[str, Any]:
    from deerflow.directionai.ppt.api import _ThinkingStreamSanitizer

    sanitizer = _ThinkingStreamSanitizer()
    steps: list[dict[str, Any]] = []
    preview: dict[str, Any] | None = None
    done_data: dict[str, Any] | None = None
    error: str | None = None
    expected_total = 0

    def mark_active_steps_done() -> None:
        for step in steps:
            if step.get("status") == "active":
                step["status"] = "done"

    def find_step_index(step_number: int) -> int:
        for index, step in enumerate(steps):
            if step.get("step") == step_number:
                return index
        return -1

    def find_last_active_step() -> dict[str, Any] | None:
        for step in reversed(steps):
            if step.get("status") == "active":
                return step
        return None

    def append_thinking_content(text: Any) -> None:
        active_step = find_last_active_step()
        if active_step is None:
            return
        for chunk in sanitizer.feed(text):
            active_step["content"] = f"{active_step.get('content', '')}{chunk}"

    def flush_thinking_content() -> None:
        active_step = find_last_active_step()
        if active_step is None:
            return
        for chunk in sanitizer.flush():
            active_step["content"] = f"{active_step.get('content', '')}{chunk}"

    for item in history:
        event = item.get("event")
        data = item.get("data")

        if event == "thinking_start" and isinstance(data, dict):
            step_number = int(data.get("step") or (len(steps) + 1))
            node = str(data.get("node") or "")
            mark_active_steps_done()
            sanitizer.reset()
            existing_index = find_step_index(step_number)
            if existing_index >= 0:
                existing = steps[existing_index]
                existing["node"] = node or existing.get("node", "")
                existing["status"] = "active"
            else:
                steps.append(
                    {
                        "step": step_number,
                        "node": node,
                        "content": "",
                        "status": "active",
                    }
                )
            continue

        if event == "thinking_chunk":
            append_thinking_content(data)
            continue

        if event == "thinking_end":
            flush_thinking_content()
            target_step = data.get("step") if isinstance(data, dict) else None
            if target_step is not None:
                existing_index = find_step_index(int(target_step))
                if existing_index >= 0:
                    steps[existing_index]["status"] = "done"
            else:
                active_step = find_last_active_step()
                if active_step is not None:
                    active_step["status"] = "done"
            continue

        if event == "progress" and isinstance(data, dict):
            step_number = int(data.get("step") or 0)
            total = int(data.get("total") or 0)
            message = str(data.get("message") or "")
            if total > 0:
                expected_total = max(expected_total, total)

            existing_index = find_step_index(step_number)
            if existing_index >= 0:
                existing = steps[existing_index]
                existing["node"] = str(existing.get("node") or message)
                existing["progressMessage"] = message
                if existing.get("status") == "active":
                    existing["status"] = "done"
            else:
                steps.append(
                    {
                        "step": step_number if step_number > 0 else (len(steps) + 1),
                        "node": message,
                        "content": "",
                        "status": "done",
                        "progressMessage": message,
                    }
                )
            continue

        if event == "preview" and isinstance(data, dict):
            preview = data
            continue

        if event == "done" and isinstance(data, dict):
            mark_active_steps_done()
            done_data = data
            error = None
            continue

        if event == "error":
            mark_active_steps_done()
            if isinstance(data, dict):
                error = str(data.get("detail") or "未知错误")
            else:
                error = str(data or "未知错误")

    return {
        "steps": steps,
        "preview": preview,
        "doneData": done_data,
        "error": error,
        "expectedTotal": expected_total,
        "isStreaming": done_data is None and error is None,
    }


def _stream_task_runtime(request: Request, runtime: _PPTTaskRuntime):
    from deerflow.directionai.ppt.api import _ThinkingStreamSanitizer, _yield_stream_item

    async def event_stream():
        sanitizer = _ThinkingStreamSanitizer()
        subscriber, history, terminal_event = runtime.subscribe()
        try:
            if history or terminal_event is not None:
                yield _serialize_sse("snapshot", _build_runtime_snapshot(history))

            if terminal_event is not None:
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.to_thread(subscriber.get, timeout=15)
                except queue.Empty:
                    if runtime.is_terminal():
                        break
                    yield "data: \u6bcf15\u79d2\u8fde\u63a5\u4fdd\u6d3b\n\n"
                    continue

                async for payload in _yield_stream_item(item, sanitizer):
                    yield payload

                if item.get("event") in {"done", "error"}:
                    break
        finally:
            runtime.unsubscribe(subscriber)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── SSE streaming ────────────────────────────────────────────────────────────
@router.get("/stream_ppt")
async def stream_ppt(request: Request) -> StreamingResponse:
    """Stream PPT generation progress via SSE (GET with query params)."""
    params = dict(request.query_params)
    if task_id := params.get("task_id"):
        orig_req = _build_generation_request(_load_task_payload(str(task_id)))
        runtime = _get_or_start_task_runtime(str(task_id), orig_req)
        return _stream_task_runtime(request, runtime)

    orig_req = _build_generation_request(params)

    event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()

    def emit(event: str, data: Any) -> None:
        event_queue.put({"event": event, "data": data})

    thread = _start_generation_worker(orig_req, emit)

    return _stream_queue_with_thread(request, event_queue, thread)


@router.get("/stream_ppt/{task_id}")
async def stream_ppt_task(task_id: str, request: Request) -> StreamingResponse:
    """Stream PPT generation progress via a persisted task payload."""
    orig_req = _build_generation_request(_load_task_payload(task_id))
    runtime = _get_or_start_task_runtime(task_id, orig_req)
    return _stream_task_runtime(request, runtime)


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
