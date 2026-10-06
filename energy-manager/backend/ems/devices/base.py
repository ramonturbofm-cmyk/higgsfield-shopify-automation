"""Driver interface every device integration implements.

A driver translates between the EMS vocabulary (Metric / Command) and one
concrete device protocol. Rules (see DEVICE_INTEGRATION_GUIDE.md):

* Only implement registers/endpoints/topics from official documentation;
  set ``manifest.documentation`` to the source and ``verified=True`` only
  after testing against real hardware.
* ``release_control()`` must return the device to its own safe, native
  behaviour. The EMS calls it on fail-safe, shutdown and override expiry.
* Never block the event loop; all I/O is async.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from ems.core.models import (
    ACTION_CAPABILITY,
    CAPABILITY_LABELS_NL,
    CAPABILITY_METRICS,
    Capability,
    Command,
    DeviceCategory,
    Metric,
)

if TYPE_CHECKING:
    from ems.core.clock import Clock
    from ems.core.config import DeviceConfig


class DriverError(Exception):
    """Generic driver failure."""


class DeviceUnavailableError(DriverError):
    """Device cannot be reached (timeout, connection refused, offline)."""


class UnsupportedCommandError(DriverError):
    """The device/driver cannot execute this command."""


@dataclass(frozen=True)
class DriverManifest:
    driver_id: str                      # e.g. "mock.battery", "generic.modbus_tcp"
    display_name: str
    vendor: str
    categories: tuple[DeviceCategory, ...]
    capabilities: frozenset[Capability]
    connection_types: tuple[str, ...] = ()   # "modbus_tcp", "modbus_rtu", "mqtt", "rest", "p1_serial", "simulated"
    models: tuple[str, ...] = ()
    simulated: bool = False             # True: never touches real hardware
    verified: bool = False              # tested against real hardware
    documentation: str | None = None    # URL / title of the official source
    connection_schema: dict[str, Any] = field(default_factory=dict)  # fields for the wizard
    notes: str = ""


@dataclass
class DriverContext:
    """Services a driver may use. ``simulator`` is only set in simulation."""

    clock: Clock
    simulator: Any = None
    secrets: dict[str, str] = field(default_factory=dict)


@dataclass
class TestCheck:
    key: str
    label: str
    ok: bool
    detail: str = ""

    def render(self) -> str:
        return f"{'✓' if self.ok else '✗'} {self.label}" + (f" — {self.detail}" if self.detail else "")


@dataclass
class TestReport:
    device_id: str
    driver_id: str
    reachable: bool
    checks: list[TestCheck] = field(default_factory=list)
    sample: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> list[str]:
        return [c.key for c in self.checks if c.ok]

    @property
    def unavailable(self) -> list[str]:
        return [c.key for c in self.checks if not c.ok]

    def render(self) -> str:
        return "\n".join(c.render() for c in self.checks)


class DeviceDriver(ABC):
    manifest: ClassVar[DriverManifest]

    def __init__(self, config: DeviceConfig, context: DriverContext) -> None:
        self.config = config
        self.context = context

    @property
    def device_id(self) -> str:
        return self.config.id

    def capabilities(self) -> frozenset[Capability]:
        """Capabilities of this instance (may be narrower than the manifest)."""
        return self.manifest.capabilities

    def supports(self, command: Command) -> bool:
        return ACTION_CAPABILITY[command.action] in self.capabilities()

    @abstractmethod
    async def connect(self) -> None: ...

    async def disconnect(self) -> None:  # noqa: B027 - optional hook
        pass

    @abstractmethod
    async def read(self) -> dict[Metric, Any]: ...

    @abstractmethod
    async def apply(self, command: Command) -> None: ...

    @abstractmethod
    async def release_control(self) -> None:
        """Return the device to its own native, safe operating mode."""

    async def self_test(self) -> TestReport:
        """Connection test for the device wizard: what can we really do?"""
        report = TestReport(self.device_id, self.manifest.driver_id, reachable=False)
        try:
            await self.connect()
            values = await self.read()
        except Exception as exc:  # report, never raise, in the wizard
            report.checks.append(TestCheck("reachable", "apparaat bereikbaar", False, str(exc)))
            return report
        report.reachable = True
        report.sample = {str(k): v for k, v in values.items()}
        report.checks.append(TestCheck("reachable", "apparaat bereikbaar", True))
        caps = self.capabilities()
        for cap in Capability:
            label = CAPABILITY_LABELS_NL.get(cap, cap.value)
            metrics = CAPABILITY_METRICS.get(cap)
            if metrics is not None:
                if cap not in caps:
                    continue
                present = all(m in values for m in metrics)
                report.checks.append(TestCheck(cap.value, label, present,
                                               "" if present else "geen waarde ontvangen"))
            elif cap in caps:
                # Control capabilities are not exercised during a test, to
                # avoid moving real hardware without the user's consent.
                report.checks.append(TestCheck(cap.value, label, True, "ondersteund door driver"))
        for cap in sorted(set(Capability) - set(caps), key=lambda c: c.value):
            if cap.value.startswith("control_") and _relevant(cap, self.config.category):
                report.checks.append(TestCheck(cap.value, CAPABILITY_LABELS_NL[cap], False,
                                               "niet ondersteund door deze driver"))
        return report


_RELEVANT_CONTROLS: dict[Capability, tuple[DeviceCategory, ...]] = {
    Capability.CONTROL_PV_LIMIT: (DeviceCategory.PV_INVERTER, DeviceCategory.HYBRID_INVERTER),
    Capability.CONTROL_BATTERY_MODE: (DeviceCategory.BATTERY, DeviceCategory.HYBRID_INVERTER),
    Capability.CONTROL_HP_MODE: (DeviceCategory.HEAT_PUMP, DeviceCategory.HEAT_PUMP_BOILER),
    Capability.CONTROL_EV_CURRENT: (DeviceCategory.EV_CHARGER,),
    Capability.CONTROL_SWITCH: (DeviceCategory.SMART_PLUG, DeviceCategory.BOILER),
}


def _relevant(cap: Capability, category: DeviceCategory) -> bool:
    return category in _RELEVANT_CONTROLS.get(cap, ())
