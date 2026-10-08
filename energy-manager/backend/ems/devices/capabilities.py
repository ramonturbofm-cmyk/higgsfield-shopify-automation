"""The single source of truth for what a device can be asked to do.

A device's capabilities are the **intersection** of
  (1) what its device type can have at all (TYPE_SCHEMAS — a heat pump never has a battery SOC),
  (2) what the driver/profile actually supports for this instance (``driver.capabilities()``).
Actions are only offered when their specific capability is present; their value ranges come
from the device's own configured limits (PARAM_SCHEMAS). The same functions feed the API,
the web UI, automations, commissioning, the optimizer and the command validation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ems.core.models import (
    ACTION_CAPABILITY,
    CAPABILITY_LABELS_NL,
    CAPABILITY_METRICS,
    HP_MODES,
    Capability,
    CommandAction,
    DeviceCategory,
)

C = Capability
D = DeviceCategory

_GRID = {C.READ_GRID_POWER, C.READ_GRID_PHASES, C.READ_GRID_ENERGY}
_PV = {C.READ_PV_POWER, C.CONTROL_PV_LIMIT}
_BATTERY = {C.READ_BATTERY_POWER, C.READ_BATTERY_SOC, C.READ_BATTERY_TEMPERATURE, C.READ_SOC_LIMIT,
            C.CONTROL_BATTERY_MODE, C.CONTROL_BATTERY_POWER, C.CONTROL_SOC_LIMIT}
_HEATPUMP = {C.READ_HP_POWER, C.READ_INDOOR_TEMP, C.READ_OUTDOOR_TEMP, C.READ_FLOW_TEMP, C.READ_RETURN_TEMP,
             C.READ_HP_COP, C.READ_HP_STATUS, C.READ_TEMP_SETPOINT, C.READ_DHW_TEMP,
             C.CONTROL_HP_MODE, C.CONTROL_TEMP_SETPOINT}
_LOAD = {C.READ_LOAD_POWER, C.CONTROL_SWITCH}


@dataclass(frozen=True)
class TypeSchema:
    label: str
    capabilities: frozenset[Capability]
    phase_relevant: bool = True
    grid_meter_capable: bool = False


TYPE_SCHEMAS: dict[DeviceCategory, TypeSchema] = {
    D.SMART_METER: TypeSchema("Netmeter (slimme meter)", frozenset(_GRID), phase_relevant=False, grid_meter_capable=True),
    D.ENERGY_METER: TypeSchema("Energiemeter / submeter", frozenset(_GRID | {C.READ_LOAD_POWER}),
                               grid_meter_capable=True),
    D.PV_INVERTER: TypeSchema("Zonnepanelen (omvormer)", frozenset(_PV)),
    D.HYBRID_INVERTER: TypeSchema("Hybride omvormer", frozenset(_PV | _BATTERY)),
    D.BATTERY: TypeSchema("Thuisbatterij", frozenset(_BATTERY)),
    D.BATTERY_SYSTEM: TypeSchema("Batterijsysteem", frozenset(_BATTERY)),
    D.HEAT_PUMP: TypeSchema("Warmtepomp", frozenset(_HEATPUMP)),
    D.HEAT_PUMP_BOILER: TypeSchema("Warmtepompboiler", frozenset(
        {C.READ_HP_POWER, C.READ_DHW_TEMP, C.READ_HP_STATUS, C.READ_TEMP_SETPOINT,
         C.CONTROL_HP_MODE, C.CONTROL_TEMP_SETPOINT, C.CONTROL_SWITCH})),
    D.BOILER: TypeSchema("Elektrische boiler", frozenset({C.READ_LOAD_POWER, C.READ_DHW_TEMP, C.READ_TEMP_SETPOINT,
                                                         C.CONTROL_SWITCH, C.CONTROL_TEMP_SETPOINT})),
    D.EV_CHARGER: TypeSchema("Laadpaal", frozenset({C.READ_EV_POWER, C.READ_EV_CONNECTED, C.READ_EV_SOC,
                                                     C.CONTROL_EV_CURRENT})),
    D.EV: TypeSchema("Elektrische auto", frozenset({C.READ_EV_SOC, C.READ_EV_CONNECTED}), phase_relevant=False),
    D.AIRCO: TypeSchema("Airco", frozenset({C.READ_LOAD_POWER, C.READ_INDOOR_TEMP, C.READ_OUTDOOR_TEMP,
                                            C.READ_TEMP_SETPOINT, C.CONTROL_SWITCH, C.CONTROL_TEMP_SETPOINT})),
    D.HVAC: TypeSchema("Klimaatinstallatie (HVAC)", frozenset({C.READ_LOAD_POWER, C.READ_INDOOR_TEMP,
                                                              C.READ_OUTDOOR_TEMP, C.READ_TEMP_SETPOINT,
                                                              C.CONTROL_SWITCH, C.CONTROL_TEMP_SETPOINT})),
    D.VENTILATION: TypeSchema("Ventilatie", frozenset({C.READ_LOAD_POWER, C.CONTROL_SWITCH})),
    D.SMART_PLUG: TypeSchema("Slimme stekker", frozenset(_LOAD)),
    D.SMART_RELAY: TypeSchema("Slim relais", frozenset(_LOAD)),
    D.FLEXIBLE_LOAD: TypeSchema("Flexibele verbruiker", frozenset(_LOAD)),
}


def type_capabilities(category: DeviceCategory | str) -> frozenset[Capability]:
    return TYPE_SCHEMAS[DeviceCategory(category)].capabilities


def device_capabilities(driver) -> frozenset[Capability]:
    """What this device instance can really do: type schema ∩ driver/profile support."""
    if driver is None:
        return frozenset()
    return frozenset(driver.capabilities()) & type_capabilities(driver.config.category)


# ----------------------------------------------------------------- parameters
@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    unit: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    recommended: float | None = None
    help: str = ""
    kind: str = "number"            # number | time | enum
    options: tuple[str, ...] = ()
    required_for_control: bool = False
    show_if: tuple[str, Any] | None = None   # (capability, True) -> only shown when the capability exists


PARAM_SCHEMAS: dict[DeviceCategory, tuple[ParamSpec, ...]] = {
    D.BATTERY: (
        ParamSpec("capacity_kwh", "Bruikbare capaciteit", "kWh", 0.5, 500, 0.1, help="Uit het datablad van de batterij.",
                  required_for_control=True),
        ParamSpec("max_charge_w", "Maximaal laadvermogen", "W", 100, 100000, 100, help="Nooit hoger dan het datablad.",
                  required_for_control=True),
        ParamSpec("max_discharge_w", "Maximaal ontlaadvermogen", "W", 100, 100000, 100,
                  help="Nooit hoger dan het datablad.", required_for_control=True),
        ParamSpec("max_temp_c", "Maximale celtemperatuur voor regeling", "°C", 0, 70, 1, 45,
                  "Boven deze temperatuur stuurt het EMS de batterij niet."),
        ParamSpec("min_temp_c", "Minimale celtemperatuur voor laden", "°C", -30, 30, 1, 0,
                  "Onder deze temperatuur wordt niet geladen."),
    ),
    D.EV_CHARGER: (
        ParamSpec("rated_current_a", "Maximale stroom van de laadpaal", "A", 6, 80, 1, 16,
                  "Typeplaatje / installatie van de laadpaal; het EMS gaat hier nooit boven.", required_for_control=True),
        ParamSpec("min_current_a", "Minimale laadstroom", "A", 6, 16, 1, 6, "Meestal 6 A (IEC 61851)."),
        ParamSpec("max_current_a", "Maximale laadstroom (EMS)", "A", 6, 80, 1, 16,
                  "Niet hoger dan de laadpaal toestaat."),
        ParamSpec("departure", "Vertrektijd", kind="time", help="Wanneer de auto klaar moet zijn."),
        ParamSpec("target_soc_pct", "Gewenste laadtoestand auto", "%", 10, 100, 5, 80,
                  show_if=(C.READ_EV_SOC.value, True)),
        ParamSpec("session_target_kwh", "Gewenste energie per laadsessie", "kWh", 0, 150, 1, 20,
                  "Gebruikt als de laadpaal de SOC van de auto niet kan uitlezen.", show_if=(C.READ_EV_SOC.value, False)),
        ParamSpec("charge_mode", "Laadmodus", kind="enum", options=("smart", "pv_only", "min_pv", "max", "off")),
    ),
    D.PV_INVERTER: (
        ParamSpec("peak_power_kw", "Piekvermogen", "kWp", 0.1, 1000, 0.1, help="Totaal van de aangesloten panelen.",
                  required_for_control=True),
        ParamSpec("azimuth_deg", "Oriëntatie (azimut)", "°", 0, 360, 1, 180, "180 = zuid."),
        ParamSpec("tilt_deg", "Hellingshoek", "°", 0, 90, 1, 35),
    ),
    D.HEAT_PUMP: (
        ParamSpec("rated_power_w", "Elektrisch vermogen (max.)", "W", 100, 30000, 50, help="Datablad."),
        ParamSpec("setpoint_min_c", "Laagste toegestane setpoint", "°C", 5, 30, 0.5, 18),
        ParamSpec("setpoint_max_c", "Hoogste toegestane setpoint", "°C", 5, 30, 0.5, 23),
    ),
    D.HEAT_PUMP_BOILER: (
        ParamSpec("setpoint_min_c", "Laagste toegestane watertemperatuur", "°C", 30, 75, 1, 45),
        ParamSpec("setpoint_max_c", "Hoogste toegestane watertemperatuur", "°C", 30, 75, 1, 60),
    ),
    D.BOILER: (
        ParamSpec("rated_power_w", "Vermogen", "W", 100, 10000, 50),
        ParamSpec("setpoint_min_c", "Laagste toegestane watertemperatuur", "°C", 30, 85, 1, 50),
        ParamSpec("setpoint_max_c", "Hoogste toegestane watertemperatuur", "°C", 30, 85, 1, 65),
    ),
}
PARAM_SCHEMAS[D.BATTERY_SYSTEM] = PARAM_SCHEMAS[D.BATTERY]
PARAM_SCHEMAS[D.HYBRID_INVERTER] = PARAM_SCHEMAS[D.BATTERY] + PARAM_SCHEMAS[D.PV_INVERTER]


def _param(params: dict, key: str, default: Any = None) -> Any:
    if key in params:
        return params[key]
    return (params.get("sim") or {}).get(key, default)


def validate_params(category: DeviceCategory | str, params: dict) -> list[str]:
    """Range / consistency errors for a device's parameters (empty list = valid)."""
    errors = []
    specs = {p.key: p for p in PARAM_SCHEMAS.get(DeviceCategory(category), ())}
    for key, spec in specs.items():
        v = params.get(key)
        if v is None or spec.kind != "number":
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            errors.append(f"{spec.label}: geen getal")
            continue
        if spec.min is not None and v < spec.min:
            errors.append(f"{spec.label}: minimaal {spec.min:g} {spec.unit}".strip())
        if spec.max is not None and v > spec.max:
            errors.append(f"{spec.label}: maximaal {spec.max:g} {spec.unit}".strip())
    rated = params.get("rated_current_a")
    if rated is not None:
        for key in ("max_current_a", "min_current_a"):
            if params.get(key) is not None and float(params[key]) > float(rated):
                errors.append(f"{specs[key].label} ({float(params[key]):g} A) is hoger dan de laadpaal toestaat "
                              f"({float(rated):g} A)")
    if params.get("min_current_a") is not None and params.get("max_current_a") is not None \
            and float(params["min_current_a"]) > float(params["max_current_a"]):
        errors.append("Minimale laadstroom is hoger dan de maximale laadstroom")
    if params.get("setpoint_min_c") is not None and params.get("setpoint_max_c") is not None \
            and float(params["setpoint_min_c"]) > float(params["setpoint_max_c"]):
        errors.append("Laagste setpoint is hoger dan het hoogste setpoint")
    return errors


def missing_control_params(category: DeviceCategory | str, params: dict) -> list[str]:
    """Safety limits that must be configured before the EMS may control the device."""
    return [p.label for p in PARAM_SCHEMAS.get(DeviceCategory(category), ())
            if p.required_for_control and _param(params, p.key) in (None, "")]


# -------------------------------------------------------------------- actions
@dataclass
class ActionSpec:
    action: str
    label: str
    capability: str
    value: dict | None = None        # {"type": "number", "min", "max", "step", "unit"} | {"type": "enum", "options"}
    available: bool = True
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


ACTION_LABELS = {
    CommandAction.BATTERY_AUTO: "Automatisch (eigen regeling)",
    CommandAction.BATTERY_STANDBY: "Stand-by",
    CommandAction.BATTERY_CHARGE: "Laden",
    CommandAction.BATTERY_DISCHARGE: "Ontladen",
    CommandAction.BATTERY_SOC_LIMIT: "Maximale SOC instellen",
    CommandAction.PV_LIMIT: "PV-vermogen begrenzen",
    CommandAction.HP_MODE: "Warmtepompmodus",
    CommandAction.TEMP_SETPOINT: "Temperatuur-setpoint",
    CommandAction.EV_CURRENT: "Laadstroom",
    CommandAction.SWITCH: "Aan / uit",
}


def value_spec(action: CommandAction, category: DeviceCategory, params: dict, config=None) -> dict | None:
    """Allowed value range for an action on this device, from its own configured limits."""
    match action:
        case CommandAction.BATTERY_CHARGE | CommandAction.BATTERY_DISCHARGE:
            key = "max_charge_w" if action == CommandAction.BATTERY_CHARGE else "max_discharge_w"
            hi = _param(params, key)
            return {"type": "number", "min": 0, "max": None if hi is None else float(hi), "step": 100, "unit": "W"}
        case CommandAction.PV_LIMIT:
            peak = _param(params, "peak_power_kw")
            return {"type": "number", "min": 0, "max": None if peak is None else float(peak) * 1000, "step": 100,
                    "unit": "W", "nullable": True}
        case CommandAction.EV_CURRENT:
            lo = float(_param(params, "min_current_a", 6))
            hi = _param(params, "max_current_a")
            rated = _param(params, "rated_current_a")
            cap = min(float(x) for x in (hi, rated) if x is not None) if (hi is not None or rated is not None) else None
            return {"type": "number", "min": lo, "max": cap, "step": 1, "unit": "A", "zero_allowed": True}
        case CommandAction.HP_MODE:
            return {"type": "enum", "options": list(HP_MODES)}
        case CommandAction.TEMP_SETPOINT:
            return {"type": "number", "min": float(_param(params, "setpoint_min_c", 15)),
                    "max": float(_param(params, "setpoint_max_c", 25)), "step": 0.5, "unit": "°C"}
        case CommandAction.BATTERY_SOC_LIMIT:
            lo = config.battery.min_soc if config is not None else 10
            return {"type": "number", "min": float(lo), "max": 100, "step": 1, "unit": "%"}
        case CommandAction.SWITCH:
            return {"type": "enum", "options": ["on", "off"]}
    return None


def actions_for(category: DeviceCategory | str, caps: frozenset[Capability], params: dict, config=None) -> list[ActionSpec]:
    """Every action this device really supports (others are never offered)."""
    cat = DeviceCategory(category)
    out = []
    for action, cap in ACTION_CAPABILITY.items():
        if cap in caps and cap in type_capabilities(cat):
            out.append(ActionSpec(action.value, ACTION_LABELS.get(action, action.value), cap.value,
                                  value_spec(action, cat, params, config)))
    return out


def check_value(spec: dict | None, value: Any) -> tuple[Any, str | None]:
    """Normalise and range-check a value against a value spec; (value, error)."""
    if spec is None:
        return None, None
    if spec["type"] == "enum":
        return (value, None) if value in spec["options"] else (value, f"kies uit: {', '.join(spec['options'])}")
    if value is None and spec.get("nullable"):
        return None, None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value, "waarde moet een getal zijn"
    if v == 0 and spec.get("zero_allowed"):
        return 0.0, None
    if spec.get("max") is None and spec["unit"] in ("W", "A"):
        return v, "apparaatlimiet niet ingesteld — stel eerst het maximum in bij het apparaat"
    if spec.get("min") is not None and v < spec["min"]:
        return v, f"minimaal {spec['min']:g} {spec['unit']}"
    if spec.get("max") is not None and v > spec["max"]:
        return v, f"maximaal {spec['max']:g} {spec['unit']} (apparaatlimiet)"
    return v, None


def capability_view(caps: frozenset[Capability]) -> list[dict]:
    return [{"id": c.value, "label": CAPABILITY_LABELS_NL.get(c, c.value), "kind": "control" if c.value.startswith(
        "control_") else "read", "metrics": [m.value for m in CAPABILITY_METRICS.get(c, ())]}
        for c in sorted(caps, key=lambda c: (not c.value.startswith("read_"), c.value))]


def schema_view(category: DeviceCategory | str) -> dict:
    cat = DeviceCategory(category)
    t = TYPE_SCHEMAS[cat]
    return {"category": cat.value, "label": t.label, "phase_relevant": t.phase_relevant,
            "grid_meter_capable": t.grid_meter_capable, "capabilities": capability_view(t.capabilities),
            "params": [asdict(p) for p in PARAM_SCHEMAS.get(cat, ())]}
