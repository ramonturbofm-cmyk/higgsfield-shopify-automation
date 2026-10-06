"""Async client for the HomeWizard local API (v1 HTTP, v2 HTTPS/WebSocket)."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from ems.integrations.homewizard.protocol import (
    CA_CERT,
    USER_NAME,
    V2_HEADERS,
    cert_hostname,
    chain_only_context,
    ssl_context,
)

log = logging.getLogger(__name__)


class HomeWizardError(Exception):
    pass


class ButtonPressRequired(HomeWizardError):
    """POST /api/user answered 403 user:creation-not-enabled — press the button on the meter."""


class Unauthorized(HomeWizardError):
    """401 user:unauthorized — token invalid or removed."""


def _error_code(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error", ""))
    except (ValueError, AttributeError):
        return ""


class HomeWizardV2Client:
    def __init__(self, host: str, serial: str | None, token: str | None = None, *, product_type: str = "HWE-P1",
                 verify: bool = True, cafile: Path | str = CA_CERT, port: int = 443,
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 5.0) -> None:
        self.host, self.port = host, port
        self.serial = serial
        self.token = token
        self.product_type = product_type
        self.verify = verify
        self.cafile = cafile
        self.hostname = cert_hostname(serial, product_type) if serial else None
        if verify and not self.hostname and transport is None:
            raise HomeWizardError("serienummer nodig voor certificaatcontrole (gebruik ontdekken of identiteit opvragen)")
        self.ctx = ssl_context(verify, cafile)
        self._client = httpx.AsyncClient(base_url=f"https://{host}:{port}", verify=self.ctx, timeout=timeout,
                                         transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    def _ext(self) -> dict:
        return {"sni_hostname": self.hostname} if (self.verify and self.hostname) else {}

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        headers = dict(V2_HEADERS)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = await self._client.request(method, path, headers=headers, extensions=self._ext(), **kw)
        except httpx.HTTPError as exc:
            raise HomeWizardError(f"verbinding met {self.host} mislukt: {exc}") from exc
        if resp.status_code == 401:
            raise Unauthorized("token ongeldig of verwijderd (opnieuw koppelen)")
        return resp

    async def create_user(self, name: str = USER_NAME) -> str:
        """One pairing attempt. Raises ButtonPressRequired until the button was pressed."""
        resp = await self._request("POST", "/api/user", json={"name": name})
        if resp.status_code == 403 and _error_code(resp) == "user:creation-not-enabled":
            raise ButtonPressRequired("druk op de knop van de P1 Meter")
        if resp.status_code != 200:
            raise HomeWizardError(f"koppelen mislukt: HTTP {resp.status_code} {_error_code(resp)}")
        token = resp.json().get("token", "")
        if not token:
            raise HomeWizardError("geen token ontvangen")
        self.token = token
        return token

    async def delete_user(self, name: str = USER_NAME) -> None:
        resp = await self._request("DELETE", "/api/user", json={"name": name})
        if resp.status_code not in (200, 204):
            raise HomeWizardError(f"gebruiker verwijderen mislukt: HTTP {resp.status_code}")

    async def device_info(self) -> dict:
        resp = await self._request("GET", "/api")
        if resp.status_code != 200:
            raise HomeWizardError(f"/api: HTTP {resp.status_code} {_error_code(resp)}")
        return resp.json()

    async def measurement(self) -> dict:
        resp = await self._request("GET", "/api/measurement")
        if resp.status_code != 200:
            raise HomeWizardError(f"/api/measurement: HTTP {resp.status_code} {_error_code(resp)}")
        return resp.json()

    async def system(self) -> dict:
        resp = await self._request("GET", "/api/system")
        if resp.status_code != 200:
            raise HomeWizardError(f"/api/system: HTTP {resp.status_code}")
        return resp.json()

    async def identify(self) -> None:
        resp = await self._request("PUT", "/api/system/identify")
        if resp.status_code not in (200, 204):
            raise HomeWizardError(f"identify: HTTP {resp.status_code}")

    async def stream(self, topic: str = "measurement") -> AsyncIterator[dict]:
        """WebSocket /api/ws: authorize within 40 s, subscribe, yield topic data."""
        import websockets

        if not self.token:
            raise Unauthorized("geen token")
        uri = f"wss://{self.host}:{self.port}/api/ws"
        kwargs = {"ssl": self.ctx, "open_timeout": 10, "ping_interval": 20}
        if self.verify and self.hostname:
            kwargs["server_hostname"] = self.hostname
        async with websockets.connect(uri, **kwargs) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if msg.get("type") != "authorization_requested":
                raise HomeWizardError(f"onverwacht websocketbericht: {msg.get('type')}")
            await ws.send(json.dumps({"type": "authorization", "data": self.token}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if msg.get("type") != "authorized":
                raise Unauthorized(f"websocket-autorisatie geweigerd: {msg}")
            await ws.send(json.dumps({"type": "subscribe", "data": topic}))
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == topic:
                    yield msg.get("data") or {}
                elif msg.get("type") == "error":
                    log.warning("homewizard websocket error", extra={"error": msg.get("data")})


class HomeWizardV1Client:
    def __init__(self, host: str, *, port: int = 80, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 5.0) -> None:
        self.host = host
        self._client = httpx.AsyncClient(base_url=f"http://{host}:{port}", timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> dict:
        try:
            resp = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise HomeWizardError(f"verbinding met {self.host} mislukt: {exc}") from exc
        if resp.status_code == 403:
            raise HomeWizardError("lokale API staat uit: zet 'Local API' aan in de HomeWizard-app")
        if resp.status_code != 200:
            raise HomeWizardError(f"{path}: HTTP {resp.status_code}")
        return resp.json()

    async def device_info(self) -> dict:
        return await self._get("/api")

    async def data(self) -> dict:
        return await self._get("/api/v1/data")

    async def identify(self) -> None:
        try:
            resp = await self._client.put("/api/v1/identify")
        except httpx.HTTPError as exc:
            raise HomeWizardError(str(exc)) from exc
        if resp.status_code != 200:
            raise HomeWizardError(f"identify: HTTP {resp.status_code}")


async def probe_identity(host: str, port: int = 443, cafile: Path | str = CA_CERT, timeout: float = 5.0) -> dict:
    """Learn a device's certificate identity from its IP address.

    The chain must verify against the HomeWizard CA; the subject CN then gives the
    hostname (appliance/<type>/<serial>) used for strict validation afterwards."""
    ctx = chain_only_context(cafile)
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx), timeout)
    except ssl.SSLCertVerificationError as exc:
        raise HomeWizardError(f"certificaat niet uitgegeven door HomeWizard: {exc.verify_message}") from exc
    except (OSError, TimeoutError) as exc:
        raise HomeWizardError(f"{host}:{port} niet bereikbaar: {exc}") from exc
    try:
        cert = writer.get_extra_info("peercert") or {}
    finally:
        writer.close()
    cn = None
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                cn = value
    if not cn or not cn.startswith("appliance/"):
        raise HomeWizardError(f"onverwachte certificaatnaam: {cn!r}")
    _, cert_type, serial = cn.split("/", 2)
    return {"hostname": cn, "cert_product_type": cert_type, "serial": serial}
