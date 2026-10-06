"""Shared domain vocabulary of the EMS.

Sign conventions (used everywhere, documented in ARCHITECTURE.md):
  * grid power      > 0  = import from the grid,   < 0 = export
  * battery power   > 0  = charging,               < 0 = discharging
  * pv power        >= 0 = production
  * consumer power  >= 0 = consumption (heat pump, EV, loads)
  * phase currents are signed like grid power (+ import)
All power in W, energy in kWh, temperature in degC, SOC in percent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class DeviceCategory(StrEnum):
    SMART_METER = "smart_meter"
    ENERGY_METER = "energy_meter"
    PV_INVERTER = "pv_inverter"
    HYBRID_INVERTER = "hybrid_inverter"
    BATTERY = "battery"
    HEAT_PUMP = "heat_pump"
    HEAT_PUMP_BOILER = "heat_pump_boiler"
    BOILER = "boiler"
    EV_CHARGER = "ev_charger"
    EV = "ev"
    AIRCO = "airco"
    SMART_PLUG = "smart_plug"
    VENTILATION = "ventilation"


class Metric(StrEnum):
    # Grid / smart meter
    GRID_POWER_W = "grid_power_w"
    GRID_IMPORT_POWER_W = "grid_import_power_w"
    GRID_EXPORT_POWER_W = "grid_export_power_w"
    GRID_VOLTAGE_L1_V = "grid_voltage_l1_v"
    GRID_VOLTAGE_L2_V = "grid_voltage_l2_v"
    GRID_VOLTAGE_L3_V = "grid_voltage_l3_v"
    GRID_CURRENT_L1_A = "grid_current_l1_a"
    GRID_CURRENT_L2_A = "grid_current_l2_a"
    GRID_CURRENT_L3_A = "grid_current_l3_a"
    GRID_POWER_L1_W = "grid_power_l1_w"
    GRID_POWER_L2_W = "grid_power_l2_w"
    GRID_POWER_L3_W = "grid_power_l3_w"
    GRID_IMPORT_ENERGY_KWH = "grid_import_energy_kwh"
    GRID_EXPORT_ENERGY_KWH = "grid_export_energy_kwh"
    # PV
    PV_POWER_W = "pv_power_w"
    PV_LIMIT_W = "pv_limit_w"
    PV_ENERGY_KWH = "pv_energy_kwh"
    # Battery
    BATTERY_POWER_W = "battery_power_w"
    BATTERY_SOC_PCT = "battery_soc_pct"
    BATTERY_TEMPERATURE_C = "battery_temperature_c"
    BATTERY_MODE = "battery_mode"
    # Heat pump
    HP_POWER_W = "hp_power_w"
    HP_THERMAL_POWER_W = "hp_thermal_power_w"
    HP_COP = "hp_cop"
    HP_COMPRESSOR_ON = "hp_compressor_on"
    HP_SETPOINT_C = "hp_setpoint_c"
    HP_MODE = "hp_mode"
    INDOOR_TEMP_C = "indoor_temp_c"
    OUTDOOR_TEMP_C = "outdoor_temp_c"
    FLOW_TEMP_C = "flow_temp_c"
    RETURN_TEMP_C = "return_temp_c"
    # EV
    EV_POWER_W = "ev_power_w"
    EV_CONNECTED = "ev_connected"
    EV_SOC_PCT = "ev_soc_pct"
    EV_CURRENT_LIMIT_A = "ev_current_limit_a"
    EV_SESSION_ENERGY_KWH = "ev_session_energy_kwh"
    # Generic loads
    LOAD_POWER_W = "load_power_w"


class Capability(StrEnum):
    # Read capabilities
    READ_GRID_POWER = "read_grid_power"
    READ_GRID_PHASES = "read_grid_phases"
    READ_GRID_ENERGY = "read_grid_energy"
    READ_PV_POWER = "read_pv_power"
    READ_BATTERY_POWER = "read_battery_power"
    READ_BATTERY_SOC = "read_battery_soc"
    READ_BATTERY_TEMPERATURE = "read_battery_temperature"
    READ_HP_POWER = "read_hp_power"
    READ_INDOOR_TEMP = "read_indoor_temp"
    READ_OUTDOOR_TEMP = "read_outdoor_temp"
    READ_FLOW_TEMP = "read_flow_temp"
    READ_EV_POWER = "read_ev_power"
    READ_EV_CONNECTED = "read_ev_connected"
    READ_EV_SOC = "read_ev_soc"
    READ_LOAD_POWER = "read_load_power"
    # Control capabilities
    CONTROL_PV_LIMIT = "control_pv_limit"
    CONTROL_BATTERY_MODE = "control_battery_mode"
    CONTROL_HP_MODE = "control_hp_mode"
    CONTROL_EV_CURRENT = "control_ev_current"
    CONTROL_SWITCH = "control_switch"


# Metrics a read capability must deliver (used by the connection test).
CAPABILITY_METRICS: dict[Capability, tuple[Metric, ...]] = {
    Capability.READ_GRID_POWER: (Metric.GRID_POWER_W,),
    Capability.READ_GRID_PHASES: (
        Metric.GRID_CURRENT_L1_A, Metric.GRID_VOLTAGE_L1_V, Metric.GRID_POWER_L1_W,
    ),
    Capability.READ_GRID_ENERGY: (Metric.GRID_IMPORT_ENERGY_KWH, Metric.GRID_EXPORT_ENERGY_KWH),
    Capability.READ_PV_POWER: (Metric.PV_POWER_W,),
    Capability.READ_BATTERY_POWER: (Metric.BATTERY_POWER_W,),
    Capability.READ_BATTERY_SOC: (Metric.BATTERY_SOC_PCT,),
    Capability.READ_BATTERY_TEMPERATURE: (Metric.BATTERY_TEMPERATURE_C,),
    Capability.READ_HP_POWER: (Metric.HP_POWER_W,),
    Capability.READ_INDOOR_TEMP: (Metric.INDOOR_TEMP_C,),
    Capability.READ_OUTDOOR_TEMP: (Metric.OUTDOOR_TEMP_C,),
    Capability.READ_FLOW_TEMP: (Metric.FLOW_TEMP_C,),
    Capability.READ_EV_POWER: (Metric.EV_POWER_W,),
    Capability.READ_EV_CONNECTED: (Metric.EV_CONNECTED,),
    Capability.READ_EV_SOC: (Metric.EV_SOC_PCT,),
    Capability.READ_LOAD_POWER: (Metric.LOAD_POWER_W,),
}

# Human readable (Dutch) capability labels for the device wizard.
CAPABILITY_LABELS_NL: dict[Capability, str] = {
    Capability.READ_GRID_POWER: "netvermogen uitleesbaar",
    Capability.READ_GRID_PHASES: "fasewaarden (V/A/W) uitleesbaar",
    Capability.READ_GRID_ENERGY: "meterstanden uitleesbaar",
    Capability.READ_PV_POWER: "PV-vermogen uitleesbaar",
    Capability.READ_BATTERY_POWER: "batterijvermogen uitleesbaar",
    Capability.READ_BATTERY_SOC: "batterij-SOC uitleesbaar",
    Capability.READ_BATTERY_TEMPERATURE: "batterijtemperatuur uitleesbaar",
    Capability.READ_HP_POWER: "warmtepompvermogen uitleesbaar",
    Capability.READ_INDOOR_TEMP: "binnentemperatuur uitleesbaar",
    Capability.READ_OUTDOOR_TEMP: "buitentemperatuur uitleesbaar",
    Capability.READ_FLOW_TEMP: "aanvoertemperatuur uitleesbaar",
    Capability.READ_EV_POWER: "laadvermogen uitleesbaar",
    Capability.READ_EV_CONNECTED: "auto aangesloten uitleesbaar",
    Capability.READ_EV_SOC: "auto-SOC uitleesbaar",
    Capability.READ_LOAD_POWER: "verbruik uitleesbaar",
    Capability.CONTROL_PV_LIMIT: "vermogensbegrenzing beschikbaar",
    Capability.CONTROL_BATTERY_MODE: "batterijbesturing beschikbaar",
    Capability.CONTROL_HP_MODE: "warmtepompmodus (normaal/boost/eco) beschikbaar",
    Capability.CONTROL_EV_CURRENT: "laadstroomregeling beschikbaar",
    Capability.CONTROL_SWITCH: "aan/uit schakelen beschikbaar",
}


class CommandAction(StrEnum):
    BATTERY_AUTO = "battery_auto"            # device-native self-consumption
    BATTERY_CHARGE = "battery_charge"        # value: W
    BATTERY_DISCHARGE = "battery_discharge"  # value: W
    BATTERY_STANDBY = "battery_standby"
    PV_LIMIT = "pv_limit"                    # value: W, None = no limit
    HP_MODE = "hp_mode"                      # value: "normal" | "boost" | "eco"
    EV_CURRENT = "ev_current"                # value: A per phase, 0 = pause
    SWITCH = "switch"                        # value: "on" | "off"


ACTION_CAPABILITY: dict[CommandAction, Capability] = {
    CommandAction.BATTERY_AUTO: Capability.CONTROL_BATTERY_MODE,
    CommandAction.BATTERY_CHARGE: Capability.CONTROL_BATTERY_MODE,
    CommandAction.BATTERY_DISCHARGE: Capability.CONTROL_BATTERY_MODE,
    CommandAction.BATTERY_STANDBY: Capability.CONTROL_BATTERY_MODE,
    CommandAction.PV_LIMIT: Capability.CONTROL_PV_LIMIT,
    CommandAction.HP_MODE: Capability.CONTROL_HP_MODE,
    CommandAction.EV_CURRENT: Capability.CONTROL_EV_CURRENT,
    CommandAction.SWITCH: Capability.CONTROL_SWITCH,
}

# Commands in the same group overwrite each other (used for de-duplication).
ACTION_GROUP: dict[CommandAction, str] = {
    CommandAction.BATTERY_AUTO: "battery_mode",
    CommandAction.BATTERY_CHARGE: "battery_mode",
    CommandAction.BATTERY_DISCHARGE: "battery_mode",
    CommandAction.BATTERY_STANDBY: "battery_mode",
    CommandAction.PV_LIMIT: "pv_limit",
    CommandAction.HP_MODE: "hp_mode",
    CommandAction.EV_CURRENT: "ev_current",
    CommandAction.SWITCH: "switch",
}

HP_MODES = ("normal", "boost", "eco")


@dataclass(frozen=True, slots=True)
class Command:
    device_id: str
    action: CommandAction
    value: float | str | None = None

    @property
    def group_key(self) -> tuple[str, str]:
        return (self.device_id, ACTION_GROUP[self.action])

    def describe_nl(self) -> str:
        v = self.value
        match self.action:
            case CommandAction.BATTERY_AUTO:
                return "batterij automatisch (zelfconsumptie)"
            case CommandAction.BATTERY_CHARGE:
                return f"batterij laden met {float(v or 0) / 1000:.1f} kW"
            case CommandAction.BATTERY_DISCHARGE:
                return f"batterij ontladen met {float(v or 0) / 1000:.1f} kW"
            case CommandAction.BATTERY_STANDBY:
                return "batterij stand-by"
            case CommandAction.PV_LIMIT:
                return "PV onbegrensd" if v is None else f"PV begrenzen tot {float(v) / 1000:.1f} kW"
            case CommandAction.HP_MODE:
                return f"warmtepomp {v}"
            case CommandAction.EV_CURRENT:
                return "laden pauzeren" if not v else f"laadstroom {float(v):.0f} A"
            case CommandAction.SWITCH:
                return f"schakelen {v}"
        return f"{self.action} {v}"  # pragma: no cover


class DeviceStatus(StrEnum):
    ONLINE = "online"
    STALE = "stale"      # reachable but data too old / frozen
    OFFLINE = "offline"
    DISABLED = "disabled"


@dataclass(slots=True)
class DeviceState:
    device_id: str
    category: DeviceCategory
    status: DeviceStatus
    values: dict[Metric, Any] = field(default_factory=dict)
    last_update: datetime | None = None
    error: str | None = None
    rejected: dict[Metric, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.status == DeviceStatus.ONLINE

    def get(self, metric: Metric, default: Any = None) -> Any:
        return self.values.get(metric, default)


@dataclass(slots=True)
class Decision:
    """A command plus the explanation of why the EMS wants it.

    Every action of the EMS — automatic, manual or fail-safe — travels as a
    Decision so it can always be explained and journaled.
    """

    command: Command
    summary: str
    reasons: list[str] = field(default_factory=list)
    source: str = "controller"
    data: dict[str, Any] = field(default_factory=dict)
    expected_benefit_eur: float | None = None
