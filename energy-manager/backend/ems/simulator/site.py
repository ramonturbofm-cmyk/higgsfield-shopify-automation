"""SimulatedSite: couples all components behind one grid connection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ems.simulator.components import (
    Component,
    SimBaseLoad,
    SimBattery,
    SimEVCharger,
    SimHeatPump,
    SimPV,
    phase_split,
)
from ems.simulator.environment import Environment, EnvironmentState, hours_since_epoch, smooth_noise


@dataclass
class MeterState:
    power_w: float = 0.0
    phase_power_w: tuple[float, float, float] = (0.0, 0.0, 0.0)
    phase_current_a: tuple[float, float, float] = (0.0, 0.0, 0.0)
    phase_voltage_v: tuple[float, float, float] = (230.0, 230.0, 230.0)
    import_kwh: float = 0.0
    export_kwh: float = 0.0


@dataclass
class SiteTotals:
    seconds: float = 0.0
    max_phase_current_a: float = 0.0
    phase_overload_s: float = 0.0
    import_over_limit_s: float = 0.0
    export_over_limit_s: float = 0.0
    spot_value_eur: float = 0.0          # import*spot - export*spot (indicative)


@dataclass
class SimulatedSite:
    env: Environment
    start: datetime
    grid_phases: int = 3
    ampere_per_phase: float = 25.0
    voltage_v: float = 230.0
    max_import_w: float = 17_250.0
    max_export_w: float = 17_250.0
    line_resistance_ohm: float = 0.25
    meter_id: str | None = None
    components: dict[str, Component] = field(default_factory=dict)
    meter: MeterState = field(default_factory=MeterState)
    totals: SiteTotals = field(default_factory=SiteTotals)
    env_state: EnvironmentState | None = None

    def __post_init__(self) -> None:
        self.now = self.start

    def add(self, component: Component) -> Component:
        if component.id in self.components:
            raise ValueError(f"duplicate component {component.id}")
        self.components[component.id] = component
        return component

    STATE_FIELDS = {
        "SimBattery": ("soc_pct", "temperature_c", "charged_kwh", "discharged_kwh"),
        "SimHeatPump": ("indoor_temp_c", "compressor_on", "time_in_state_s", "energy_kwh", "heat_kwh"),
        "SimEVCharger": ("connected", "soc_pct", "energy_kwh", "session_kwh"),
        "SimPV": ("energy_kwh", "curtailed_kwh"),
        "SimBaseLoad": ("energy_kwh",),
    }

    def carry_state_from(self, old: SimulatedSite) -> None:
        """Keep the physical state (SOC, temperatures, counters) when the site is rebuilt
        after a configuration change, so the demo world does not reset."""
        self.now = old.now
        self.meter.import_kwh, self.meter.export_kwh = old.meter.import_kwh, old.meter.export_kwh
        for cid, comp in self.components.items():
            prev = old.components.get(cid)
            if prev is None or type(prev) is not type(comp):
                continue
            for f in self.STATE_FIELDS.get(type(comp).__name__, ()):
                setattr(comp, f, getattr(prev, f))

    def component(self, component_id: str) -> Component:
        if component_id == self.meter_id:
            return self.meter  # type: ignore[return-value]
        return self.components[component_id]

    def of_type(self, cls: type) -> list:
        return [c for c in self.components.values() if isinstance(c, cls)]

    def advance(self, dt: float) -> None:
        """Compute powers for [now, now+dt) from current setpoints, integrate, step time."""
        t = self.now
        env_state = self.env.state(t)
        self.env_state = env_state
        for pv in self.of_type(SimPV):
            pv.update(self.env, t)
        for load in self.of_type(SimBaseLoad):
            load.update(self.env, t)
        for hp in self.of_type(SimHeatPump):
            hp.update(self.env, t, env_state)
        for ev in self.of_type(SimEVCharger):
            ev.update(self.env, t)

        batteries: list[SimBattery] = self.of_type(SimBattery)
        non_battery = sum(c.power_w() for c in self.components.values() if not isinstance(c, SimBattery))
        auto = [b for b in batteries if b.mode == "auto"]
        fixed = [b for b in batteries if b.mode != "auto"]
        for b in fixed:
            b.resolve(0.0, dt)
        # Native self-consumption: auto batteries jointly steer grid power to 0.
        residual = -(non_battery + sum(b.output_w for b in fixed))
        total_max = sum(b.max_charge_w if residual > 0 else b.max_discharge_w for b in auto) or 1.0
        for b in auto:
            share = (b.max_charge_w if residual > 0 else b.max_discharge_w) / total_max
            b.resolve(residual * share, dt)

        self._update_meter(dt, env_state)

        for c in self.components.values():
            c.integrate(dt)  # type: ignore[attr-defined]
        self.now = t + timedelta(seconds=dt)

    def _update_meter(self, dt: float, env_state: EnvironmentState) -> None:
        per_phase = [0.0, 0.0, 0.0]
        for c in self.components.values():
            p = c.power_w()
            if isinstance(c, SimBaseLoad):
                shares = c.phase_shares if self.grid_phases == 3 else (1.0, 0.0, 0.0)
            else:
                shares = phase_split(c.phase, self.grid_phases)
            for i in range(3):
                per_phase[i] += p * shares[i]
        volts, amps = [], []
        h = hours_since_epoch(self.now)
        for n, p in enumerate(per_phase):
            i_est = p / self.voltage_v
            # Distribution grid voltage wanders a few volts independently of the site.
            v_grid = self.voltage_v + 4.0 * (smooth_noise(self.env.seed, f"volt:{n}", h, 0.05) - 0.5) + 2.0
            v = v_grid - self.line_resistance_ohm * i_est
            volts.append(v)
            amps.append(p / v)
        m = self.meter
        m.power_w = sum(per_phase)
        m.phase_power_w = tuple(per_phase)  # type: ignore[assignment]
        m.phase_current_a = tuple(amps)  # type: ignore[assignment]
        m.phase_voltage_v = tuple(volts)  # type: ignore[assignment]
        e_kwh = m.power_w * dt / 3.6e6
        if e_kwh >= 0:
            m.import_kwh += e_kwh
        else:
            m.export_kwh += -e_kwh

        tot = self.totals
        tot.seconds += dt
        tot.spot_value_eur += e_kwh * env_state.spot_price_eur_kwh
        peak = max(abs(a) for a in amps)
        tot.max_phase_current_a = max(tot.max_phase_current_a, peak)
        if peak > self.ampere_per_phase:
            tot.phase_overload_s += dt
        if m.power_w > self.max_import_w:
            tot.import_over_limit_s += dt
        if -m.power_w > self.max_export_w:
            tot.export_over_limit_s += dt
