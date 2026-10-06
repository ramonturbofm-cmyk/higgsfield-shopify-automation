"""Physics tests: the simulator must be trustworthy before anything is built on it."""

from datetime import timedelta

import pytest

from conftest import BASE_DEVICES, local, make_config
from ems.simulator.builder import build_site
from ems.simulator.components import SimBaseLoad, SimBattery, SimEVCharger, SimHeatPump, SimPV
from ems.simulator.environment import Environment


def run(site, hours: float, dt: float = 30.0, each=None):
    for _ in range(int(hours * 3600 / dt)):
        site.advance(dt)
        if each:
            each(site)


def test_energy_balance_holds():
    cfg = make_config()
    site = build_site(cfg, local(2026, 4, 10), seed=3)
    run(site, 24)
    m = site.meter
    pv = sum(p.energy_kwh for p in site.of_type(SimPV))
    load = sum(c.energy_kwh for c in site.of_type(SimBaseLoad))
    hp = sum(c.energy_kwh for c in site.of_type(SimHeatPump))
    ev = sum(c.energy_kwh for c in site.of_type(SimEVCharger))
    bat = sum(b.charged_kwh - b.discharged_kwh for b in site.of_type(SimBattery))
    assert m.import_kwh - m.export_kwh == pytest.approx(load + hp + ev + bat - pv, abs=1e-6)
    assert pv > 5 and load > 3


def test_pv_follows_sun_and_seasons():
    env = Environment(52.09, 5.12, "Europe/Amsterdam", seed=1)
    assert env.clear_sky_ghi(local(2026, 6, 21, 1, 0)) == 0
    summer = env.clear_sky_ghi(local(2026, 6, 21, 13, 40))
    winter = env.clear_sky_ghi(local(2026, 12, 21, 12, 40))
    assert summer > 800 and 150 < winter < 400


def test_environment_is_deterministic_and_seeded():
    a = Environment(52, 5, "Europe/Amsterdam", seed=1)
    b = Environment(52, 5, "Europe/Amsterdam", seed=1)
    c = Environment(52, 5, "Europe/Amsterdam", seed=2)
    t = local(2026, 3, 3, 14, 7)
    assert a.state(t) == b.state(t)
    assert a.state(t) != c.state(t)


def test_price_profile_shape():
    env = Environment(52.09, 5.12, "Europe/Amsterdam", seed=1)
    days = [local(2026, 6, d) for d in range(1, 29)]
    noon = sum(env.spot_price(d.replace(hour=13)) for d in days) / len(days)
    evening = sum(env.spot_price(d.replace(hour=19)) for d in days) / len(days)
    assert evening > noon + 0.05
    # 15-minute resolution: constant within a quarter hour.
    t = local(2026, 6, 1, 13, 15)
    assert env.spot_price(t) == env.spot_price(t + timedelta(minutes=14))


def test_pv_limit_curtails_and_is_accounted():
    cfg = make_config()
    site = build_site(cfg, local(2026, 6, 21, 12, 0))
    pv = site.components["pv"]
    pv.limit_w = 1000.0
    run(site, 1)
    assert pv.output_w <= 1000.0
    assert pv.curtailed_kwh > 1.0
    assert pv.energy_kwh == pytest.approx(1.0, abs=0.05)


def test_battery_forced_charge_efficiency_and_bounds():
    cfg = make_config()
    site = build_site(cfg, local(2026, 1, 10, 0, 0))
    bat = site.components["bat"]
    bat.mode, bat.setpoint_w = "charge", 2000.0
    run(site, 1, dt=10)
    assert bat.charged_kwh == pytest.approx(2.0, rel=1e-3)
    assert bat.soc_pct == pytest.approx(50 + 2.0 * 0.95 / 10 * 100, abs=0.01)
    run(site, 5, dt=10)
    assert bat.soc_pct <= 100.0 + 1e-9 and bat.output_w == 0.0  # full: no more charging
    bat.mode, bat.setpoint_w = "discharge", 5000.0
    run(site, 5, dt=10)
    assert bat.soc_pct >= bat.hw_min_soc_pct - 1e-9
    assert bat.output_w == 0.0


def test_battery_auto_mode_balances_grid():
    cfg = make_config([d for d in BASE_DEVICES if d["id"] != "ev"])
    site = build_site(cfg, local(2026, 5, 1, 6, 0))
    bat = site.components["bat"]
    errors = []

    def track(s):
        unsaturated = 11 < bat.soc_pct < 99 and abs(bat.output_w) < bat.max_charge_w - 1
        if unsaturated:
            errors.append(abs(s.meter.power_w))

    run(site, 24, dt=10, each=track)
    # Whenever the battery has room and power headroom it holds the grid at 0 W.
    assert len(errors) > 1000
    assert max(errors) < 1.0


def test_heat_pump_keeps_comfort_in_winter_and_respects_min_run():
    cfg = make_config()
    site = build_site(cfg, local(2026, 1, 15, 0, 0))
    hp = site.components["hp"]
    on_periods, run_s, temps = [], 0.0, []

    def track(s):
        nonlocal run_s
        temps.append(hp.indoor_temp_c)
        if hp.compressor_on:
            run_s += 30
        elif run_s:
            on_periods.append(run_s)
            run_s = 0.0

    run(site, 48, each=track)
    assert min(temps) > 20.3 and max(temps) < 21.8
    assert on_periods and min(on_periods) >= hp.min_run_s
    assert hp.energy_kwh > 5
    assert 2.0 < hp.heat_kwh / hp.energy_kwh < 5.5  # plausible seasonal COP


def test_heat_pump_boost_and_eco_shift_temperature():
    def mean_temp(mode: str) -> float:
        site = build_site(make_config(), local(2026, 1, 15, 0, 0))
        hp = site.components["hp"]
        hp.mode = mode
        temps = []
        run(site, 24, each=lambda s: temps.append(hp.indoor_temp_c))
        return sum(temps[len(temps) // 2:]) / (len(temps) // 2)

    normal, boost, eco = mean_temp("normal"), mean_temp("boost"), mean_temp("eco")
    assert boost > normal + 0.5 > eco + 0.8


def test_single_phase_load_shows_on_its_phase():
    site = build_site(make_config(), local(2026, 1, 15, 3, 0))
    hp = site.components["hp"]
    site.advance(0)
    hp.compressor_on, hp.time_in_state_s = True, 0
    hp.mode = "boost"
    site.advance(10)
    l1, l2, l3 = site.meter.phase_current_a
    assert hp.output_w > 500
    assert l1 > l2 + 2 and l1 > l3 + 2


def test_ev_schedule_setpoint_and_car_limit():
    cfg = make_config()
    site = build_site(cfg, local(2026, 3, 1, 1, 0))
    ev = site.components["ev"]
    run(site, 0.1)
    assert ev.connected and ev.output_w == pytest.approx(3 * 230 * 16)
    ev.current_setpoint_a = 8
    site.advance(10)
    assert ev.output_w == pytest.approx(3 * 230 * 8)
    ev.current_setpoint_a = 4  # below minimum: car does not charge
    site.advance(10)
    assert ev.output_w == 0
    ev.current_setpoint_a = None
    run(site, 12)
    assert ev.soc_pct == pytest.approx(ev.car_soc_limit_pct, abs=0.5)
    assert ev.output_w == 0
