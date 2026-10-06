"""Run the complete EMS (real engine, real controllers) against the simulator."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ems.control.base import Controller
from ems.control.self_consumption import SelfConsumptionController
from ems.core.clock import SimulatedClock
from ems.core.config import EMSConfig
from ems.core.engine import EMSEngine
from ems.core.journal import DecisionJournal
from ems.devices.base import DriverContext
from ems.devices.manager import DeviceManager
from ems.simulator.builder import build_site
from ems.simulator.components import SimBaseLoad, SimBattery, SimEVCharger, SimHeatPump, SimPV
from ems.simulator.site import SimulatedSite

ScheduledAction = Callable[["SimulationRunner"], Any]


@dataclass
class SimulationSummary:
    controller: str
    start: str
    hours: float
    pv_kwh: float
    pv_curtailed_kwh: float
    base_load_kwh: float
    heat_pump_kwh: float
    ev_kwh: float
    import_kwh: float
    export_kwh: float
    battery_charged_kwh: float
    battery_discharged_kwh: float
    battery_equivalent_full_cycles: float
    self_consumption_pct: float | None
    self_sufficiency_pct: float | None
    max_phase_current_a: float
    phase_overload_s: float
    indoor_temp_min_c: float | None
    indoor_temp_max_c: float | None
    comfort_deficit_kh: float          # Kelvin-hours below heatpump.min_temperature
    ev_departure_soc_pct: list[float]
    spot_value_eur: float              # indicative: (import - export) x spot, no tariffs
    commands_sent: int
    failsafe_events: int

    def render_nl(self) -> str:
        def pct(v: float | None) -> str:
            return "n.v.t." if v is None else f"{v:.1f} %"
        lines = [
            f"Simulatie ({self.controller}) vanaf {self.start}, {self.hours:.0f} uur",
            f"  PV-opwek               {self.pv_kwh:9.2f} kWh   (afgeregeld {self.pv_curtailed_kwh:.2f} kWh)",
            f"  Huisverbruik           {self.base_load_kwh:9.2f} kWh",
            f"  Warmtepomp             {self.heat_pump_kwh:9.2f} kWh",
            f"  Elektrische auto       {self.ev_kwh:9.2f} kWh",
            f"  Netafname              {self.import_kwh:9.2f} kWh",
            f"  Teruglevering          {self.export_kwh:9.2f} kWh",
            f"  Batterij in / uit      {self.battery_charged_kwh:9.2f} / {self.battery_discharged_kwh:.2f} kWh"
            f"  ({self.battery_equivalent_full_cycles:.2f} cycli)",
            f"  Zelfconsumptie         {pct(self.self_consumption_pct):>13}",
            f"  Zelfvoorzienendheid    {pct(self.self_sufficiency_pct):>13}",
            f"  Max. fasestroom        {self.max_phase_current_a:9.1f} A     (overbelast {self.phase_overload_s:.0f} s)",
        ]
        if self.indoor_temp_min_c is not None:
            lines.append(f"  Binnentemperatuur      {self.indoor_temp_min_c:.1f} – {self.indoor_temp_max_c:.1f} °C"
                         f"  (comforttekort {self.comfort_deficit_kh:.2f} Kh)")
        if self.ev_departure_soc_pct:
            lines.append("  Auto-SOC bij vertrek   " + ", ".join(f"{s:.0f} %" for s in self.ev_departure_soc_pct))
        lines += [
            f"  Spotwaarde (indicatief){self.spot_value_eur:9.2f} EUR  (zonder opslagen/belasting; tariefengine volgt in fase 5)",
            f"  Commando's verstuurd   {self.commands_sent:9d}",
            f"  Fail-safe gebeurtenissen {self.failsafe_events:7d}",
        ]
        return "\n".join(lines)


@dataclass
class SimulationResult:
    summary: SimulationSummary
    rows: list[dict[str, Any]] = field(default_factory=list)
    journal: DecisionJournal | None = None

    def write_csv(self, path: str | Path) -> None:
        if not self.rows:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)

    def write_summary(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self.summary), indent=2, ensure_ascii=False), encoding="utf-8")


class SimulationRunner:
    def __init__(self, config: EMSConfig, start: datetime, *, controller: Controller | None = None,
                 seed: int = 1, step_s: float | None = None, record_every_s: float = 300.0,
                 base_load_kwh: float = 3500.0, journal: DecisionJournal | None = None) -> None:
        self.config = config
        self.clock = SimulatedClock(start)
        self.site: SimulatedSite = build_site(config, start, seed=seed, base_load_kwh=base_load_kwh)
        self.step_s = step_s or config.control.interval_s
        self.control_every = max(1, round(config.control.interval_s / self.step_s))
        self.record_every = max(1, round(record_every_s / self.step_s))
        self.devices = DeviceManager(config, DriverContext(self.clock, simulator=self.site))
        self.engine = EMSEngine(config, self.devices, controller or SelfConsumptionController(), self.clock,
                                journal=journal or DecisionJournal())
        self.schedule: list[tuple[datetime, ScheduledAction]] = []
        self.rows: list[dict[str, Any]] = []
        self._indoor: list[float] = []
        self._deficit_kh = 0.0

    def at(self, when: datetime, action: ScheduledAction) -> None:
        """Schedule an action (fault injection, override, ...) at a sim time."""
        self.schedule.append((when, action))
        self.schedule.sort(key=lambda x: x[0])

    async def run(self, duration: timedelta) -> SimulationResult:
        self.site.advance(0)  # initial physical state
        await self.engine.start()
        steps = int(duration.total_seconds() // self.step_s)
        min_temp = self.config.heatpump.min_temperature
        hps = self.site.of_type(SimHeatPump)
        for i in range(steps):
            while self.schedule and self.schedule[0][0] <= self.clock.now():
                _, action = self.schedule.pop(0)
                res = action(self)
                if hasattr(res, "__await__"):
                    await res
            if i % self.control_every == 0:
                await self.engine.tick()
            if i % self.record_every == 0:
                self.rows.append(self._row())
            self.site.advance(self.step_s)
            self.clock.advance(self.step_s)
            for hp in hps:
                self._indoor.append(hp.indoor_temp_c)
                self._deficit_kh += max(0.0, min_temp - hp.indoor_temp_c) * self.step_s / 3600
        return SimulationResult(self._summary(duration), self.rows, self.engine.journal)

    def _row(self) -> dict[str, Any]:
        s, m = self.site, self.site.meter
        env = s.env_state
        bats = s.of_type(SimBattery)
        cap = sum(b.capacity_kwh for b in bats)
        evs = s.of_type(SimEVCharger)
        hps = s.of_type(SimHeatPump)
        return {
            "timestamp": self.clock.now().isoformat(),
            "local_time": self.clock.now().astimezone(s.env.tz).strftime("%Y-%m-%d %H:%M"),
            "spot_price_eur_kwh": env.spot_price_eur_kwh if env else None,
            "outdoor_temp_c": round(env.outdoor_temp_c, 2) if env else None,
            "pv_w": round(sum(p.output_w for p in s.of_type(SimPV)), 1),
            "pv_available_w": round(sum(p.available_w for p in s.of_type(SimPV)), 1),
            "base_load_w": round(sum(b.output_w for b in s.of_type(SimBaseLoad)), 1),
            "heat_pump_w": round(sum(h.output_w for h in hps), 1),
            "ev_w": round(sum(e.output_w for e in evs), 1),
            "battery_w": round(sum(b.output_w for b in bats), 1),
            "battery_soc_pct": round(sum(b.soc_pct * b.capacity_kwh for b in bats) / cap, 2) if cap else None,
            "grid_w": round(m.power_w, 1),
            "l1_a": round(m.phase_current_a[0], 2),
            "l2_a": round(m.phase_current_a[1], 2),
            "l3_a": round(m.phase_current_a[2], 2),
            "indoor_temp_c": round(hps[0].indoor_temp_c, 2) if hps else None,
            "ev_soc_pct": round(evs[0].soc_pct, 1) if evs and evs[0].connected else None,
            "failsafe": self.engine.failsafe_active,
        }

    def _summary(self, duration: timedelta) -> SimulationSummary:
        s = self.site
        pv = sum(p.energy_kwh for p in s.of_type(SimPV))
        base = sum(b.energy_kwh for b in s.of_type(SimBaseLoad))
        hp = sum(h.energy_kwh for h in s.of_type(SimHeatPump))
        ev = sum(e.energy_kwh for e in s.of_type(SimEVCharger))
        bats = s.of_type(SimBattery)
        ch = sum(b.charged_kwh for b in bats)
        dis = sum(b.discharged_kwh for b in bats)
        cap = sum(b.capacity_kwh for b in bats)
        imp, exp = s.meter.import_kwh, s.meter.export_kwh
        consumption = base + hp + ev
        return SimulationSummary(
            controller=self.engine.controller.name,
            start=self.site.start.isoformat(),
            hours=duration.total_seconds() / 3600,
            pv_kwh=round(pv, 3),
            pv_curtailed_kwh=round(sum(p.curtailed_kwh for p in s.of_type(SimPV)), 3),
            base_load_kwh=round(base, 3), heat_pump_kwh=round(hp, 3), ev_kwh=round(ev, 3),
            import_kwh=round(imp, 3), export_kwh=round(exp, 3),
            battery_charged_kwh=round(ch, 3), battery_discharged_kwh=round(dis, 3),
            battery_equivalent_full_cycles=round((ch + dis) / 2 / cap, 3) if cap else 0.0,
            self_consumption_pct=round(100 * (pv - exp) / pv, 2) if pv > 0.01 else None,
            self_sufficiency_pct=round(100 * (consumption - imp) / consumption, 2) if consumption > 0.01 else None,
            max_phase_current_a=round(s.totals.max_phase_current_a, 2),
            phase_overload_s=s.totals.phase_overload_s,
            indoor_temp_min_c=round(min(self._indoor), 2) if self._indoor else None,
            indoor_temp_max_c=round(max(self._indoor), 2) if self._indoor else None,
            comfort_deficit_kh=round(self._deficit_kh, 3),
            ev_departure_soc_pct=[round(soc, 1) for e in s.of_type(SimEVCharger) for _, soc in e.departures],
            spot_value_eur=round(s.totals.spot_value_eur, 2),
            commands_sent=self.engine.commands_sent,
            failsafe_events=self.engine.failsafe_events,
        )
