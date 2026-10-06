"""Software watchdog + systemd notify.

If the control loop stops producing heartbeats the watchdog releases all
devices (native behaviour) and stops petting systemd, so systemd/Docker
restarts the service. Combined with the Raspberry Pi hardware watchdog
this covers hangs of the process and of the whole OS.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from datetime import datetime

from ems.core.clock import Clock

log = logging.getLogger(__name__)


def sd_notify(message: str) -> bool:
    """Send a systemd notification (no-op when not run under systemd)."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode())
        return True
    except OSError:
        return False


class Watchdog:
    def __init__(self, heartbeat: Callable[[], datetime | None], clock: Clock, timeout_s: float,
                 on_timeout: Callable[[], Awaitable[object]]) -> None:
        self.heartbeat = heartbeat
        self.clock = clock
        self.timeout_s = timeout_s
        self.on_timeout = on_timeout
        self.tripped = False

    async def check(self) -> bool:
        """Returns True when healthy. Separated from run() for testability."""
        hb = self.heartbeat()
        age = None if hb is None else (self.clock.now() - hb).total_seconds()
        if age is not None and age <= self.timeout_s:
            self.tripped = False
            sd_notify("WATCHDOG=1")
            return True
        if not self.tripped:
            self.tripped = True
            log.critical("watchdog: control loop stalled", extra={"heartbeat_age_s": age})
            try:
                await self.on_timeout()
            except Exception:
                log.exception("watchdog release failed")
        return False

    async def run(self, stop: asyncio.Event) -> None:
        sd_notify("READY=1")
        while not stop.is_set():
            await self.check()
            await self.clock.sleep(max(1.0, self.timeout_s / 3))
