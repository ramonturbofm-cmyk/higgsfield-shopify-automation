"""Audit P0-07/P0-08: every command type through the real command path under every fault situation.

Path under test: CommandGate (commissioning level → limited scaling → SafetyValidator → shadow →
control lease → dedupe → gate mode → driver). Expected outcome per (command, situation) is explicit.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from ems.control.gate import Outcome
from ems.control.safety import is_release
from ems.core.models import Command, CommandAction, Decision, Metric
from ems.integrations.mock.drivers import FaultMode
from ems.server.runtime import EMSRuntime

A = CommandAction
COMMANDS = {
    "battery_charge": ("battery", A.BATTERY_CHARGE, 2000, False),
    "battery_discharge": ("battery", A.BATTERY_DISCHARGE, 2000, False),
    "battery_standby": ("battery", A.BATTERY_STANDBY, None, False),
    "battery_auto": ("battery", A.BATTERY_AUTO, None, True),          # release
    "pv_limit": ("pv_roof", A.PV_LIMIT, 1000, False),
    "pv_unlimited": ("pv_roof", A.PV_LIMIT, None, True),              # release
    "ev_current": ("ev", A.EV_CURRENT, 10, False),
    "hp_boost": ("heatpump", A.HP_MODE, "boost", False),
    "hp_normal": ("heatpump", A.HP_MODE, "normal", True),             # release
}
WRITTEN = {Outcome.SENT, Outcome.REFRESHED}


@pytest.fixture
async def rt(tmp_path):
    r = EMSRuntime(tmp_path / "m", mode="demo", env={})
    await r.start(loops=False)
    await r.tick_once()
    yield r
    await r.stop()


def _prepare(rt, device_id: str):
    rt.engine.gate.reset()
    rt.engine.gate.safety.forget()
    md = rt.devices.devices[device_id]
    md.connected, md.last_ok = True, rt.now()
    return md


async def _submit(rt, device_id, action, value, requester=None):
    d = Decision(Command(device_id, action, value), "test", [], source="test")
    return await rt.engine.gate.submit(d, rt.now(), requester=requester)


@pytest.mark.parametrize("name", list(COMMANDS))
@pytest.mark.parametrize("situation", ["full", "limited", "shadow", "read_only", "connection_test",
                                       "offline", "stale", "lease_refused"])
async def test_command_matrix(rt, name, situation):
    device_id, action, value, release = COMMANDS[name]
    md = _prepare(rt, device_id)
    if device_id == "battery":
        rt.site.component("battery").soc_pct = 50.0
        md.last_values[Metric.BATTERY_SOC_PCT] = 50.0
    level = situation if situation in ("full", "limited", "shadow", "read_only", "connection_test") else "full"
    rt.engine.gate.levels = lambda _d, lvl=level: (lvl, 0.5)
    rt.engine.gate.owner_guard = None
    if situation == "offline":
        md.connected = False
    elif situation == "stale":
        md.last_ok = rt.now() - timedelta(minutes=10)
    elif situation == "lease_refused":
        rt.engine.gate.owner_guard = lambda cmd, req: "geen regelrecht (lease bij andere controller)"
    applied = len(rt.devices.driver(device_id).applied)
    gr = await _submit(rt, device_id, action, value, requester="node-other" if situation == "lease_refused" else None)

    if situation in ("read_only", "connection_test"):
        expected = {Outcome.NOT_COMMISSIONED}
    elif situation == "offline":
        expected = {Outcome.REJECTED}                                   # nothing reaches a disconnected device
    elif situation == "stale":
        expected = WRITTEN if release else {Outcome.REJECTED}           # releasing is always allowed
    elif situation == "shadow":
        expected = {Outcome.SHADOW}
    elif situation == "lease_refused":
        expected = WRITTEN if release else {Outcome.REJECTED}
    else:
        expected = WRITTEN
    assert gr.outcome in expected, (name, situation, gr.outcome, gr.error)
    wrote = len(rt.devices.driver(device_id).applied) > applied
    assert wrote == (gr.outcome in WRITTEN), (name, situation)
    if situation == "limited" and action in (A.BATTERY_CHARGE, A.BATTERY_DISCHARGE):
        assert gr.decision.command.value == 1000                        # 50 % of the request


async def test_limits_capability_and_soc(rt):
    rt.engine.gate.levels = lambda _d: ("full", 1.0)
    md = _prepare(rt, "battery")
    md.last_values[Metric.BATTERY_SOC_PCT] = 50.0
    gr = await _submit(rt, "battery", A.BATTERY_CHARGE, 99999)
    assert gr.outcome in WRITTEN and gr.decision.command.value == 6000       # clamped to the device limit
    _prepare(rt, "battery")
    md.last_values[Metric.BATTERY_SOC_PCT] = 99.0
    assert (await _submit(rt, "battery", A.BATTERY_CHARGE, 1000)).outcome == Outcome.REJECTED   # full
    md.last_values[Metric.BATTERY_SOC_PCT] = 5.0
    assert (await _submit(rt, "battery", A.BATTERY_DISCHARGE, 1000)).outcome == Outcome.REJECTED  # reserve
    md.last_values.pop(Metric.BATTERY_SOC_PCT)
    assert (await _submit(rt, "battery", A.BATTERY_CHARGE, 1000)).outcome == Outcome.REJECTED   # SOC unknown
    _prepare(rt, "battery")
    assert (await _submit(rt, "battery", A.HP_MODE, "boost")).outcome == Outcome.REJECTED      # no capability
    _prepare(rt, "ev")
    gr = await _submit(rt, "ev", A.EV_CURRENT, 40)
    assert gr.decision.command.value == 16                                   # charger maximum
    _prepare(rt, "ev")
    assert (await _submit(rt, "ev", A.EV_CURRENT, 3)).outcome == Outcome.REJECTED              # below minimum


async def test_rate_limit(rt):
    rt.engine.gate.levels = lambda _d: ("full", 1.0)
    md = _prepare(rt, "battery")
    md.last_values[Metric.BATTERY_SOC_PCT] = 50.0
    assert (await _submit(rt, "battery", A.BATTERY_CHARGE, 1000)).outcome in WRITTEN
    gr = await _submit(rt, "battery", A.BATTERY_CHARGE, 3000)
    assert gr.outcome == Outcome.REJECTED and "te snel" in gr.error


async def test_stale_grid_meter_releases_everything_and_blocks_control(rt):
    """P0-08: with stale/missing grid data the engine never steers on old data: it falls back
    to the devices' own regulation and only sends release commands."""
    for _ in range(2):
        await rt.tick_once()
    rt.devices.driver("p1").inject_fault(FaultMode.OFFLINE)
    before = {d: len(rt.devices.driver(d).applied) for d in ("battery", "ev", "heatpump", "pv_roof")}
    for _ in range(8):
        await rt.tick_once()
    assert rt.engine.failsafe_active
    for d, n in before.items():
        for cmd in rt.devices.driver(d).applied[n:]:
            assert is_release(cmd), (d, cmd)       # only hand-backs, never a steering command
    assert rt.engine.control_state("battery").value == "safe_mode"
    rt.devices.driver("p1").inject_fault(FaultMode.NONE)
