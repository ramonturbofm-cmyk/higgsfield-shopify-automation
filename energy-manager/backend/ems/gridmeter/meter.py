"""GridMeter interface, primary-meter selection and feature gating."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from ems.core.models import DeviceCategory, DeviceState, Metric

if TYPE_CHECKING:
    from ems.core.config import EMSConfig
    from ems.devices.registry import DriverRegistry

_PHASE_POWER = (Metric.GRID_POWER_L1_W, Metric.GRID_POWER_L2_W, Metric.GRID_POWER_L3_W)
_PHASE_CURRENT = (Metric.GRID_CURRENT_L1_A, Metric.GRID_CURRENT_L2_A, Metric.GRID_CURRENT_L3_A)
_PHASE_VOLTAGE = (Metric.GRID_VOLTAGE_L1_V, Metric.GRID_VOLTAGE_L2_V, Metric.GRID_VOLTAGE_L3_V)


def _triple(values: dict, metrics: tuple[Metric, ...]) -> tuple[float | None, float | None, float | None] | None:
    t = tuple(None if values.get(m) is None else float(values[m]) for m in metrics)
    return None if all(v is None for v in t) else t  # type: ignore[return-value]


@dataclass(frozen=True)
class GridMeterReading:
    device_id: str
    timestamp: datetime                 # when the EMS received it
    power_w: float                      # + import / - export (sum of phases)
    phase_power_w: tuple[float | None, float | None, float | None] | None = None
    phase_current_a: tuple[float | None, float | None, float | None] | None = None
    phase_voltage_v: tuple[float | None, float | None, float | None] | None = None
    import_kwh: float | None = None
    export_kwh: float | None = None

    @property
    def import_power_w(self) -> float:
        return max(0.0, self.power_w)

    @property
    def export_power_w(self) -> float:
        return max(0.0, -self.power_w)


class GridMeter:
    """Read-only adapter around the primary meter's latest valid DeviceState."""

    def __init__(self, reading: GridMeterReading | None) -> None:
        self.reading = reading

    @classmethod
    def from_state(cls, state: DeviceState | None) -> GridMeter:
        if state is None or not state.usable or state.get(Metric.GRID_POWER_W) is None:
            return cls(None)
        v = state.values
        return cls(GridMeterReading(
            device_id=state.device_id, timestamp=state.last_update,  # type: ignore[arg-type]
            power_w=float(v[Metric.GRID_POWER_W]),
            phase_power_w=_triple(v, _PHASE_POWER), phase_current_a=_triple(v, _PHASE_CURRENT),
            phase_voltage_v=_triple(v, _PHASE_VOLTAGE),
            import_kwh=None if v.get(Metric.GRID_IMPORT_ENERGY_KWH) is None else float(v[Metric.GRID_IMPORT_ENERGY_KWH]),
            export_kwh=None if v.get(Metric.GRID_EXPORT_ENERGY_KWH) is None else float(v[Metric.GRID_EXPORT_ENERGY_KWH]),
        ))

    @property
    def available(self) -> bool:
        return self.reading is not None

    def get_power(self) -> float | None:
        return None if self.reading is None else self.reading.power_w

    def get_import_power(self) -> float | None:
        return None if self.reading is None else self.reading.import_power_w

    def get_export_power(self) -> float | None:
        return None if self.reading is None else self.reading.export_power_w

    def get_phase_power(self):
        return None if self.reading is None else self.reading.phase_power_w

    def get_phase_current(self):
        return None if self.reading is None else self.reading.phase_current_a

    def get_phase_voltage(self):
        return None if self.reading is None else self.reading.phase_voltage_v

    def get_energy_totals(self) -> tuple[float | None, float | None] | None:
        return None if self.reading is None else (self.reading.import_kwh, self.reading.export_kwh)

    def age_s(self, now: datetime) -> float | None:
        return None if self.reading is None else (now - self.reading.timestamp).total_seconds()


class GridMeterStatus(StrEnum):
    PRIMARY_EXPLICIT = "primary_explicit"     # chosen by the user
    PRIMARY_AUTO = "primary_auto"             # auto-selected (HomeWizard P1 / DSMR / simulated), confirm in UI
    NO_PRIMARY_GRID_METER = "no_primary_grid_meter"


# Preference order for automatic selection. Generic Modbus/MQTT/REST meters may be
# sub-meters, so they are only *offered* as candidates, never auto-selected.
AUTO_KINDS = ("homewizard_p1", "dsmr", "simulated")
OFFER_ONLY_KINDS = ("modbus", "mqtt", "rest")


@dataclass
class GridMeterSelection:
    device_id: str | None
    status: GridMeterStatus
    kind: str | None = None
    reason: str = ""
    candidates: list[dict] = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        return self.status == GridMeterStatus.PRIMARY_AUTO

    def to_dict(self) -> dict:
        return {"device_id": self.device_id, "status": self.status.value, "kind": self.kind, "reason": self.reason,
                "needs_confirmation": self.needs_confirmation, "candidates": self.candidates}


def select_primary_grid_meter(config: EMSConfig, registry: DriverRegistry) -> GridMeterSelection:
    candidates = []
    for dev in config.devices:
        if not dev.enabled:
            continue
        try:
            manifest = registry.get(dev.driver).manifest
        except KeyError:
            continue
        kind = manifest.grid_meter_kind
        if kind is None and dev.role not in ("primary_grid_meter", "grid_reference"):
            continue
        candidates.append({"device_id": dev.id, "name": dev.name, "kind": kind, "driver": dev.driver})
        if dev.role in ("primary_grid_meter", "grid_reference"):
            return GridMeterSelection(dev.id, GridMeterStatus.PRIMARY_EXPLICIT, kind,
                                      f"{dev.name} is door de gebruiker ingesteld als primaire netmeter", candidates)
    for kind in AUTO_KINDS:
        for cand in candidates:
            if cand["kind"] == kind:
                label = {"homewizard_p1": "HomeWizard P1 Meter", "dsmr": "directe DSMR/P1-aansluiting",
                         "simulated": "gesimuleerde slimme meter"}[kind]
                return GridMeterSelection(cand["device_id"], GridMeterStatus.PRIMARY_AUTO, kind,
                                          f"Automatisch gekozen: {label} ({cand['name']}). Bevestig dit in Apparaten.",
                                          candidates)
    offer = [c for c in candidates if c["kind"] in OFFER_ONLY_KINDS]
    reason = "Geen primaire netmeter ingesteld. Sommige EMS-functies zijn beperkt."
    if offer:
        reason += " Mogelijke netmeter: " + ", ".join(c["name"] for c in offer) + " — stel deze in als primaire netmeter."
    return GridMeterSelection(None, GridMeterStatus.NO_PRIMARY_GRID_METER, None, reason, candidates)


GRID_FEATURES = {
    "zero_export_closed_loop": "Nauwkeurige zero-export (closed-loop)",
    "peak_shaving": "Piekbegrenzing van de netafname",
    "phase_balancing": "Fasebewaking / load balancing",
    "grid_feedback": "Terugkoppeling op het netvermogen",
}


def feature_availability(selection: GridMeterSelection, grid_valid: bool,
                         has_phase_data: bool) -> dict[str, dict]:
    """Which grid-dependent functions may run right now, and why not."""
    out = {}
    for key, label in GRID_FEATURES.items():
        if selection.status == GridMeterStatus.NO_PRIMARY_GRID_METER:
            ok, why = False, "geen primaire netmeter ingesteld"
        elif not grid_valid:
            ok, why = False, "netmeting ontbreekt of is verouderd"
        elif key == "phase_balancing" and not has_phase_data:
            ok, why = False, "netmeter levert geen fasestromen"
        else:
            ok, why = True, ""
        out[key] = {"label": label, "available": ok, "reason": why}
    return out


def is_grid_meter_category(category: DeviceCategory) -> bool:
    return category in (DeviceCategory.SMART_METER, DeviceCategory.ENERGY_METER)
