"""Value mapping shared by the generic drivers.

A mapping entry translates one raw value into one EMS metric::

    {"metric": "grid_power_w", "scale": 1.0, "offset": 0.0, "invert": false, ...source fields}

``scale``/``offset`` convert units (raw * scale + offset); ``invert`` flips the sign so
the EMS sign convention holds (grid: + import / − export, battery: + charge).
If only import and export power are mapped, ``grid_power_w`` = import − export.
"""

from __future__ import annotations

import json
from typing import Any

from ems.core.models import CAPABILITY_METRICS, Capability, DeviceCategory, Metric
from ems.devices.base import DriverError

READ_CATEGORIES = (
    DeviceCategory.SMART_METER, DeviceCategory.PV_INVERTER, DeviceCategory.HYBRID_INVERTER,
    DeviceCategory.BATTERY, DeviceCategory.HEAT_PUMP, DeviceCategory.HEAT_PUMP_BOILER,
    DeviceCategory.EV_CHARGER,
)
BOOL_METRICS = {Metric.EV_CONNECTED, Metric.HP_COMPRESSOR_ON}
TEXT_METRICS = {Metric.BATTERY_MODE, Metric.HP_MODE}

VALUES_SCHEMA = {
    "type": "array", "label_nl": "Waardetoewijzing (JSON)",
    "help_nl": "Lijst van {metric, ...bron, scale, offset, invert}. Neem adressen/paden/topics uitsluitend "
               "over uit de officiële documentatie van het apparaat.",
}


class MappingError(DriverError):
    """Invalid value mapping in the device configuration."""


def parse_entries(raw: Any, required: tuple[str, ...]) -> list[dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError as exc:
            raise MappingError(f"waardetoewijzing is geen geldige JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise MappingError("geen waardetoewijzing ingesteld (connection.values)")
    entries = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            raise MappingError(f"regel {i + 1}: verwacht een object")
        try:
            metric = Metric(e.get("metric"))
        except ValueError as exc:
            raise MappingError(f"regel {i + 1}: onbekende metric {e.get('metric')!r}") from exc
        missing = [k for k in required if e.get(k) in (None, "")]
        if missing:
            raise MappingError(f"regel {i + 1} ({metric}): ontbreekt {', '.join(missing)}")
        for k in ("scale", "offset"):
            if k in e and not isinstance(e[k], int | float):
                raise MappingError(f"regel {i + 1} ({metric}): {k} moet een getal zijn")
        entries.append({**e, "metric": metric})
    return entries


def convert(entry: dict, raw: Any) -> Any:
    """Raw source value -> EMS value (None when absent or not numeric)."""
    metric: Metric = entry["metric"]
    if raw is None:
        return None
    if metric in TEXT_METRICS:
        return str(raw)
    if metric in BOOL_METRICS:
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "on", "yes", "connected")
        return bool(raw)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    value = value * float(entry.get("scale", 1.0)) + float(entry.get("offset", 0.0))
    return -value if entry.get("invert") else value


def finish(values: dict[Metric, Any]) -> dict[Metric, Any]:
    if Metric.GRID_POWER_W not in values and Metric.GRID_IMPORT_POWER_W in values and Metric.GRID_EXPORT_POWER_W in values:
        values[Metric.GRID_POWER_W] = values[Metric.GRID_IMPORT_POWER_W] - values[Metric.GRID_EXPORT_POWER_W]
    return values


def capabilities_for(entries: list[dict]) -> frozenset[Capability]:
    metrics = {e["metric"] for e in entries}
    if Metric.GRID_IMPORT_POWER_W in metrics and Metric.GRID_EXPORT_POWER_W in metrics:
        metrics.add(Metric.GRID_POWER_W)
    return frozenset(cap for cap, needed in CAPABILITY_METRICS.items() if all(m in metrics for m in needed))


def json_path(doc: Any, path: str) -> Any:
    """Dotted path lookup: ``"data.power"`` or ``"phases.0.current"``. None if absent."""
    cur = doc
    for part in str(path).split(".") if path else []:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
        if cur is None:
            return None
    return cur


def resolve_secret(value: Any, store: Any) -> str | None:
    """``secret:<name>`` -> value from the encrypted SecretStore; other strings as given."""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("secret:"):
        return None if store is None else store.get(value.removeprefix("secret:"))
    return value
