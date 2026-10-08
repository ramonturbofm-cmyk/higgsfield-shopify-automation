"""Generic MQTT read-only driver (subscribes only, never publishes).

Each mapping entry names a topic and optionally a dotted JSON path inside the
payload (plain numeric payloads need no path). Topics come from the device's or
gateway's own documentation. Values older than ``stale_after_s`` are dropped so
the EMS never acts on frozen data.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
import time
from typing import Any

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

log = logging.getLogger(__name__)


def _payload_value(entry: dict, payload: bytes) -> Any:
    text = payload.decode("utf-8", "replace").strip()
    path = entry.get("path")
    if path:
        try:
            return json_path(json.loads(text), path)
        except ValueError:
            return None
    return text


@register_driver
class GenericMQTTDriver(DeviceDriver):
    manifest = DriverManifest(
        driver_id="generic.mqtt",
        display_name="Generiek MQTT (alleen lezen)",
        vendor="Generiek",
        categories=READ_CATEGORIES,
        capabilities=frozenset(),   # derived per device from its validated mapping only
        connection_types=("mqtt",),
        documentation="Topics en payloadformaat uit de documentatie van het apparaat/de gateway",
        grid_meter_kind="mqtt",
        notes="Abonneert op de ingestelde topics; publiceert nooit.",
        connection_schema={
            "host": {"type": "string", "label_nl": "MQTT-broker (IP/hostnaam)"},
            "port": {"type": "integer", "default": 1883, "label_nl": "Poort"},
            "username": {"type": "string", "default": "", "label_nl": "Gebruikersnaam (optioneel)"},
            "password": {"type": "string", "default": "", "label_nl": "Wachtwoord (optioneel)"},
            "tls": {"type": "boolean", "default": False, "label_nl": "TLS gebruiken"},
            "stale_after_s": {"type": "integer", "default": 60, "label_nl": "Waarde verouderd na (s)"},
            "values": {**VALUES_SCHEMA, "example": [{"metric": "grid_power_w", "topic": "meter/power", "path": "value"}]},
        },
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        c = config.connection
        self.host = str(c.get("host", ""))
        self.port = int(c.get("port", 1883))
        self.stale_after = float(c.get("stale_after_s", 60))
        self._entries: list[dict] | None = None
        self._latest: dict[Metric, tuple[Any, float]] = {}
        self._task: asyncio.Task | None = None
        self._connected = asyncio.Event()
        self.diag: dict[str, Any] = {"messages": 0, "connected": False, "errors": 0}

    def entries(self) -> list[dict]:
        if self._entries is None:
            self._entries = parse_entries(self.config.connection.get("values"), ("topic",))
        return self._entries

    def capabilities(self) -> frozenset[Capability]:
        try:
            return capabilities_for(self.entries())
        except MappingError:
            return frozenset()

    def on_message(self, topic: str, payload: bytes) -> None:
        now = time.monotonic()
        for e in self.entries():
            if e["topic"] == topic:
                v = convert(e, _payload_value(e, payload))
                if v is not None:
                    self._latest[e["metric"]] = (v, now)
        self.diag["messages"] += 1

    async def _run(self) -> None:
        import aiomqtt  # optional dependency, imported lazily

        c = self.config.connection
        tls = ssl.create_default_context() if c.get("tls") else None
        backoff = 2.0
        while True:
            try:
                async with aiomqtt.Client(self.host, self.port, username=c.get("username") or None,
                                          password=resolve_secret(c.get("password"), self.context.secrets),
                                          tls_context=tls, timeout=10) as client:
                    for topic in sorted({e["topic"] for e in self.entries()}):
                        await client.subscribe(topic)
                    self._connected.set()
                    self.diag["connected"] = True
                    backoff = 2.0
                    async for msg in client.messages:
                        self.on_message(str(msg.topic), bytes(msg.payload or b""))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.diag.update(connected=False, last_error=str(exc))
                self.diag["errors"] += 1
                self._connected.clear()
                log.warning("mqtt connection lost", extra={"device_id": self.device_id, "error": str(exc)})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def connect(self) -> None:
        if not self.host:
            raise DriverError("geen MQTT-broker ingesteld")
        self.entries()
        try:
            import aiomqtt  # noqa: F401
        except ImportError as exc:
            raise DriverError("MQTT-ondersteuning ontbreekt (pip install aiomqtt)") from exc
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"mqtt-{self.device_id}")
        try:
            await asyncio.wait_for(self._connected.wait(), 10)
        except TimeoutError as exc:
            raise DeviceUnavailableError(f"MQTT-broker {self.host}:{self.port} niet bereikbaar: "
                                         f"{self.diag.get('last_error', 'time-out')}") from exc

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._connected.clear()
        self.diag["connected"] = False

    async def read(self) -> dict[Metric, Any]:
        now = time.monotonic()
        values = {m: v for m, (v, at) in self._latest.items() if now - at <= self.stale_after}
        if not values:
            raise DeviceUnavailableError("nog geen (recente) MQTT-berichten ontvangen")
        return finish(values)

    async def self_test(self):
        """Give retained/periodic messages time to arrive before checking the values."""
        with contextlib.suppress(Exception):
            await self.connect()
            for _ in range(30):
                if self._latest:
                    break
                await asyncio.sleep(0.5)
        return await super().self_test()

    async def apply(self, command: Command) -> None:
        raise UnsupportedCommandError("generieke MQTT-driver is alleen-lezen")

    async def release_control(self) -> None:
        return None

    def diagnostics(self) -> dict[str, Any]:
        return dict(self.diag)
