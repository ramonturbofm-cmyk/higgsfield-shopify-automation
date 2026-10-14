"""Testomgeving: virtual devices of the simulated house, reachable over the *real* protocols on 127.0.0.1.

The EMS talks to them exactly as to hardware — the same drivers, discovery, connection tests,
commissioning, command validation and optimizer — only the device on the other end is simulated:

=========  =====  =======================================  ====================================
port       proto  device                                   behaves like
=========  =====  =======================================  ====================================
15502      MB     SunSpec PV inverter 5 kW                 models 1, 103, 120, 123 (official defs)
15503      MB     heat pump (register map: lab profile)    mode / setpoint / measurements
15504      MB     home battery 10 kWh                      mode / power setpoint / max SOC
15505      MB     EV charger 11 kW (no car SOC)            current limit 0 / 6–16 A
15599      MB     unknown Modbus device                    answers, but has no known identity
15580      HTTP   P1 meter (HomeWizard local API v1)       /api, /api/v1/data
15581      HTTP   2-relay module (Shelly Gen2 RPC)         SG Ready contacts of the heat pump
15582      HTTP   1-relay module (Shelly Gen2 RPC)         electric boiler 2 kW
=========  =====  =======================================  ====================================

Every control has a device-side expiry (watchdog): when the EMS stops refreshing a command, the virtual
device returns to its own behaviour, like well-designed hardware. Faults can be injected (offline,
slow, Modbus exception) to test error handling. Simulated values are never shown outside the
Testomgeving: it only exists when the runtime runs the lab profile.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ems.integrations.modbus.codec import encode
from ems.integrations.sunspec.definitions import model as ss_model
from ems.simulator.components import SimBattery, SimEVCharger, SimHeatPump, SimPV, SimSwitchLoad
from ems.simulator.httpd import MiniHTTPServer

log = logging.getLogger(__name__)

PORTS = {"pv": 15502, "heat_pump": 15503, "battery": 15504, "ev": 15505, "unknown": 15599,
         "p1": 15580, "sgready": 15581, "boiler": 15582}
# Library profile of each virtual Modbus device (the wizard fills in the port the lab really listens on).
PROFILE_KEY = {"energymanager.lab.sunspec_inverter": "pv", "energymanager.lab.heat_pump": "heat_pump",
               "energymanager.lab.battery": "battery", "energymanager.lab.ev_charger": "ev"}


def port_of(name: str) -> int:
    """Fixed local port per virtual device; EMS_LAB_PORT_OFFSET shifts all of them (port conflicts, tests)."""
    return PORTS[name] + int(os.environ.get("EMS_LAB_PORT_OFFSET", "0") or 0)
SERIALS = {"heat_pump": "EMLAB-HP-0001", "battery": "EMLAB-BAT-0001", "ev": "EMLAB-EV-0001",
           "pv": "EMLAB-PV-0001", "p1": "emlab0000p1", "sgready": "emlab-relay2", "boiler": "emlab-relay1"}
MODELS = {"heat_pump": "EM-VWP-1", "battery": "EM-VBAT-10", "ev": "EM-VEVSE-11"}
FW = "1.0.0"


def lab_components() -> list:
    return [SimPV(id="lab_pv", peak_power_kw=5.0, azimuth_deg=190),
            SimBattery(id="lab_battery", capacity_kwh=10.0, max_charge_w=5000, max_discharge_w=5000, soc_pct=40.0),
            SimHeatPump(id="lab_hp", phase="L1"),
            SimEVCharger(id="lab_ev", arrive="17:00", depart="07:30", battery_kwh=60, arrival_soc_pct=30),
            SimSwitchLoad(id="lab_boiler", phase="L2")]


def _str_regs(text: str, regs: int) -> list[int]:
    raw = text.encode("ascii")[: regs * 2].ljust(regs * 2, b"\x00")
    return [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw), 2)]


@dataclass
class Fault:
    offline: bool = False
    exception: int | None = None     # Modbus exception code to answer with
    delay_s: float = 0.0


@dataclass
class LabDevices:
    site_fn: Any                                   # () -> current SimulatedSite
    faults: dict[str, Fault] = field(default_factory=dict)
    expiry: dict[str, datetime] = field(default_factory=dict)
    expiry_s: dict[str, int] = field(default_factory=dict)
    sunspec_limit: dict[str, int] = field(default_factory=lambda: {"pct": 10000, "ena": 0, "rvrt": 0})
    sg_contacts: list[bool] = field(default_factory=lambda: [False, False])
    sg_timers: list[datetime | None] = field(default_factory=lambda: [None, None])
    boiler_timer: datetime | None = None
    servers: list = field(default_factory=list)
    ports: dict[str, int] = field(default_factory=dict)
    sim_hp_setpoint_default: float | None = None
    write_log: list = field(default_factory=list)   # (device, address/channel, values) of every write received

    # ----------------------------------------------------------- helpers
    @property
    def site(self):
        return self.site_fn()

    def comp(self, cid: str):
        return self.site.components.get(cid)

    def now(self) -> datetime:
        return self.site.now

    def fault(self, name: str) -> Fault:
        return self.faults.setdefault(name, Fault())

    def arm(self, name: str) -> None:
        secs = self.expiry_s.get(name, 900)
        if secs:
            self.expiry[name] = self.now() + timedelta(seconds=secs)

    def expire(self, t: datetime) -> None:
        """Device-side watchdog: a command the EMS no longer refreshes falls back to native behaviour."""
        for name, deadline in list(self.expiry.items()):
            if t < deadline:
                continue
            del self.expiry[name]
            if name == "heat_pump" and (hp := self.comp("lab_hp")):
                hp.mode = "normal"
                if self.sim_hp_setpoint_default is not None:
                    hp.comfort_temperature = self.sim_hp_setpoint_default
            elif name == "battery" and (b := self.comp("lab_battery")):
                b.mode, b.setpoint_w = "auto", 0.0
            elif name == "ev" and (ev := self.comp("lab_ev")):
                ev.current_setpoint_a = None
            elif name == "pv":
                self.sunspec_limit["ena"] = 0
                self._apply_pv_limit()
        for i, tm in enumerate(self.sg_timers):
            if tm is not None and t >= tm:
                self.sg_contacts[i], self.sg_timers[i] = False, None
                self._apply_sg()
        if self.boiler_timer is not None and t >= self.boiler_timer:
            self.boiler_timer = None
            if (bo := self.comp("lab_boiler")):
                bo.relay_on = not bo.relay_on

    # -------------------------------------------------------- register maps
    def ident(self, name: str) -> dict[tuple[str, int], int]:
        regs = _str_regs("EM Testomgeving", 8) + _str_regs(MODELS[name], 8) + _str_regs(SERIALS[name], 8) + \
            _str_regs(FW, 4)
        return {("holding", i): v for i, v in enumerate(regs)}

    def regs_heat_pump(self) -> dict[tuple[str, int], int]:
        hp: SimHeatPump = self.comp("lab_hp")
        if hp is None:
            return {}
        m = self.ident("heat_pump")
        thermal = hp.thermal_w if hp.compressor_on else 0.0
        vals = [("input", 0, int(hp.output_w), "uint16"), ("input", 1, int(thermal), "uint16"),
                ("input", 2, round(hp.indoor_temp_c * 10), "int16"), ("input", 3, round(hp.outdoor_temp_c * 10), "int16"),
                ("input", 4, round(hp.flow_temp_c * 10), "int16"),
                ("input", 5, round((hp.flow_temp_c - (5 if hp.compressor_on else 0)) * 10), "int16"),
                ("input", 6, 480, "int16"), ("input", 7, round((hp.cop or 0) * 100), "uint16"),
                ("input", 8, int(hp.compressor_on), "uint16"),
                ("input", 9, int(30 + 60 * hp.output_w / 2500) if hp.compressor_on else 0, "uint16"),
                ("input", 10, int(hp.compressor_on and hp.outdoor_temp_c < 3 and hp.time_in_state_s % 3600 < 300), "uint16"),
                ("input", 11, 0, "uint16"),
                ("holding", 100, {"normal": 1, "boost": 2, "eco": 3}.get(hp.mode, 1), "uint16"),
                ("holding", 101, round(hp.comfort_temperature * 10), "int16"),
                ("holding", 110, self.expiry_s.get("heat_pump", 900), "uint16")]
        for table, a, v, t in vals:
            m[(table, a)] = encode(v, t)[0]
        return m

    def write_heat_pump(self, address: int, values: list[int]) -> int | None:
        hp: SimHeatPump = self.comp("lab_hp")
        for i, v in enumerate(values):
            a = address + i
            if a == 100:
                mode = {1: "normal", 2: "boost", 3: "eco"}.get(v)
                if mode is None:
                    return 3
                hp.mode = mode
                self.arm("heat_pump")
            elif a == 101:
                c = (v - 65536 if v > 32767 else v) / 10
                if not 15 <= c <= 24:
                    return 3
                if self.sim_hp_setpoint_default is None:
                    self.sim_hp_setpoint_default = hp.comfort_temperature
                hp.comfort_temperature = c
                self.arm("heat_pump")
            elif a == 110:
                if not 0 <= v <= 86400:
                    return 3
                self.expiry_s["heat_pump"] = v
            else:
                return 2
        return None

    def regs_battery(self) -> dict[tuple[str, int], int]:
        b: SimBattery = self.comp("lab_battery")
        if b is None:
            return {}
        m = self.ident("battery")
        mode = {"auto": 0, "standby": 1}.get(b.mode, 2)
        cap = int(b.capacity_kwh * 1000)
        sp = int(b.setpoint_w if b.mode == "charge" else -b.setpoint_w if b.mode == "discharge" else 0)
        for table, a, v, t in [("input", 0, int(b.output_w), "int16"), ("input", 1, round(b.soc_pct * 10), "uint16"),
                               ("input", 2, round(b.temperature_c * 10), "int16"), ("input", 3, int(b.hw_max_soc_pct), "uint16"),
                               ("input", 6, 0, "uint16"), ("holding", 100, mode, "uint16"), ("holding", 101, sp, "int16"),
                               ("holding", 102, int(b.hw_max_soc_pct), "uint16"),
                               ("holding", 110, self.expiry_s.get("battery", 900), "uint16")]:
            m[(table, a)] = encode(v, t)[0]
        hi, lo = encode(cap, "uint32")
        m[("input", 4)], m[("input", 5)] = hi, lo
        return m

    def write_battery(self, address: int, values: list[int]) -> int | None:
        b: SimBattery = self.comp("lab_battery")
        for i, v in enumerate(values):
            a = address + i
            if a == 100:
                if v not in (0, 1):
                    return 3
                b.mode, b.setpoint_w = ("auto" if v == 0 else "standby"), 0.0
                self.expiry.pop("battery", None) if v == 0 else self.arm("battery")
            elif a == 101:
                w = v - 65536 if v > 32767 else v
                if abs(w) > 5000:
                    return 3
                b.mode, b.setpoint_w = ("charge", float(w)) if w >= 0 else ("discharge", float(-w))
                self.arm("battery")
            elif a == 102:
                if not 50 <= v <= 100:
                    return 3
                b.hw_max_soc_pct = float(v)
            elif a == 110:
                self.expiry_s["battery"] = v
            else:
                return 2
        return None

    def regs_ev(self) -> dict[tuple[str, int], int]:
        ev: SimEVCharger = self.comp("lab_ev")
        if ev is None:
            return {}
        m = self.ident("ev")
        limit = ev.max_current_a if ev.current_setpoint_a is None else ev.current_setpoint_a
        for table, a, v, t in [("input", 0, int(ev.output_w), "uint16"), ("input", 1, int(ev.connected), "uint16"),
                               ("input", 2, min(65535, round(ev.session_kwh * 100)), "uint16"),
                               ("holding", 100, round(limit * 10), "uint16"),
                               ("holding", 110, self.expiry_s.get("ev", 900), "uint16")]:
            m[(table, a)] = encode(v, t)[0]
        return m

    def write_ev(self, address: int, values: list[int]) -> int | None:
        ev: SimEVCharger = self.comp("lab_ev")
        for i, v in enumerate(values):
            a = address + i
            if a == 100:
                amps = v / 10
                if not (amps == 0 or 6 <= amps <= 16):
                    return 3
                ev.current_setpoint_a = amps
                self.arm("ev")
            elif a == 110:
                self.expiry_s["ev"] = v
            else:
                return 2
        return None

    # ---------------------------------------------------------------- SunSpec
    def _ss_block(self, mid: int, values: dict[str, int | str]) -> list[int]:
        d = ss_model(mid)
        length = sum(p.size for p in d.values()) - 2
        block = [0] * (length + 2)
        for p in d.values():
            if p.type in ("uint16", "enum16", "bitfield16"):
                block[p.offset] = 0xFFFF
            elif p.type in ("int16", "sunssf"):
                block[p.offset] = 0x8000
            elif p.type in ("uint32", "bitfield32"):
                block[p.offset:p.offset + 2] = [0xFFFF, 0xFFFF]
            elif p.type == "int32":
                block[p.offset:p.offset + 2] = [0x8000, 0]
        block[0], block[1] = mid, length
        for name, v in values.items():
            p = d[name]
            if p.type == "string":
                block[p.offset:p.offset + p.size] = _str_regs(str(v), p.size)
            else:
                t = {"enum16": "uint16", "sunssf": "int16", "acc32": "uint32", "bitfield32": "uint32"}.get(p.type, p.type)
                regs = encode(int(v), t)
                block[p.offset:p.offset + len(regs)] = regs
        return block

    def regs_pv(self) -> dict[tuple[str, int], int]:
        pv: SimPV = self.comp("lab_pv")
        if pv is None:
            return {}
        rated = int(pv.peak_power_kw * 1000)
        w = int(pv.output_w)
        blocks = [
            self._ss_block(1, {"Mn": "Energy Manager Lab", "Md": "EM-VPV-5", "Opt": "lab", "Vr": FW, "SN": SERIALS["pv"],
                               "DA": 1}),
            self._ss_block(103, {"A": round(w / 230 / 3 * 100), "AphA": round(w / 690 * 100), "AphB": round(w / 690 * 100),
                                 "AphC": round(w / 690 * 100), "A_SF": -2, "PhVphA": 2300, "PhVphB": 2300, "PhVphC": 2300,
                                 "V_SF": -1, "W": w, "W_SF": 0, "Hz": 5000, "Hz_SF": -2, "WH": int(pv.energy_kwh * 1000) or 1,
                                 "WH_SF": 0, "DCW": int(w * 1.03), "DCW_SF": 0, "TmpCab": 350, "Tmp_SF": -1,
                                 "St": 4 if w > 0 else 2}),
            self._ss_block(120, {"DERTyp": 4, "WRtg": rated, "WRtg_SF": 0}),
            self._ss_block(123, {"Conn": 1, "WMaxLimPct": self.sunspec_limit["pct"],
                                 "WMaxLimPct_RvrtTms": self.sunspec_limit["rvrt"],
                                 "WMaxLim_Ena": self.sunspec_limit["ena"], "WMaxLimPct_SF": -2}),
        ]
        regs = [0x5375, 0x6E53]
        self._ss_layout = {}
        addr = 40002
        for b in blocks:
            self._ss_layout[b[0]] = addr
            regs += b
            addr += len(b)
        regs += [0xFFFF, 0]
        return {("holding", 40000 + i): v for i, v in enumerate(regs)}

    def _apply_pv_limit(self) -> None:
        pv: SimPV = self.comp("lab_pv")
        if pv is None:
            return
        if self.sunspec_limit["ena"] == 1:
            pv.limit_w = pv.peak_power_kw * 1000 * self.sunspec_limit["pct"] / 10000
        else:
            pv.limit_w = None

    def write_pv(self, address: int, values: list[int]) -> int | None:
        self.regs_pv()
        base = self._ss_layout.get(123)
        d = ss_model(123)
        for i, v in enumerate(values):
            off = address + i - base
            name = next((p.name for p in d.values() if p.offset == off), None)
            if name == "WMaxLimPct":
                if v > 10000:
                    return 3
                self.sunspec_limit["pct"] = v
            elif name == "WMaxLim_Ena":
                if v not in (0, 1):
                    return 3
                self.sunspec_limit["ena"] = v
            elif name == "WMaxLimPct_RvrtTms":
                self.sunspec_limit["rvrt"] = v
                self.expiry_s["pv"] = v
            else:
                return 2
        if self.sunspec_limit["ena"] == 1 and self.sunspec_limit["rvrt"]:
            self.arm("pv")
        elif self.sunspec_limit["ena"] == 0:
            self.expiry.pop("pv", None)
        self._apply_pv_limit()
        return None

    # --------------------------------------------------------------- HTTP
    async def http_p1(self, method: str, path: str, query: dict, body) -> tuple[int, Any]:
        m = self.site.meter
        if path == "/api":
            return 200, {"product_name": "P1 meter (virtueel)", "product_type": "HWE-P1", "serial": SERIALS["p1"],
                         "firmware_version": FW, "api_version": "v1"}
        if path == "/api/v1/data":
            d = {"wifi_ssid": "testomgeving", "wifi_strength": 100, "smr_version": 50, "meter_model": "Virtuele meter",
                 "active_tariff": 2, "total_power_import_kwh": round(m.import_kwh, 3),
                 "total_power_export_kwh": round(m.export_kwh, 3), "active_power_w": round(m.power_w, 1)}
            for n in range(3):
                d[f"active_power_l{n + 1}_w"] = round(m.phase_power_w[n], 1)
                d[f"active_voltage_l{n + 1}_v"] = round(m.phase_voltage_v[n], 1)
                d[f"active_current_l{n + 1}_a"] = round(abs(m.phase_current_a[n]), 2)
            return 200, d
        return 404, {"error": "not found"}

    def _apply_sg(self) -> None:
        hp: SimHeatPump = self.comp("lab_hp")
        if hp is None:
            return
        state = {(True, False): "eco", (False, False): "normal", (False, True): "boost", (True, True): "boost"}
        hp.mode = state[tuple(self.sg_contacts)]

    async def http_shelly(self, kind: str, method: str, path: str, query: dict, body) -> tuple[int, Any]:
        channels = 2 if kind == "sgready" else 1
        if path == "/shelly":
            return 200, {"name": "Virtuele relaismodule", "id": SERIALS[kind], "mac": "EMLAB00000" + str(channels),
                         "model": "EM-VRELAY-" + str(channels), "gen": 2, "fw_id": "lab", "ver": FW,
                         "app": "Pro2" if channels == 2 else "Plus1PM", "auth_en": False}
        if path != "/rpc" or not isinstance(body, dict):
            return 404, {"error": "not found"}
        rid, mth, params = body.get("id"), body.get("method"), body.get("params") or {}

        def status(ch: int) -> dict:
            if kind == "sgready":
                return {"id": ch, "source": "rpc", "output": self.sg_contacts[ch]}
            bo: SimSwitchLoad = self.comp("lab_boiler")
            return {"id": 0, "source": "rpc", "output": bo.relay_on, "apower": round(bo.output_w, 1), "voltage": 230.0,
                    "temperature": {"tC": 38.0}, "aenergy": {"total": round(bo.energy_kwh * 1000, 1)}}
        ch = int(params.get("id", 0))
        if mth == "Shelly.GetStatus":
            return 200, {"id": rid, "result": {f"switch:{i}": status(i) for i in range(channels)}}
        if ch >= channels:
            return 200, {"id": rid, "error": {"code": -105, "message": f"Argument 'id', value {ch} not found!"}}
        if mth == "Switch.GetStatus":
            return 200, {"id": rid, "result": status(ch)}
        if mth == "Switch.Set":
            on = bool(params.get("on"))
            ta = params.get("toggle_after")
            self.write_log.append((kind, ch, [int(on)]))
            if kind == "sgready":
                was = self.sg_contacts[ch]
                self.sg_contacts[ch] = on
                self.sg_timers[ch] = self.now() + timedelta(seconds=float(ta)) if ta else None
                self._apply_sg()
            else:
                bo = self.comp("lab_boiler")
                was = bo.relay_on
                bo.relay_on = on
                self.boiler_timer = self.now() + timedelta(seconds=float(ta)) if ta else None
            return 200, {"id": rid, "result": {"was_on": was}}
        return 200, {"id": rid, "error": {"code": -114, "message": f"Method {mth} not found"}}

    # -------------------------------------------------------------- servers
    async def start(self) -> None:
        from pymodbus.server import ModbusTcpServer

        from ems.simulator.labmodbus import LabContext

        devices = {"pv": (self.regs_pv, self.write_pv), "heat_pump": (self.regs_heat_pump, self.write_heat_pump),
                   "battery": (self.regs_battery, self.write_battery), "ev": (self.regs_ev, self.write_ev),
                   "unknown": (lambda: {("holding", a): 0 for a in range(0, 200)} |
                               {("input", a): 0 for a in range(0, 200)}, lambda a, v: 2)}
        for name, (reader, writer) in devices.items():
            ctx = LabContext(self, name, reader, writer)
            from pymodbus.datastore import ModbusServerContext
            server = ModbusTcpServer(ModbusServerContext(devices={1: ctx}, single=False), address=("127.0.0.1", port_of(name)))
            try:
                await server.listen()
            except OSError as exc:
                log.warning("lab port busy", extra={"device": name, "port": port_of(name), "error": str(exc)})
                continue
            self.servers.append(server)
            self.ports[name] = port_of(name)
        for name, handler in (("p1", self.http_p1),
                              ("sgready", lambda *a: self.http_shelly("sgready", *a)),
                              ("boiler", lambda *a: self.http_shelly("boiler", *a))):
            srv = MiniHTTPServer(self._faulty_http(name, handler), port=port_of(name))
            try:
                await srv.start()
            except OSError as exc:
                log.warning("lab port busy", extra={"device": name, "port": port_of(name), "error": str(exc)})
                continue
            self.servers.append(srv)
            self.ports[name] = srv.port

    def _faulty_http(self, name: str, handler):
        async def wrapped(method, path, query, body):
            f = self.fault(name)
            if f.delay_s:
                await asyncio.sleep(f.delay_s)
            if f.offline:
                return 500, {"error": "device offline (testomgeving)"}
            return await handler(method, path, query, body)
        return wrapped

    async def stop(self) -> None:
        for s in self.servers:
            try:
                if isinstance(s, MiniHTTPServer):
                    await s.stop()
                else:
                    await s.shutdown()
            except Exception:
                log.exception("lab server stop failed")
        self.servers.clear()
        self.ports.clear()

    def overview(self) -> list[dict]:
        labels = {"pv": "Virtuele SunSpec-omvormer 5 kW (Modbus TCP)", "heat_pump": "Virtuele warmtepomp (Modbus TCP)",
                  "battery": "Virtuele thuisbatterij 10 kWh (Modbus TCP)", "ev": "Virtuele laadpaal 11 kW (Modbus TCP)",
                  "unknown": "Onbekend Modbus-apparaat (geen identiteit)", "p1": "Virtuele P1-meter (HomeWizard API v1)",
                  "sgready": "Virtuele relaismodule voor SG Ready (Shelly Gen2 RPC)",
                  "boiler": "Virtuele boiler via slim relais (Shelly Gen2 RPC)"}
        return [{"key": k, "label": labels[k], "host": "127.0.0.1", "port": p,
                 "protocol": "modbus_tcp" if k in ("pv", "heat_pump", "battery", "ev", "unknown") else "http",
                 "fault": vars(self.fault(k))} for k, p in self.ports.items()]
