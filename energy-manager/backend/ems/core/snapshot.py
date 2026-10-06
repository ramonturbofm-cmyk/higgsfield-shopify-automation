"""SiteSnapshot: one consistent picture of the whole energy system.

All devices (several PV arrays, batteries, EVs ...) are aggregated into a
single view so controllers and the optimizer treat the site as one system.
The grid reference meter is the single source of truth at the connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ems.core.config import EMSConfig
from ems.core.models import DeviceCategory, DeviceState, Metric

_PHASE_CURRENTS = (Metric.GRID_CURRENT_L1_A, Metric.GRID_CURRENT_L2_A, Metric.GRID_CURRENT_L3_A)

PV_CATEGORIES = (DeviceCategory.PV_INVERTER, DeviceCategory.HYBRID_INVERTER)
BATTERY_CATEGORIES = (DeviceCategory.BATTERY, DeviceCategory.HYBRID_INVERTER)


@dataclass(slots=True)
class SiteSnapshot:
    timestamp: datetime
    site_id: str
    devices: dict[str, DeviceState]
    grid_device_id: str | None = None
    grid_valid: bool = False
    grid_power_w: float | None = None
    phase_currents_a: tuple[float, float, float] | None = None
    pv_power_w: float = 0.0
    battery_power_w: float = 0.0
    battery_soc_pct: float | None = None
    battery_capacity_kwh: float = 0.0
    hp_power_w: float = 0.0
    ev_power_w: float = 0.0
    indoor_temp_c: float | None = None
    outdoor_temp_c: float | None = None
    house_load_w: float | None = None
    missing: list[str] = field(default_factory=list)

    def states(self, *categories: DeviceCategory) -> list[DeviceState]:
        return [s for s in self.devices.values() if s.category in categories]

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "site_id": self.site_id,
            "grid_valid": self.grid_valid,
            "grid_power_w": self.grid_power_w,
            "phase_currents_a": self.phase_currents_a,
            "pv_power_w": self.pv_power_w,
            "battery_power_w": self.battery_power_w,
            "battery_soc_pct": self.battery_soc_pct,
            "hp_power_w": self.hp_power_w,
            "ev_power_w": self.ev_power_w,
            "house_load_w": self.house_load_w,
            "indoor_temp_c": self.indoor_temp_c,
            "outdoor_temp_c": self.outdoor_temp_c,
            "devices": {k: {"status": v.status, "error": v.error} for k, v in self.devices.items()},
        }


def _sum(states: list[DeviceState], metric: Metric, missing: list[str]) -> float:
    total = 0.0
    for s in states:
        value = s.get(metric) if s.usable else None
        if value is None:
            missing.append(f"{s.device_id}.{metric}")
        else:
            total += float(value)
    return total


def build_snapshot(config: EMSConfig, states: dict[str, DeviceState], now: datetime) -> SiteSnapshot:
    snap = SiteSnapshot(timestamp=now, site_id=config.site.id, devices=states)
    missing = snap.missing

    ref = config.grid_reference()
    if ref is not None and ref.id in states:
        snap.grid_device_id = ref.id
        st = states[ref.id]
        power = st.get(Metric.GRID_POWER_W) if st.usable else None
        if power is not None:
            snap.grid_valid = True
            snap.grid_power_w = float(power)
            currents = [st.get(m) for m in _PHASE_CURRENTS]
            if config.grid.phases == 1 and currents[0] is not None:
                snap.phase_currents_a = (float(currents[0]), 0.0, 0.0)
            elif all(c is not None for c in currents):
                snap.phase_currents_a = tuple(float(c) for c in currents)  # type: ignore[assignment]
        else:
            missing.append(f"{ref.id}.{Metric.GRID_POWER_W}")
    else:
        missing.append("grid_reference")

    snap.pv_power_w = _sum(snap.states(*PV_CATEGORIES), Metric.PV_POWER_W, [])
    batteries = [s for s in snap.states(*BATTERY_CATEGORIES) if Metric.BATTERY_POWER_W in s.values
                 or s.category == DeviceCategory.BATTERY]
    snap.battery_power_w = _sum(batteries, Metric.BATTERY_POWER_W, missing)

    # Capacity-weighted SOC across all batteries.
    energy = cap_total = 0.0
    for s in batteries:
        soc = s.get(Metric.BATTERY_SOC_PCT) if s.usable else None
        cap = float(config.device(s.device_id).params.get("capacity_kwh", 0) or 0)
        if soc is not None and cap > 0:
            energy += cap * float(soc) / 100.0
            cap_total += cap
    if cap_total > 0:
        snap.battery_soc_pct = 100.0 * energy / cap_total
        snap.battery_capacity_kwh = cap_total

    hps = snap.states(DeviceCategory.HEAT_PUMP, DeviceCategory.HEAT_PUMP_BOILER)
    snap.hp_power_w = _sum(hps, Metric.HP_POWER_W, missing)
    for s in hps:
        if not s.usable:
            continue
        if (indoor := s.get(Metric.INDOOR_TEMP_C)) is not None:
            snap.indoor_temp_c = float(indoor)
        if (outdoor := s.get(Metric.OUTDOOR_TEMP_C)) is not None:
            snap.outdoor_temp_c = float(outdoor)

    snap.ev_power_w = _sum(snap.states(DeviceCategory.EV_CHARGER), Metric.EV_POWER_W, missing)

    # House load = everything not separately measured.
    if snap.grid_valid:
        snap.house_load_w = max(
            0.0,
            snap.grid_power_w + snap.pv_power_w - snap.battery_power_w - snap.hp_power_w - snap.ev_power_w,
        )
    return snap
