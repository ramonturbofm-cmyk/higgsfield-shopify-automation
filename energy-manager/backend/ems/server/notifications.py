"""Notifications: stored in the database, pushed over the WebSocket and optionally
POSTed as JSON to a webhook. De-duplicated per (code, key) with a cooldown."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ems.core.events import EventBus
from ems.database import Database

log = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, db: Database, bus: EventBus, webhook_url: str = "") -> None:
        self.db = db
        self.bus = bus
        self.webhook_url = webhook_url
        self._recent: dict[tuple[str, str], float] = {}

    async def notify(self, level: str, code: str, message: str, data: dict | None = None,
                     key: str = "", cooldown_s: float = 3600) -> bool:
        now = time.monotonic()
        last = self._recent.get((code, key))
        if last is not None and now - last < cooldown_s:
            return False
        self._recent[(code, key)] = now
        nid = await asyncio.to_thread(self.db.insert_notification, level, code, message, data)
        payload = {"id": nid, "level": level, "code": code, "message": message, "data": data or {},
                   "ts": time.time()}
        await self.bus.publish("notification", payload)
        if self.webhook_url:
            asyncio.create_task(self._webhook(payload))
        return True

    def clear(self, code: str, key: str = "") -> None:
        """Allow a condition to be notified again after it recovered."""
        self._recent.pop((code, key), None)

    async def _webhook(self, payload: dict) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(self.webhook_url, json=payload)
        except Exception as exc:
            log.warning("webhook failed", extra={"error": str(exc)})
