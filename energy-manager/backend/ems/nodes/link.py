"""NodeLink: the controller's connection to one paired device-gateway node.

Heartbeat (every few seconds) = node info + lease renewal. Commands carry the lease
holder id and epoch plus an issue time; the gateway refuses anything else. Outages are
remembered as gaps so the history buffered on the gateway can be synced afterwards.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx

from ems.core.models import Command

log = logging.getLogger(__name__)
COMMAND_TTL_S = 30.0
MAX_CLOCK_SKEW_S = 10.0     # commands carry a timestamp: refuse to steer when clocks disagree more than this


class NodeLinkError(Exception):
    pass


class NodeLink:
    def __init__(self, node_id: str, name: str, address: str, token: str, own_node_id: str,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.node_id, self.name, self.address = node_id, name, address.rstrip("/")
        self.own_node_id = own_node_id
        self._client = httpx.AsyncClient(base_url=self.address, timeout=4.0, transport=transport,
                                         headers={"X-EMS-Node-Token": token, "X-EMS-Node-Id": own_node_id})
        self.online = False
        self.last_seen: float | None = None
        self.error: str | None = None
        self.info: dict = {}
        self.devices: dict[str, dict] = {}
        self.lease_ok = False
        self.lease_error: str | None = None
        self.lease: dict = {}
        self.epoch: int | None = None
        self.latency_ms: float | None = None
        self.down_since: float | None = None
        self.gaps: list[tuple[float, float]] = []

    async def close(self) -> None:
        await self._client.aclose()

    async def _req(self, method: str, path: str, **kw) -> Any:
        try:
            t0 = time.perf_counter()
            r = await self._client.request(method, f"/api/v1/node{path}", **kw)
            self.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        except httpx.HTTPError as exc:
            self._mark_down(f"{type(exc).__name__}: {exc}")
            raise NodeLinkError(f"node {self.name} onbereikbaar") from exc
        if r.status_code == 401:
            self._mark_down("koppeling ingetrokken of ongeldig token")
            raise NodeLinkError(f"node {self.name}: niet (meer) gekoppeld")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            raise NodeLinkError(str(detail or f"HTTP {r.status_code}"))
        self._mark_up()
        return r.json()

    def _mark_down(self, error: str) -> None:
        if self.online or self.down_since is None:
            self.down_since = self.down_since or time.time()
        self.online, self.error, self.lease_ok = False, error, False

    def _mark_up(self) -> None:
        now = time.time()
        if self.down_since is not None and self.last_seen is not None:
            self.gaps.append((self.down_since, now))
        self.down_since = None
        self.online, self.last_seen, self.error = True, now, None

    # ------------------------------------------------------------ heartbeat
    async def heartbeat(self, want_lease: bool) -> None:
        t0 = time.time()
        self.info = await self._req("GET", "/info")
        t1 = time.time()
        if self.info.get("server_time") is not None:
            self.clock_offset_s = float(self.info["server_time"]) - (t0 + t1) / 2
        if want_lease:
            res = await self._req("POST", "/lease")
            self.lease = res
            self.lease_ok = bool(res.get("granted"))
            self.epoch = res.get("epoch") if self.lease_ok else None
            self.lease_error = None if self.lease_ok else \
                f"andere controller heeft het regelrecht ({res.get('holder_name') or res.get('holder')})"

    async def refresh_devices(self) -> dict[str, dict]:
        rows = await self._req("GET", "/devices")
        self.devices = {d["id"]: d for d in rows}
        return self.devices

    # -------------------------------------------------------------- devices
    async def state(self, remote_id: str) -> dict:
        return await self._req("GET", f"/devices/{remote_id}/state")

    async def command(self, remote_id: str, cmd: Command, reason: str = "") -> dict:
        if not self.lease_ok or self.epoch is None:
            raise NodeLinkError(self.lease_error or self.error or "geen regelrecht (lease) op deze node")
        offset = getattr(self, "clock_offset_s", 0.0) or 0.0
        if abs(offset) > MAX_CLOCK_SKEW_S:
            raise NodeLinkError(f"klokverschil met node {self.name} is {offset:+.0f} s — synchroniseer de tijd (NTP); "
                                "geen opdrachten tot dit is opgelost")
        res = await self._req("POST", f"/devices/{remote_id}/command", json={
            "command_id": uuid.uuid4().hex, "action": cmd.action.value, "value": cmd.value, "epoch": self.epoch,
            "issued_ts": time.time(),
            "ttl_s": COMMAND_TTL_S, "reason": reason})
        if res.get("outcome") not in ("sent", "refreshed", "skipped", "dry_run", "shadow"):
            raise NodeLinkError(f"{res.get('outcome')}: {res.get('error') or ''}".strip(": "))
        return res

    async def release(self, remote_id: str) -> dict:
        return await self._req("POST", f"/devices/{remote_id}/release")

    async def set_control_level(self, remote_id: str, level: str, fraction: float) -> dict:
        return await self._req("PUT", f"/devices/{remote_id}/control-level",
                               json={"control_level": level, "limited_fraction": fraction})

    async def history(self, device_ids: list[str], start: float, end: float) -> dict[str, list[dict]]:
        return await self._req("GET", "/history", params={"device_ids": ",".join(device_ids),
                                                          "start": start, "end": end}, timeout=30)

    def to_dict(self) -> dict:
        lease = {"granted": self.lease_ok, "epoch": self.epoch, "error": self.lease_error}
        lease.update({k: v for k, v in self.lease.items() if k in ("holder", "expires_in_s")})
        return {"node_id": self.node_id, "name": self.name, "address": self.address, "online": self.online,
                "clock_offset_s": None if getattr(self, "clock_offset_s", None) is None else round(self.clock_offset_s, 1),
                "last_seen": self.last_seen, "error": self.error, "latency_ms": self.latency_ms,
                "lease": lease, "info": self.info, "device_count": len(self.devices)}
