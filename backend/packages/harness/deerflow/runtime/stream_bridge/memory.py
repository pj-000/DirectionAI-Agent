"""In-memory stream bridge with replay support."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .base import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridge, StreamEvent

logger = logging.getLogger(__name__)


@dataclass
class _RunState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    events: list[StreamEvent] = field(default_factory=list)
    next_seq: int = 0
    base_seq: int = 0
    ended: bool = False


class MemoryStreamBridge(StreamBridge):
    """Per-run in-memory stream bridge.

    Unlike a queue-backed implementation, events are retained so that:
    - multiple subscribers can consume the same run concurrently
    - reconnecting clients can resume from ``Last-Event-ID``
    """

    def __init__(self, *, queue_maxsize: int = 256) -> None:
        self._maxsize = max(1, queue_maxsize)
        self._states: dict[str, _RunState] = {}

    def _get_or_create_state(self, run_id: str) -> _RunState:
        if run_id not in self._states:
            self._states[run_id] = _RunState()
        return self._states[run_id]

    @staticmethod
    def _build_event_id(seq: int) -> str:
        ts = int(time.time() * 1000)
        return f"{ts}-{seq}"

    async def publish(self, run_id: str, event: str, data: Any) -> None:
        state = self._get_or_create_state(run_id)
        async with state.condition:
            entry = StreamEvent(
                id=self._build_event_id(state.next_seq),
                event=event,
                data=data,
            )
            state.next_seq += 1
            state.events.append(entry)
            if len(state.events) > self._maxsize:
                overflow = len(state.events) - self._maxsize
                del state.events[:overflow]
                state.base_seq += overflow
            state.condition.notify_all()

    async def publish_end(self, run_id: str) -> None:
        state = self._get_or_create_state(run_id)
        async with state.condition:
            state.ended = True
            state.condition.notify_all()

    async def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:
        state = self._get_or_create_state(run_id)
        next_seq = self._resolve_next_seq(state, last_event_id)

        while True:
            entry = await self._next_event(
                state,
                next_seq=next_seq,
                heartbeat_interval=heartbeat_interval,
            )
            if entry is HEARTBEAT_SENTINEL:
                yield HEARTBEAT_SENTINEL
                continue
            if entry is END_SENTINEL:
                yield END_SENTINEL
                return

            yield entry
            next_seq += 1

    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        self._states.pop(run_id, None)

    async def close(self) -> None:
        self._states.clear()

    def _resolve_next_seq(self, state: _RunState, last_event_id: str | None) -> int:
        if last_event_id is None:
            return state.base_seq

        for local_index, entry in enumerate(state.events):
            if entry.id == last_event_id:
                return state.base_seq + local_index + 1

        if state.events:
            logger.info(
                "Last-Event-ID %s not available in memory bridge history; replaying from earliest retained event %s",
                last_event_id,
                state.events[0].id,
            )
            return state.base_seq
        return 0

    async def _next_event(
        self,
        state: _RunState,
        *,
        next_seq: int,
        heartbeat_interval: float,
    ) -> StreamEvent:
        while True:
            async with state.condition:
                local_index = max(0, next_seq - state.base_seq)
                if local_index < len(state.events):
                    return state.events[local_index]
                if state.ended:
                    return END_SENTINEL
                try:
                    await asyncio.wait_for(
                        state.condition.wait(),
                        timeout=heartbeat_interval,
                    )
                except TimeoutError:
                    return HEARTBEAT_SENTINEL

