"""Minimal in-process async pub/sub.

The engine publishes snapshots, decisions and alarms here; the API layer
(phase 2) forwards them to WebSocket clients and an optional MQTT bridge.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[[str, Any], Awaitable[None] | None]
log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to a topic ('*' = everything). Returns an unsubscribe fn."""
        self._handlers[topic].append(handler)
        return lambda: self._handlers[topic].remove(handler)

    async def publish(self, topic: str, payload: Any) -> None:
        for handler in [*self._handlers.get(topic, ()), *self._handlers.get("*", ())]:
            try:
                result = handler(topic, payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # a broken subscriber must never stop the EMS
                log.exception("event handler failed", extra={"topic": topic})
