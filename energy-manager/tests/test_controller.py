from datetime import timedelta

import pytest

from conftest import local, make_config
from ems.control.base import ControlContext
from ems.control.self_consumption import SelfConsumptionController
from ems.core.models import Capability, CommandAction, DeviceCategory, DeviceState, DeviceStatus, Metric
from ems.core.snapshot import build_snapshot

DEVICES = [
    {"id": "p1", "name": "Meter", "category": "smart_meter", "driver": "mock.smart_meter"},
    {"id": "bat", "name": "Batterij", "category": "battery", "driver": "mock.battery",
     "params": {"capacity_kwh": 10}},
    {"id": "ev", "name": "Laadpaal", "category": "ev_charger", "driver": "mock.ev_charger",
     "params": {"charge_mode": "pv_only", "min_current_a": 6, "max_current_a": 16}},
]
CAPS = {"bat": frozenset({Capability.CONTROL_BATTERY_MODE}), "ev": frozenset({Capability.CONTROL_EV_CURRENT})}


def cfg_with(mode="pv_only", **sections):
    devices = [dict(d) for d in DEVICES]
    devices[2] = {**devices[2], "params": {**devices[2]["params"], "charge_mode": mode}}
    return make_config(devices, **sections)


def snap(cfg, now, grid_w, currents=(0.0, 0.0, 0.0), ev_w=0.0, connected=True, bat_w=0.0, soc=50.0):
    on = DeviceStatus.ONLINE
    states = {
        "p1": DeviceState("p1", DeviceCategory.SMART_METER, on, {
            Metric.GRID_POWER_W: grid_w, Metric.GRID_CURRENT_L1_A: currents[0],
            Metric.GRID_CURRENT_L2_A: currents[1], Metric.GRID_CURRENT_L3_A: currents[2]}),
        "bat": DeviceState("bat", DeviceCategory.BATTERY, on,
                           {Metric.BATTERY_POWER_W: bat_w, Metric.BATTERY_SOC_PCT: soc}),
        "ev": DeviceState("ev", DeviceCategory.EV_CHARGER, on,
                          {Metric.EV_POWER_W: ev_w, Metric.EV_CONNECTED: connected}),
    }
    return build_snapshot(cfg, states, now)


def ev_decision(ctrl, cfg, s):
    decisions = ctrl.decide(s, ControlContext(cfg, s.timestamp, CAPS))
    evs = [d for d in decisions if d.command.device_id == "ev"]
    return evs[0] if evs else None


T0 = local(2026, 6, 1, 12, 0)


def test_battery_runs_native_self_consumption():
    cfg = cfg_with()
    decisions = SelfConsumptionController().decide(snap(cfg, T0, 0.0), ControlContext(cfg, T0, CAPS))
    bat = [d for d in decisions if d.command.device_id == "bat"][0]
    assert bat.command.action == CommandAction.BATTERY_AUTO
    assert any("SOC 50%" in r for r in bat.reasons)


def test_pv_only_waits_for_stable_surplus_then_starts():
    cfg, ctrl = cfg_with(), SelfConsumptionController()
    d = ev_decision(ctrl, cfg, snap(cfg, T0, grid_w=-6000))
    assert d.command.value == 0 and "wacht" in d.reasons[0]
    d = ev_decision(ctrl, cfg, snap(cfg, T0 + timedelta(seconds=30), grid_w=-6000))
    assert d.command.value == 0
    d = ev_decision(ctrl, cfg, snap(cfg, T0 + timedelta(seconds=60), grid_w=-6000))
    assert d.command.value == 8  # 6000 W / (3 x 230 V) = 8.7 A -> 8 A
    assert "gestart" in d.reasons[0]
    assert d.data["surplus_w"] == 6000


def test_pv_only_hysteresis_holds_minimum_then_stops():
    cfg, ctrl = cfg_with(), SelfConsumptionController()
    for i in range(7):
        ev_decision(ctrl, cfg, snap(cfg, T0 + timedelta(seconds=10 * i), grid_w=-7000))
    t = T0 + timedelta(seconds=70)
    values = []
    for i in range(30):  # surplus collapses: EV draws 4140 W but grid imports 3000 W
        d = ev_decision(ctrl, cfg, snap(cfg, t + timedelta(seconds=10 * i), grid_w=3000, ev_w=4140))
        values.append(d.command.value)
    assert values[0] >= 6
    assert values[5] == 6  # held at minimum (no flapping)
    assert values[-1] == 0  # stopped after the stop delay
    first_zero = values.index(0)
    assert first_zero * 10 >= SelfConsumptionController.STOP_DELAY_S


def test_battery_discharge_is_not_surplus():
    cfg, ctrl = cfg_with(), SelfConsumptionController()
    # Grid ~0 because the battery is discharging into the car.
    for i in range(10):
        d = ev_decision(ctrl, cfg, snap(cfg, T0 + timedelta(seconds=10 * i), grid_w=0, ev_w=4140, bat_w=-3500))
    assert d.data["surplus_w"] == pytest.approx(640)
    assert d.command.value == 0


def test_phase_guard_limits_and_pauses():
    cfg, ctrl = cfg_with("max"), SelfConsumptionController()
    d = ev_decision(ctrl, cfg, snap(cfg, T0, grid_w=5000, currents=(12.0, 5.0, 5.0)))
    assert d.command.value == 11  # 23.75 A limit - 12 A
    assert any("Fasebewaking" in r for r in d.reasons)
    d = ev_decision(ctrl, cfg, snap(cfg, T0, grid_w=5000, currents=(22.0, 5.0, 5.0)))
    assert d.command.value == 0
    # The EV's own current is not counted against itself.
    d = ev_decision(ctrl, cfg, snap(cfg, T0, grid_w=11040, currents=(16.0, 16.0, 16.0), ev_w=11040))
    assert d.command.value == 16


def test_import_limit_caps_current():
    cfg, ctrl = cfg_with("max", grid={"max_import_kw": 8}), SelfConsumptionController()
    d = ev_decision(ctrl, cfg, snap(cfg, T0, grid_w=2000, currents=(3.0, 3.0, 3.0)))
    assert d.command.value == 8  # (8000 - 2000) / 690 = 8.7 A


def test_min_pv_mode():
    cfg, ctrl = cfg_with("min_pv"), SelfConsumptionController()
    assert ev_decision(ctrl, cfg, snap(cfg, T0, grid_w=500)).command.value == 6
    assert ev_decision(ctrl, cfg, snap(cfg, T0, grid_w=-8000)).command.value == 11


def test_disconnected_ev_gets_no_command():
    cfg = cfg_with()
    assert ev_decision(SelfConsumptionController(), cfg, snap(cfg, T0, grid_w=-9000, connected=False)) is None
