"""Mock drivers: behave like real drivers but talk to the simulator.

They support fault injection (offline, frozen data, garbage values, slow
responses) so fail-safe behaviour can be tested without hardware.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any

from ems.core.models import HP_MODES, Capability, Command, CommandAction, DeviceCategory, Metric
from ems.devices.base import (
    DeviceDriver,
    DeviceUnavailableError,
    DriverManifest,
    UnsupportedCommandError,
)
from ems.devices.registry import register_driver
from ems.simulator.components import SimBattery, SimEVCharger, SimHeatPump, SimPV
from ems.simulator.site import MeterState

C = Capability


class FaultMode(StrEnum):
    NONE = "none"
    OFFLINE = "offline"     # every call raises DeviceUnavailableError
    FROZEN = "frozen"       # keeps returning the last reading
    GARBAGE = "garbage"     # physically impossible values
    SLOW = "slow"           # responds slower than any sane timeout
    REJECT_COMMANDS = "reject_commands"


def _manifest(driver_id: str, name: str, category: DeviceCategory, caps: set[Capability],
              grid_meter_kind: str | None = None) -> DriverManifest:
    return DriverManifest(
        grid_meter_kind=grid_meter_kind,
        write_capable=any(c.value.startswith("control_") for c in caps),
        driver_id=driver_id, display_name=name, vendor="Simulator",
        categories=(category,), capabilities=frozenset(caps),
        connection_types=("simulated",), models=("Simulated",),
        simulated=True, verified=True, documentation="ems.simulator (intern model)",
    )


class MockDriverBase(DeviceDriver):
    component_type: type = object

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        self.fault = FaultMode.NONE
        self.slow_delay_s = 30.0
        self.applied: list[Command] = []
        self._last: dict[Metric, Any] | None = None
        self._component: Any = None

    def inject_fault(self, fault: FaultMode | str) -> None:
        self.fault = FaultMode(fault)

    async def _guard(self) -> None:
        if self.fault == FaultMode.OFFLINE:
            raise DeviceUnavailableError("gesimuleerde storing: apparaat offline")
        if self.fault == FaultMode.SLOW:
            await asyncio.sleep(self.slow_delay_s)

    @property
    def component(self) -> Any:
        if self._component is None:
            raise DeviceUnavailableError("niet verbonden")
        return self._component

    async def connect(self) -> None:
        await self._guard()
        sim = self.context.simulator
        if sim is None:
            raise DeviceUnavailableError("gesimuleerd apparaat: alleen beschikbaar in Demo Mode / simulatie")
        comp = sim.component(self.device_id)
        if not isinstance(comp, self.component_type):
            raise DeviceUnavailableError(f"simulatiecomponent {self.device_id} heeft verkeerd type")
        self._component = comp

    async def read(self) -> dict[Metric, Any]:
        await self._guard()
        if self.fault == FaultMode.FROZEN and self._last is not None:
            return dict(self._last)
        values = self._read()
        if self.fault == FaultMode.GARBAGE:
            values = {k: (1e12 if isinstance(v, float) else v) for k, v in values.items()}
        self._last = values
        return dict(values)

    async def apply(self, command: Command) -> None:
        await self._guard()
        if self.fault == FaultMode.REJECT_COMMANDS:
            raise DeviceUnavailableError("gesimuleerde storing: commando geweigerd")
        if not self.supports(command):
            raise UnsupportedCommandError(f"{self.manifest.driver_id} ondersteunt {command.action} niet")
        self._apply(command)
        self.applied.append(command)

    def _read(self) -> dict[Metric, Any]:
        raise NotImplementedError

    def _apply(self, command: Command) -> None:
        raise UnsupportedCommandError(str(command.action))


@register_driver
class MockSmartMeter(MockDriverBase):
    manifest = _manifest("mock.smart_meter", "Gesimuleerde slimme meter (P1)", DeviceCategory.SMART_METER,
                         {C.READ_GRID_POWER, C.READ_GRID_PHASES, C.READ_GRID_ENERGY}, grid_meter_kind="simulated")
    component_type = MeterState

    def _read(self) -> dict[Metric, Any]:
        m: MeterState = self.component
        v = {
            Metric.GRID_POWER_W: round(m.power_w, 1),
            Metric.GRID_IMPORT_POWER_W: round(max(0.0, m.power_w), 1),
            Metric.GRID_EXPORT_POWER_W: round(max(0.0, -m.power_w), 1),
            Metric.GRID_IMPORT_ENERGY_KWH: round(m.import_kwh, 3),
            Metric.GRID_EXPORT_ENERGY_KWH: round(m.export_kwh, 3),
        }
        phase_metrics = (
            (Metric.GRID_VOLTAGE_L1_V, Metric.GRID_CURRENT_L1_A, Metric.GRID_POWER_L1_W),
            (Metric.GRID_VOLTAGE_L2_V, Metric.GRID_CURRENT_L2_A, Metric.GRID_POWER_L2_W),
            (Metric.GRID_VOLTAGE_L3_V, Metric.GRID_CURRENT_L3_A, Metric.GRID_POWER_L3_W),
        )
        for i, (mv, ma, mp) in enumerate(phase_metrics):
            v[mv] = round(m.phase_voltage_v[i], 1)
            v[ma] = round(m.phase_current_a[i], 2)
            v[mp] = round(m.phase_power_w[i], 1)
        return v

    async def release_control(self) -> None:
        return None


@register_driver
class MockSolarInverter(MockDriverBase):
    manifest = _manifest("mock.pv_inverter", "Gesimuleerde PV-omvormer", DeviceCategory.PV_INVERTER,
                         {C.READ_PV_POWER, C.CONTROL_PV_LIMIT})
    component_type = SimPV

    def _read(self) -> dict[Metric, Any]:
        pv: SimPV = self.component
        return {
            Metric.PV_POWER_W: round(pv.output_w, 1),
            Metric.PV_LIMIT_W: pv.limit_w,
            Metric.PV_ENERGY_KWH: round(pv.energy_kwh, 3),
        }

    def _apply(self, command: Command) -> None:
        pv: SimPV = self.component
        pv.limit_w = None if command.value is None else max(0.0, float(command.value))

    async def release_control(self) -> None:
        await self._guard()
        self.component.limit_w = None


@register_driver
class MockBattery(MockDriverBase):
    manifest = _manifest("mock.battery", "Gesimuleerde thuisbatterij", DeviceCategory.BATTERY,
                         {C.READ_BATTERY_POWER, C.READ_BATTERY_SOC, C.READ_BATTERY_TEMPERATURE,
                          C.CONTROL_BATTERY_MODE})
    component_type = SimBattery

    def _read(self) -> dict[Metric, Any]:
        b: SimBattery = self.component
        return {
            Metric.BATTERY_POWER_W: round(b.output_w, 1),
            Metric.BATTERY_SOC_PCT: round(b.soc_pct, 2),
            Metric.BATTERY_TEMPERATURE_C: round(b.temperature_c, 1),
            Metric.BATTERY_MODE: b.mode,
        }

    def _apply(self, command: Command) -> None:
        b: SimBattery = self.component
        match command.action:
            case CommandAction.BATTERY_AUTO:
                b.mode, b.setpoint_w = "auto", 0.0
            case CommandAction.BATTERY_CHARGE:
                b.mode, b.setpoint_w = "charge", abs(float(command.value or 0))
            case CommandAction.BATTERY_DISCHARGE:
                b.mode, b.setpoint_w = "discharge", abs(float(command.value or 0))
            case CommandAction.BATTERY_STANDBY:
                b.mode, b.setpoint_w = "standby", 0.0
            case _:
                raise UnsupportedCommandError(str(command.action))

    async def release_control(self) -> None:
        await self._guard()
        self.component.mode, self.component.setpoint_w = "auto", 0.0


@register_driver
class MockHeatPump(MockDriverBase):
    manifest = _manifest("mock.heat_pump", "Gesimuleerde warmtepomp", DeviceCategory.HEAT_PUMP,
                         {C.READ_HP_POWER, C.READ_INDOOR_TEMP, C.READ_OUTDOOR_TEMP, C.READ_FLOW_TEMP,
                          C.CONTROL_HP_MODE})
    component_type = SimHeatPump

    def _read(self) -> dict[Metric, Any]:
        hp: SimHeatPump = self.component
        return {
            Metric.HP_POWER_W: round(hp.output_w, 1),
            Metric.HP_THERMAL_POWER_W: round(hp.thermal_w, 1),
            Metric.HP_COP: round(hp.cop, 2),
            Metric.HP_COMPRESSOR_ON: hp.compressor_on,
            Metric.HP_SETPOINT_C: round(hp.setpoint_c, 2),
            Metric.HP_MODE: hp.mode,
            Metric.INDOOR_TEMP_C: round(hp.indoor_temp_c, 2),
            Metric.OUTDOOR_TEMP_C: round(hp.outdoor_temp_c, 2),
            Metric.FLOW_TEMP_C: round(hp.flow_temp_c, 1),
        }

    def _apply(self, command: Command) -> None:
        if command.value not in HP_MODES:
            raise UnsupportedCommandError(f"onbekende warmtepompmodus {command.value!r}")
        self.component.mode = str(command.value)

    async def release_control(self) -> None:
        await self._guard()
        self.component.mode = "normal"


@register_driver
class MockEVCharger(MockDriverBase):
    manifest = _manifest("mock.ev_charger", "Gesimuleerde laadpaal", DeviceCategory.EV_CHARGER,
                         {C.READ_EV_POWER, C.READ_EV_CONNECTED, C.READ_EV_SOC, C.CONTROL_EV_CURRENT})
    component_type = SimEVCharger

    def _read(self) -> dict[Metric, Any]:
        ev: SimEVCharger = self.component
        values: dict[Metric, Any] = {
            Metric.EV_POWER_W: round(ev.output_w, 1),
            Metric.EV_CONNECTED: ev.connected,
            Metric.EV_CURRENT_LIMIT_A: ev.max_current_a if ev.current_setpoint_a is None else ev.current_setpoint_a,
            Metric.EV_SESSION_ENERGY_KWH: round(ev.session_kwh, 3),
        }
        if ev.connected:
            values[Metric.EV_SOC_PCT] = round(ev.soc_pct, 1)
        return values

    def _apply(self, command: Command) -> None:
        ev: SimEVCharger = self.component
        current = max(0.0, float(command.value or 0))
        ev.current_setpoint_a = min(current, ev.max_current_a)

    async def release_control(self) -> None:
        await self._guard()
        self.component.current_setpoint_a = None
