"""Shelly Gen2+ local RPC over HTTP (Shelly Gen2 API documentation, "RPC Protocol" and "Switch" component).

* ``GET /shelly`` — device identification (id, mac, model, gen, app, ver, auth_en) — no auth required;
* ``POST /rpc`` with ``{"id", "method", "params"}`` — e.g. ``Switch.GetStatus {id}``,
  ``Switch.Set {id, on, toggle_after}``, ``Shelly.GetStatus``;
* authentication (if enabled on the device): HTTP digest, user ``admin``.

``toggle_after`` makes the relay flip back by itself after the given seconds: the device-side watchdog
used so a command never stays active when the EMS stops.
"""

from __future__ import annotations

import itertools
from typing import Any

import httpx

from ems.devices.base import DeviceUnavailableError, DriverError

_ids = itertools.count(1)


class ShellyClient:
    def __init__(self, host: str, password: str | None = None, timeout: float = 4.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not host:
            raise DriverError("geen IP-adres/hostnaam van de Shelly ingesteld")
        self.base = host if host.startswith("http") else f"http://{host}"
        self.auth = httpx.DigestAuth("admin", password) if password else None
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base, timeout=self.timeout, auth=self.auth, follow_redirects=False,
                                 transport=self.transport)

    async def info(self) -> dict[str, Any]:
        try:
            async with self._client() as c:
                r = await c.get("/shelly")
        except httpx.HTTPError as exc:
            raise DeviceUnavailableError(f"Shelly {self.base} niet bereikbaar ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise DriverError(f"Shelly: HTTP {r.status_code}")
        data = r.json()
        if int(data.get("gen") or 0) < 2:
            raise DriverError("geen Shelly Gen2+ apparaat (Gen1 wordt niet ondersteund)")
        return data

    async def rpc(self, method: str, params: dict | None = None) -> Any:
        body = {"id": next(_ids), "method": method, "params": params or {}}
        try:
            async with self._client() as c:
                r = await c.post("/rpc", json=body)
        except httpx.HTTPError as exc:
            raise DeviceUnavailableError(f"Shelly {self.base} niet bereikbaar ({type(exc).__name__})") from exc
        if r.status_code == 401:
            raise DriverError("Shelly: wachtwoord vereist of onjuist")
        if r.status_code != 200:
            raise DriverError(f"Shelly {method}: HTTP {r.status_code}")
        data = r.json()
        if "error" in data:
            raise DriverError(f"Shelly {method}: {data['error'].get('message', data['error'])}")
        return data.get("result")

    async def switch_status(self, channel: int) -> dict[str, Any]:
        return await self.rpc("Switch.GetStatus", {"id": int(channel)})

    async def switch_set(self, channel: int, on: bool, toggle_after: float | None = None) -> None:
        params: dict[str, Any] = {"id": int(channel), "on": bool(on)}
        if toggle_after:
            params["toggle_after"] = float(toggle_after)
        await self.rpc("Switch.Set", params)

    async def channels(self) -> list[int]:
        status = await self.rpc("Shelly.GetStatus")
        return sorted(int(k.split(":")[1]) for k in (status or {}) if k.startswith("switch:"))
