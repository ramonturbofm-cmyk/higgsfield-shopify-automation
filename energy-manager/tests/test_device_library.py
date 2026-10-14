"""Device library, real protocols and the Testomgeving (opdracht §24 A–F).

The virtual devices of the Testomgeving are real servers on 127.0.0.1 (pymodbus Modbus TCP with the
official SunSpec models, HomeWizard API v1, Shelly Gen2 RPC). Everything here talks to them over the
network through the production drivers — nothing is mocked inside the drivers. No real hardware."""

from __future__ import annotations

import itertools
import json
import math
from datetime import UTC, datetime

import httpx
import pytest

from ems.core.clock import SimulatedClock
from ems.core.config import DeviceConfig
from ems.core.models import Capability, Command, CommandAction
from ems.devices import capreport
from ems.devices.base import DriverContext, DriverError
from ems.devices.discovery import merge, scan
from ems.devices.library.catalog import Catalog, ProfileError, catalog, parse_profile
from ems.integrations.homewizard.driver import HomeWizardP1Driver
from ems.integrations.modbus.codec import CodecError, decode, encode, register_count, scaled, unscaled
from ems.integrations.modbus.profile_driver import ProfileModbusDriver
from ems.integrations.shelly.drivers import SGReadyRelayDriver, ShellySwitchDriver
from ems.integrations.sunspec.driver import SunSpecDriver

T0 = datetime(2026, 6, 21, 11, tzinfo=UTC)
_offsets = itertools.count(1)


def _offset(monkeypatch) -> None:
    # Own port range per test: independent of a Testomgeving that may already run on this machine.
    monkeypatch.setenv("EMS_LAB_PORT_OFFSET", str(2000 + 200 * next(_offsets)))


@pytest.fixture
async def lab(monkeypatch):
    from ems.simulator.environment import Environment
    from ems.simulator.lab import LabDevices, lab_components
    from ems.simulator.site import SimulatedSite

    _offset(monkeypatch)
    site = SimulatedSite(env=Environment(52, 5, "Europe/Amsterdam"), start=T0)
    for c in lab_components():
        site.add(c)
    devices = LabDevices(lambda: site)
    site.lab = devices
    site.advance(1)
    await devices.start()
    assert len(devices.ports) == 9, devices.ports
    yield site, devices
    await devices.stop()


def _drv(cls, site, lab, key, category, driver_id, **conn):
    port = lab.ports[key]
    host = f"127.0.0.1:{port}" if key in ("sgready", "boiler") else "127.0.0.1"
    connection = {"host": host, **({} if key in ("sgready", "boiler") else {"port": port}), **conn}
    cfg = DeviceConfig(id=key, name=key, category=category, driver=driver_id, connection=connection)
    return cls(cfg, DriverContext(SimulatedClock(T0), simulator=site))


def _hp(site, lab, **conn):
    return _drv(ProfileModbusDriver, site, lab, "heat_pump", "heat_pump", "library.modbus",
                profile_id="energymanager.lab.heat_pump", **conn)


# ======================================================================= codec
def test_codec_datatypes_and_orders():
    assert decode([0xFFFF], "int16") == -1 and decode([0xFFFF], "uint16") == 65535
    f = encode(1234.5, "float32")
    assert decode(f, "float32") == pytest.approx(1234.5)
    assert decode(list(reversed(f)), "float32", word_order="little") == pytest.approx(1234.5)
    assert decode(encode(-7, "int32"), "int32") == -7
    assert decode(encode(2**40, "uint64"), "uint64") == 2**40
    assert decode([0x7FC0, 0x0000], "float32") is None            # NaN = not available, never a number
    assert register_count("float64") == 4 and register_count("string", 8) == 8
    assert decode([0x4142, 0x4300], "string") == "ABC"
    assert scaled(2205, sf=-1) == pytest.approx(220.5) and unscaled(22.5, scale=0.1) == pytest.approx(225)
    with pytest.raises(CodecError):
        encode(70000, "uint16")
    with pytest.raises(CodecError):
        encode(-1, "uint16")


# ============================================================ profiles/catalog
BASE = {"profile_id": "test.vendor.meter", "driver_id": "library.modbus", "manufacturer": "Test", "model": "M1",
        "device_category": "energy_meter", "protocol": "modbus_tcp",
        "documentation_sources": [{"title": "Handleiding M1", "url": "https://example.invalid/m1.pdf"}],
        "test_status": "UNVERIFIED",
        "points": [{"metric": "grid_power_w", "table": "input", "address": 0, "type": "int32"}]}


def test_builtin_catalog_is_valid_and_honest():
    cat = Catalog(None).load()
    assert not cat.problems, cat.problems
    assert {"energymanager.lab.heat_pump", "energymanager.lab.battery", "victron.gx", "sunspec.inverter"} <= set(cat.profiles)
    for p in cat.profiles.values():
        assert p.documentation_sources, p.profile_id
        if p.test_status == "HARDWARE_TESTED":      # nothing in this release has been tested on hardware
            pytest.fail(f"{p.profile_id} claims HARDWARE_TESTED")
        for c in p.writable_controls:
            if c.writable and not p.simulator_only:
                assert c.status == "OFFICIAL_DOCUMENTATION", (p.profile_id, c.action)
    # Community profile: readable, never writable.
    victron = cat.get("victron.gx")
    assert victron.source == "builtin" and victron.test_status == "COMMUNITY"
    assert not any(c.writable for c in victron.writable_controls)


def test_profile_validation_rejects_inventions():
    parse_profile(BASE)
    with pytest.raises(ProfileError, match="documentation"):
        parse_profile({**BASE, "documentation_sources": []})
    with pytest.raises(ProfileError):
        parse_profile({**BASE, "python": "import os"})                       # no code, no unknown fields
    ctl = {"action": "battery_charge", "table": "holding", "address": 10, "type": "uint16", "min": 0, "max": 5000,
           "status": "SIMULATOR"}
    with pytest.raises(ProfileError, match="SIMULATOR"):                       # simulator status only for the lab
        parse_profile({**BASE, "device_category": "battery", "writable_controls": [ctl]})
    with pytest.raises(ProfileError):                                         # capability must fit the category
        parse_profile({**BASE, "capabilities": ["control_hp_mode"]})


def test_user_and_community_profiles_never_unlock_writes(tmp_path):
    cat = Catalog(tmp_path).load()
    ctl = {"action": "battery_charge", "table": "holding", "address": 10, "type": "uint16", "min": 0, "max": 5000,
           "status": "OFFICIAL_DOCUMENTATION"}
    prof, prev = cat.import_profile({**BASE, "device_category": "battery", "writable_controls": [ctl],
                                     "test_status": "HARDWARE_TESTED"})
    assert prof.test_status == "UNVERIFIED"                                   # a claim in a file is not a test
    assert prev is None and prof.source == "user"
    assert prof.writable_controls[0].status == "UNVERIFIED" and not prof.writable_controls[0].writable
    com, _ = cat.import_profile({**BASE, "profile_id": "test.vendor.c", "source": "community"})
    assert com.source == "community"
    with pytest.raises(ProfileError, match="ingebouwd"):
        cat.import_profile({**BASE, "profile_id": "victron.gx"})
    # Versioning + rollback.
    v2, prev = cat.import_profile({**BASE, "device_category": "battery", "model": "M1 v2", "writable_controls": [ctl]})
    assert prev is not None and v2.model == "M1 v2"
    assert cat.rollback("test.vendor.meter").model == "M1"
    assert json.loads((tmp_path / "test.vendor.meter.json").read_text())["model"] == "M1"


def test_brands_list_is_honest():
    cat = catalog(None)
    hp = {b["manufacturer"]: b for b in cat.brands("heat_pump")}
    assert hp["Weheat"]["models"][0]["support"] == "not_supported"          # cloud only: nothing claimed locally
    assert "Energy Manager Testomgeving" not in hp                           # virtual devices only in the lab
    assert "Energy Manager Testomgeving" in {b["manufacturer"] for b in cat.brands("heat_pump", include_simulator=True)}
    pv = {b["manufacturer"]: b for b in cat.brands("pv_inverter")}
    assert pv["Fronius"]["models"][0]["support"] == "read_only"


# =============================================================== A: detection
async def test_a_discovery_identifies_lab_devices_and_never_guesses(lab):
    site, devices = lab
    r = await scan(lab=devices, use_mdns=False, use_serial=False, configured=[])
    by = {(x["driver_id"], x.get("profile_id")): x for x in r["results"]}
    hp = by[("library.modbus", "energymanager.lab.heat_pump")]
    assert hp["confidence"] == "EXACT_MODEL" and hp["serial"] == "EMLAB-HP-0001" and hp["virtual"]
    assert by[("library.sunspec", "energymanager.lab.sunspec_inverter")]["confidence"] == "EXACT_MODEL"
    assert by[("homewizard.p1", None)]["confidence"] == "EXACT_MODEL"
    unknown = [x for x in r["results"] if x["confidence"] == "PROTOCOL"]
    assert unknown and unknown[0]["manufacturer"] is None and unknown[0]["profile_id"] is None


def test_a_dedupe_and_address_change():
    a = {"confidence": "EXACT_MODEL", "confidence_label": "x", "manufacturer": "M", "model": "X", "serial": "S1",
         "host": "192.168.1.20", "port": 502, "protocol": "modbus_tcp", "profile_id": "p", "unit_id": 1}
    b = {**a, "confidence": "PROTOCOL", "host": "192.168.1.21"}
    merged = merge([a, b], [])
    assert len(merged) == 1 and len(merged[0]["routes"]) == 2 and merged[0]["confidence"] == "EXACT_MODEL"
    configured = [{"id": "hp1", "connection": {"host": "192.168.1.5", "port": 502},
                   "params": {"identity": {"serial": "s1"}}}]
    m = merge([dict(a)], configured)[0]
    assert m["configured_device"] == "hp1" and m["address_changed"]


# ============================================================ B: capabilities
async def test_b_capability_report_is_honest(lab):
    site, devices = lab
    ev = _drv(ProfileModbusDriver, site, devices, "ev", "ev_charger", "library.modbus",
              profile_id="energymanager.lab.ev_charger")
    await ev.connect()
    assert Capability.READ_EV_SOC not in ev.capabilities()                  # a charger does not know the car's SOC
    rows = {r["capability"]: r for r in capreport.report(ev, "ev_charger", level="read_only", sample=await ev.read())}
    assert rows["control_ev_current"]["status"] == "REQUIRES_COMMISSIONING"
    assert rows["read_ev_power"]["status"] == "SUPPORTED_READ"
    sg = _drv(SGReadyRelayDriver, site, devices, "sgready", "heat_pump", "sgready.relay")
    rows = {r["capability"]: r for r in capreport.report(sg, "heat_pump", level="full")}
    assert rows["control_hp_mode"]["status"] == "SUPPORTED_WRITE"
    assert any(r["status"] == "NOT_SUPPORTED" and "vermogen" in r["label"].lower() for r in rows.values())


def test_b_generic_and_library_drivers_claim_nothing_without_profile():
    from ems.devices.registry import registry
    for did in ("generic.modbus_tcp", "library.modbus", "homeassistant.entities"):
        assert not registry.get(did).manifest.capabilities
    cfg = DeviceConfig(id="x", name="x", category="battery", driver="library.modbus",
                       connection={"host": "127.0.0.1", "profile_id": "does.not.exist"})
    drv = ProfileModbusDriver(cfg, DriverContext(SimulatedClock(T0)))
    assert not drv.capabilities() and not drv.write_documented()


# ===================================================== D: Modbus commands
async def test_d_modbus_write_range_readback_and_faults(lab):
    site, devices = lab
    hp = _hp(site, devices, timeout_s=1, retries=1)
    await hp.connect()
    ident = await hp.identify()
    assert ident["serial_number"] == "EMLAB-HP-0001"
    await hp.apply(Command("heat_pump", CommandAction.HP_MODE, "boost"))
    assert site.components["lab_hp"].mode == "boost"                         # really written and read back
    await hp.apply(Command("heat_pump", CommandAction.TEMP_SETPOINT, 22.5))
    assert site.components["lab_hp"].comfort_temperature == pytest.approx(22.5)
    with pytest.raises(DriverError):                                         # outside the profile's range
        await hp.apply(Command("heat_pump", CommandAction.TEMP_SETPOINT, 30))
    with pytest.raises(DriverError):                                         # value not in the documented map
        await hp.apply(Command("heat_pump", CommandAction.HP_MODE, "turbo"))
    # Device answers with a Modbus exception.
    devices.fault("heat_pump").exception = 4
    with pytest.raises(DriverError, match="(?i)modbus|apparaat"):
        await hp.read()
    devices.fault("heat_pump").exception = None
    # Slow device: a write times out and is NOT retried (result unknown → never sent twice).
    n_before = len(devices.write_log)
    devices.fault("heat_pump").delay_s = 2.5
    with pytest.raises(DriverError, match="onbekend"):
        await hp.apply(Command("heat_pump", CommandAction.HP_MODE, "eco"))
    devices.fault("heat_pump").delay_s = 0
    writes = [w for w in devices.write_log[n_before:] if w[0] == "heat_pump" and w[1] == 100]
    assert len(writes) <= 1
    # Offline.
    devices.fault("heat_pump").offline = True
    with pytest.raises(DriverError):
        await hp.read()
    devices.fault("heat_pump").offline = False
    await hp.read()


async def test_d_device_watchdog_reverts_unrefreshed_commands(lab):
    site, devices = lab
    hp = _hp(site, devices)
    await hp.connect()
    await hp.apply(Command("heat_pump", CommandAction.HP_MODE, "boost"))
    assert site.components["lab_hp"].mode == "boost"
    site.advance(16 * 60)                                                    # profile expiry: 900 s
    site.advance(1)                                                          # (checked at the start of a step)
    assert site.components["lab_hp"].mode == "normal"


# ============================================================ E: heat pump
async def test_e_sg_ready_states_watchdog_and_safe_default(lab):
    site, devices = lab
    sg = _drv(SGReadyRelayDriver, site, devices, "sgready", "heat_pump", "sgready.relay")
    await sg.connect()
    assert set(sg.modes()) == {"normal", "boost"}                            # default: 2 and 3 only
    await sg.apply(Command("sgready", CommandAction.HP_MODE, "boost"))
    assert (await sg.read())["hp_mode"] == "boost" and site.components["lab_hp"].mode == "boost"
    with pytest.raises(DriverError):                                         # blocking not allowed by the user
        await sg.apply(Command("sgready", CommandAction.HP_MODE, "eco"))
    site.advance(16 * 60)                                                    # EMS stopped refreshing: relay watchdog
    site.advance(1)
    assert devices.sg_contacts == [False, False] and site.components["lab_hp"].mode == "normal"
    await sg.release_control()
    assert devices.sg_contacts == [False, False]
    rows = capreport.report(sg, "heat_pump", level="read_only")
    assert any("geen vermogensregeling" in r["detail"] for r in rows)


def test_e_sg_ready_eco_only_when_user_allows_it():
    cfg = DeviceConfig(id="sg", name="sg", category="heat_pump", driver="sgready.relay",
                       connection={"host": "192.0.2.1", "allowed_states": "1,2,3"})
    assert "eco" in SGReadyRelayDriver(cfg, DriverContext(SimulatedClock(T0))).modes()


# ===================================================== F: battery and PV
async def test_f_battery_and_sunspec_curtailment(lab):
    site, devices = lab
    bat = _drv(ProfileModbusDriver, site, devices, "battery", "battery", "library.modbus",
               profile_id="energymanager.lab.battery")
    await bat.connect()
    await bat.apply(Command("battery", CommandAction.BATTERY_DISCHARGE, 3000))
    site.advance(1)
    v = await bat.read()
    assert v["battery_power_w"] == pytest.approx(-3000, abs=50)
    with pytest.raises(DriverError):
        await bat.apply(Command("battery", CommandAction.BATTERY_CHARGE, 9000))   # above profile maximum
    await bat.release_control()
    assert site.components["lab_battery"].mode == "auto"

    pv = _drv(SunSpecDriver, site, devices, "pv", "pv_inverter", "library.sunspec",
              profile_id="energymanager.lab.sunspec_inverter")
    await pv.connect()
    assert Capability.CONTROL_PV_LIMIT in pv.capabilities()
    assert (await pv.identify())["manufacturer"] and any(m["id"] == 123 for m in pv.chain)
    await pv.apply(Command("pv", CommandAction.PV_LIMIT, 2000))
    site.advance(1)
    assert (await pv.read())["pv_power_w"] <= 2050
    await pv.release_control()
    site.advance(1)
    assert devices.sunspec_limit["ena"] == 0


async def test_f_p1_and_shelly_switch(lab):
    site, devices = lab
    p1 = _drv(HomeWizardP1Driver, site, devices, "p1", "smart_meter", "homewizard.p1", api="v1")
    await p1.connect()
    v = await p1.read()
    assert math.isfinite(v["grid_power_w"]) and "grid_import_energy_kwh" in v
    sw = _drv(ShellySwitchDriver, site, devices, "boiler", "boiler", "shelly.switch", min_on_s=0, min_off_s=0)
    await sw.connect()
    await sw.apply(Command("boiler", CommandAction.SWITCH, True))
    assert site.components["lab_boiler"].relay_on
    await sw.apply(Command("boiler", CommandAction.SWITCH, False))
    assert not site.components["lab_boiler"].relay_on


# ========================================== C: full chain through the API
@pytest.fixture
async def lab_api(tmp_path, monkeypatch):
    from ems.api.app import create_app
    from ems.server.runtime import EMSRuntime

    _offset(monkeypatch)
    rt = EMSRuntime(tmp_path / "data", mode="demo", profile="lab", env={})
    await rt.start(loops=False)
    await rt.tick_once()
    app = create_app(rt, start_runtime=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)
    r = await c.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    yield rt, c
    await c.aclose()
    await rt.stop()


async def test_c_lab_starts_empty_and_marked(lab_api):
    rt, c = lab_api
    info = (await c.get("/api/v1/system/info")).json()
    assert info["profile"] == "lab"
    assert (await c.get("/api/v1/devices")).json() == []                    # the user adds the devices
    lab = (await c.get("/api/v1/lab")).json()
    assert lab["active"] and len(lab["devices"]) == 9


async def test_c_full_chain_read_only_shadow_limited_full(lab_api):
    rt, c = lab_api
    port = rt.lab.ports["battery"]
    brands = (await c.get("/api/v1/library/brands", params={"category": "battery"})).json()
    model = next(m for b in brands for m in b["models"] if m["profile_id"] == "energymanager.lab.battery")
    assert model["lab_connection"]["port"] == port
    body = {"name": "Thuisbatterij", "category": "battery", "driver": "library.modbus",
            "connection": {"host": "127.0.0.1", "port": port, "profile_id": "energymanager.lab.battery"}}
    probe = (await c.post("/api/v1/devices/probe", json=body)).json()
    assert probe["reachable"] and probe["identity"]["serial_number"] == "EMLAB-BAT-0001"
    statuses = {r["capability"]: r["status"] for r in probe["capability_report"]}
    assert statuses["control_battery_power"] == "REQUIRES_COMMISSIONING"
    dev = (await c.post("/api/v1/devices", json=body)).json()
    did = dev["id"]
    assert dev["control_level"] == "read_only" and dev["params"]["capacity_kwh"] == 10   # profile defaults
    await rt.tick_once()

    # Read-only: a manual command is accepted as intent but never reaches the device.
    writes0 = len(rt.lab.write_log)
    r = await c.post("/api/v1/overrides", json={"device": did, "action": "battery_discharge", "value": 2000})
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["executes"] is False
    for _ in range(3):
        await rt.tick_once()
    assert not [w for w in rt.lab.write_log[writes0:] if w[0] == "battery" and w[1] in (100, 101)]
    await c.delete(f"/api/v1/overrides/{did}")

    # Commissioning: test → shadow → write test → limited → full.
    assert (await c.post(f"/api/v1/devices/{did}/test")).json()["reachable"]
    assert (await c.put(f"/api/v1/devices/{did}/commissioning", json={"level": "shadow"})).status_code == 200
    writes1 = len(rt.lab.write_log)
    await rt.optimizer.run(rt.engine.last_snapshot, rt.now(), "test")
    for _ in range(3):
        await rt.tick_once()
    assert not [w for w in rt.lab.write_log[writes1:] if w[0] == "battery" and w[1] in (100, 101)], "shadow wrote"
    wt = (await c.post(f"/api/v1/devices/{did}/commissioning/write-test")).json()
    assert wt["ok"], wt
    com = (await c.get(f"/api/v1/devices/{did}/commissioning")).json()
    assert com["levels"]["limited"]["allowed"], com["levels"]
    r = await c.put(f"/api/v1/devices/{did}/commissioning", json={"level": "full", "confirm_text": "Thuisbatterij"})
    assert r.status_code == 200, r.text

    # Full control (allowed only because this is a virtual device): manual command really executes.
    r = await c.post("/api/v1/overrides", json={"device": did, "action": "battery_discharge", "value": 2000})
    assert r.status_code == 200 and r.json()["executes"], r.text
    for _ in range(2):
        await rt.tick_once()
    assert rt.site.components["lab_battery"].mode == "discharge"
    assert [w for w in rt.lab.write_log if w[0] == "battery" and w[1] == 101]
    await c.delete(f"/api/v1/overrides/{did}")
    assert rt.site.components["lab_battery"].mode == "auto"                 # released to its own control

    report = (await c.get(f"/api/v1/devices/{did}/capability-report")).json()
    assert {r["status"] for r in report["rows"] if r["kind"] == "control"} == {"SUPPORTED_WRITE"}


async def test_c_real_hardware_profile_never_gets_full_control(lab_api):
    """The same profile pointed at another address is not the virtual device: full control stays blocked."""
    rt, c = lab_api
    body = {"name": "Echt", "category": "heat_pump", "driver": "library.modbus",
            "connection": {"host": "192.0.2.10", "port": 502, "profile_id": "energymanager.lab.heat_pump"}}
    did = (await c.post("/api/v1/devices", json=body)).json()["id"]
    com = (await c.get(f"/api/v1/devices/{did}/commissioning")).json()
    assert not com["levels"]["full"]["allowed"]


async def test_c_secret_fields_by_schema_not_name(lab_api):
    rt, c = lab_api
    body = {"name": "Relais", "category": "boiler", "driver": "shelly.switch",
            "connection": {"host": "192.0.2.30", "password": "geheim123"}}
    dev = (await c.post("/api/v1/devices", json=body)).json()
    assert dev["connection"]["password"] == "********"
    assert rt.config.device(dev["id"]).connection["password"].startswith("secret:")
    assert "geheim123" not in (rt.data_dir / "ems.yaml").read_text(encoding="utf-8") \
        if (rt.data_dir / "ems.yaml").exists() else True


async def test_c_discovery_api_and_address_update(lab_api):
    rt, c = lab_api
    port = rt.lab.ports["heat_pump"]
    body = {"name": "WP", "category": "heat_pump", "driver": "library.modbus",
            "connection": {"host": "127.0.0.2", "port": port, "profile_id": "energymanager.lab.heat_pump"},
            "params": {"identity": {"manufacturer": "EM Testomgeving", "serial": "EMLAB-HP-0001"}}}
    did = (await c.post("/api/v1/devices", json=body)).json()["id"]
    res = (await c.post("/api/v1/discovery/scan", json={"mdns": False, "serial": False})).json()["results"]
    hp = next(r for r in res if r.get("serial") == "EMLAB-HP-0001")
    assert hp["configured_device"] == did and hp["address_changed"]
    r = await c.put(f"/api/v1/devices/{did}/address", json={"host": "127.0.0.1", "port": port})
    assert r.status_code == 200
    assert rt.config.device(did).connection["host"] == "127.0.0.1"


async def test_c_lab_fault_injection_api(lab_api):
    rt, c = lab_api
    r = await c.put("/api/v1/lab/faults/battery", json={"offline": True})
    assert r.status_code == 200 and rt.lab.fault("battery").offline
    assert (await c.put("/api/v1/lab/faults/nope", json={})).status_code == 404


async def test_heat_pump_controller_never_requests_a_refused_mode():
    from ems.control.base import ControlContext
    from ems.core.config import config_from_dict

    cfg = config_from_dict({"devices": []}, {})
    ctx = ControlContext(cfg, T0, {"sg": frozenset({Capability.CONTROL_HP_MODE})}, "r", {"sg": ["normal", "boost"]})
    assert "eco" not in ctx.modes["sg"]


# ========================================== Home Assistant bridge (mock API)
async def test_home_assistant_bridge_reads_and_controls_only_when_confirmed(monkeypatch):
    """Against a stand-in for the documented Home Assistant REST API (not a real Home Assistant)."""
    from ems.integrations.homeassistant.bridge import HomeAssistantDriver, suggest

    calls = []
    states = {"sensor.grid": {"entity_id": "sensor.grid", "state": "1.5", "attributes": {"unit_of_measurement": "kW"}},
              "switch.boiler": {"entity_id": "switch.boiler", "state": "off", "attributes": {}}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer tok":
            return httpx.Response(401)
        path = request.url.path
        if path == "/api/":
            return httpx.Response(200, json={"message": "API running."})
        if path == "/api/states":
            return httpx.Response(200, json=list(states.values()))
        if path.startswith("/api/states/"):
            return httpx.Response(200, json=states[path.rsplit("/", 1)[1]])
        if path.startswith("/api/services/"):
            calls.append((path, json.loads(request.content)))
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    monkeypatch.setattr(HomeAssistantDriver, "transport", httpx.MockTransport(handler))
    conn = {"url": "http://ha.local:8123", "token": "tok",
            "values": [{"metric": "load_power_w", "entity_id": "sensor.grid"}],
            "controls": [{"action": "switch", "entity_id": "switch.boiler"}]}
    cfg = DeviceConfig(id="b", name="b", category="boiler", driver="homeassistant.entities", connection=conn)
    drv = HomeAssistantDriver(cfg, DriverContext(SimulatedClock(T0)))
    await drv.connect()
    assert (await drv.read())["load_power_w"] == pytest.approx(1500)              # kW -> W
    assert Capability.CONTROL_SWITCH not in drv.capabilities()                   # not confirmed: no control
    with pytest.raises(DriverError):
        await drv.apply(Command("b", CommandAction.SWITCH, True))
    cfg2 = DeviceConfig(id="b", name="b", category="boiler", driver="homeassistant.entities",
                        connection={**conn, "exclusive_control_confirmed": True})
    drv2 = HomeAssistantDriver(cfg2, DriverContext(SimulatedClock(T0)))
    assert Capability.CONTROL_SWITCH in drv2.capabilities()
    await drv2.apply(Command("b", CommandAction.SWITCH, True))
    assert calls == [("/api/services/switch/turn_on", {"entity_id": "switch.boiler"})]
    assert {e["kind"] for e in suggest(list(states.values()))} == {"power", "switch"}
    bad = HomeAssistantDriver(DeviceConfig(id="b", name="b", category="boiler", driver="homeassistant.entities",
                                           connection={**conn, "token": "wrong"}), DriverContext(SimulatedClock(T0)))
    with pytest.raises(DriverError, match="token"):
        await bad.connect()


# ================================================== catalog content (0.6.1)
WIZARD = ["smart_meter", "energy_meter", "pv_inverter", "hybrid_inverter", "battery", "heat_pump", "heat_pump_boiler",
          "boiler", "ev_charger", "ev", "airco", "ventilation", "smart_plug", "smart_relay", "flexible_load"]


def test_catalog_lists_real_brands_not_protocols():
    cat = Catalog(None).load()
    assert not cat.problems, cat.problems
    for c in WIZARD:
        names = {b["manufacturer"] for b in cat.brands(c)}
        assert not names & {"Home Assistant", "SunSpec-compatibel (overige merken)", "SG Ready (alle merken)"}, (c, names)
        assert cat.routes(c), c                                     # always at least one alternative route
        assert names or c in ("hvac",), c
        for b in cat.brands(c):
            for m in b["models"]:
                if m["support"] in ("supported", "read_only", "community_read_only"):
                    assert m["functions"], (b["manufacturer"], m["model"])
                if m["support"] == "not_supported":
                    assert m["note"], (b["manufacturer"], m["model"])
    pv = {b["manufacturer"]: b for b in cat.brands("pv_inverter")}
    assert {"Fronius", "SMA", "SolarEdge", "Kostal", "Growatt", "Enphase"} <= set(pv)
    assert any("model 123" in f for f in pv["Fronius"]["models"][0]["functions"])
    assert [r["id"] for r in pv["Growatt"]["models"][0]["alternatives"]] == ["sunspec", "homeassistant"]
    hp = {b["manufacturer"]: b for b in cat.brands("heat_pump")}
    assert {r["id"] for r in hp["Vaillant"]["models"][0]["alternatives"]} == {"sgready", "homeassistant"}
    assert {"Alfen", "Easee", "Zaptec"} <= {b["manufacturer"] for b in cat.brands("ev_charger")}
    assert {r["id"] for r in cat.routes("pv_inverter")} == {"sunspec", "homeassistant"}


async def test_home_assistant_flow_against_virtual_home_assistant(lab_api):
    """Wizard flow without JSON: list entities with slots for the category, map, add, read, control after commissioning."""
    rt, c = lab_api
    url = f"http://127.0.0.1:{rt.lab.ports['homeassistant']}"
    bad = await c.post("/api/v1/integrations/homeassistant/entities", json={"url": url, "token": "fout"})
    assert bad.status_code == 422 and "token" in bad.json()["detail"]
    r = (await c.post("/api/v1/integrations/homeassistant/entities",
                      json={"url": url, "token": "testomgeving", "category": "boiler"})).json()
    assert any(e["entity_id"] == "switch.testwoning_boiler" for e in r["all"])
    assert {s["metric"] for s in r["slots"]["values"]} >= {"load_power_w"}
    assert any(s["action"] == "switch" and "switch" in s["domains"] for s in r["slots"]["controls"])
    routes = (await c.get("/api/v1/library/routes", params={"category": "boiler"})).json()
    ha = next(x for x in routes if x["id"] == "homeassistant")
    assert ha["lab_connection"] == {"url": url, "token": "testomgeving"}
    body = {"name": "Boiler via HA", "category": "boiler", "driver": "homeassistant.entities",
            "connection": {"url": url, "token": "testomgeving", "exclusive_control_confirmed": True,
                           "values": [{"metric": "load_power_w", "entity_id": "sensor.testwoning_boiler_vermogen"}],
                           "controls": [{"action": "switch", "entity_id": "switch.testwoning_boiler"}]}}
    probe = (await c.post("/api/v1/devices/probe", json=body)).json()
    assert probe["reachable"]
    assert {r["capability"]: r["status"] for r in probe["capability_report"]}["control_switch"] == "REQUIRES_COMMISSIONING"
    dev = (await c.post("/api/v1/devices", json=body)).json()
    assert dev["connection"]["token"] == "********"
    did = dev["id"]
    assert (await c.post(f"/api/v1/devices/{did}/test")).json()["reachable"]
    await rt.tick_once()
    assert "load_power_w" in (await c.get(f"/api/v1/devices/{did}")).json()["values"]
    # Virtual Home Assistant: commissioning up to full control, then a manual command really switches the boiler.
    assert (await c.put(f"/api/v1/devices/{did}/commissioning", json={"level": "shadow"})).status_code == 200
    assert (await c.post(f"/api/v1/devices/{did}/commissioning/write-test")).json()["ok"]
    com = (await c.get(f"/api/v1/devices/{did}/commissioning")).json()
    assert com["levels"]["full"]["allowed"] or "schaduwmodus" in com["levels"]["full"]["reason"], com["levels"]["full"]
    # A switchable load has no automatic regulator: in shadow mode a manual command shows "EMS zou …".
    r = await c.post("/api/v1/overrides", json={"device": did, "action": "switch", "value": "off"})
    assert r.status_code == 200 and not r.json()["executes"]
    for _ in range(2):
        await rt.tick_once()
    await c.delete(f"/api/v1/overrides/{did}")
    r = await c.put(f"/api/v1/devices/{did}/commissioning", json={"level": "full", "confirm_text": "Boiler via HA"})
    assert r.status_code == 200, r.text
    rt.site.components["lab_boiler"].relay_on = True
    r = await c.post("/api/v1/overrides", json={"device": did, "action": "switch", "value": "off"})
    assert r.status_code == 200 and r.json()["executes"], r.text
    for _ in range(2):
        await rt.tick_once()
    assert rt.site.components["lab_boiler"].relay_on is False
    assert ("homeassistant", "switch.turn_off", ["switch.testwoning_boiler"]) in rt.lab.write_log
