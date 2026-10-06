"""Rule-based self-consumption controller (phase-1 strategy).

* Batteries run in their native self-consumption mode.
* EV chargers follow the configured charge mode:
    - ``pv_only``: charge only from PV surplus, with EMA smoothing,
      start/stop delays and a minimum-current hysteresis (no flapping),
    - ``min_pv``: always at least the minimum current, plus PV surplus
      (avoids exporting surpluses too small for a 3-phase car),
    - ``max``: charge at maximum current,
    - ``off``: paused.
* Dynamic load balancing: EV current is always capped so no phase exceeds
  the fuse rating minus the safety margin and total import stays within
  the configured maximum.

Superseded for economic decisions by the optimizer from phase 6 onwards;
the phase guard stays as a safety layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ems.control.base import ControlContext, Controller
from ems.core.models import Capability, Command, CommandAction, Decision, DeviceCategory, DeviceState, Metric
from ems.core.snapshot import SiteSnapshot

_PHASE_INDEX = {"L1": 0, "L2": 1, "L3": 2}


@dataclass
class _EvMemory:
    ema_w: float | None = None
    charging: bool = False
    above_since: datetime | None = None
    below_since: datetime | None = None


class SelfConsumptionController(Controller):
    name = "self_consumption"
    EMA_ALPHA = 0.3
    START_DELAY_S = 60.0
    STOP_DELAY_S = 180.0
    START_MARGIN_W = 300.0

    def __init__(self) -> None:
        self._ev: dict[str, _EvMemory] = {}

    def decide(self, snap: SiteSnapshot, ctx: ControlContext) -> list[Decision]:
        decisions = self._batteries(snap, ctx)
        if snap.grid_valid:
            decisions += self._evs(snap, ctx)
        return decisions

    # ----------------------------------------------------------- batteries
    def _batteries(self, snap: SiteSnapshot, ctx: ControlContext) -> list[Decision]:
        out = []
        for st in snap.states(DeviceCategory.BATTERY, DeviceCategory.HYBRID_INVERTER):
            if not st.usable or not ctx.can(st.device_id, Capability.CONTROL_BATTERY_MODE):
                continue
            soc = st.get(Metric.BATTERY_SOC_PCT)
            reasons = ["Strategie zelfconsumptie: batterij vangt PV-overschot op en dekt het huisverbruik"]
            if soc is not None:
                reasons.append(f"Huidige SOC {soc:.0f}%")
            out.append(Decision(Command(st.device_id, CommandAction.BATTERY_AUTO),
                                "Batterij in zelfconsumptiemodus", reasons,
                                source=f"controller:{self.name}"))
        return out

    # ----------------------------------------------------------------- EVs
    def _evs(self, snap: SiteSnapshot, ctx: ControlContext) -> list[Decision]:
        out = []
        for st in snap.states(DeviceCategory.EV_CHARGER):
            if not st.usable or not ctx.can(st.device_id, Capability.CONTROL_EV_CURRENT):
                continue
            mem = self._ev.setdefault(st.device_id, _EvMemory())
            if not st.get(Metric.EV_CONNECTED, False):
                self._ev[st.device_id] = _EvMemory()
                continue
            out.append(self._ev_decision(snap, ctx, st, mem))
        return out

    def _ev_decision(self, snap: SiteSnapshot, ctx: ControlContext, st: DeviceState, mem: _EvMemory) -> Decision:
        cfg = ctx.config
        dcfg = cfg.device(st.device_id)
        p = dcfg.params
        mode = p.get("charge_mode", "pv_only")
        min_a = float(p.get("min_current_a", 6))
        max_a = float(p.get("max_current_a", 16))
        phases = 3 if dcfg.phase == "3P" and cfg.grid.phases == 3 else 1
        volt = cfg.grid.voltage_v
        w_per_a = volt * phases
        ev_w = float(st.get(Metric.EV_POWER_W, 0.0) or 0.0)
        now = snap.timestamp
        reasons: list[str] = []
        data: dict = {"mode": mode}

        if mode in ("max", "smart"):
            target = max_a
            reasons.append(f"Laadmodus 'maximaal': {max_a:.0f} A" if mode == "max" else
                           "Slim laden zonder planning: normaal laden om het vertrekdoel te halen")
        elif mode == "off":
            target = 0.0
            reasons.append("Laadmodus 'uit'")
        elif mode == "min_pv":
            surplus = self._surplus(snap, cfg, ev_w)
            target = min(max_a, max(min_a, float(math.floor(surplus / w_per_a))))
            data["surplus_w"] = round(surplus, 1)
            reasons.append(f"Laadmodus 'minimum + PV': PV-overschot {surplus / 1000:.1f} kW → {target:.0f} A")
        else:
            target = self._pv_only_target(snap, cfg, mem, ev_w, w_per_a, min_a, max_a, now, reasons, data)

        allowed = self._allowed_current(snap, cfg, dcfg.phase, phases, ev_w, max_a)
        data["allowed_a"] = round(allowed, 2)
        if target > allowed:
            if allowed >= min_a:
                reasons.append(f"Fasebewaking: laadstroom begrensd tot {math.floor(allowed):.0f} A "
                               f"(max {cfg.grid.max_phase_current_a:.1f} A per fase)")
                target = float(math.floor(allowed))
            else:
                reasons.append(f"Fasebewaking: onvoldoende ruimte op de aansluiting "
                               f"({allowed:.1f} A beschikbaar, minimaal {min_a:.0f} A nodig) — laden gepauzeerd")
                target = 0.0
                mem.charging = False
        summary = f"Laadpaal: {Command(st.device_id, CommandAction.EV_CURRENT, target).describe_nl()}"
        return Decision(Command(st.device_id, CommandAction.EV_CURRENT, target), summary, reasons,
                        source=f"controller:{self.name}", data=data)

    def _pv_only_target(self, snap, cfg, mem: _EvMemory, ev_w, w_per_a, min_a, max_a, now, reasons, data) -> float:
        surplus = self._surplus(snap, cfg, ev_w)
        mem.ema_w = surplus if mem.ema_w is None else (
            self.EMA_ALPHA * surplus + (1 - self.EMA_ALPHA) * mem.ema_w)
        avail_a = mem.ema_w / w_per_a
        data.update(surplus_w=round(surplus, 1), smoothed_surplus_w=round(mem.ema_w, 1))
        start_w = min_a * w_per_a + self.START_MARGIN_W

        if mem.charging:
            if avail_a >= min_a:
                mem.below_since = None
                target = min(max_a, float(math.floor(avail_a)))
                reasons.append(f"PV-overschot {mem.ema_w / 1000:.1f} kW → {target:.0f} A")
                return target
            mem.below_since = mem.below_since or now
            waited = (now - mem.below_since).total_seconds()
            if waited >= self.STOP_DELAY_S:
                mem.charging, mem.below_since = False, None
                reasons.append(f"PV-overschot {mem.ema_w / 1000:.1f} kW al {waited:.0f} s onder minimum — laden gestopt")
                return 0.0
            reasons.append(f"PV-overschot tijdelijk laag ({mem.ema_w / 1000:.1f} kW); "
                           f"minimale stroom {min_a:.0f} A aangehouden (hysterese)")
            return min_a

        if mem.ema_w >= start_w:
            mem.above_since = mem.above_since or now
            waited = (now - mem.above_since).total_seconds()
            if waited >= self.START_DELAY_S:
                mem.charging, mem.above_since = True, None
                target = min(max_a, float(math.floor(avail_a)))
                reasons.append(f"PV-overschot {mem.ema_w / 1000:.1f} kW stabiel — laden gestart met {target:.0f} A")
                return target
            reasons.append(f"PV-overschot {mem.ema_w / 1000:.1f} kW, wacht {self.START_DELAY_S - waited:.0f} s op stabiliteit")
            return 0.0
        mem.above_since = None
        reasons.append(f"Onvoldoende PV-overschot ({mem.ema_w / 1000:.1f} kW, start vanaf {start_w / 1000:.1f} kW)")
        return 0.0

    @staticmethod
    def _surplus(snap: SiteSnapshot, cfg, ev_w: float) -> float:
        """PV power the EV could use, honouring the configured surplus priority."""
        prio = cfg.strategy.surplus_priority
        ev_before_battery = "ev" in prio and ("battery" not in prio or prio.index("ev") < prio.index("battery"))
        bat_w = snap.battery_power_w
        battery_full = snap.battery_soc_pct is not None and snap.battery_soc_pct >= cfg.battery.max_soc - 1
        surplus = ev_w - (snap.grid_power_w or 0.0)
        surplus += min(0.0, bat_w)          # battery discharging is never real surplus
        if ev_before_battery or battery_full:
            surplus += max(0.0, bat_w)      # EV may take what the battery absorbs
        return surplus

    @staticmethod
    def _allowed_current(snap: SiteSnapshot, cfg, phase: str, phases: int, ev_w: float, max_a: float) -> float:
        volt = cfg.grid.voltage_v
        ev_a = ev_w / (volt * phases) if ev_w > 0 else 0.0
        allowed = max_a
        if snap.phase_currents_a is not None:
            idx = [0, 1, 2] if phases == 3 else [_PHASE_INDEX.get(phase, 0) if cfg.grid.phases == 3 else 0]
            limit = cfg.grid.max_phase_current_a
            allowed = min(allowed, *(limit - (snap.phase_currents_a[i] - ev_a) for i in idx))
        if snap.grid_power_w is not None:
            import_room_w = cfg.grid.effective_max_import_kw * 1000 - (snap.grid_power_w - ev_w)
            allowed = min(allowed, import_room_w / (volt * phases))
        return max(0.0, allowed)
