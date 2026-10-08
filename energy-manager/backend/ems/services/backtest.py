"""Backtesting and Auto-Tune.

The backtester replays a period day by day: for every day the real optimizer plans
24 h with that day's actual consumption, PV and prices (perfect foresight — an upper
bound, stated in the result), the battery state carries over to the next day, and the
plan is executed on the energy balance. Heat pump and EV consumption are replayed as
measured (not shifted). It compares current settings, proposed settings and
"zonder EMS" (battery in its own self-consumption mode).

Data source: measured 15-minute history. Only in Demo Mode, when there is not enough
history yet, the demo world (simulator) supplies the series — and says so.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.core.config import EMSConfig, copy_config
from ems.optimizer.model import BatteryModel, OptimizerInput, _baseline_cost, solve
from ems.optimizer.service import profile_settings
from ems.prices.service import local_day_slots
from ems.simulator.components import SimBaseLoad, SimPV
from ems.simulator.environment import Environment
from ems.tariffs import TariffEngine

log = logging.getLogger(__name__)
SLOT_S = 900


@dataclass
class DaySeries:
    start: datetime
    load_w: list[float]
    pv_w: list[float]
    spot: list[float | None]
    slots: list[datetime] | None = None       # UTC starts of the local day (92 / 96 / 100 quarter-hours)
    have: int = 0                             # measured quarter-hours

    def __post_init__(self) -> None:
        if self.slots is None:
            self.slots = [self.start + timedelta(minutes=15 * i) for i in range(len(self.load_w))]
        if not self.have:
            self.have = len(self.load_w)


MIN_DAY_COVERAGE = 80 / 96


def series_from_history(rows: list[dict], first_day: date, days: int, tz: ZoneInfo) -> tuple[list[DaySeries], list[dict]]:
    """Local days (DST-correct). Returns the usable days and a per-day coverage report."""
    by_ts = {r["slot_ts"]: r for r in rows}
    out, report = [], []
    for d in range(days):
        day = first_day + timedelta(days=d)
        slots = local_day_slots(day, tz)
        load, pv, spot, have = [], [], [], 0
        for t in slots:
            r = by_ts.get(t.timestamp())
            if r is None or r.get("house_kwh") is None:
                load.append(0.0), pv.append(0.0), spot.append(None)
                continue
            have += 1
            kw = 4000.0  # kWh per 15 min -> W
            load.append(((r.get("house_kwh") or 0) + (r.get("hp_kwh") or 0) + (r.get("ev_kwh") or 0)) * kw)
            pv.append((r.get("pv_kwh") or 0) * kw)
            spot.append(r.get("spot"))
        used = have >= MIN_DAY_COVERAGE * len(slots)
        report.append({"date": day.isoformat(), "quarters": len(slots), "measured": have, "used": used})
        if used:
            out.append(DaySeries(slots[0], load, pv, spot, slots, have))
    return out, report


def series_from_demo(config: EMSConfig, first_day: date, days: int, tz: ZoneInfo) -> list[DaySeries]:
    env = Environment(config.site.latitude, config.site.longitude, config.site.timezone, seed=1)
    load_model = SimBaseLoad(id="bt", annual_kwh=config.site.annual_consumption_kwh)
    arrays = []
    for d in config.devices:
        p = d.params.get("sim", {})
        if d.category.value in ("pv_inverter", "hybrid_inverter") and p.get("peak_power_kw"):
            arrays.append(SimPV(id=d.id, peak_power_kw=p["peak_power_kw"], azimuth_deg=p.get("azimuth_deg", 180)))
    out = []
    for d in range(days):
        slots = local_day_slots(first_day + timedelta(days=d), tz)
        load, pv, spot = [], [], []
        for s0 in slots:
            t = s0 + timedelta(minutes=7)
            load_model.update(env, t)
            for a in arrays:
                a.update(env, t)
            load.append(load_model.output_w + 600.0)   # + average heat pump/EV demand (replayed, not shifted)
            pv.append(sum(a.available_w for a in arrays))
            spot.append(env.spot_price(t))
        out.append(DaySeries(slots[0], load, pv, spot, slots))
    return out


def _battery_from_config(config: EMSConfig, soc_kwh: float | None) -> BatteryModel | None:
    devs = [d for d in config.devices if d.enabled and d.category.value in ("battery", "hybrid_inverter")
            and d.params.get("capacity_kwh")]
    if not devs:
        return None
    get = lambda d, k, dflt: d.params.get(k, d.params.get("sim", {}).get(k, dflt))  # noqa: E731
    cap = sum(float(d.params["capacity_kwh"]) for d in devs)
    b = config.battery
    settings = profile_settings(config)
    floor = max(b.min_soc, b.reserve_soc, settings.min_soc_floor_pct or 0)
    soc = cap * 0.5 if soc_kwh is None else soc_kwh
    return BatteryModel(cap, soc, min(cap * floor / 100, soc), max(cap * b.max_soc / 100, soc),
                        sum(float(get(d, "max_charge_w", 3000)) for d in devs),
                        sum(float(get(d, "max_discharge_w", 3000)) for d in devs),
                        min(float(get(d, "charge_efficiency", 0.95)) for d in devs),
                        min(float(get(d, "discharge_efficiency", 0.95)) for d in devs),
                        b.degradation_cost_per_kwh, b.min_arbitrage_spread_eur,
                        settings.max_cycles or b.max_cycles_per_day, settings.grid_charging, b.grid_export_allowed)


def simulate_variant(config: EMSConfig, days: list[DaySeries], progress: Callable[[float], None] | None = None) -> dict:
    tariff = TariffEngine(config.tariff, lambda ts: None, config.site.timezone)
    settings = profile_settings(config)
    totals = {"cost_eur": 0.0, "baseline_cost_eur": 0.0, "import_kwh": 0.0, "export_kwh": 0.0,
              "throughput_kwh": 0.0, "curtailed_kwh": 0.0, "grid_charge_kwh": 0.0, "days": 0, "failed_days": 0}
    soc = None
    battery_cap = 0.0
    for n, day in enumerate(days):
        slots = day.slots
        imp, exp = [], []
        for t, s in zip(slots, day.spot, strict=True):
            b = tariff.breakdown_with_spot(t, s)
            imp.append(b.import_price if b.import_price is not None else 0.25)
            e = b.export_price if b.export_price is not None else 0.0
            exp.append(e if settings.export_value_cap is None else min(e, settings.export_value_cap))
        battery = _battery_from_config(config, soc)
        battery_cap = battery.capacity_kwh if battery else 0.0
        inp = OptimizerInput(slots, 0.25, imp, exp, day.pv_w, day.load_w,
                             config.grid.effective_max_import_kw * 1000, config.grid.effective_max_export_kw * 1000,
                             battery=battery, wear_mode=settings.wear_mode, time_limit_s=10)
        plan = solve(inp)
        totals["baseline_cost_eur"] += _baseline_cost(inp)
        if not plan.ok:
            totals["failed_days"] += 1
            continue
        totals["days"] += 1
        totals["cost_eur"] += sum((r["import_price"] * max(0, r["grid_w"]) - r["export_price"] * max(0, -r["grid_w"]))
                                  * 0.25 / 1000 for r in plan.slots)
        totals["import_kwh"] += sum(max(0, r["grid_w"]) for r in plan.slots) / 4000
        totals["export_kwh"] += sum(max(0, -r["grid_w"]) for r in plan.slots) / 4000
        totals["throughput_kwh"] += sum(abs(r["battery_w"]) for r in plan.slots) / 4000
        totals["curtailed_kwh"] += sum(r["curtail_w"] for r in plan.slots) / 4000
        totals["grid_charge_kwh"] += sum(r["battery_grid_charge_w"] for r in plan.slots) / 4000
        if battery:
            soc = plan.slots[-1]["soc_kwh"]
        if progress:
            progress((n + 1) / len(days))
    r2 = lambda x: round(x, 2)  # noqa: E731
    wear = totals["throughput_kwh"] / 2 * config.battery.degradation_cost_per_kwh
    return {**{k: (r2(v) if isinstance(v, float) else v) for k, v in totals.items()},
            "battery_cycles": r2(totals["throughput_kwh"] / 2 / battery_cap) if battery_cap else 0.0,
            "estimated_wear_eur": r2(wear), "net_cost_incl_wear_eur": r2(totals["cost_eur"] + wear)}


def apply_overrides(config: EMSConfig, overrides: dict) -> EMSConfig:
    """overrides: {"battery": {"min_arbitrage_spread_eur": 0.1}, "strategy": {...}, "tariff": {...}}"""
    data = config.model_dump(mode="json")
    for section, values in (overrides or {}).items():
        if section not in ("battery", "strategy", "tariff", "grid", "heatpump"):
            raise ValueError(f"sectie {section!r} kan niet worden getest")
        data[section] = {**data[section], **values}
    return copy_config(config, data)


def load_period(config: EMSConfig, db, mode: str, first_day: date, last_day: date) -> tuple[list[DaySeries], str, dict]:
    """Days ``first_day`` .. ``last_day`` (local dates, inclusive) with a data-coverage report."""
    tz = ZoneInfo(config.site.timezone)
    days = (last_day - first_day).days + 1
    if days < 1 or days > 366:
        raise ValueError("periode moet 1 t/m 366 dagen zijn")
    start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=tz).astimezone(UTC)
    nxt = last_day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(UTC)
    rows = db.slots_between(config.site.id, start.timestamp(), end.timestamp()) if db else []
    hist, report = series_from_history(rows, first_day, days, tz)
    expected = sum(r["quarters"] for r in report)
    measured = sum(r["measured"] for r in report)
    coverage = {"days_requested": days, "days_used": len(hist), "quarters_expected": expected,
                "quarters_measured": measured, "coverage_pct": round(100 * measured / expected, 1) if expected else 0.0,
                "skipped_days": [r["date"] for r in report if not r["used"]],
                "rule": f"een dag telt mee bij minimaal {MIN_DAY_COVERAGE:.0%} gemeten kwartieren"}
    if hist and len(hist) >= max(1, int(days * 0.8)):
        return hist, f"gemeten historie ({len(hist)} van {days} dagen)", coverage
    if mode == "demo":
        coverage["note"] = "demowereld gebruikt: nog onvoldoende eigen historie"
        return (series_from_demo(config, first_day, days, tz),
                "demowereld (simulator) — nog onvoldoende eigen historie", coverage)
    raise ValueError(f"onvoldoende historie: {len(hist)} bruikbare dagen van {days}, nodig {max(1, int(days * 0.8))} "
                     f"({coverage['coverage_pct']}% van de kwartieren gemeten)")


def load_days(config: EMSConfig, db, mode: str, days: int, now: datetime) -> tuple[list[DaySeries], str]:
    """The last ``days`` complete local days before ``now``."""
    today = now.astimezone(ZoneInfo(config.site.timezone)).date()
    series, source, _ = load_period(config, db, mode, today - timedelta(days=days), today - timedelta(days=1))
    return series, source


def inputs_fingerprint(config: EMSConfig, series: list[DaySeries], overrides: dict) -> str:
    """Hash of everything that determines the result: same hash -> same numbers (reproducible)."""
    h = hashlib.sha256()
    h.update(json.dumps({"tariff": config.tariff.model_dump(mode="json"), "battery": config.battery.model_dump(mode="json"),
                         "strategy": config.strategy.model_dump(mode="json"), "overrides": overrides},
                        sort_keys=True).encode())
    for d in series:
        h.update(json.dumps([d.start.isoformat(), d.load_w, d.pv_w, d.spot]).encode())
    return h.hexdigest()[:16]


def run_backtest(config: EMSConfig, db, mode: str, days: int, overrides: dict, now: datetime,
                 progress: Callable[[float], None] | None = None, start: date | None = None,
                 end: date | None = None) -> dict:
    """Backtest the last ``days`` days, or a custom period ``start``..``end`` (local dates, inclusive)."""
    today = now.astimezone(ZoneInfo(config.site.timezone)).date()
    if start is not None or end is not None:
        if start is None or end is None or end < start:
            raise ValueError("geef een begin- en einddatum (einde niet vóór begin)")
        if end >= today:
            raise ValueError("einddatum moet vóór vandaag liggen (alleen volledige dagen)")
    else:
        if days not in (1, 7, 14, 30, 90, 365):
            raise ValueError("periode moet 1, 7, 14, 30, 90 of 365 dagen zijn")
        start, end = today - timedelta(days=days), today - timedelta(days=1)
    series, source, coverage = load_period(config, db, mode, start, end)
    new_cfg = apply_overrides(config, overrides)
    half = (lambda f: progress(f / 2)) if progress else None
    rest = (lambda f: progress(0.5 + f / 2)) if progress else None
    current = simulate_variant(config, series, half)
    proposed = simulate_variant(new_cfg, series, rest)
    return {"days": len(series), "start": start.isoformat(), "end": end.isoformat(), "source": source,
            "coverage": coverage, "overrides": overrides,
            "fingerprint": inputs_fingerprint(config, series, overrides),
            "method": "dagelijkse optimalisatie met perfecte kennis van verbruik, PV en prijs (bovengrens); "
                      "warmtepomp en auto zoals gemeten",
            "baseline": "Zonder EMS = zelfde verbruik en PV, batterij op haar eigen zelfconsumptieregeling",
            "current": current, "proposed": proposed,
            "difference_eur": round(proposed["net_cost_incl_wear_eur"] - current["net_cost_incl_wear_eur"], 2),
            "without_ems_eur": current["baseline_cost_eur"]}


AUTOTUNE_CANDIDATES = [
    ("battery", "min_arbitrage_spread_eur", [0.0, 0.05, 0.10, 0.15, 0.20],
     "Minimaal prijsverschil voor batterijhandel"),
    ("battery", "degradation_cost_per_kwh", [0.02, 0.04, 0.06, 0.08], "Batterijslijtage per kWh"),
    ("battery", "reserve_soc", [10.0, 15.0, 20.0, 30.0], "Noodstroomreserve"),
]


def autotune(config: EMSConfig, db, mode: str, now: datetime, days: int = 14,
             progress: Callable[[float], None] | None = None) -> list[dict]:
    series, source = load_days(config, db, mode, days, now)
    base = simulate_variant(config, series)
    suggestions = []
    total = sum(len(v) for _, _, v, _ in AUTOTUNE_CANDIDATES)
    done = 0
    for section, key, values, label in AUTOTUNE_CANDIDATES:
        current_value = getattr(getattr(config, section), key)
        best = None
        for v in values:
            done += 1
            if progress:
                progress(done / total)
            if v == current_value:
                continue
            try:
                cfg = apply_overrides(config, {section: {key: v}})
            except Exception:
                continue
            res = simulate_variant(cfg, series)
            gain = base["net_cost_incl_wear_eur"] - res["net_cost_incl_wear_eur"]
            if best is None or gain > best[1]:
                best = (v, gain, res)
        if best and best[1] > 0.05 * max(1, len(series)) / 7:
            per_month = best[1] / max(1, len(series)) * 30
            suggestions.append({
                "id": f"{section}.{key}", "section": section, "key": key, "label": label,
                "current": current_value, "suggested": best[0],
                "estimated_saving_per_month_eur": round(per_month, 2),
                "text": f"{label}: {current_value} → {best[0]}. Geschatte extra besparing "
                        f"€{per_month:.2f}".replace(".", ",") + f" per maand (backtest {len(series)} dagen, {source}).",
                "status": "new", "source": source,
            })
    return suggestions
