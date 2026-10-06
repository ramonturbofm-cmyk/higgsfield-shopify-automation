"""Physical component models.

Each component exposes the setpoints a real device would accept (via the
mock drivers) and implements the device's *native* behaviour when the EMS
does not intervene — e.g. the heat pump has its own thermostat, the hybrid
battery its own self-consumption mode. That native behaviour is exactly
what real hardware falls back to when the EMS is gone.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from ems.simulator.environment import Environment, EnvironmentState, hours_since_epoch, smooth_noise

PHASES = ("L1", "L2", "L3")


def phase_split(phase: str, grid_phases: int) -> tuple[float, float, float]:
    """Fraction of a component's power on L1/L2/L3."""
    if grid_phases == 1:
        return (1.0, 0.0, 0.0)
    if phase == "3P":
        return (1 / 3, 1 / 3, 1 / 3)
    return tuple(1.0 if p == phase else 0.0 for p in PHASES)  # type: ignore[return-value]


@dataclass
class Component:
    id: str
    phase: str = "3P"

    def power_w(self) -> float:
        """Signed contribution to grid power (+ = consumption)."""
        raise NotImplementedError


# --------------------------------------------------------------------------- PV
@dataclass
class SimPV(Component):
    peak_power_kw: float = 5.0
    inverter_max_kw: float | None = None
    performance_ratio: float = 0.85
    azimuth_deg: float = 180.0
    limit_w: float | None = None          # EMS setpoint (None = unlimited)
    available_w: float = 0.0
    output_w: float = 0.0
    energy_kwh: float = 0.0
    curtailed_kwh: float = 0.0

    def update(self, env: Environment, t: datetime) -> None:
        shift = (self.azimuth_deg - 180.0) / 90.0 * 1.5  # E/W orientation shifts the peak
        derate = 1.0 - 0.12 * min(1.0, abs(self.azimuth_deg - 180.0) / 90.0)
        dc = self.peak_power_kw * 1000 * env.ghi(t, hour_shift=shift) / 1000.0 * self.performance_ratio * derate
        cap = (self.inverter_max_kw or self.peak_power_kw) * 1000
        self.available_w = max(0.0, min(dc, cap))
        limit = self.available_w if self.limit_w is None else max(0.0, self.limit_w)
        self.output_w = min(self.available_w, limit)

    def integrate(self, dt: float) -> None:
        self.energy_kwh += self.output_w * dt / 3.6e6
        self.curtailed_kwh += (self.available_w - self.output_w) * dt / 3.6e6

    def power_w(self) -> float:
        return -self.output_w


# ------------------------------------------------------------------ base load
@dataclass
class SimBaseLoad(Component):
    annual_kwh: float = 3500.0
    seed: int = 1
    phase_shares: tuple[float, float, float] = (0.5, 0.3, 0.2)
    output_w: float = 0.0
    energy_kwh: float = 0.0
    _spike_until: datetime | None = None
    _spike_w: float = 0.0

    @staticmethod
    def shape(hod: float, weekend: bool) -> float:
        morning = 0.9 * math.exp(-((hod - (8.5 if weekend else 7.5)) ** 2) / 1.5)
        midday = (0.5 if weekend else 0.25) * math.exp(-((hod - 12.5) ** 2) / 3.0)
        evening = 1.5 * math.exp(-((hod - 18.5) ** 2) / 2.5)
        late = 0.6 * math.exp(-((hod - 21.5) ** 2) / 2.0)
        return 0.55 + morning + midday + evening + late

    def update(self, env: Environment, t: datetime) -> None:
        local = t.astimezone(env.tz)
        hod = local.hour + local.minute / 60
        mean_w = self.annual_kwh * 1000 / 8760
        base = mean_w * self.shape(hod, local.weekday() >= 5) / 1.08
        noise = 0.75 + 0.5 * smooth_noise(self.seed, f"load:{self.id}", hours_since_epoch(t), 0.25)
        # Short appliance spikes (kettle, oven, washing machine heater).
        minute_key = int(hours_since_epoch(t) * 60)
        if self._spike_until is None or t >= self._spike_until:
            self._spike_w = 0.0
            rnd = random.Random(f"{self.seed}:{self.id}:spike:{minute_key}")
            if rnd.random() < 0.012 * self.shape(hod, False):
                self._spike_w = rnd.choice((1200.0, 2000.0, 2200.0, 3000.0))
                self._spike_until = t + timedelta(minutes=rnd.randint(2, 12))
        self.output_w = base * noise + self._spike_w

    def integrate(self, dt: float) -> None:
        self.energy_kwh += self.output_w * dt / 3.6e6

    def power_w(self) -> float:
        return self.output_w


# ------------------------------------------------------------------- battery
@dataclass
class SimBattery(Component):
    capacity_kwh: float = 10.0
    max_charge_w: float = 5000.0
    max_discharge_w: float = 5000.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    soc_pct: float = 50.0
    hw_min_soc_pct: float = 5.0
    hw_max_soc_pct: float = 100.0
    auto_min_soc_pct: float = 10.0        # native self-consumption stops here
    temperature_c: float = 22.0
    mode: str = "auto"                    # auto | charge | discharge | standby
    setpoint_w: float = 0.0
    output_w: float = 0.0                 # + charging (AC side)
    charged_kwh: float = 0.0              # AC energy in
    discharged_kwh: float = 0.0           # AC energy out

    def limits(self, dt: float) -> tuple[float, float]:
        """(max charge W, max discharge W) honouring SOC bounds within dt."""
        cap_wh = self.capacity_kwh * 1000
        lo = self.auto_min_soc_pct if self.mode == "auto" else self.hw_min_soc_pct
        room_wh = max(0.0, (self.hw_max_soc_pct - self.soc_pct) / 100 * cap_wh)
        avail_wh = max(0.0, (self.soc_pct - lo) / 100 * cap_wh)
        ch, dis = self.max_charge_w, self.max_discharge_w
        if dt > 0:
            ch = min(ch, room_wh / self.charge_efficiency * 3600 / dt)
            dis = min(dis, avail_wh * self.discharge_efficiency * 3600 / dt)
        else:
            ch = ch if room_wh > 0 else 0.0
            dis = dis if avail_wh > 0 else 0.0
        return ch, dis

    def resolve(self, requested_w: float, dt: float) -> None:
        """requested_w: + charge / - discharge (only used in auto mode)."""
        if self.mode == "charge":
            requested_w = abs(self.setpoint_w)
        elif self.mode == "discharge":
            requested_w = -abs(self.setpoint_w)
        elif self.mode == "standby":
            requested_w = 0.0
        ch, dis = self.limits(dt)
        self.output_w = max(-dis, min(ch, requested_w))

    def integrate(self, dt: float) -> None:
        e_kwh = self.output_w * dt / 3.6e6
        if e_kwh >= 0:
            self.charged_kwh += e_kwh
            self.soc_pct += e_kwh * self.charge_efficiency / self.capacity_kwh * 100
        else:
            self.discharged_kwh += -e_kwh
            self.soc_pct -= -e_kwh / self.discharge_efficiency / self.capacity_kwh * 100
        self.soc_pct = max(0.0, min(100.0, self.soc_pct))
        # Gentle thermal model: losses warm the pack, it relaxes to 20 degC.
        self.temperature_c += dt * (abs(self.output_w) * 2e-7 - (self.temperature_c - 20.0) / 7200)

    def power_w(self) -> float:
        return self.output_w


# ------------------------------------------------------- heat pump + house
@dataclass
class SimHeatPump(Component):
    thermal_max_w: float = 6000.0
    min_modulation: float = 0.3
    heat_loss_w_per_k: float = 150.0
    thermal_capacity_kwh_per_k: float = 7.5
    internal_gains_w: float = 300.0
    solar_aperture_m2: float = 4.0
    comfort_temperature: float = 21.0
    boost_offset: float = 0.8
    eco_offset: float = -0.5
    hysteresis: float = 0.3
    min_run_s: float = 900.0
    min_off_s: float = 600.0
    standby_w: float = 15.0
    frost_protection_c: float = 7.0
    indoor_temp_c: float = 21.0
    outdoor_temp_c: float = 10.0
    mode: str = "normal"                   # normal | boost | eco  (SG-ready-like)
    compressor_on: bool = False
    time_in_state_s: float = 1e9
    thermal_w: float = 0.0
    output_w: float = 0.0
    cop: float = 0.0
    flow_temp_c: float = 20.0
    energy_kwh: float = 0.0
    heat_kwh: float = 0.0
    _solar_gain_w: float = 0.0

    @property
    def setpoint_c(self) -> float:
        offset = {"boost": self.boost_offset, "eco": self.eco_offset}.get(self.mode, 0.0)
        return self.comfort_temperature + offset

    def update(self, env: Environment, t: datetime, env_state: EnvironmentState) -> None:
        self.outdoor_temp_c = env_state.outdoor_temp_c
        self._solar_gain_w = env_state.ghi_w_m2 * self.solar_aperture_m2 * 0.6
        sp = self.setpoint_c
        # Native thermostat with hysteresis and anti-short-cycling.
        if self.compressor_on:
            if self.time_in_state_s >= self.min_run_s and self.indoor_temp_c >= sp + self.hysteresis:
                self.compressor_on, self.time_in_state_s = False, 0.0
        else:
            frost = self.indoor_temp_c < self.frost_protection_c
            if frost or (self.time_in_state_s >= self.min_off_s and self.indoor_temp_c <= sp - self.hysteresis):
                self.compressor_on, self.time_in_state_s = True, 0.0
        self.flow_temp_c = max(25.0, min(55.0, 35.0 + 0.6 * (20.0 - self.outdoor_temp_c)))
        if self.compressor_on:
            demand = self.heat_loss_w_per_k * (sp - self.outdoor_temp_c) + 2500.0 * (sp + self.hysteresis - self.indoor_temp_c)
            self.thermal_w = max(self.min_modulation * self.thermal_max_w, min(self.thermal_max_w, demand))
            t_flow_k = self.flow_temp_c + 273.15
            lift = max(5.0, self.flow_temp_c - self.outdoor_temp_c)
            self.cop = max(1.5, min(6.5, 0.45 * t_flow_k / lift))
            self.output_w = self.thermal_w / self.cop
        else:
            self.thermal_w, self.cop, self.output_w = 0.0, 0.0, self.standby_w
            self.flow_temp_c = self.indoor_temp_c + 2.0

    def integrate(self, dt: float) -> None:
        c_j_per_k = self.thermal_capacity_kwh_per_k * 3.6e6
        q = self.thermal_w + self.internal_gains_w + self._solar_gain_w \
            - self.heat_loss_w_per_k * (self.indoor_temp_c - self.outdoor_temp_c)
        self.indoor_temp_c += q * dt / c_j_per_k
        self.time_in_state_s += dt
        self.energy_kwh += self.output_w * dt / 3.6e6
        self.heat_kwh += self.thermal_w * dt / 3.6e6

    def power_w(self) -> float:
        return self.output_w


# ------------------------------------------------------------------------ EV
def _parse_time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@dataclass
class SimEVCharger(Component):
    battery_kwh: float = 60.0
    arrival_soc_pct: float = 35.0
    car_soc_limit_pct: float = 90.0
    arrive: str = "18:00"
    depart: str = "07:30"
    phases: int = 3
    min_current_a: float = 6.0
    max_current_a: float = 16.0
    voltage_v: float = 230.0
    charge_efficiency: float = 0.9
    current_setpoint_a: float | None = None  # None = native (max)
    connected: bool = False
    soc_pct: float = 35.0
    output_w: float = 0.0
    energy_kwh: float = 0.0
    session_kwh: float = 0.0
    departures: list[tuple[datetime, float]] = field(default_factory=list)

    def _present(self, local_t: time) -> bool:
        a, d = _parse_time(self.arrive), _parse_time(self.depart)
        return (local_t >= a or local_t < d) if a > d else (a <= local_t < d)

    def update(self, env: Environment, t: datetime) -> None:
        present = self._present(t.astimezone(env.tz).time())
        if present and not self.connected:
            self.connected, self.soc_pct, self.session_kwh = True, self.arrival_soc_pct, 0.0
        elif not present and self.connected:
            self.connected = False
            self.departures.append((t, self.soc_pct))
        current = self.max_current_a if self.current_setpoint_a is None else self.current_setpoint_a
        current = min(current, self.max_current_a)
        if self.connected and self.soc_pct < self.car_soc_limit_pct and current >= self.min_current_a:
            self.output_w = self.phases * self.voltage_v * current
        else:
            self.output_w = 0.0

    @property
    def active_current_a(self) -> float:
        return self.output_w / (self.phases * self.voltage_v) if self.output_w else 0.0

    def integrate(self, dt: float) -> None:
        e = self.output_w * dt / 3.6e6
        self.energy_kwh += e
        self.session_kwh += e
        self.soc_pct = min(100.0, self.soc_pct + e * self.charge_efficiency / self.battery_kwh * 100)

    def power_w(self) -> float:
        return self.output_w
