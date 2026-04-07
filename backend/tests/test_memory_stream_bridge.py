from __future__ import annotations

import asyncio

from deerflow.runtime.stream_bridge import END_SENTINEL
from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge


async def _collect_events(bridge: MemoryStreamBridge, run_id: str, *, last_event_id: str | None = None):
    events = []
    async for entry in bridge.subscribe(run_id, last_event_id=last_event_id, heartbeat_interval=0.01):
        if entry is END_SENTINEL:
            break
        events.append(entry)
    return events


def test_memory_stream_bridge_allows_multiple_subscribers() -> None:
    async def scenario():
        bridge = MemoryStreamBridge(queue_maxsize=10)
        await bridge.publish("run-1", "metadata", {"run_id": "run-1"})
        await bridge.publish("run-1", "values", {"step": 1})
        await bridge.publish_end("run-1")

        first, second = await asyncio.gather(
            _collect_events(bridge, "run-1"),
            _collect_events(bridge, "run-1"),
        )

        assert [event.event for event in first] == ["metadata", "values"]
        assert [event.event for event in second] == ["metadata", "values"]
        assert [event.id for event in first] == [event.id for event in second]

    asyncio.run(scenario())


def test_memory_stream_bridge_replays_after_last_event_id() -> None:
    async def scenario():
        bridge = MemoryStreamBridge(queue_maxsize=10)
        await bridge.publish("run-2", "metadata", {"run_id": "run-2"})
        await bridge.publish("run-2", "updates", {"step": 1})
        await bridge.publish("run-2", "values", {"step": 2})
        await bridge.publish_end("run-2")

        initial = await _collect_events(bridge, "run-2")
        replayed = await _collect_events(
            bridge,
            "run-2",
            last_event_id=initial[0].id,
        )

        assert [event.event for event in replayed] == ["updates", "values"]
        assert replayed[0].id == initial[1].id

    asyncio.run(scenario())

