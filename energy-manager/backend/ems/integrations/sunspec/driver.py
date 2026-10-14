"""SunSpec driver: scans the model chain and maps standard models to EMS metrics.

* 1 common: manufacturer, model, version, serial (identity for discovery);
* 101/102/103 (int + scale factor) and 111/112/113 (float) inverters: PV power, energy, status;
* 120 nameplate: rated power (needed to express a W limit as a percentage);
* 123 immediate controls: active-power limit (``WMaxLimPct``) with a device-side revert time
  (``WMaxLimPct_RvrtTms``) and enable (``WMaxLim_Ena``) — only offered when the device implements them;
* 201–204 meters: grid power, per phase, energy counters;
* 124 storage / 802 battery: state of charge (read only in this version).

Write documentation: the SunSpec Information Model Specification (models 120/123). Whether a device
really honours model 123 differs per manufacturer and firmware: the capability exists only when the
model is present, writes are read back, and full control requires a hardware-proven profile.
"""

from __future__ import annotations

import time
from typing import Any

from ems.core.models import Capability, Command, CommandAction, DeviceCategory, Metric
from ems.devices.base import DeviceDriver, DeviceUnavailableError, DriverError, DriverManifest, UnsupportedCommandError
from ems.devices.registry import register_driver
from ems.integrations.modbus.codec import encode
from ems.integrations.modbus.profile_driver import LibraryDriverMixin, load_profile
from ems.integrations.modbus.transport import ModbusError, ModbusTransport
from ems.integrations.sunspec.definitions import BASES, SUNS, model, value

INVERTERS = (101, 102, 103, 111, 112, 113)
METERS = (201, 202, 203, 204)
DOC = "SunSpec Information Model Specification; official model definitions github.com/sunspec/models (Apache-2.0)"


async def scan(transport: ModbusTransport, unit: int, bases=BASES) -> dict[str, Any]:
    """Find the SunSpec base address and the model chain. Raises DriverError when not SunSpec."""
    for base in bases:
        try:
            regs = await transport.read("holding", base, 2, unit)
        except ModbusError:
            continue
        if regs != SUNS:
            continue
        models, addr = [], base + 2
        for _ in range(64):
            hdr = await transport.read("holding", addr, 2, unit)
            mid, length = hdr
            if mid == 0xFFFF or length == 0:
                break
            models.append({"id": mid, "address": addr, "length": length})
            addr += 2 + length
        return {"base": base, "models": models}
    raise DriverError("geen SunSpec-apparaat (markering 'SunS' niet gevonden op 40000, 0 of 50000)")


async def read_block(transport: ModbusTransport, unit: int, m: dict) -> list[int]:
    total, out, addr = m["length"] + 2, [], m["address"]
    while len(out) < total:
        n = min(100, total - len(out))
        out += await transport.read("holding", addr + len(out), n, unit)
    return out


async def identify(transport: ModbusTransport, unit: int) -> dict[str, Any]:
    info = await scan(transport, unit)
    ident: dict[str, Any] = {"sunspec_models": [m["id"] for m in info["models"]], "base": info["base"]}
    common = next((m for m in info["models"] if m["id"] == 1), None)
    if common:
        block = await read_block(transport, unit, common)
        d = model(1)
        ident.update({"manufacturer": value(block, d, "Mn"), "model": value(block, d, "Md"),
                      "firmware_version": value(block, d, "Vr"), "serial_number": value(block, d, "SN")})
    return ident


@register_driver
class SunSpecDriver(LibraryDriverMixin, DeviceDriver):
    manifest = DriverManifest(
        driver_id="library.sunspec",
        display_name="SunSpec (omvormers, meters, opslag)",
        vendor="SunSpec Alliance-standaard",
        categories=(DeviceCategory.PV_INVERTER, DeviceCategory.HYBRID_INVERTER, DeviceCategory.SMART_METER,
                    DeviceCategory.ENERGY_METER, DeviceCategory.BATTERY),
        capabilities=frozenset(),
        connection_types=("modbus_tcp", "modbus_rtu"),
        documentation=DOC,
        write_capable=True,
        grid_meter_kind="modbus",
        notes="Herkent zelf welke SunSpec-modellen het apparaat aanbiedt.",
        connection_schema={
            "profile_id": {"type": "string", "label_nl": "Apparaatprofiel", "readonly": True},
            "transport": {"type": "string", "enum": ["tcp", "rtu"], "default": "tcp", "label_nl": "Verbinding",
                          "level": "advanced"},
            "host": {"type": "string", "label_nl": "IP-adres / hostnaam", "show_if": ["transport", "tcp"]},
            "port": {"type": "integer", "default": 502, "label_nl": "Poort", "level": "advanced",
                     "show_if": ["transport", "tcp"]},
            "serial_port": {"type": "string", "label_nl": "Seriële poort", "show_if": ["transport", "rtu"]},
            "baudrate": {"type": "integer", "default": 9600, "label_nl": "Baudrate", "level": "expert",
                         "show_if": ["transport", "rtu"]},
            "unit_id": {"type": "integer", "default": 1, "label_nl": "Unit-ID", "level": "advanced"},
            "meter_invert": {"type": "boolean", "default": False, "label_nl": "Teken netvermogen omdraaien",
                             "level": "expert"},
            "revert_s": {"type": "integer", "default": 900, "label_nl": "Begrenzing vervalt in het apparaat na (s)",
                         "level": "expert"},
        },
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        self.profile, self.profile_error = load_profile(config) if config.connection.get("profile_id") else (None, None)
        c = dict(self.profile.connection_parameters if self.profile else {}) | dict(config.connection)
        self.unit = int(c.get("unit_id", 1))
        self.meter_invert = bool(c.get("meter_invert", False))
        self.revert_s = int(c.get("revert_s", 900))
        self.transport = ModbusTransport.from_connection(c, int(c.get("port", 502)))
        self.chain: list[dict] = []
        self.identity: dict[str, Any] = {}
        self.diag: dict[str, Any] = {"reads": 0, "errors": 0, "points": {}}
        self._rated_w: float | None = None

    def _has(self, *ids: int) -> dict | None:
        return next((m for m in self.chain if m["id"] in ids), None)

    def capabilities(self) -> frozenset[Capability]:
        if not self.chain:
            return frozenset(self.profile.read_capabilities) if self.profile else frozenset()
        caps: set[Capability] = set()
        if self._has(*INVERTERS):
            caps.add(Capability.READ_PV_POWER)
            ctl = self._has(123)
            if ctl and model(123) and (self._has(120) or self.config.params.get("max_power_w")):
                caps.add(Capability.CONTROL_PV_LIMIT)
        if self._has(*METERS):
            caps |= {Capability.READ_GRID_POWER, Capability.READ_GRID_PHASES, Capability.READ_GRID_ENERGY}
        if self._has(124, 802):
            caps.add(Capability.READ_BATTERY_SOC)
        return frozenset(caps)

    def write_documented(self) -> bool:
        return Capability.CONTROL_PV_LIMIT in self.capabilities()

    def documentation_ref(self) -> str | None:
        return DOC

    async def connect(self) -> None:
        await self.transport.connect()
        if not self.chain:
            info = await scan(self.transport, self.unit)
            self.chain = info["models"]
            self.diag["base"] = info["base"]
            self.diag["models"] = [m["id"] for m in self.chain]

    async def disconnect(self) -> None:
        await self.transport.close()

    async def identify(self) -> dict[str, Any]:
        """Manufacturer / model / serial / firmware from the SunSpec common model (1)."""
        if not self.chain:
            await self.connect()
        if (m := self._has(1)) and not self.identity:
            b, d = await read_block(self.transport, self.unit, m), model(1)
            self.identity = {"manufacturer": value(b, d, "Mn"), "model": value(b, d, "Md"),
                             "firmware_version": value(b, d, "Vr"), "serial_number": value(b, d, "SN")}
        return {k: v for k, v in self.identity.items() if v}

    async def read(self) -> dict[Metric, Any]:
        if not self.chain:
            await self.connect()
        v: dict[Metric, Any] = {}
        diag: dict[str, Any] = {}
        try:
            if (m := self._has(1)) and not self.identity:
                b, d = await read_block(self.transport, self.unit, m), model(1)
                self.identity = {"manufacturer": value(b, d, "Mn"), "model": value(b, d, "Md"),
                                 "firmware_version": value(b, d, "Vr"), "serial_number": value(b, d, "SN")}
            if (m := self._has(*INVERTERS)):
                b, d = await read_block(self.transport, self.unit, m), model(m["id"])
                w = value(b, d, "W")
                if w is not None:
                    v[Metric.PV_POWER_W] = max(0.0, float(w))
                wh = value(b, d, "WH")
                if wh is not None:
                    v[Metric.PV_ENERGY_KWH] = float(wh) / 1000
                for n in ("St", "Hz", "DCW", "DCV", "TmpCab", "A", "PhVphA"):
                    x = value(b, d, n)
                    if x is not None:
                        diag[n] = x
            if (m := self._has(120)) and self._rated_w is None:
                b, d = await read_block(self.transport, self.unit, m), model(120)
                self._rated_w = value(b, d, "WRtg")
                diag["WRtg"] = self._rated_w
            if (m := self._has(123)):
                b, d = await read_block(self.transport, self.unit, m), model(123)
                pct, ena = value(b, d, "WMaxLimPct"), value(b, d, "WMaxLim_Ena")
                diag["WMaxLimPct"], diag["WMaxLim_Ena"] = pct, ena
                rated = self._rated()
                if pct is not None and rated and ena == "ENABLED":
                    v[Metric.PV_LIMIT_W] = rated * float(pct) / 100
            if (m := self._has(*METERS)):
                b, d = await read_block(self.transport, self.unit, m), model(m["id"])
                sign = -1.0 if self.meter_invert else 1.0
                if (w := value(b, d, "W")) is not None:
                    v[Metric.GRID_POWER_W] = sign * float(w)
                for n, ph in (("A", 1), ("B", 2), ("C", 3)):
                    for src, met in ((f"Wph{n}", f"grid_power_l{ph}_w"), (f"Aph{n}", f"grid_current_l{ph}_a"),
                                     (f"PhVph{n}", f"grid_voltage_l{ph}_v")):
                        x = value(b, d, src)
                        if x is not None:
                            v[Metric(met)] = (sign * float(x)) if src.startswith(("W", "A")) else float(x)
                if (x := value(b, d, "TotWhImp")) is not None:
                    v[Metric.GRID_IMPORT_ENERGY_KWH] = float(x) / 1000
                if (x := value(b, d, "TotWhExp")) is not None:
                    v[Metric.GRID_EXPORT_ENERGY_KWH] = float(x) / 1000
            if (m := self._has(802)):
                b, d = await read_block(self.transport, self.unit, m), model(802)
                if (x := value(b, d, "SoC")) is not None:
                    v[Metric.BATTERY_SOC_PCT] = float(x)
            elif (m := self._has(124)):
                b, d = await read_block(self.transport, self.unit, m), model(124)
                if (x := value(b, d, "ChaState")) is not None:
                    v[Metric.BATTERY_SOC_PCT] = float(x)
        except ModbusError as exc:
            self.diag["errors"] += 1
            self.diag["last_error"] = str(exc)
            if not v:
                raise DeviceUnavailableError(str(exc)) from exc
        if not v and not diag:
            raise DeviceUnavailableError("geen SunSpec-waarden gelezen")
        self.diag["points"] = diag
        self.diag["reads"] += 1
        self.diag["last_read"] = time.time()
        return v

    def _rated(self) -> float | None:
        return self._rated_w or (float(self.config.params["max_power_w"]) if self.config.params.get("max_power_w") else None)

    async def _write_point(self, block_model: dict, name: str, raw_value: int) -> None:
        d = model(block_model["id"])
        p = d[name]
        t = {"enum16": "uint16"}.get(p.type, p.type)
        await self.transport.write_registers(block_model["address"] + p.offset, encode(raw_value, t), self.unit)

    async def apply(self, command: Command) -> None:
        if command.action != CommandAction.PV_LIMIT or Capability.CONTROL_PV_LIMIT not in self.capabilities():
            raise UnsupportedCommandError(f"{command.action.value} wordt door dit SunSpec-apparaat niet ondersteund")
        m = self._has(123)
        if self._rated_w is None and (m120 := self._has(120)):   # nameplate (model 120) not read yet
            b, d = await read_block(self.transport, self.unit, m120), model(120)
            self._rated_w = value(b, d, "WRtg")
        rated = self._rated()
        if not rated:
            raise UnsupportedCommandError("nominaal vermogen onbekend (model 120 of instelling max_power_w)")
        b, d = await read_block(self.transport, self.unit, m), model(123)
        sf_point = d["WMaxLimPct"].sf
        sf = value(b, d, sf_point) if isinstance(sf_point, str) else 0
        sf = int(sf or 0)
        if command.value is None:
            await self._write_point(m, "WMaxLim_Ena", 0)          # DISABLED: no limit
            return
        limit_w = max(0.0, min(float(command.value), rated))
        pct_raw = int(round(limit_w / rated * 100 / (10 ** sf)))
        await self._write_point(m, "WMaxLimPct_RvrtTms", self.revert_s)   # device reverts on its own
        await self._write_point(m, "WMaxLimPct", pct_raw)
        await self._write_point(m, "WMaxLim_Ena", 1)
        b2 = await read_block(self.transport, self.unit, m)
        if b2[d["WMaxLimPct"].offset] != pct_raw or value(b2, d, "WMaxLim_Ena") != "ENABLED":
            raise DriverError("teruglezen van de begrenzing komt niet overeen")

    async def release_control(self) -> None:
        if self.chain and (m := self._has(123)) and "WMaxLim_Ena" in model(123):
            await self._write_point(m, "WMaxLim_Ena", 0)

    def diagnostics(self) -> dict[str, Any]:
        return {**self.diag, "identity": self.identity, "transport": self.transport.stats, "target": self.transport.target}
