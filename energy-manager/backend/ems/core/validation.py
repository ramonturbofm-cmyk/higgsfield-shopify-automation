"""Sensor validation: plausibility ranges and frozen-value detection.

Unrealistic data must never drive control decisions. Values outside their
physical range are rejected; a meter that keeps reporting the exact same
power for minutes is treated as stale.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from ems.core.models import Metric

# Generous physical limits — values outside these are certainly wrong.
PLAUSIBLE_RANGES: dict[Metric, tuple[float, float]] = {
    Metric.GRID_POWER_W: (-250_000, 250_000),
    Metric.GRID_IMPORT_POWER_W: (0, 250_000),
    Metric.GRID_EXPORT_POWER_W: (0, 250_000),
    Metric.GRID_VOLTAGE_L1_V: (150, 300),
    Metric.GRID_VOLTAGE_L2_V: (150, 300),
    Metric.GRID_VOLTAGE_L3_V: (150, 300),
    Metric.GRID_CURRENT_L1_A: (-400, 400),
    Metric.GRID_CURRENT_L2_A: (-400, 400),
    Metric.GRID_CURRENT_L3_A: (-400, 400),
    Metric.GRID_POWER_L1_W: (-100_000, 100_000),
    Metric.GRID_POWER_L2_W: (-100_000, 100_000),
    Metric.GRID_POWER_L3_W: (-100_000, 100_000),
    Metric.GRID_IMPORT_ENERGY_KWH: (0, 1e9),
    Metric.GRID_EXPORT_ENERGY_KWH: (0, 1e9),
    Metric.PV_POWER_W: (0, 250_000),
    Metric.PV_ENERGY_KWH: (0, 1e9),
    Metric.BATTERY_POWER_W: (-250_000, 250_000),
    Metric.BATTERY_SOC_PCT: (0, 100),
    Metric.BATTERY_TEMPERATURE_C: (-40, 90),
    Metric.HP_POWER_W: (0, 50_000),
    Metric.HP_THERMAL_POWER_W: (0, 150_000),
    Metric.HP_COP: (0, 15),
    Metric.HP_SETPOINT_C: (0, 80),
    Metric.INDOOR_TEMP_C: (-30, 60),
    Metric.OUTDOOR_TEMP_C: (-50, 60),
    Metric.FLOW_TEMP_C: (-10, 95),
    Metric.RETURN_TEMP_C: (-10, 95),
    Metric.EV_POWER_W: (0, 400_000),
    Metric.EV_SOC_PCT: (0, 100),
    Metric.EV_CURRENT_LIMIT_A: (0, 100),
    Metric.EV_SESSION_ENERGY_KWH: (0, 500),
    Metric.LOAD_POWER_W: (0, 100_000),
}


def validate_values(values: dict[Metric, Any]) -> tuple[dict[Metric, Any], dict[Metric, Any]]:
    """Split readings into (accepted, rejected)."""
    ok: dict[Metric, Any] = {}
    bad: dict[Metric, Any] = {}
    for metric, value in values.items():
        rng = PLAUSIBLE_RANGES.get(metric)
        if rng is None or value is None:
            ok[metric] = value
            continue
        if isinstance(value, bool):      # a flag where a number is expected
            bad[metric] = value
            continue
        try:
            f = float(value)             # also accepts numeric strings ("12.5")
        except (TypeError, ValueError):
            bad[metric] = value
            continue
        if math.isnan(f) or math.isinf(f) or not rng[0] <= f <= rng[1]:
            bad[metric] = value
        else:
            ok[metric] = f
    return ok, bad


class FrozenValueDetector:
    """Flags a device whose measurement fingerprint has not changed for too long.

    A single metric is not enough: a battery in self-consumption mode can
    legitimately hold grid power at exactly 0 W for hours. The fingerprint
    therefore combines power, phase voltages and energy counters — mains
    voltage always fluctuates, so an identical fingerprint means the data
    source (P1 port, gateway, Modbus bridge) has stopped updating.
    """

    FINGERPRINT = (
        Metric.GRID_POWER_W,
        Metric.GRID_VOLTAGE_L1_V, Metric.GRID_VOLTAGE_L2_V, Metric.GRID_VOLTAGE_L3_V,
        Metric.GRID_IMPORT_ENERGY_KWH, Metric.GRID_EXPORT_ENERGY_KWH,
    )

    def __init__(self, frozen_after_s: float) -> None:
        self.frozen_after_s = frozen_after_s
        self._last: dict[str, tuple[tuple[Any, ...], datetime]] = {}

    def is_frozen(self, device_id: str, values: dict[Metric, Any], now: datetime) -> bool:
        if Metric.GRID_POWER_W not in values:
            return False
        fingerprint = tuple(values.get(m) for m in self.FINGERPRINT)
        prev = self._last.get(device_id)
        if prev is None or prev[0] != fingerprint:
            self._last[device_id] = (fingerprint, now)
            return False
        return (now - prev[1]).total_seconds() >= self.frozen_after_s

    def reset(self, device_id: str) -> None:
        self._last.pop(device_id, None)
