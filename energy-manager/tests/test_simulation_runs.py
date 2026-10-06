"""End-to-end: real engine + controllers against the simulated house."""

from datetime import timedelta

import pytest

from conftest import BASE_DEVICES, EXAMPLE_CONFIG, local, make_config
from ems.control.base import NativeController
from ems.core.config import load_config
from ems.integrations.mock import FaultMode
from ems.simulator.__main__ import main
from ems.simulator.runner import SimulationRunner


async def test_example_config_day_runs_clean(example_config):
    runner = SimulationRunner(example_config, local(2026, 6, 15), step_s=10)
    result = await runner.run(timedelta(days=1))
    s = result.summary
    assert s.failsafe_events == 0
    assert s.phase_overload_s == 0
    assert 0 < s.commands_sent < 60           # de-duplication works
    assert s.pv_kwh > 20
    balance = s.base_load_kwh + s.heat_pump_kwh + s.ev_kwh + s.battery_charged_kwh - s.battery_discharged_kwh - s.pv_kwh
    assert s.import_kwh - s.export_kwh == pytest.approx(balance, abs=1e-3)
    assert len(result.rows) == 24 * 12


async def test_winter_comfort_is_kept():
    cfg = load_config(EXAMPLE_CONFIG, env={})
    result = await SimulationRunner(cfg, local(2026, 1, 20), step_s=30).run(timedelta(days=2))
    s = result.summary
    assert s.heat_pump_kwh > 10
    assert s.comfort_deficit_kh < 0.5
    assert s.indoor_temp_min_c > 20.0


async def test_dynamic_load_balancing_prevents_phase_overload():
    devices = [dict(d) for d in BASE_DEVICES]
    devices[4] = {**devices[4], "params": {"charge_mode": "max", "max_current_a": 16,
                                           "sim": {"arrive": "00:00", "depart": "23:59", "arrival_soc_pct": 5,
                                                   "battery_kwh": 100}}}
    devices[3] = {**devices[3], "params": {"sim": {"thermal_max_w": 9000, "heat_loss_w_per_k": 260}}}
    cfg = make_config(devices, grid={"phases": 3, "ampere_per_phase": 16})
    start, day = local(2026, 1, 20), timedelta(days=1)
    native = (await SimulationRunner(cfg, start, controller=NativeController(), step_s=10).run(day)).summary
    managed = (await SimulationRunner(cfg, start, step_s=10).run(day)).summary
    assert native.phase_overload_s > 1800                 # without EMS the fuse is overloaded for >30 min
    assert managed.phase_overload_s < native.phase_overload_s * 0.1
    assert managed.ev_kwh > 0.6 * native.ev_kwh           # while still charging most of the energy


async def test_scheduled_meter_outage_enters_and_leaves_failsafe(example_config):
    start = local(2026, 6, 15, 10)
    runner = SimulationRunner(example_config, start, step_s=10, record_every_s=60)
    meter = lambda r: r.devices.driver("p1")  # noqa: E731
    runner.at(start + timedelta(minutes=30), lambda r: meter(r).inject_fault(FaultMode.OFFLINE))
    runner.at(start + timedelta(minutes=90), lambda r: meter(r).inject_fault(FaultMode.NONE))
    result = await runner.run(timedelta(hours=3))
    flags = [row["failsafe"] for row in result.rows]
    assert result.summary.failsafe_events == 1
    assert not any(flags[:30]) and any(flags[30:95]) and not any(flags[110:])
    actions = [e.action for e in result.journal.recent]
    assert "failsafe" in actions and "failsafe_cleared" in actions


async def test_dry_run_simulation_sends_nothing():
    cfg = load_config(EXAMPLE_CONFIG, env={"DRY_RUN": "true"})
    result = await SimulationRunner(cfg, local(2026, 6, 15, 8), step_s=10).run(timedelta(hours=8))
    assert result.summary.commands_sent == 0
    assert any(e.outcome == "dry_run" for e in result.journal.recent)


def test_cli_writes_outputs(tmp_path, capsys):
    rc = main(["--config", str(EXAMPLE_CONFIG), "--start", "2026-06-15", "--days", "0.25",
               "--step", "30", "--compare-native", "--out", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Simulatie (self_consumption)" in out and "Vergelijking" in out
    for name in ("timeseries-self_consumption.csv", "summary-self_consumption.json",
                 "timeseries-native.csv", "summary-native.json"):
        assert (tmp_path / name).stat().st_size > 0
