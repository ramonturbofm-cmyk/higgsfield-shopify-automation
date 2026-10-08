"""OptimizingController: executes the current plan slot with real-time corrections.

Per tick:
  battery   plan slot -> CHARGE / DISCHARGE / STANDBY / AUTO (self-consumption)
  PV        export not wanted -> closed-loop zero-export on the primary grid meter;
            without a grid meter only an open-loop limit from the plan is possible
  EV        'smart' -> planned power; other modes -> rule-based logic; always phase guard
  heat pump plan vs. comfort reference -> boost / normal / eco with a minimum dwell time
  peak      grid import above the peak limit -> extra battery discharge
Without a valid plan it falls back to the self-consumption strategy and says so.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.control.base import ControlContext, Controller
from ems.control.self_consumption import SelfConsumptionController
from ems.control.zero_export import ZeroExportRegulator
from ems.core.models import Capability, Command, CommandAction, Decision, DeviceCategory, Metric
from ems.core.snapshot import SiteSnapshot
from ems.optimizer.service import OptimizerService, export_limit_w, profile_settings

HP_MIN_DWELL = timedelta(minutes=20)


def _eur(v: float | None) -> str:
    return "?" if v is None else f"€{v:.3f}".replace(".", ",")


class OptimizingController(Controller):
    name = "optimizer"

    def __init__(self, service: OptimizerService) -> None:
        self.service = service
        self.fallback = SelfConsumptionController()
        self.zero_export = ZeroExportRegulator()
        self._hp_mode: dict[str, tuple[str, datetime]] = {}

    # ------------------------------------------------------------------ main
    def decide(self, snap: SiteSnapshot, ctx: ControlContext) -> list[Decision]:
        cfg = ctx.config
        tz = ZoneInfo(cfg.site.timezone)
        slot = self.service.current_slot(snap.timestamp)
        if slot is None:
            plan = self.service.plan
            why = "nog geen planning" if plan is None else f"planning niet bruikbaar ({plan.status}: {plan.message})"
            out = self.fallback.decide(snap, ctx)
            for d in out:
                d.reasons.insert(0, f"Terugval op zelfconsumptie: {why}")
                d.source = f"controller:{self.name}:fallback"
            out += self._pv(snap, ctx, None, tz)
            return out
        upcoming = self.service.upcoming(snap.timestamp, 24)
        out = []
        out += self._batteries(snap, ctx, slot, upcoming, tz)
        out += self._pv(snap, ctx, slot, tz)
        out += self._evs(snap, ctx, slot, tz)
        out += self._heat_pumps(snap, ctx, slot, upcoming, tz)
        out = self._peak_shaving(snap, ctx, out)
        return out

    # --------------------------------------------------------------- battery
    def _battery_action(self, slot: dict) -> tuple[CommandAction, float | None, str]:
        """Map the planned battery power onto a device mode by comparing it with what the
        battery's own self-consumption mode (AUTO) would do in that slot."""
        planned = slot["battery_w"]
        ev = sum((slot.get("ev_w") or {}).values())
        surplus = slot["pv_w"] - slot["load_w"] - (slot.get("hp_w") or 0.0) - ev
        auto = surplus                     # AUTO absorbs surplus / covers deficit
        if planned > 200 and planned > auto + 300:
            return CommandAction.BATTERY_CHARGE, round(planned, -1), "grid_charge" if auto <= 0 else "charge"
        if planned < -200 and planned < auto - 300:
            return CommandAction.BATTERY_DISCHARGE, round(-planned, -1), "export"
        if abs(planned) < 100 and abs(auto) > 200:
            return CommandAction.BATTERY_STANDBY, None, "hold"
        return CommandAction.BATTERY_AUTO, None, "self_consumption"

    def _batteries(self, snap, ctx, slot, upcoming, tz) -> list[Decision]:
        devs = [s for s in snap.states(DeviceCategory.BATTERY, DeviceCategory.HYBRID_INVERTER)
                if s.usable and ctx.can(s.device_id, Capability.CONTROL_BATTERY_MODE)]
        if not devs or slot.get("soc_pct") is None:
            return self.fallback._batteries(snap, ctx) if devs else []
        action, value, why = self._battery_action(slot)
        reasons = [f"Importprijs nu {_eur(slot['import_price'])}/kWh, terugleverprijs {_eur(slot['export_price'])}/kWh"]
        if upcoming:
            hi = max(upcoming, key=lambda r: r["import_price"])
            lo = min(upcoming, key=lambda r: r["import_price"])
            reasons.append(f"Verwacht duurste moment {datetime.fromisoformat(hi['start']).astimezone(tz):%H:%M} "
                           f"{_eur(hi['import_price'])}, goedkoopste {datetime.fromisoformat(lo['start']).astimezone(tz):%H:%M} "
                           f"{_eur(lo['import_price'])}")
            pv_kwh = sum(r["pv_forecast_w"] for r in upcoming) / 4000
            reasons.append(f"PV-verwachting komende 24 uur {pv_kwh:.1f} kWh".replace(".", ","))
        if snap.battery_soc_pct is not None:
            reasons.append(f"Huidige SOC {snap.battery_soc_pct:.0f}%, gepland einde kwartier {slot['soc_pct']:.0f}%")
        benefit = self.service.plan.expected_benefit if self.service.plan else None
        if benefit is not None:
            reasons.append(f"Verwacht netto voordeel planning t.o.v. zonder EMS: {_eur(benefit)}")
        summary = {
            "grid_charge": f"Batterij laadt met {(value or 0) / 1000:.1f} kW uit het net",
            "charge": f"Batterij laadt met {(value or 0) / 1000:.1f} kW",
            "export": f"Batterij ontlaadt met {(value or 0) / 1000:.1f} kW (ook naar het net)",
            "hold": "Batterij bewaart haar lading (stand-by)",
            "self_consumption": "Batterij in zelfconsumptie",
        }[why].replace(".", ",")
        data = {"plan_battery_w": slot["battery_w"], "plan_soc_pct": slot["soc_pct"], "mode": why}
        power = (CommandAction.BATTERY_CHARGE, CommandAction.BATTERY_DISCHARGE)
        out = []
        for s in devs:
            if action in power and not ctx.can(s.device_id, Capability.CONTROL_BATTERY_POWER):
                # The device only supports modes, no power setpoint: never send a charge/discharge
                # power command it cannot execute; keep it in its own self-consumption mode.
                out.append(Decision(Command(s.device_id, CommandAction.BATTERY_AUTO),
                                    "Batterij in zelfconsumptie (vermogen instellen niet ondersteund)",
                                    list(reasons) + ["Plan wil laden/ontladen met vast vermogen, maar dit apparaat "
                                                     "ondersteunt geen vermogensopdracht"],
                                    source=f"controller:{self.name}", data={**data, "reason_code": "capability_missing"},
                                    expected_benefit_eur=None))
                continue
            out.append(Decision(Command(s.device_id, action, value if action in power else None),
                                summary, list(reasons), source=f"controller:{self.name}", data=data,
                                expected_benefit_eur=benefit))
        return out

    # -------------------------------------------------------------------- PV
    def _pv(self, snap: SiteSnapshot, ctx: ControlContext, slot: dict | None, tz) -> list[Decision]:
        cfg = ctx.config
        pvs = [s for s in snap.states(DeviceCategory.PV_INVERTER, DeviceCategory.HYBRID_INVERTER)
               if s.usable and ctx.can(s.device_id, Capability.CONTROL_PV_LIMIT)]
        if not pvs:
            return []
        settings = profile_settings(cfg)
        export_price = None if slot is None else slot["export_price"]
        limit_target = export_limit_w(cfg, export_price, settings)
        full_export = cfg.grid.effective_max_export_kw * 1000
        closed_loop = snap.features.get("zero_export_closed_loop", {}).get("available", False)
        total_pv = snap.pv_power_w
        pv_max = sum(float((cfg.device(s.device_id).params.get("sim", {}) | cfg.device(s.device_id).params)
                           .get("peak_power_kw", 10)) * 1000 for s in pvs)
        reasons: list[str] = []
        if limit_target >= full_export:
            limit = None
            self.zero_export.reset()
            if self._any_limited(pvs):
                reasons.append("Teruglevering weer toegestaan: PV-begrenzing opgeheven")
            else:
                return [Decision(Command(s.device_id, CommandAction.PV_LIMIT, None), "PV onbegrensd",
                                 ["Teruglevering toegestaan"], source=f"controller:{self.name}") for s in pvs]
        elif closed_loop:
            limit = self.zero_export.update(total_pv, snap.grid_power_w, limit_target, pv_max)
            reasons.append(f"Terugleverprijs {_eur(export_price)}/kWh — gewenste export "
                           f"{limit_target:.0f} W ±{cfg.strategy.export_tolerance_w:.0f} W (regeling op netmeter)")
            reasons.append(f"Gemeten netvermogen {snap.grid_power_w:.0f} W, PV {total_pv:.0f} W")
        else:
            planned = None if slot is None else slot["pv_w"]
            limit = None if planned is None or slot["curtail_w"] < 100 else planned
            reasons.append("Geen primaire netmeter: alleen vaste begrenzing volgens planning (geen closed-loop)")
        out = []
        for s in pvs:
            share = None if limit is None else (limit * (float(s.get(Metric.PV_POWER_W, 0) or 0) / total_pv
                                                         if total_pv > 1 else limit / len(pvs)))
            share = None if share is None else round(max(0.0, share), -1)
            summary = "PV onbegrensd" if share is None else f"PV begrensd tot {share / 1000:.1f} kW".replace(".", ",")
            out.append(Decision(Command(s.device_id, CommandAction.PV_LIMIT, share), summary, list(reasons),
                                source=f"controller:{self.name}", data={"target_export_w": limit_target}))
        return out

    @staticmethod
    def _any_limited(pvs) -> bool:
        return any(s.get(Metric.PV_LIMIT_W) is not None for s in pvs)

    # -------------------------------------------------------------------- EV
    def _evs(self, snap, ctx, slot, tz) -> list[Decision]:
        cfg = ctx.config
        out = []
        for st in snap.states(DeviceCategory.EV_CHARGER):
            if not st.usable or not ctx.can(st.device_id, Capability.CONTROL_EV_CURRENT):
                continue
            dcfg = cfg.device(st.device_id)
            mode = dcfg.params.get("charge_mode", "smart")
            if mode != "smart":
                d = self.fallback._ev_decision(snap, ctx, st, self.fallback._ev.setdefault(st.device_id, _mem()))
                if st.get(Metric.EV_CONNECTED):
                    out.append(d)
                continue
            if not st.get(Metric.EV_CONNECTED):
                continue
            phases = 3 if dcfg.phase == "3P" and cfg.grid.phases == 3 else 1
            w_per_a = cfg.grid.voltage_v * phases
            planned = float((slot.get("ev_w") or {}).get(st.device_id, 0.0))
            min_a, max_a = float(dcfg.params.get("min_current_a", 6)), float(dcfg.params.get("max_current_a", 16))
            target = 0.0 if planned < min_a * w_per_a * 0.9 else min(max_a, max(min_a, math.floor(planned / w_per_a)))
            reasons = [f"Slim laden: gepland {planned / 1000:.1f} kW bij importprijs {_eur(slot['import_price'])}/kWh",
                       f"Vertrek {dcfg.params.get('departure', '07:30')}, doel {dcfg.params.get('target_soc_pct', 80)}%"]
            if snap.features.get("phase_balancing", {}).get("available"):
                allowed = self.fallback._allowed_current(snap, cfg, dcfg.phase, phases,
                                                         float(st.get(Metric.EV_POWER_W, 0) or 0), max_a)
                if target > allowed:
                    target = float(math.floor(allowed)) if allowed >= min_a else 0.0
                    reasons.append(f"Fasebewaking: begrensd tot {target:.0f} A")
            out.append(Decision(Command(st.device_id, CommandAction.EV_CURRENT, target),
                                f"Laadpaal: {Command(st.device_id, CommandAction.EV_CURRENT, target).describe_nl()}",
                                reasons, source=f"controller:{self.name}", data={"planned_w": planned}))
        return out

    # ------------------------------------------------------------ heat pump
    def _heat_pumps(self, snap, ctx, slot, upcoming, tz) -> list[Decision]:
        out = []
        if slot.get("hp_w") is None:
            return out
        for st in snap.states(DeviceCategory.HEAT_PUMP):
            if not st.usable or not ctx.can(st.device_id, Capability.CONTROL_HP_MODE):
                continue
            ref = slot.get("hp_reference_w") or 0.0
            planned = slot["hp_w"]
            target_c = slot.get("indoor_c")
            if planned > ref * 1.3 + 200:
                mode, why = "boost", "goedkope stroom: woning wordt binnen de comfortgrenzen voorverwarmd"
            elif ref > 300 and planned < ref * 0.5:
                mode, why = "eco", "dure stroom: verwarming tijdelijk teruggeschaald (binnen minimumtemperatuur)"
            else:
                mode, why = "normal", "normale verwarming"
            prev = self._hp_mode.get(st.device_id)
            if prev and prev[0] != mode and snap.timestamp - prev[1] < HP_MIN_DWELL:
                mode, why = prev[0], f"modus vastgehouden (minimaal {HP_MIN_DWELL.seconds // 60} min tussen wisselingen)"
            elif prev is None or prev[0] != mode:
                self._hp_mode[st.device_id] = (mode, snap.timestamp)
            reasons = [why, f"Importprijs {_eur(slot['import_price'])}/kWh",
                       f"Gepland vermogen {planned:.0f} W (normaal {ref:.0f} W)"]
            if target_c is not None:
                reasons.append(f"Verwachte binnentemperatuur {target_c:.1f} °C".replace(".", ","))
            out.append(Decision(Command(st.device_id, CommandAction.HP_MODE, mode), f"Warmtepomp {mode}", reasons,
                                source=f"controller:{self.name}"))
        return out

    # ---------------------------------------------------------- peak shaving
    def _peak_shaving(self, snap, ctx, decisions: list[Decision]) -> list[Decision]:
        cfg = ctx.config
        limit = cfg.strategy.peak_limit_kw
        if limit is None or not snap.features.get("peak_shaving", {}).get("available"):
            return decisions
        excess = (snap.grid_power_w or 0) - limit * 1000
        if excess <= 0 or snap.battery_soc_pct is None or snap.battery_soc_pct <= cfg.battery.reserve_soc:
            return decisions
        out = []
        for d in decisions:
            if d.command.action in (CommandAction.BATTERY_AUTO, CommandAction.BATTERY_STANDBY,
                                    CommandAction.BATTERY_CHARGE, CommandAction.BATTERY_DISCHARGE):
                current = max(0.0, -snap.battery_power_w)
                value = round(current + excess, -1)
                d = Decision(Command(d.command.device_id, CommandAction.BATTERY_DISCHARGE, value),
                             f"Piekbegrenzing: batterij ontlaadt {value / 1000:.1f} kW".replace(".", ","),
                             [f"Netafname {snap.grid_power_w / 1000:.1f} kW boven de grens van {limit:.1f} kW"
                              .replace(".", ",")] + d.reasons, source=d.source, data=d.data)
            out.append(d)
        return out


def _mem():
    from ems.control.self_consumption import _EvMemory
    return _EvMemory()
