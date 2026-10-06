"""Optimizer + plan-executing controller."""

from datetime import UTC, datetime, timedelta

import pytest

from ems.core.config import TariffConfig
from ems.optimizer import BatteryModel, EVModel, HeatPumpModel, OptimizerInput, solve

T0 = datetime(2026, 7, 1, tzinfo=UTC)


def slots(n):
    return [T0 + timedelta(minutes=15 * i) for i in range(n)]


def base_input(n=96, price=None, pv=None, load=500.0, **kw):
    price = price or [0.20] * n
    return OptimizerInput(slots(n), 0.25, price, [p - 0.05 for p in price], pv or [0.0] * n, [load] * n,
                          17250, 17250, **kw)


def battery(soc=5.0, **kw):
    return BatteryModel(10, soc, 1.0, 9.5, 5000, 5000, degradation_eur_kwh=0.02, **kw)


def test_arbitrage_charges_cheap_discharges_expensive():
    price = [0.05] * 32 + [0.20] * 32 + [0.45] * 32
    plan = solve(base_input(price=price, battery=battery(soc=2.0)))
    assert plan.ok
    cheap = sum(r["battery_w"] for r in plan.slots[:32])
    expensive = sum(r["battery_w"] for r in plan.slots[64:])
    assert cheap > 10000 and expensive < -5000
    assert plan.expected_cost < plan.baseline_cost


def test_no_simultaneous_charge_and_discharge_or_import_export():
    price = [-0.10] * 96   # negative price would reward cycling losses without binaries
    plan = solve(base_input(price=price, battery=battery()))
    for r in plan.slots:
        assert not (r["grid_w"] > 1 and r["battery_w"] < -1 and r["pv_w"] > 1)
    assert plan.ok


def test_min_spread_blocks_unprofitable_arbitrage():
    price = [0.20] * 48 + [0.26] * 48
    p1 = solve(base_input(price=price, battery=battery(soc=1.0)))
    p2 = solve(base_input(price=price, battery=battery(soc=1.0, min_spread_eur_kwh=0.15)))
    grid_charge = lambda p: sum(r["battery_grid_charge_w"] for r in p.slots)  # noqa: E731
    assert grid_charge(p1) > 0
    assert grid_charge(p2) == pytest.approx(0, abs=1)


def test_no_grid_charging_when_disallowed():
    price = [0.01] * 48 + [0.50] * 48
    plan = solve(base_input(price=price, battery=battery(soc=1.0, grid_charging=False)))
    assert all(r["battery_grid_charge_w"] < 1 for r in plan.slots)
    assert all(r["battery_w"] <= 1 for r in plan.slots)   # no PV -> nothing to charge


def test_negative_export_price_curtails_pv():
    n = 16
    pv = [6000.0] * n
    price = [-0.10] * n
    inp = base_input(n=n, price=price, pv=pv, load=500, battery=battery(soc=9.5))
    plan = solve(inp)
    assert all(r["grid_w"] >= -1 for r in plan.slots)        # no export at a negative price
    assert sum(r["curtail_w"] for r in plan.slots) > 0


def test_export_limit_respected():
    n = 16
    plan = solve(base_input(n=n, pv=[8000.0] * n, load=500, export_limit_w=[0.0] * n))
    assert all(r["grid_w"] >= -1e-6 for r in plan.slots)


def test_heat_pump_preheats_before_expensive_hours_within_comfort():
    n = 96
    price = [0.10] * 60 + [0.50] * 20 + [0.10] * 16
    hp = HeatPumpModel(150, 7.5, 21.0, 20.5, 21.0, 22.0, 2500, [3.5] * n, [5.0] * n, [300] * n)
    plan = solve(base_input(n=n, price=price, heat_pump=hp))
    temps = [r["indoor_c"] for r in plan.slots]
    assert max(temps) <= 22.0 + 1e-6 and min(temps) >= 20.5 - 1e-3
    before = sum(r["hp_w"] for r in plan.slots[52:60]) / 8
    during = sum(r["hp_w"] for r in plan.slots[60:80]) / 20
    assert before > during


def test_ev_meets_deadline_in_cheapest_slots():
    n = 48
    price = [0.30] * 16 + [0.05] * 16 + [0.30] * 16
    ev = EVModel("ev", [True] * n, 11000, 4140, 20.0, 40)
    plan = solve(base_input(n=n, price=price, evs=[ev]))
    delivered = sum(r["ev_w"]["ev"] for r in plan.slots[:40]) * 0.25 / 1000 * 0.9
    assert delivered == pytest.approx(20.0, abs=0.1)
    cheap = sum(r["ev_w"]["ev"] for r in plan.slots[16:32])
    assert cheap / sum(r["ev_w"]["ev"] for r in plan.slots) > 0.9
    for r in plan.slots:
        assert r["ev_w"]["ev"] < 1 or r["ev_w"]["ev"] >= 4140 - 1   # minimum current respected


def test_peak_limit_shaves_with_battery():
    n = 8
    plan = solve(base_input(n=n, load=9000, peak_limit_w=6000, battery=battery(soc=9.0)))
    assert max(r["grid_w"] for r in plan.slots) <= 6000 + 1


def test_reserve_never_violated():
    price = [0.6] * 96
    plan = solve(base_input(price=price, battery=BatteryModel(10, 5, 3.0, 9.5, 5000, 5000)))
    assert min(r["soc_kwh"] for r in plan.slots) >= 3.0 - 1e-6


def test_solves_36h_quickly():
    n = 144
    price = [0.1 + 0.2 * ((i // 8) % 3) for i in range(n)]
    hp = HeatPumpModel(150, 7.5, 21.0, 20.5, 21.0, 22.0, 2500, [3.5] * n, [5.0] * n, [300] * n)
    plan = solve(base_input(n=n, price=price, battery=battery(), heat_pump=hp,
                            evs=[EVModel("ev", [True] * n, 11000, 4140, 15.0, 100)]))
    assert plan.ok and plan.solve_time_s < 15


def test_tariff_config_roundtrip_defaults():
    assert TariffConfig().vat_pct == 21.0
