"""DSMRP1Driver — reads the smart meter's P1 port directly (GridMeter source).

Transports: ``serial`` (USB P1 cable; DSMR 4/5: 115200 8N1, DSMR 2/3: 9600 7E1) or ``tcp``
(a serial-to-network bridge that forwards the raw telegrams, e.g. ser2net).
Read-only: nothing is ever written to the meter.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import Any

from ems.core.models import Capability, Command, DeviceCategory, Metric
from ems.devices.base import DeviceDriver, DeviceUnavailableError, DriverManifest, UnsupportedCommandError
from ems.devices.registry import register_driver
from ems.integrations.dsmr.parser import TelegramBuffer, TelegramError, parse_telegram, to_metrics

log = logging.getLogger(__name__)
STALE_AFTER_S = 30.0


@register_driver
class DSMRP1Driver(DeviceDriver):
    manifest = DriverManifest(
        driver_id="dsmr.p1",
        display_name="Slimme meter via P1-poort (DSMR)",
        vendor="DSMR (Netbeheer Nederland)",
        categories=(DeviceCategory.SMART_METER,),
        capabilities=frozenset({Capability.READ_GRID_POWER, Capability.READ_GRID_PHASES,
                                Capability.READ_GRID_ENERGY}),
        connection_types=("serial", "tcp"),
        models=("DSMR 4.x", "DSMR 5.x"),
        simulated=False,
        verified=False,
        documentation="Netbeheer Nederland, P1 Companion Standard DSMR 5.0.2",
        grid_meter_kind="dsmr",
        connection_schema={
            "transport": {"type": "string", "enum": ["serial", "tcp"], "default": "serial"},
            "port": {"type": "string", "default": "/dev/ttyUSB0", "label_nl": "Seriële poort"},
            "baudrate": {"type": "integer", "default": 115200},
            "dsmr_legacy": {"type": "boolean", "default": False, "label_nl": "DSMR 2.2/3.0 (9600 7E1)"},
            "host": {"type": "string", "label_nl": "Host (TCP)"},
            "tcp_port": {"type": "integer", "default": 2000, "label_nl": "Poort (TCP)"},
        },
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        self.c = config.connection
        self._latest: dict[Metric, Any] | None = None
        self._latest_at = 0.0
        self._task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.diag: dict[str, Any] = {"telegrams": 0, "crc_errors": 0, "transport": self.c.get("transport", "serial")}

    def _on_text(self, text: str, buf: TelegramBuffer) -> None:
        for raw in buf.feed(text):
            try:
                tg = parse_telegram(raw, require_crc=not self.c.get("dsmr_legacy", False))
            except TelegramError as exc:
                self.diag["crc_errors"] += 1
                self.diag["last_error"] = str(exc)
                continue
            metrics = to_metrics(tg)
            if Metric.GRID_POWER_W in metrics:
                self._latest, self._latest_at = metrics, time.monotonic()
                self.diag["telegrams"] += 1
                self.diag["header"] = tg.header
                self.diag["version"] = tg.values.get("version")
                self.diag["meter_time"] = None if tg.timestamp is None else tg.timestamp.isoformat()

    async def connect(self) -> None:
        await self.disconnect()
        self._stop.clear()
        if self.c.get("transport", "serial") == "tcp":
            host, port = self.c.get("host"), int(self.c.get("tcp_port", 2000))
            if not host:
                raise DeviceUnavailableError("geen host ingesteld")
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
            except (OSError, TimeoutError) as exc:
                raise DeviceUnavailableError(f"{host}:{port} niet bereikbaar: {exc}") from exc
            self._task = asyncio.create_task(self._tcp_loop(reader, writer))
        else:
            try:
                import serial  # pyserial
            except ImportError as exc:
                raise DeviceUnavailableError("pyserial niet geïnstalleerd") from exc
            legacy = self.c.get("dsmr_legacy", False)
            try:
                ser = serial.Serial(self.c.get("port", "/dev/ttyUSB0"),
                                    int(self.c.get("baudrate", 9600 if legacy else 115200)),
                                    bytesize=serial.SEVENBITS if legacy else serial.EIGHTBITS,
                                    parity=serial.PARITY_EVEN if legacy else serial.PARITY_NONE, timeout=1)
            except Exception as exc:
                raise DeviceUnavailableError(f"seriële poort: {exc}") from exc
            self._thread = threading.Thread(target=self._serial_loop, args=(ser,), daemon=True)
            self._thread.start()
        # Wait for the first telegram (meters send every 1 s, older ones every 10 s).
        deadline = time.monotonic() + 12
        while self._latest is None and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        if self._latest is None:
            await self.disconnect()
            raise DeviceUnavailableError("geen geldig telegram ontvangen (kabel, poort of baudrate?)")

    async def _tcp_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buf = TelegramBuffer()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    self.diag["last_error"] = "verbinding gesloten"
                    break
                self._on_text(chunk.decode("ascii", "replace"), buf)
        finally:
            writer.close()

    def _serial_loop(self, ser) -> None:
        buf = TelegramBuffer()
        try:
            while not self._stop.is_set():
                data = ser.read(1024)
                if data:
                    self._on_text(data.decode("ascii", "replace"), buf)
        except Exception as exc:
            self.diag["last_error"] = str(exc)
        finally:
            with contextlib.suppress(Exception):
                ser.close()

    async def disconnect(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._thread = None

    async def read(self) -> dict[Metric, Any]:
        if self._latest is None or time.monotonic() - self._latest_at > STALE_AFTER_S:
            raise DeviceUnavailableError("geen recente P1-telegrammen")
        self.diag["data_age_s"] = round(time.monotonic() - self._latest_at, 2)
        return dict(self._latest)

    async def apply(self, command: Command) -> None:
        raise UnsupportedCommandError("de slimme meter is alleen-lezen")

    async def release_control(self) -> None:
        return None

    def diagnostics(self) -> dict:
        return dict(self.diag)
