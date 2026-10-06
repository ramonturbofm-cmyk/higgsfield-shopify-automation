"""HomeWizardP1Driver — a GridMeter source.

v2 (preferred): HTTPS with the HomeWizard CA + device hostname, Bearer token from the
encrypted secret store, realtime data via the WebSocket ``measurement`` topic; if the
WebSocket is down the driver polls /api/measurement (>= 500 ms apart, per the docs).
v1: HTTP polling of /api/v1/data (requires 'Local API' enabled in the HomeWizard app).
Read-only: this driver never writes to the meter except the explicit 'identify' blink.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from ems.core.models import Capability, Command, DeviceCategory, Metric
from ems.devices.base import (
    DeviceDriver,
    DeviceUnavailableError,
    DriverManifest,
    TestCheck,
    TestReport,
    UnsupportedCommandError,
)
from ems.devices.registry import register_driver
from ems.integrations.homewizard.client import (
    HomeWizardError,
    HomeWizardV1Client,
    HomeWizardV2Client,
    Unauthorized,
)
from ems.integrations.homewizard.protocol import P1_PRODUCT_TYPE, map_data_v1, map_measurement_v2, meter_info

log = logging.getLogger(__name__)

WS_FRESH_S = 5.0          # use websocket data younger than this
NO_NEW_DATA_S = 90.0      # meter delivered nothing new for this long -> unavailable
MIN_POLL_INTERVAL_S = 0.5


@register_driver
class HomeWizardP1Driver(DeviceDriver):
    manifest = DriverManifest(
        driver_id="homewizard.p1",
        display_name="HomeWizard P1 Meter",
        vendor="HomeWizard",
        categories=(DeviceCategory.SMART_METER,),
        capabilities=frozenset({Capability.READ_GRID_POWER, Capability.READ_GRID_PHASES,
                                Capability.READ_GRID_ENERGY}),
        connection_types=("homewizard_v2", "homewizard_v1"),
        models=("HWE-P1",),
        simulated=False,
        verified=False,  # implemented from documentation; not yet tested against hardware
        documentation="HomeWizard Local API documentation (api-documentation.homewizard.com; "
                      "github.com/homewizard/api-documentation @724362f, 2026-06-09): v1 /api/v1/data, "
                      "v2 /api, /api/measurement, /api/user, /api/ws, /api/system/identify",
        grid_meter_kind="homewizard_p1",
        connection_schema={
            "host": {"type": "string", "label_nl": "IP-adres", "required": True},
            "api": {"type": "string", "enum": ["v2", "v1"], "default": "v2", "label_nl": "API-versie"},
            "serial": {"type": "string", "label_nl": "Serienummer (via ontdekken/koppelen)"},
            "token_ref": {"type": "string", "label_nl": "Token (versleuteld opgeslagen)", "readonly": True},
            "tls_verify": {"type": "boolean", "default": True, "label_nl": "Certificaat controleren"},
        },
        notes="Gebruik volgens HomeWizard-licentie: persoonlijk, niet-commercieel.",
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        c = config.connection
        self.host = c.get("host", "")
        self.api = c.get("api", "v2")
        self.serial = c.get("serial")
        self.token_ref = c.get("token_ref") or (f"homewizard:{self.serial}" if self.serial else None)
        self.tls_verify = bool(c.get("tls_verify", True))
        self._v2: HomeWizardV2Client | None = None
        self._v1: HomeWizardV1Client | None = None
        self._ws_task: asyncio.Task | None = None
        self._ws_data: dict | None = None
        self._ws_at = 0.0
        self._last_poll = 0.0
        self._last_raw: dict | None = None
        self._last_change = time.monotonic()
        self.info: dict = {}
        self.diag: dict[str, Any] = {"transport": None, "ws_connected": False, "ws_errors": 0, "polls": 0}

    # --------------------------------------------------------------- setup
    def _token(self) -> str | None:
        store = self.context.secrets
        return store.get(self.token_ref) if (store is not None and self.token_ref) else None

    async def connect(self) -> None:
        if not self.host:
            raise DeviceUnavailableError("geen IP-adres ingesteld")
        await self.disconnect()
        try:
            if self.api == "v1":
                self._v1 = HomeWizardV1Client(self.host)
                self.info = await self._v1.device_info()
            else:
                token = self._token()
                if not token:
                    raise DeviceUnavailableError("nog niet gekoppeld: druk op 'Koppelen' en daarna op de knop van de meter")
                self._v2 = HomeWizardV2Client(self.host, self.serial, token, verify=self.tls_verify)
                self.info = await self._v2.device_info()
                self._ws_task = asyncio.create_task(self._ws_loop())
        except Unauthorized as exc:
            raise DeviceUnavailableError(f"{exc}") from exc
        except HomeWizardError as exc:
            raise DeviceUnavailableError(str(exc)) from exc
        if self.info.get("product_type") not in (None, P1_PRODUCT_TYPE):
            raise DeviceUnavailableError(f"dit is geen P1 Meter maar {self.info.get('product_type')}")
        self.diag.update({"api": self.api, "firmware_version": self.info.get("firmware_version"),
                          "api_version": self.info.get("api_version"), "serial": self.info.get("serial")})

    async def disconnect(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._ws_task
            self._ws_task = None
        for c in (self._v1, self._v2):
            if c is not None:
                with contextlib.suppress(Exception):
                    await c.close()
        self._v1 = self._v2 = None

    async def _ws_loop(self) -> None:
        backoff = 2.0
        while True:
            try:
                async for data in self._v2.stream("measurement"):  # type: ignore[union-attr]
                    self._ws_data, self._ws_at = data, time.monotonic()
                    self.diag["ws_connected"] = True
                    backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.diag["ws_errors"] += 1
                self.diag["ws_last_error"] = str(exc)
            self.diag["ws_connected"] = False
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

    # ---------------------------------------------------------------- read
    async def _fetch(self) -> dict:
        if self.api == "v1":
            if self._v1 is None:
                raise DeviceUnavailableError("niet verbonden")
            self.diag["transport"] = "http-polling (v1)"
            return await self._v1.data()
        if self._v2 is None:
            raise DeviceUnavailableError("niet verbonden")
        if self._ws_data is not None and time.monotonic() - self._ws_at < WS_FRESH_S:
            self.diag["transport"] = "websocket (v2)"
            return self._ws_data
        wait = MIN_POLL_INTERVAL_S - (time.monotonic() - self._last_poll)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_poll = time.monotonic()
        self.diag["polls"] += 1
        self.diag["transport"] = "https-polling (v2, websocket niet beschikbaar)"
        return await self._v2.measurement()

    async def read(self) -> dict[Metric, Any]:
        try:
            raw = await self._fetch()
        except Unauthorized as exc:
            raise DeviceUnavailableError(str(exc)) from exc
        except HomeWizardError as exc:
            raise DeviceUnavailableError(str(exc)) from exc
        if raw != self._last_raw:
            self._last_raw, self._last_change = raw, time.monotonic()
        elif time.monotonic() - self._last_change > NO_NEW_DATA_S:
            raise DeviceUnavailableError("P1 Meter levert geen nieuwe meetwaarden (telegrammen)")
        self.diag["meter"] = meter_info(raw)
        self.diag["data_age_s"] = round(time.monotonic() - self._last_change, 2)
        values = map_data_v1(raw) if self.api == "v1" else map_measurement_v2(raw)
        if Metric.GRID_POWER_W not in values:
            raise DeviceUnavailableError("meting zonder netvermogen (geen telegram van de slimme meter?)")
        return values

    async def apply(self, command: Command) -> None:
        raise UnsupportedCommandError("de P1 Meter is alleen-lezen")

    async def release_control(self) -> None:
        return None

    async def identify(self) -> None:
        if self._v2 is not None:
            await self._v2.identify()
        elif self._v1 is not None:
            await self._v1.identify()
        else:
            raise DeviceUnavailableError("niet verbonden")

    def diagnostics(self) -> dict:
        return dict(self.diag)

    async def self_test(self) -> TestReport:
        report = await super().self_test()
        if report.reachable:
            report.checks.insert(1, TestCheck("api", f"lokale API {self.api} bereikbaar", True,
                                              f"firmware {self.info.get('firmware_version', '?')}"))
            phases = sum(1 for m in (Metric.GRID_POWER_L1_W, Metric.GRID_POWER_L2_W, Metric.GRID_POWER_L3_W)
                         if m.value in report.sample)
            report.checks.append(TestCheck("phases", f"{phases} fase(n) gemeten", phases > 0))
        return report
