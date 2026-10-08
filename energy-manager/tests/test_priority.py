"""Command priority scheduling and grid protection."""

from datetime import UTC, datetime

from ems.control.priority import CommandPriority, GridGuard, priority_of, schedule
from ems.core.config import config_from_dict
from ems.core.models import Command, CommandAction, Decision, DeviceCategory, DeviceState, DeviceStatus, Metric
from ems.core.snapshot import SiteSnapshot


def d(action, value, source, dev="bat"):
    return Decision(Command(dev, action, value), "x", ["r"], source=source)


def test_priority_order_and_explanation():
    assert priority_of("grid_protection") < priority_of("override") < priority_of("controller:optimizer") \
        < priority_of("automation") < priority_of("unknown")
    assert priority_of("safety") == CommandPriority.SAFETY
    out = schedule([d(CommandAction.BATTERY_CHARGE, 3000, "controller:optimizer"),
                    d(CommandAction.BATTERY_DISCHARGE, 2000, "automation"),
                    d(CommandAction.BATTERY_STANDBY, None, "override")])
    assert len(out) == 1 and out[0].command.action == CommandAction.BATTERY_STANDBY
    assert "optimizer" in out[0].reasons[-1] and "automatisering" in out[0].reasons[-1]
    # Automation only acts where the optimizer has nothing planned.
    out = schedule([d(CommandAction.BATTERY_CHARGE, 3000, "controller:optimizer"),
                    d(CommandAction.HP_MODE, "eco", "automation", dev="hp")])
    assert {o.command.device_id for o in out} == {"bat", "hp"}


def _cfg():
    return config_from_dict({"grid": {"phases": 3, "ampere_per_phase": 25, "phase_safety_margin_pct": 0},
                             "devices": [
                                 {"id": "bat", "name": "B", "category": "battery", "driver": "mock.battery"},
                                 {"id": "ev", "name": "E", "category": "ev_charger", "driver": "mock.ev_charger",
                                  "params": {"min_current_a": 6, "max_current_a": 16}}]}, env={})


def _snap(grid_w, ev_w=0.0, bat_w=0.0):
    now = datetime(2026, 1, 15, 18, tzinfo=UTC)
    devs = {"bat": DeviceState("bat", DeviceCategory.BATTERY, DeviceStatus.ONLINE, {Metric.BATTERY_POWER_W: bat_w}),
            "ev": DeviceState("ev", DeviceCategory.EV_CHARGER, DeviceStatus.ONLINE, {Metric.EV_POWER_W: ev_w})}
    snap = SiteSnapshot(now, "home", devs)
    snap.grid_valid, snap.grid_power_w = True, grid_w
    return snap


def test_grid_guard_reduces_flexible_loads_first_ev_then_battery():
    guard = GridGuard(_cfg())          # 3 x 25 A x 230 V = 17.25 kW
    # House already imports 12 kW; manual EV 16 A (11 kW) + optimizer battery charge 5 kW would be 28 kW.
    out = guard.apply([d(CommandAction.EV_CURRENT, 16, "override", dev="ev"),
                       d(CommandAction.BATTERY_CHARGE, 5000, "controller:optimizer")], _snap(12000))
    ev, bat = out
    ev_w = ev.command.value * 230 * 3
    assert ev.source == "grid_protection" and ev.command.value == 0          # 16 A does not fit; below 6 A -> pause
    assert 12000 + ev_w + bat.command.value <= 17250
    assert bat.command.value == 5000 or bat.source == "grid_protection"
    assert any("Netbeveiliging" in r for r in ev.reasons)
    # Enough headroom: nothing changes.
    same = guard.apply([d(CommandAction.EV_CURRENT, 10, "controller:optimizer", dev="ev")], _snap(2000))
    assert same[0].command.value == 10 and same[0].source == "controller:optimizer"


def test_grid_guard_battery_trimmed_and_inactive_without_grid_meter():
    guard = GridGuard(_cfg())
    out = guard.apply([d(CommandAction.BATTERY_CHARGE, 6000, "override")], _snap(15000))
    assert out[0].command.value == 2250 and out[0].source == "grid_protection"
    snap = _snap(15000)
    snap.grid_valid = False
    assert guard.apply([d(CommandAction.BATTERY_CHARGE, 6000, "override")], snap)[0].command.value == 6000
