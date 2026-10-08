"""Acceptance tests 1–6 and 18 from the v0.2.0 audit (docs/Energy_Manager_Audit_v0.2.0.md §8)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from ems.api.app import create_app
from ems.control.authority import ControlState, can_execute
from ems.core.models import Capability, Command, CommandAction, Decision, Metric
from ems.devices.base import DeviceDriver, DriverManifest
from ems.devices.registry import registry as default_registry
from ems.gridmeter.meter import GridMeterSelection, GridMeterStatus, feature_availability
from ems.integrations.mock.drivers import MockBattery, MockHeatPump
from ems.server.runtime import EMSRuntime

C = Capability


TEST_DRIVERS: list = []


def _register(cls):
    TEST_DRIVERS.append(cls)
    return cls


@pytest.fixture(autouse=True)
def test_drivers():
    """Register the test-only drivers for this module and remove them afterwards."""
    for cls in TEST_DRIVERS:
        default_registry.register(cls)
    yield
    for cls in TEST_DRIVERS:
        default_registry._drivers.pop(cls.manifest.driver_id, None)


@_register
class SetpointOnlyHeatPump(MockHeatPump):
    """Test 1: a heat pump that can only read the flow temperature and take a room setpoint."""
    manifest = DriverManifest(**{**MockHeatPump.manifest.__dict__, "driver_id": "test.hp_setpoint_only",
                                 "capabilities": frozenset({C.READ_FLOW_TEMP, C.CONTROL_TEMP_SETPOINT})})


@_register
class SocLimitOnlyBattery(MockBattery):
    """Test 2: a battery that only reports SOC and accepts a maximum-SOC setpoint."""
    manifest = DriverManifest(**{**MockBattery.manifest.__dict__, "driver_id": "test.battery_soc_limit_only",
                                 "capabilities": frozenset({C.READ_BATTERY_SOC, C.CONTROL_SOC_LIMIT})})


@_register
class UndocumentedWriter(DeviceDriver):
    """Test 18: a non-simulated driver that claims writes but has no documentation source."""
    manifest = DriverManifest(driver_id="test.undocumented_writer", display_name="Test writer", vendor="Test",
                              categories=(MockBattery.manifest.categories[0],),
                              capabilities=frozenset({C.READ_BATTERY_SOC, C.READ_BATTERY_POWER,
                                                      C.CONTROL_BATTERY_MODE, C.CONTROL_BATTERY_POWER}),
                              write_capable=True, documentation="")
    writes: list = []

    async def connect(self) -> None:
        return None

    async def read(self):
        return {Metric.BATTERY_SOC_PCT: 50.0, Metric.BATTERY_POWER_W: 0.0}

    async def apply(self, command) -> None:
        self.writes.append(command)

    async def release_control(self) -> None:
        return None


@pytest.fixture
async def demo(tmp_path):
    rt = EMSRuntime(tmp_path / "data", mode="demo", env={})
    await rt.start(loops=False)
    for _ in range(2):
        await rt.tick_once()
    app = create_app(rt, start_runtime=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    yield rt, c
    await c.aclose()
    await rt.stop()


async def _add(c, **body):
    r = await c.post("/api/v1/devices", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _rule(c, device, action, value=None):
    return await c.post("/api/v1/automations", json={"name": "t", "definition": {
        "if": {"all": [{"metric": "price.import", "op": "<", "value": 0}]},
        "then": [{"type": "override", "device": device, "action": action, "value": value, "duration_min": 30}]}})


# 1 ---------------------------------------------------------------------------------------------
async def test_01_heat_pump_shows_only_its_own_functions(demo):
    rt, c = demo
    hp = await _add(c, name="WP test", category="heat_pump", driver="test.hp_setpoint_only")
    await rt.tick_once()
    dev = (await c.get(f"/api/v1/devices/{hp}")).json()
    caps = {x["id"] for x in dev["capabilities"]}
    assert caps == {"read_flow_temp", "control_temp_setpoint"}
    acts = (await c.get(f"/api/v1/devices/{hp}/actions")).json()["actions"]
    assert [a["action"] for a in acts] == ["temp_setpoint"]
    assert not any(a["action"].startswith(("battery", "ev")) for a in acts)
    # PV_LIMIT on this heat pump is refused server-side: manual override and automation.
    r = await c.post("/api/v1/overrides", json={"device": hp, "action": "pv_limit", "value": 1000})
    assert r.status_code == 422
    assert (await _rule(c, hp, "pv_limit", 1000)).status_code == 422
    # The automation catalog offers this device nothing but its setpoint.
    cat = (await c.get("/api/v1/automations/catalog")).json()
    entry = next(d for d in cat["devices"] if d["id"] == hp)
    assert [a["action"] for a in entry["actions"]] == ["temp_setpoint"]


# 2 ---------------------------------------------------------------------------------------------
async def test_02_soc_limit_battery_has_no_charge_buttons(demo):
    rt, c = demo
    bat = await _add(c, name="Accu test", category="battery", driver="test.battery_soc_limit_only",
                     params={"capacity_kwh": 5})
    await rt.tick_once()
    acts = (await c.get(f"/api/v1/devices/{bat}/actions")).json()["actions"]
    assert [a["action"] for a in acts] == ["battery_soc_limit"]
    for action in ("battery_charge", "battery_discharge", "battery_auto"):
        r = await c.post("/api/v1/overrides", json={"device": bat, "action": action, "value": 1000})
        assert r.status_code == 422, action
    md = rt.devices.devices[bat]
    chk = can_execute(md, Command(bat, CommandAction.BATTERY_CHARGE, 1000), None, ControlState.FULL_CONTROL)
    assert not chk.allowed and "ondersteunt" in chk.reasons[0]
    # The controllers never emit a charge command for it either.
    await rt.tick_once()
    assert rt.engine.last_decisions.get(bat) is None


# 3 ---------------------------------------------------------------------------------------------
async def test_03_unmapped_modbus_device_claims_nothing(demo):
    rt, c = demo
    cls = default_registry.get("generic.modbus_tcp")
    assert cls.manifest.capabilities == frozenset()
    dev = await _add(c, name="Onbekend", category="battery", driver="generic.modbus_tcp",
                     connection={"host": "192.0.2.50", "port": 502, "unit_id": 1})
    view = (await c.get(f"/api/v1/devices/{dev}")).json()
    assert view["capabilities"] == []
    assert (await c.get(f"/api/v1/devices/{dev}/actions")).json()["actions"] == []
    com = (await c.get(f"/api/v1/devices/{dev}/commissioning")).json()
    assert not com["levels"]["shadow"]["allowed"]


# 4 ---------------------------------------------------------------------------------------------
async def test_04_automation_rejects_incompatible_actions_via_api(demo):
    rt, c = demo
    bat = next(d.id for d in rt.config.devices if d.category.value == "battery")
    assert (await _rule(c, bat, "hp_mode", "boost")).status_code == 422          # wrong device type
    assert (await _rule(c, bat, "battery_charge", 999999)).status_code == 422    # above the device limit
    assert (await _rule(c, "bestaat_niet", "battery_auto")).status_code == 422   # unknown device
    r = await _rule(c, bat, "battery_charge", 2000)
    assert r.status_code == 200, r.text
    # Also enforced when the rule fires (e.g. a rule saved before the device changed).
    msg = None
    try:
        await rt._run_automation_action({"type": "override", "device": bat, "action": "hp_mode", "value": "boost"},
                                        {"automation_id": 1, "name": "t"})
    except ValueError as exc:
        msg = str(exc)
    assert msg and "geweigerd" in msg


# 5 ---------------------------------------------------------------------------------------------
async def test_05_shadow_and_read_only_never_write(demo):
    rt, c = demo
    bat = next(d.id for d in rt.config.devices if d.category.value == "battery")
    drv = rt.devices.driver(bat)
    for level in ("shadow", "read_only"):
        assert (await c.put(f"/api/v1/devices/{bat}/commissioning", json={"level": level})).status_code == 200
        before = len(drv.applied)
        r = await c.post("/api/v1/overrides", json={"device": bat, "action": "battery_charge", "value": 2000})
        assert r.status_code == 200 and r.json()["executes"] is False
        for _ in range(3):
            await rt.tick_once()
        # A command arriving from another node (node import / remote controller) is gated the same way.
        gr = await rt.engine.gate.submit(Decision(Command(bat, CommandAction.BATTERY_CHARGE, 2000), "remote", [],
                                                  source="node:x"), rt.now(), requester="node-x")
        assert gr.outcome.value in ("shadow", "not_commissioned", "skipped")
        assert len(drv.applied) == before, level
        assert rt.engine.control_state(bat) in (ControlState.SHADOW, ControlState.READ_ONLY)
        await c.delete(f"/api/v1/overrides/{bat}")
    # A gateway node refuses to put its own non-writing device into limited/full for a controller.
    from ems.server.commissioning import node_level_problem
    dev = await _add(c, name="Alleen lezen", category="battery", driver="generic.modbus_tcp",
                     connection={"host": "192.0.2.51", "port": 502, "unit_id": 1})
    assert node_level_problem(rt, dev, "full")


# 6 ---------------------------------------------------------------------------------------------
def test_06_no_grid_meter_or_phases_disables_grid_functions():
    none = GridMeterSelection(None, GridMeterStatus.NO_PRIMARY_GRID_METER, None, "geen", [])
    f = feature_availability(none, False, False)
    assert not f["zero_export_closed_loop"]["available"] and not f["phase_balancing"]["available"]
    ok = GridMeterSelection("p1", GridMeterStatus.PRIMARY_EXPLICIT, "p1", "", [])
    f = feature_availability(ok, True, False)
    assert f["zero_export_closed_loop"]["available"] and not f["phase_balancing"]["available"]
    assert "fasestromen" in f["phase_balancing"]["reason"]
    f = feature_availability(ok, False, True)      # stale grid data
    assert not any(v["available"] for v in f.values())


async def test_06b_runtime_without_grid_meter_has_no_closed_loop(demo):
    rt, c = demo
    meter = rt.engine.grid_selection.device_id
    assert (await c.delete(f"/api/v1/devices/{meter}/primary-grid-meter")).status_code == 200
    data = rt.config.model_dump(mode="json")
    for d in data["devices"]:
        if d["id"] == meter:
            d["enabled"] = False
    await rt.reload(data, "test", "netmeter uit")
    await rt.tick_once()
    feats = rt.engine.last_snapshot.features
    assert not feats["zero_export_closed_loop"]["available"] and not feats["phase_balancing"]["available"]
    for d in rt.engine.last_decisions.values():
        assert "regeling op netmeter" not in " ".join(d.get("reasons") or [])


# 18 --------------------------------------------------------------------------------------------
async def test_18_hardware_writes_need_commissioning_and_documentation(tmp_path):
    rt = EMSRuntime(tmp_path / "prod", mode="production", env={})
    await rt.start(loops=False)
    app = create_app(rt, start_runtime=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        r = await c.post("/api/v1/auth/setup", json={"username": "beheer", "password": "geheim-wachtwoord"})
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        dev = await _add(c, name="Thuisaccu", category="battery", driver="test.undocumented_writer",
                         params={"capacity_kwh": 10, "max_charge_w": 3000, "max_discharge_w": 3000})
        assert rt.config.device(dev).control_level == "read_only"            # new devices never write
        await rt.tick_once()
        assert (await c.post(f"/api/v1/devices/{dev}/test")).json()["reachable"]
        assert (await c.put(f"/api/v1/devices/{dev}/commissioning", json={"level": "shadow"})).status_code == 200
        for _ in range(2):
            await rt.tick_once()
        com = (await c.get(f"/api/v1/devices/{dev}/commissioning")).json()
        assert not com["levels"]["limited"]["allowed"]
        assert "gedocumenteerde" in com["levels"]["limited"]["reason"]
        r = await c.put(f"/api/v1/devices/{dev}/commissioning", json={"level": "full", "confirm_text": "Thuisaccu"})
        assert r.status_code == 409
        r = await c.post("/api/v1/overrides", json={"device": dev, "action": "battery_charge", "value": 1000})
        assert r.status_code == 200 and r.json()["executes"] is False
        for _ in range(2):
            await rt.tick_once()
        assert UndocumentedWriter.writes == []                                # nothing reached the device
    finally:
        await c.aclose()
        await rt.stop()


def test_confirmation_tracker_states():
    from ems.control.authority import ConfirmationTracker
    t = ConfirmationTracker(timeout_s=60)
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    cmd = Command("bat", CommandAction.BATTERY_CHARGE, 2000)
    t.record(cmd, cmd, "sent", now)
    t.evaluate({"bat": {Metric.BATTERY_POWER_W: 1900}}, now)
    assert t.for_device("bat")[0]["status"] == "confirmed"
    t.record(cmd, cmd, "sent", now)
    t.evaluate({"bat": {Metric.BATTERY_POWER_W: 0}}, now)
    assert t.for_device("bat")[0]["status"] == "sent"
    t.evaluate({"bat": {Metric.BATTERY_POWER_W: 0}}, now.replace(minute=5))
    assert t.for_device("bat")[0]["status"] == "unconfirmed"
    hp = Command("hp", CommandAction.HP_MODE, "boost")
    t.record(hp, hp, "sent", now)
    t.evaluate({"hp": {}}, now)
    assert t.for_device("hp")[0]["status"] == "no_feedback"


# P0-06 ------------------------------------------------------------------------------------------
async def test_p0_06_cookie_session_csrf_rotation_logout(tmp_path, monkeypatch):
    rt = EMSRuntime(tmp_path / "auth", mode="demo", env={})
    await rt.start(loops=False)
    app = create_app(rt, start_runtime=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        r = await c.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})
        sc = r.headers.get_list("set-cookie")
        session = next(x for x in sc if x.startswith("ems_session="))
        assert "HttpOnly" in session and "samesite=strict" in session.lower()
        csrf = r.json()["csrf"]
        # Cookie alone works for reads ...
        assert (await c.get("/api/v1/auth/me")).status_code == 200
        # ... but a state change without the CSRF header is refused (cross-site request).
        assert (await c.put("/api/v1/settings", json={"site": {"name": "x"}})).status_code == 403
        r = await c.put("/api/v1/settings", json={"site": {"name": "x"}}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        # Sliding rotation: an old session gets a fresh cookie.
        import ems.api.deps as deps
        monkeypatch.setattr(deps, "SESSION_ROTATE_S", -1)
        r = await c.get("/api/v1/auth/me")
        assert any(x.startswith("ems_session=") for x in r.headers.get_list("set-cookie"))
        monkeypatch.setattr(deps, "SESSION_ROTATE_S", 10**6)
        # Logout revokes the session server-side and clears the cookies.
        old = c.cookies.get("ems_session")
        assert (await c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})).status_code == 200
        assert (await c.get("/api/v1/auth/me")).status_code == 401
        anon = httpx.AsyncClient(transport=c._transport, base_url="http://test")
        assert (await anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old}"})).status_code == 401
        # Downloads no longer accept ?token= in the URL.
        assert (await anon.get(f"/api/v1/history/export?token={old}")).status_code == 401
        await anon.aclose()
    finally:
        await c.aclose()
        await rt.stop()


def test_p0_06_frontend_keeps_no_tokens():
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "backend" / "ems" / "web" / "js"
    src = "\n".join(p.read_text() for p in web.rglob("*.js"))
    assert "localStorage.setItem(\"ems.token\"" not in src and "ems.token" not in src
    assert "?token=" not in src and "Authorization" not in src
