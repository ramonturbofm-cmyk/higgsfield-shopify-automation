"""OptimizerService: builds the optimization problem from the live site and keeps
a rolling-horizon plan up to date.

Re-planning happens every ``optimizer.interval_minutes`` and immediately (debounced)
on events: new prices, a strongly changed forecast, SOC far from plan, a device going
offline/online, a manual override, or a large unexpected load.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.core.config import EMSConfig, ExportMode, StrategyProfile
from ems.core.models import Capability, DeviceCategory, Metric
from ems.core.snapshot import SiteSnapshot
from ems.forecasting.service import SLOT_MIN, Forecast, ForecastService
from ems.optimizer.model import BatteryModel, EVModel, HeatPumpModel, OptimizerInput, Plan, solve
from ems.prices.service import PriceService
from ems.tariffs import TariffEngine

log = logging.getLogger(__name__)


def _param(params: dict, key: str, default=None):
    if key in params:
        return params[key]
    return params.get("sim", {}).get(key, default)


def cop_estimate(outdoor_c: float) -> float:
    """Generic air/water COP from a weather-compensated flow temperature (Carnot x 0.45)."""
    flow = max(25.0, min(55.0, 35.0 + 0.6 * (20.0 - outdoor_c)))
    return max(1.5, min(6.5, 0.45 * (flow + 273.15) / max(5.0, flow - outdoor_c)))


def next_local_time(now: datetime, hhmm: str, tz: ZoneInfo) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    local = now.astimezone(tz)
    cand = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if cand <= local:
        cand += timedelta(days=1)
    return cand.astimezone(UTC)


@dataclass
class ProfileSettings:
    wear_mode: str
    export_value_cap: float | None = None   # cap export price (self-consumption profiles)
    grid_charging: bool = True
    zero_export: bool = False
    comfort_weight: float = 0.05
    min_soc_floor_pct: float | None = None
    max_cycles: float | None = None
    peak_shaving: bool = True


def profile_settings(cfg: EMSConfig) -> ProfileSettings:
    p = cfg.strategy.profile
    base = ProfileSettings(wear_mode=cfg.battery.wear_mode.value, grid_charging=cfg.battery.grid_charging_allowed)
    match p:
        case StrategyProfile.MAXIMUM_PROFIT:
            base.wear_mode = "profit"
        case StrategyProfile.MAXIMUM_SELF_CONSUMPTION:
            base.export_value_cap, base.grid_charging = 0.0, False
        case StrategyProfile.ZERO_EXPORT:
            base.zero_export = True
        case StrategyProfile.BATTERY_SAVER:
            base.wear_mode, base.max_cycles = "battery_saver", 1.0
        case StrategyProfile.COMFORT:
            base.comfort_weight = 0.5
        case StrategyProfile.ECO:
            base.comfort_weight = 0.01
        case StrategyProfile.BACKUP_PRIORITY:
            base.min_soc_floor_pct = max(cfg.battery.reserve_soc, 50.0)
    return base


def export_limit_w(cfg: EMSConfig, export_price: float | None, settings: ProfileSettings) -> float:
    full = cfg.grid.effective_max_export_kw * 1000
    if settings.zero_export or cfg.strategy.export_mode == ExportMode.ZERO:
        return max(0.0, cfg.strategy.export_target_w)
    if cfg.strategy.export_mode == ExportMode.SMART and export_price is not None \
            and export_price < cfg.strategy.export_price_threshold_eur:
        return max(0.0, cfg.strategy.export_target_w)
    return full


class OptimizerService:
    def __init__(self, config: EMSConfig, prices: PriceService, tariff: TariffEngine, forecast: ForecastService,
                 capabilities: dict[str, frozenset] | None = None) -> None:
        self.config = config
        self.prices = prices
        self.tariff = tariff
        self.forecast = forecast
        self.capabilities = capabilities or {}
        self.plan: Plan | None = None
        self.plan_created: datetime | None = None
        self.plan_trigger: str | None = None
        self.last_forecast: Forecast | None = None
        self._event = asyncio.Event()
        self._pending_reason: str | None = None
        self.runs = 0

    # ------------------------------------------------------------- triggers
    def request(self, reason: str) -> None:
        self._pending_reason = self._pending_reason or reason
        self._event.set()

    def check_triggers(self, snap: SiteSnapshot) -> None:
        """Cheap per-tick checks that may ask for an early re-plan."""
        slot = self.current_slot(snap.timestamp)
        if slot is None:
            return
        if snap.battery_soc_pct is not None and slot.get("soc_pct") is not None:
            if abs(snap.battery_soc_pct - slot["soc_pct"]) > 10:
                self.request("SOC wijkt >10%-punt af van de planning")
        if snap.house_load_w is not None and abs(snap.house_load_w - slot["load_w"]) > 2500:
            self.request("grote onverwachte belasting")

    # -------------------------------------------------------------- inputs
    def build_input(self, snap: SiteSnapshot, now: datetime) -> tuple[OptimizerInput | None, str]:
        cfg = self.config
        tz = ZoneInfo(cfg.site.timezone)
        settings = profile_settings(cfg)
        fc = self.forecast.build(now, cfg.optimizer.horizon_hours, snap.outdoor_temp_c)
        self.last_forecast = fc
        slots, imp, exp, est = [], [], [], []
        for t in fc.slots:
            spot, estimated = self.prices.spot_or_estimate(t)
            if spot is None and self.tariff.needs_spot:
                break
            if spot is not None and not estimated:     # hourly contract: hourly average of known quarters
                spot = self.tariff.contract_spot(t, spot)
            b = self.tariff.breakdown_with_spot(t, spot)
            if b.import_price is None or b.export_price is None:
                break
            e = b.export_price if settings.export_value_cap is None else min(b.export_price, settings.export_value_cap)
            slots.append(t), imp.append(b.import_price), exp.append(e), est.append(estimated)
        n = len(slots)
        if n < 4:
            return None, "geen (of te weinig) prijsdata beschikbaar voor planning"
        dt = SLOT_MIN / 60
        load = list(fc.load_w[:n])
        inp = OptimizerInput(
            slots=slots, dt_h=dt, import_price=imp, export_price=exp, pv_w=list(fc.pv_w[:n]), load_w=load,
            max_import_w=cfg.grid.effective_max_import_kw * 1000, max_export_w=cfg.grid.effective_max_export_kw * 1000,
            export_limit_w=[export_limit_w(cfg, e, settings) for e in exp],
            curtailable=any(Capability.CONTROL_PV_LIMIT in self.capabilities.get(d.id, ())
                            for d in cfg.devices_of(DeviceCategory.PV_INVERTER, DeviceCategory.HYBRID_INVERTER)),
            peak_limit_w=None if cfg.strategy.peak_limit_kw is None else cfg.strategy.peak_limit_kw * 1000,
            price_estimated=est, wear_mode=settings.wear_mode,
        )
        inp.battery = self._battery(snap, settings)
        inp.heat_pump, hp_note = self._heat_pump(snap, fc, n, settings)
        if inp.heat_pump is None:
            inp.load_w = [lw + hw for lw, hw in zip(load, fc.hp_w[:n], strict=True)]
        inp.evs = self._evs(snap, slots, now, tz, fc)
        return inp, hp_note

    def _battery(self, snap: SiteSnapshot, settings: ProfileSettings) -> BatteryModel | None:
        cfg = self.config
        devs = [d for d in cfg.devices_of(DeviceCategory.BATTERY, DeviceCategory.HYBRID_INVERTER)
                if Capability.CONTROL_BATTERY_MODE in self.capabilities.get(d.id, ())
                and d.params.get("capacity_kwh")]
        if not devs or snap.battery_soc_pct is None:
            return None
        cap = sum(float(d.params["capacity_kwh"]) for d in devs)
        pc = sum(float(_param(d.params, "max_charge_w", 3000)) for d in devs)
        pd = sum(float(_param(d.params, "max_discharge_w", 3000)) for d in devs)
        eff_c = min(float(_param(d.params, "charge_efficiency", 0.95)) for d in devs)
        eff_d = min(float(_param(d.params, "discharge_efficiency", 0.95)) for d in devs)
        b = cfg.battery
        floor = max(b.min_soc, b.reserve_soc, settings.min_soc_floor_pct or 0)
        soc_kwh = cap * snap.battery_soc_pct / 100
        min_kwh = min(cap * floor / 100, soc_kwh)            # never infeasible if already below the floor
        max_kwh = max(cap * b.max_soc / 100, soc_kwh)
        return BatteryModel(cap, soc_kwh, min_kwh, max_kwh, pc, pd, eff_c, eff_d, b.degradation_cost_per_kwh,
                            b.min_arbitrage_spread_eur, settings.max_cycles or b.max_cycles_per_day,
                            settings.grid_charging, b.grid_export_allowed)

    def _heat_pump(self, snap, fc: Forecast, n: int, settings: ProfileSettings) -> tuple[HeatPumpModel | None, str]:
        cfg = self.config
        hps = [d for d in cfg.devices_of(DeviceCategory.HEAT_PUMP)
               if Capability.CONTROL_HP_MODE in self.capabilities.get(d.id, ())]
        if not hps:
            return None, "geen bestuurbare warmtepomp"
        d = hps[0]
        loss, mass = _param(d.params, "heat_loss_w_per_k"), _param(d.params, "thermal_capacity_kwh_per_k")
        if not loss or not mass:
            return None, "warmtepomp niet geoptimaliseerd: vul warmteverlies (W/K) en thermische massa (kWh/K) in"
        if snap.indoor_temp_c is None or any(t is None for t in fc.outdoor_c[:n]):
            return None, "warmtepomp niet geoptimaliseerd: binnen- of buitentemperatuur onbekend"
        hpc = cfg.heatpump
        thermal_max = float(_param(d.params, "thermal_max_w", 6000))
        outdoor = [float(t) for t in fc.outdoor_c[:n]]
        cops = [cop_estimate(t) for t in outdoor]
        max_el = float(_param(d.params, "max_electric_w", thermal_max / 3))
        gains = float(_param(d.params, "internal_gains_w", 300))
        return HeatPumpModel(float(loss), float(mass), float(snap.indoor_temp_c),
                             min(hpc.min_temperature, snap.indoor_temp_c), hpc.comfort_temperature,
                             max(hpc.max_preheat_temperature, snap.indoor_temp_c), max_el, cops, outdoor,
                             [gains] * n, settings.comfort_weight), "thermisch model actief"

    def _evs(self, snap, slots, now, tz, fc: Forecast) -> list[EVModel]:
        out = []
        n = len(slots)
        for d in self.config.devices_of(DeviceCategory.EV_CHARGER):
            if Capability.CONTROL_EV_CURRENT not in self.capabilities.get(d.id, ()):
                continue
            mode = d.params.get("charge_mode", "smart")
            st = snap.devices.get(d.id)
            if mode not in ("smart", "pv_only") or st is None or not st.usable or not st.get(Metric.EV_CONNECTED):
                continue
            phases = 3 if d.phase == "3P" and self.config.grid.phases == 3 else 1
            volt = self.config.grid.voltage_v
            max_w = phases * volt * float(d.params.get("max_current_a", 16))
            min_w = phases * volt * float(d.params.get("min_current_a", 6))
            soc = st.get(Metric.EV_SOC_PCT)
            batt = float(d.params.get("battery_kwh", _param(d.params, "battery_kwh", 60)))
            if soc is not None:
                need = max(0.0, (float(d.params.get("target_soc_pct", 80)) - float(soc)) / 100 * batt)
            else:
                need = float(d.params.get("session_target_kwh", 0))
            depart = next_local_time(now, d.params.get("departure", "07:30"), tz)
            deadline = sum(1 for t in slots if t + timedelta(minutes=SLOT_MIN) <= depart)
            avail = [t < depart for t in slots]
            cap = None
            if mode == "pv_only":
                cap = [max(0.0, fc.pv_w[i] - fc.load_w[i]) for i in range(n)]
                need = 0.0
            out.append(EVModel(d.id, avail, max_w, min_w, need, deadline,
                               float(d.params.get("charge_efficiency", 0.9)), cap))
        return out

    # ----------------------------------------------------------------- run
    async def run(self, snap: SiteSnapshot, now: datetime, trigger: str = "interval") -> Plan:
        inp, note = await asyncio.to_thread(self.build_input, snap, now)
        if inp is None:
            plan = Plan("no_prices", note, [], None, None, 0.0)
        else:
            plan = await asyncio.to_thread(solve, inp)
            plan.inputs_summary["heat_pump_note"] = note
            plan.inputs_summary["forecast_sources"] = self.last_forecast.sources if self.last_forecast else {}
            plan.inputs_summary["profile"] = self.config.strategy.profile.value
        self.plan, self.plan_created, self.plan_trigger = plan, now, trigger
        self.runs += 1
        self.run_id = f"{now:%Y%m%dT%H%M%S}-{self.runs}"     # reasons and numbers all belong to this run
        log.info("optimizer run", extra={"status": plan.status, "trigger": trigger, "solve_s": plan.solve_time_s})
        return plan

    async def wait_trigger(self, timeout_s: float) -> str:
        try:
            await asyncio.wait_for(self._event.wait(), timeout_s)
        except TimeoutError:
            return "interval"
        await asyncio.sleep(2)  # debounce bursts of events
        self._event.clear()
        reason, self._pending_reason = self._pending_reason or "event", None
        return reason

    def current_slot(self, now: datetime) -> dict | None:
        if self.plan is None or not self.plan.ok:
            return None
        for row in self.plan.slots:
            start = datetime.fromisoformat(row["start"])
            if start <= now < start + timedelta(minutes=SLOT_MIN):
                return row
        return None

    def upcoming(self, now: datetime, hours: float = 24) -> list[dict]:
        if self.plan is None or not self.plan.ok:
            return []
        end = now + timedelta(hours=hours)
        return [r for r in self.plan.slots if datetime.fromisoformat(r.get("end") or r["start"]) > now
                and datetime.fromisoformat(r["start"]) < end]

    def status(self) -> dict:
        return {"status": None if self.plan is None else self.plan.status,
                "created": None if self.plan_created is None else self.plan_created.isoformat(),
                "trigger": self.plan_trigger, "runs": self.runs, "run_id": getattr(self, "run_id", None),
                "message": None if self.plan is None else self.plan.message}


def finite(v: float | None) -> bool:
    return v is not None and math.isfinite(v)
