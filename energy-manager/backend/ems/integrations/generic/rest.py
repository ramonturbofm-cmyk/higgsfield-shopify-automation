"""Generic HTTP/JSON read-only driver.

Polls one URL (GET) that returns JSON and maps values via dotted paths
(``"data.power"``, ``"phases.0.current"``). URL, paths and any required header
come from the device's own documentation; nothing is assumed here.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ems.core.models import Capability, Command, Metric
from ems.devices.base import DeviceDriver, DeviceUnavailableError, DriverError, DriverManifest, UnsupportedCommandError
from ems.devices.registry import register_driver
from ems.integrations.generic.mapping import (
    READ_CATEGORIES,
    VALUES_SCHEMA,
    MappingError,
    capabilities_for,
    convert,
    finish,
    json_path,
    parse_entries,
    resolve_secret,
)


@register_driver
class GenericHTTPJSONDriver(DeviceDriver):
    manifest = DriverManifest(
        driver_id="generic.http_json",
        display_name="Generiek HTTP/JSON (alleen lezen)",
        vendor="Generiek",
        categories=READ_CATEGORIES,
        capabilities=frozenset(c for c in Capability if c.value.startswith("read_")),
        connection_types=("rest",),
        documentation="URL en JSON-paden uit de documentatie van het apparaat",
        grid_meter_kind="rest",
        notes="Leest periodiek één JSON-document via HTTP GET. Er wordt nooit naar het apparaat geschreven.",
        connection_schema={
            "url": {"type": "string", "label_nl": "URL (http:// of https://)"},
            "auth_header": {"type": "string", "default": "Authorization", "label_nl": "Header voor authenticatie (optioneel)"},
            "auth_token": {"type": "string", "default": "", "label_nl": "Waarde/token voor die header (optioneel)"},
            "verify_tls": {"type": "boolean", "default": True, "label_nl": "HTTPS-certificaat controleren"},
            "timeout_s": {"type": "integer", "default": 5, "label_nl": "Time-out (s)"},
            "values": {**VALUES_SCHEMA, "example": [{"metric": "grid_power_w", "path": "data.active_power_w"}]},
        },
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        c = config.connection
        self.url = str(c.get("url", ""))
        self._client: httpx.AsyncClient | None = None
        self._entries: list[dict] | None = None
        self.diag: dict[str, Any] = {"reads": 0, "errors": 0}

    def entries(self) -> list[dict]:
        if self._entries is None:
            self._entries = parse_entries(self.config.connection.get("values"), ("path",))
        return self._entries

    def capabilities(self) -> frozenset[Capability]:
        try:
            return capabilities_for(self.entries())
        except MappingError:
            return frozenset()

    async def connect(self) -> None:
        if urlparse(self.url).scheme not in ("http", "https"):
            raise DriverError("ongeldige URL (gebruik http:// of https://)")
        self.entries()
        await self.disconnect()
        c = self.config.connection
        headers = {"Accept": "application/json"}
        token = resolve_secret(c.get("auth_token"), self.context.secrets)
        if token:
            headers[str(c.get("auth_header") or "Authorization")] = token
        self._client = httpx.AsyncClient(headers=headers, verify=bool(c.get("verify_tls", True)),
                                         timeout=float(c.get("timeout_s", 5)), follow_redirects=False)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def read(self) -> dict[Metric, Any]:
        if self._client is None:
            await self.connect()
        try:
            r = await self._client.get(self.url)
        except httpx.HTTPError as exc:
            self.diag["errors"] += 1
            raise DeviceUnavailableError(f"{self.url}: {exc.__class__.__name__}: {exc}") from exc
        if r.status_code != 200:
            self.diag["errors"] += 1
            raise DeviceUnavailableError(f"{self.url}: HTTP {r.status_code}")
        try:
            doc = r.json()
        except ValueError as exc:
            raise DeviceUnavailableError("antwoord is geen JSON") from exc
        values = {}
        for e in self.entries():
            v = convert(e, json_path(doc, e["path"]))
            if v is not None:
                values[e["metric"]] = v
        if not values:
            raise DeviceUnavailableError("geen van de ingestelde paden gaf een waarde")
        self.diag["reads"] += 1
        self.diag["last_read"] = time.time()
        return finish(values)

    async def apply(self, command: Command) -> None:
        raise UnsupportedCommandError("generieke HTTP-driver is alleen-lezen")

    async def release_control(self) -> None:
        return None
