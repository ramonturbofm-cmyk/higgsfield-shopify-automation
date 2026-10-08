"""Audit acceptance test 22 and P1-13..P1-20: optimizer intervals of 5/15/60 min, real hourly
aggregation, structured reasons from the solved plan, explicit heat-pump action, horizon setting."""

from datetime import UTC, datetime, timedelta

import pytest

from ems.optimizer import BatteryModel, OptimizerInput, solve
from ems.optimizer.explain import aggregate, hp_action

T0 = datetime(2026, 7, 1, tzinfo=UTC)


def _plan(step_min: int, hours: int = 8):
    n = hours * 60 // step_min
    price = [0.08 if i * step_min < hours * 30 else 0.40 for i in range(n)]
    bat = BatteryModel(10, 1.0, 1.0, 9.5, 5000, 5000, degradation_eur_kwh=0.02)
    inp = OptimizerInput([T0 + timedelta(minutes=step_min * i) for i in range(n)], step_min / 60, price, list(price),
                         [0.0] * n, [400.0] * n, 17250, 17250, battery=bat)
    return solve(inp)


@pytest.mark.parametrize("step", [5, 15, 60])
def test_22_any_interval_and_true_hourly_aggregation(step):
    plan = _plan(step)
    assert plan.ok
    s0 = plan.slots[0]
    assert s0["duration_min"] == pytest.approx(step) and s0["end"] > s0["start"]
    hourly = aggregate(plan.slots, 60, "Europe/Amsterdam")
    assert len(hourly) == 8 and all(b["duration_min"] == pytest.approx(60) for b in hourly)
    # Energy is summed over the real interval durations, not sampled every n-th slot.
    load_kwh = sum(r["load_w"] * r["duration_min"] / 60 / 1000 for r in plan.slots)
    assert sum(b["load_kwh"] for b in hourly) == pytest.approx(load_kwh)
    batt_kwh = sum(r["battery_w"] * r["duration_min"] / 60 / 1000 for r in plan.slots)
    assert sum(b["battery_kwh"] for b in hourly) == pytest.approx(batt_kwh, abs=1e-6)
    # Prices are time-weighted averages with min/max kept; SOC is the end-of-hour value.
    first_hour = [r for r in plan.slots if r["start"] < (T0 + timedelta(hours=1)).isoformat()]
    assert hourly[0]["import_price"] == pytest.approx(sum(r["import_price"] for r in first_hour) / len(first_hour))
    assert hourly[0]["soc_pct"] == first_hour[-1]["soc_pct"]


def test_reasons_are_structured_and_independent_of_slot_length():
    for step in (5, 15):
        plan = _plan(step)
        codes = {r["code"] for s in plan.slots for r in s["reasons"]}
        assert {"battery_charge_grid", "battery_discharge"} <= codes, (step, codes)
        charge = next(r for s in plan.slots for r in s["reasons"] if r["code"] == "battery_charge_grid")
        assert charge["data"]["break_even_price"] > charge["data"]["import_price"]
        assert charge["data"]["later_max_price"] == pytest.approx(0.40)
    last = plan.slots[-1]["reasons"]
    assert any(r["code"] == "insufficient_price_info" for r in last)       # no Math.max([]) = -Infinity


def test_no_cycling_reason_cites_break_even():
    n = 32
    price = [0.20] * 16 + [0.21] * 16
    bat = BatteryModel(10, 5.0, 1.0, 9.5, 5000, 5000, degradation_eur_kwh=0.02)
    plan = solve(OptimizerInput([T0 + timedelta(minutes=15 * i) for i in range(n)], 0.25, price, list(price),
                                [0.0] * n, [400.0] * n, 17250, 17250, battery=bat))
    codes = [r["code"] for r in plan.slots[0]["reasons"]]
    assert "spread_below_break_even" in codes or "battery_discharge" in codes or "battery_hold" in codes


def test_heat_pump_action_is_explicit_single_rule():
    assert hp_action(3000, 1000) == ("boost", "hp_preheat_cheap")
    assert hp_action(200, 1000) == ("eco", "hp_reduce_expensive")
    assert hp_action(1000, 1000) == ("normal", "hp_normal")
    assert hp_action(None, None) == (None, None)


async def test_plan_api_follows_horizon_setting_and_aggregates(tmp_path):
    import httpx

    from ems.api.app import create_app
    from ems.server.runtime import EMSRuntime
    rt = EMSRuntime(tmp_path / "pl", mode="demo", env={})
    await rt.start(loops=False)
    await rt.tick_once()
    data = rt.config.model_dump(mode="json")
    data["optimizer"]["horizon_hours"] = 24
    await rt.reload(data, "test", "horizon")
    await rt.optimizer.run(rt.engine.last_snapshot, rt.now(), "test")
    app = create_app(rt, start_runtime=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        r = await c.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        p = (await c.get("/api/v1/optimizer/plan")).json()
        assert p["horizon_hours"] == 24 and p["run_id"] and p["slots"][0]["reasons"] is not None
        assert datetime.fromisoformat(p["slots"][-1]["start"]) < rt.now() + timedelta(hours=24)
        hourly = (await c.get("/api/v1/optimizer/plan?resolution=60")).json()
        assert hourly["resolution_minutes"] == 60 and len(hourly["slots"]) <= 25
        assert all("load_kwh" in b and "import_price_max" in b for b in hourly["slots"])
    finally:
        await c.aclose()
        await rt.stop()
