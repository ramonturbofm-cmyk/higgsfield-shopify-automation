"""End-to-end scenarios: simulated house -> drivers -> engine -> optimizer -> controller -> devices.

Scenario A, 12:00: PV ~8 kW, house ~2 kW, battery 50 %, price -€0,05
   -> battery charges, flexible load (EV) used, (almost) no export.
Scenario B, 19:00: price €0,45 now and cheaper later, PV 0, battery 80 %
   -> battery discharges, heat pump turned down within comfort, (almost) no import.
"""

from datetime import UTC, timedelta

from conftest import local, make_config
from ems.control.optimizing import OptimizingController
from ems.core.clock import SimulatedClock
from ems.core.engine import EMSEngine
from ems.devices.base import DriverContext
from ems.devices.manager import DeviceManager
from ems.forecasting.service import ForecastService
from ems.forecasting.weather import WeatherPoint, WeatherProvider
from ems.optimizer.service import OptimizerService
from ems.prices.providers import PricePoint
from ems.prices.service import PriceService
from ems.simulator.builder import build_site
from ems.tariffs import TariffEngine

# Prices are the actual marginal prices in these scenarios: no markups, tax or VAT.
TARIFF = {"import_markup_eur_kwh": 0, "energy_tax_eur_kwh": 0, "vat_pct": 0, "export_markup_eur_kwh": 0}


def devices(soc, ev=False, hp=False, capacity=15):
    devs = [
        {"id": "p1", "name": "P1", "category": "smart_meter", "driver": "mock.smart_meter", "role": "primary_grid_meter"},
        {"id": "pv", "name": "PV", "category": "pv_inverter", "driver": "mock.pv_inverter",
         "params": {"peak_power_kw": 11, "sim": {"peak_power_kw": 11}}},
        {"id": "bat", "name": "Batterij", "category": "battery", "driver": "mock.battery",
         "params": {"capacity_kwh": capacity, "max_charge_w": 6000, "max_discharge_w": 6000,
                    "sim": {"soc_pct": soc, "max_charge_w": 6000, "max_discharge_w": 6000}}},
    ]
    if ev:
        devs.append({"id": "ev", "name": "EV", "category": "ev_charger", "driver": "mock.ev_charger",
                     "params": {"charge_mode": "smart", "battery_kwh": 60, "target_soc_pct": 80, "departure": "07:30",
                                "sim": {"arrive": "08:00", "depart": "07:30", "arrival_soc_pct": 30}}})
    if hp:
        devs.append({"id": "hp", "name": "Warmtepomp", "category": "heat_pump", "driver": "mock.heat_pump", "phase": "L1",
                     "params": {"heat_loss_w_per_k": 150, "thermal_capacity_kwh_per_k": 7.5, "thermal_max_w": 6000,
                                "sim": {"heat_loss_w_per_k": 150, "thermal_capacity_kwh_per_k": 7.5}}})
    return devs


class PinnedWeather(WeatherProvider):
    name = "scenario"

    def __init__(self, env, ghi_override=None):
        self.env, self.ghi_override = env, ghi_override

    async def fetch(self, lat, lon, start, end):
        out, t = [], start.replace(minute=0, second=0, microsecond=0)
        while t < end:
            ghi = self.env.ghi(t + timedelta(minutes=7)) if self.ghi_override is None else self.ghi_override(t)
            out.append(WeatherPoint(t, 15, self.env.outdoor_temp(t), ghi, 0))
            t += timedelta(minutes=15)
        return out


async def build(start, cfg, price_fn, weather_override=None, clear_sky=False):
    clock = SimulatedClock(start)
    site = build_site(cfg, start)
    if clear_sky:                       # scenario: a sunny day, not whatever the seeded weather says
        site.env.cloud_cover = lambda t: 0.0
    site.advance(0)
    dm = DeviceManager(cfg, DriverContext(clock, simulator=site))
    prices = PriceService(None, "TEST", None)
    t = start.astimezone(UTC) - timedelta(hours=1)
    prices.add_points([PricePoint(t + timedelta(minutes=15 * i), price_fn(t + timedelta(minutes=15 * i)), 15)
                       for i in range(4 * 40)], "test")
    tariff = TariffEngine(cfg.tariff, prices.spot, cfg.site.timezone)
    fc = ForecastService(cfg, None, PinnedWeather(site.env, weather_override))
    await fc.refresh_weather(start)
    caps = {i: d.driver.capabilities() for i, d in dm.devices.items()}
    opt = OptimizerService(cfg, prices, tariff, fc, caps)
    engine = EMSEngine(cfg, dm, OptimizingController(opt), clock)
    await engine.start()
    await engine.observe()
    return site, clock, engine, opt


async def run_minutes(site, clock, engine, minutes, step=10, each=None):
    for _ in range(int(minutes * 60 / step)):
        await engine.tick()
        site.advance(step)
        clock.advance(step)
        if each:
            each(site)


async def test_scenario_a_noon_negative_price_charges_and_avoids_export():
    start = local(2026, 6, 21, 12, 0)
    cfg = make_config(devices(50, ev=True), tariff=TARIFF, strategy={"export_mode": "smart"},
                      battery={"grid_charging_allowed": True})

    def price(ts):  # -€0,05 around noon, normal later
        h = ts.astimezone(start.tzinfo).hour
        return -0.05 if 11 <= h < 15 else 0.25

    site, clock, engine, opt = await build(start, cfg, price, clear_sky=True)
    await run_minutes(site, clock, engine, 1)
    plan = await opt.run(engine.last_snapshot, clock.now(), "scenario A")
    assert plan.ok, plan.message
    slot = opt.current_slot(clock.now())
    assert slot["battery_w"] > 1000, slot                     # plan: charge the battery
    exports = []
    await run_minutes(site, clock, engine, 20, each=lambda s: exports.append(max(0.0, -s.meter.power_w)))
    bat, ev, pv = site.components["bat"], site.components["ev"], site.components["pv"]
    assert pv.available_w > 7000                               # it really is sunny (~8 kW)
    assert bat.output_w > 1000                                 # battery charging
    assert ev.output_w > 0                                     # flexible load used at a negative price
    avg_export = sum(exports[-60:]) / 60
    assert avg_export < 300, f"export {avg_export:.0f} W"      # (almost) no export at a negative price
    reasons = " ".join(r for e in engine.journal.recent if e.device == "bat" for r in e.reasons)
    assert "Importprijs nu" in reasons


async def test_scenario_b_evening_peak_discharges_and_reduces_heat_pump():
    start = local(2026, 1, 15, 19, 0)
    # A 5 kWh battery at 80 % cannot carry the whole peak on its own -> the heat pump must flex too.
    cfg = make_config(devices(80, hp=True, capacity=5), tariff=TARIFF, battery={"grid_charging_allowed": True},
                      heatpump={"comfort_temperature": 21.0, "min_temperature": 20.5, "max_preheat_temperature": 22.0})

    def price(ts):
        h = ts.astimezone(start.tzinfo).hour
        return 0.45 if 17 <= h < 22 else 0.12

    site, clock, engine, opt = await build(start, cfg, price, weather_override=lambda t: 0.0)
    site.components["hp"].indoor_temp_c = 21.2
    await run_minutes(site, clock, engine, 1)
    plan = await opt.run(engine.last_snapshot, clock.now(), "scenario B")
    assert plan.ok, plan.message
    slot = opt.current_slot(clock.now())
    assert slot["battery_w"] < -200, slot                      # plan: discharge
    peak = []
    for r in plan.slots:                                       # tonight's peak (first run of €0,45 slots)
        if r["import_price"] < 0.45:
            break
        peak.append(r)
    assert sum(r["hp_w"] for r in peak) < 0.6 * sum(r["hp_reference_w"] for r in peak)   # heat pump turned down
    assert max(r["grid_w"] for r in peak) < 50                 # plan: no import during the peak
    imports, temps = [], []

    def record(s):
        imports.append(max(0.0, s.meter.power_w))
        temps.append(s.components["hp"].indoor_temp_c)

    await run_minutes(site, clock, engine, 60, each=record)
    assert site.components["bat"].output_w < -200              # battery discharging
    assert site.components["hp"].mode == "eco"                 # turned down …
    assert min(temps) >= cfg.heatpump.min_temperature - 0.15   # … within the comfort limits
    avg_import = sum(imports) / len(imports)
    assert avg_import < 400, f"import {avg_import:.0f} W"      # minimal grid import at €0,45


async def test_no_plan_without_prices_falls_back_safely():
    start = local(2026, 6, 21, 12, 0)
    cfg = make_config(devices(50), tariff=TARIFF)
    site, clock, engine, opt = await build(start, cfg, lambda ts: 0.1)
    opt.prices._set([])                                        # price source down, nothing cached
    plan = await opt.run(engine.last_snapshot, clock.now(), "no prices")
    assert plan.status == "no_prices"
    await run_minutes(site, clock, engine, 1)
    entry = [e for e in engine.journal.recent if e.device == "bat"][-1]
    assert "Terugval op zelfconsumptie" in entry.reasons[0]
    assert site.components["bat"].mode == "auto"
    assert not engine.failsafe_active
