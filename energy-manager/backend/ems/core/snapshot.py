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
from ems.gridmeter.meter import GridMeter

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
    grid: GridMeter = field(default_factory=lambda: GridMeter(None))
    features: dict = field(default_factory=dict)
    # Data quality per quantity: GOOD | ESTIMATED | CALCULATED | FORECAST | STALE | INVALID | MISSING | UNKNOWN
    quality: dict[str, str] = field(default_factory=dict)
    balance: dict = field(default_factory=dict)

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
            "grid_device_id": self.grid_device_id,
            "features": self.features,
            "quality": self.quality,
            "balance": self.balance,
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


_FROM_CONFIG = object()


def build_snapshot(config: EMSConfig, states: dict[str, DeviceState], now: datetime,
                   grid_device_id: str | None | object = _FROM_CONFIG) -> SiteSnapshot:
    """``grid_device_id`` is the selected primary grid meter (None = none configured).
    When omitted the legacy ``config.grid_reference()`` lookup is used."""
    snap = SiteSnapshot(timestamp=now, site_id=config.site.id, devices=states)
    missing = snap.missing

    if grid_device_id is _FROM_CONFIG:
        ref = config.grid_reference()
        grid_device_id = ref.id if ref is not None else None
    if grid_device_id is not None and grid_device_id in states:
        snap.grid_device_id = grid_device_id
        st = states[grid_device_id]
        snap.grid = GridMeter.from_state(st)
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
            missing.append(f"{grid_device_id}.{Metric.GRID_POWER_W}")
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
        raw = snap.grid_power_w + snap.pv_power_w - snap.battery_power_w - snap.hp_power_w - snap.ev_power_w
        snap.house_load_w = max(0.0, raw)
        snap.balance = energy_balance(snap, raw)
    snap.quality = data_quality(snap)
    return snap


def _quality_of(states: list[DeviceState], metric: Metric) -> str:
    if not states:
        return "UNKNOWN"
    worst = "GOOD"
    for s in states:
        if s.status.value == "stale":
            worst = "STALE"
        elif not s.usable or s.get(metric) is None:
            return "MISSING"
    return worst


def data_quality(snap: SiteSnapshot) -> dict[str, str]:
    q: dict[str, str] = {}
    if snap.grid_device_id is None:
        q["grid"] = "UNKNOWN"
    else:
        st = snap.devices.get(snap.grid_device_id)
        q["grid"] = ("GOOD" if snap.grid_valid else "STALE" if st is not None and st.status.value == "stale"
                     else "INVALID" if st is not None and st.usable else "MISSING")
    q["pv"] = _quality_of(snap.states(*PV_CATEGORIES), Metric.PV_POWER_W)
    batteries = [s for s in snap.states(*BATTERY_CATEGORIES)
                 if s.category == DeviceCategory.BATTERY or Metric.BATTERY_POWER_W in s.values]
    q["battery"] = _quality_of(batteries, Metric.BATTERY_POWER_W)
    q["soc"] = "UNKNOWN" if not batteries else ("GOOD" if snap.battery_soc_pct is not None else "MISSING")
    q["heat_pump"] = _quality_of(snap.states(DeviceCategory.HEAT_PUMP, DeviceCategory.HEAT_PUMP_BOILER),
                                 Metric.HP_POWER_W)
    q["ev"] = _quality_of(snap.states(DeviceCategory.EV_CHARGER), Metric.EV_POWER_W)
    q["house"] = "CALCULATED" if snap.grid_valid and "MISSING" not in (q["pv"], q["battery"]) else \
        ("ESTIMATED" if snap.grid_valid else "MISSING")
    return q


def energy_balance(snap: SiteSnapshot, raw_house_w: float) -> dict:
    """grid + PV = battery + heat pump + EV + house. The house is the remainder, so a clearly
    negative remainder means the measurements cannot all be right."""
    tolerance = max(300.0, 0.1 * (abs(snap.grid_power_w or 0) + snap.pv_power_w + abs(snap.battery_power_w)))
    ok = raw_house_w >= -tolerance
    hints = []
    if not ok:
        hints = ["tekenconventie van een meter of apparaat omgedraaid (import/export of laden/ontladen)",
                 "PV of batterij dubbel gemeten (bijv. ook al zichtbaar in een submeter)",
                 "verkeerde meetbron gekoppeld als primaire netmeter",
                 "een apparaat ontbreekt of levert verouderde waarden"]
    return {"ok": ok, "residual_w": round(raw_house_w, 1), "tolerance_w": round(tolerance, 1), "hints": hints}
