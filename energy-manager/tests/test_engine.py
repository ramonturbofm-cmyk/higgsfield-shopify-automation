"""Engine, gate, overrides, fail-safe and failure-injection tests."""

from dataclasses import replace
from datetime import timedelta

import pytest

from conftest import BASE_DEVICES, local, make_config, make_engine, step
from ems.control.base import Controller
from ems.control.gate import GateMode, Outcome
from ems.core.clock import SimulatedClock
from ems.core.events import EventBus
from ems.core.models import Command, CommandAction, DeviceStatus
from ems.core.watchdog import Watchdog
from ems.devices.registry import DriverRegistry
from ems.integrations.mock import (
    FaultMode,
    MockBattery,
    MockEVCharger,
    MockHeatPump,
    MockSmartMeter,
    MockSolarInverter,
)
from ems.simulator.components import SimBattery

NOON = local(2026, 6, 15, 12, 0)


async def test_self_consumption_tick_journals_explained_decisions():
    engine, site, clock = await make_engine(make_config(), NOON)
    result = await step(engine, site, clock)
    assert not result.failsafe and result.snapshot.grid_valid
    sent = {r.decision.command.device_id: r for r in result.results if r.outcome == Outcome.SENT}
    assert sent["bat"].decision.command.action == CommandAction.BATTERY_AUTO
    entry = next(e for e in engine.journal.recent if e.device == "bat")
    assert entry.reasons and entry.run_id and entry.site_id == "home"
    assert "Reden:" in entry.render_nl()
    # Identical decisions are not re-sent every tick.
    result = await step(engine, site, clock)
    assert all(r.outcome == Outcome.SKIPPED for r in result.results if r.decision.command.device_id == "bat")


async def test_snapshot_aggregates_site():
    engine, site, clock = await make_engine(make_config(), NOON)
    snap = (await step(engine, site, clock)).snapshot
    assert snap.pv_power_w > 1000
    assert snap.battery_soc_pct == pytest.approx(50, abs=1)
    expected_house = snap.grid_power_w + snap.pv_power_w - snap.battery_power_w - snap.hp_power_w - snap.ev_power_w
    assert snap.house_load_w == pytest.approx(max(0.0, expected_house))
    assert snap.house_load_w == pytest.approx(site.components["__base_load__"].output_w, abs=1.0)


async def test_dry_run_never_touches_devices():
    cfg = make_config(env={"DRY_RUN": "true"})
    engine, site, clock = await make_engine(cfg, NOON)
    assert engine.gate.mode == GateMode.DRY_RUN
    for _ in range(12):
        await step(engine, site, clock)
    for dev in ("bat", "ev"):
        assert engine.devices.driver(dev).applied == []
    dry = [e for e in engine.journal.recent if e.outcome == "dry_run"]
    assert dry and dry[0].render_nl().split(": ", 1)[0].endswith("EMS zou")


async def test_simulation_mode_blocks_real_hardware():
    real_manifest = replace(MockBattery.manifest, driver_id="test.real_battery", simulated=False)

    class RealBattery(MockBattery):
        manifest = real_manifest

    reg = DriverRegistry()
    reg._discovered = True
    for cls in (MockSmartMeter, MockSolarInverter, MockHeatPump, MockEVCharger, RealBattery):
        reg.register(cls)
    devices = [dict(d) for d in BASE_DEVICES]
    devices[2] = {**devices[2], "driver": "test.real_battery"}
    engine, site, clock = await make_engine(
        make_config(devices), NOON, registry=reg,
        prepare_site=lambda s: s.add(SimBattery(id="bat", capacity_kwh=10)))  # stand-in plant
    result = await step(engine, site, clock)
    bat = next(r for r in result.results if r.decision.command.device_id == "bat")
    assert bat.outcome == Outcome.BLOCKED
    assert engine.devices.driver("bat").applied == []


async def test_meter_offline_triggers_failsafe_and_recovers():
    engine, site, clock = await make_engine(make_config(), NOON)
    engine.overrides.set(Command("bat", CommandAction.BATTERY_CHARGE, 3000), duration_min=None)
    await step(engine, site, clock)
    assert site.components["bat"].mode == "charge"
    site.components["ev"].current_setpoint_a = 6

    engine.devices.driver("p1").inject_fault(FaultMode.OFFLINE)
    results = [await step(engine, site, clock) for _ in range(5)]
    assert not results[0].failsafe            # tolerate short gaps
    assert results[-1].failsafe and engine.failsafe_active
    assert "p1" in engine.failsafe_reason
    assert site.components["bat"].mode == "auto"            # released to native
    assert site.components["ev"].current_setpoint_a is None
    assert any(e.action == "failsafe" for e in engine.journal.recent)

    engine.devices.driver("p1").inject_fault(FaultMode.NONE)
    for _ in range(6):
        result = await step(engine, site, clock)
    assert not engine.failsafe_active and not result.failsafe
    assert any(e.action == "failsafe_cleared" for e in engine.journal.recent)
    assert site.components["bat"].mode == "charge"           # override resumes after recovery
    assert engine.failsafe_events == 1


@pytest.mark.parametrize("fault", [FaultMode.GARBAGE, FaultMode.FROZEN])
async def test_bad_meter_data_triggers_failsafe(fault):
    cfg = make_config(control={"frozen_after_s": 60})
    engine, site, clock = await make_engine(cfg, NOON)
    await step(engine, site, clock)
    engine.devices.driver("p1").inject_fault(fault)
    for _ in range(12):
        result = await step(engine, site, clock)
    assert result.failsafe
    state = result.snapshot.devices["p1"]
    if fault == FaultMode.GARBAGE:
        assert state.rejected
    else:
        assert state.status == DeviceStatus.STALE and "bevroren" in state.error


async def test_slow_device_times_out_without_blocking():
    cfg = make_config(control={"device_timeout_s": 0.05})
    engine, site, clock = await make_engine(cfg, NOON)
    drv = engine.devices.driver("hp")
    drv.slow_delay_s = 2.0
    drv.inject_fault(FaultMode.SLOW)
    result = await step(engine, site, clock)
    assert result.snapshot.devices["hp"].error == "timeout"
    assert not result.failsafe                    # non-critical device: EMS keeps working
    assert result.snapshot.grid_valid


async def test_failed_command_is_reported_and_retried():
    engine, site, clock = await make_engine(make_config(), NOON)
    engine.devices.driver("bat").inject_fault(FaultMode.REJECT_COMMANDS)
    result = await step(engine, site, clock)
    bat = next(r for r in result.results if r.decision.command.device_id == "bat")
    assert bat.outcome == Outcome.FAILED and bat.error
    engine.devices.driver("bat").inject_fault(FaultMode.NONE)
    result = await step(engine, site, clock)
    bat = next(r for r in result.results if r.decision.command.device_id == "bat")
    assert bat.outcome == Outcome.SENT


async def test_controller_crash_triggers_failsafe():
    class Broken(Controller):
        name = "broken"

        def decide(self, snap, ctx):
            raise RuntimeError("bug")

    engine, site, clock = await make_engine(make_config(), NOON, controller=Broken())
    result = await step(engine, site, clock)
    assert result.failsafe and "broken" in engine.failsafe_reason


async def test_override_expires_back_to_auto():
    engine, site, clock = await make_engine(make_config(), NOON)
    engine.overrides.set(Command("bat", CommandAction.BATTERY_DISCHARGE, 2500), duration_min=30, user="ramon")
    await step(engine, site, clock)
    assert site.components["bat"].mode == "discharge"
    entry = [e for e in engine.journal.recent if e.device == "bat"][-1]
    assert entry.source == "override" and "ramon" in entry.reasons[0]
    clock.advance(30 * 60)
    site.advance(1)
    await step(engine, site, clock)
    assert site.components["bat"].mode == "auto"
    assert any(e.action == "override_expired" for e in engine.journal.recent)
    assert not engine.overrides.active()


async def test_stop_releases_devices():
    engine, site, clock = await make_engine(make_config(), NOON)
    engine.overrides.set(Command("bat", CommandAction.BATTERY_STANDBY), duration_min=60)
    await step(engine, site, clock)
    assert site.components["bat"].mode == "standby"
    await engine.stop()
    assert site.components["bat"].mode == "auto"


async def test_watchdog_releases_on_stalled_loop():
    clock = SimulatedClock(NOON)
    beat = {"t": clock.now()}
    calls = []

    async def release():
        calls.append(clock.now())

    wd = Watchdog(lambda: beat["t"], clock, timeout_s=30, on_timeout=release)
    assert await wd.check()
    clock.advance(31)
    assert not await wd.check()
    assert not await wd.check()     # trips once, not on every check
    assert len(calls) == 1
    beat["t"] = clock.now()
    assert await wd.check() and not wd.tripped


async def test_event_bus_isolates_broken_subscribers():
    bus = EventBus()
    seen = []
    bus.subscribe("snapshot", lambda topic, p: 1 / 0)
    bus.subscribe("*", lambda topic, p: seen.append(topic))
    await bus.publish("snapshot", {})
    assert seen == ["snapshot"]


async def test_run_forever_survives_and_stops():
    import asyncio

    engine, site, clock = await make_engine(make_config(), NOON)
    stop = asyncio.Event()
    ticks = 0
    original = engine.tick

    async def counting_tick():
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            raise RuntimeError("transient")
        if ticks == 4:
            stop.set()
        return await original()

    engine.tick = counting_tick
    await asyncio.wait_for(engine.run_forever(stop), timeout=5)
    assert ticks == 4
    assert engine.failsafe_events == 1
    assert clock.now() - NOON >= timedelta(seconds=30)


async def test_user_facing_times_are_local():
    engine, site, clock = await make_engine(make_config(), NOON)
    engine.overrides.set(Command("bat", CommandAction.BATTERY_STANDBY), duration_min=30)
    await step(engine, site, clock)
    entry = [e for e in engine.journal.recent if e.device == "bat"][-1]
    assert "tot 12:30" in entry.reasons[0]                      # NOON is 12:00 Amsterdam
    assert entry.render_nl(engine.tz).startswith("2026-06-15 12:00:00")
