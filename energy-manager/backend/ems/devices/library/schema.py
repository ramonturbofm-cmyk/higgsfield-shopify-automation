"""Versioned device-profile schema (DEVICE_PROFILE_SCHEMA.md).

Three layers: transport/protocol (Modbus TCP/RTU, HTTP, ...) -> driver (``driver_id``) -> model profile
(this file: points, controls, limits for one model or model group). A profile is *data only*: importing a
profile can never execute code. Every profile is validated before it is loaded; controls are only usable
when their documentation status allows it (``ControlSpec.status``) and after commissioning.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ems.core.models import ACTION_CAPABILITY, Capability, CommandAction, DeviceCategory, Metric
from ems.integrations.modbus.codec import TYPES

SCHEMA_VERSION = 1
TEST_STATUSES = ("HARDWARE_TESTED", "SIMULATOR_TESTED", "UNVERIFIED", "COMMUNITY", "EXPERIMENTAL")
# Write documentation status of a control. Only OFFICIAL/SIMULATOR controls are offered for writing;
# COMMUNITY/UNVERIFIED controls stay visible as 'UNVERIFIED' and are never written.
CONTROL_STATUSES = ("OFFICIAL_DOCUMENTATION", "SIMULATOR", "COMMUNITY", "UNVERIFIED")
WRITABLE_STATUSES = ("OFFICIAL_DOCUMENTATION", "SIMULATOR")
PROTOCOLS = ("modbus_tcp", "modbus_rtu", "modbus", "sunspec", "http", "shelly_rpc", "homeassistant", "mqtt",
             "simulated", "p1_serial", "ocpp16")


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_B):
    title: str
    url: str | None = None
    license: str | None = None
    retrieved: str | None = None


class Point(_B):
    """One value read from the device (Modbus register(s) or a JSON path)."""
    metric: Metric | None = None              # EMS metric, or None for diagnostics-only points
    name: str | None = None                   # diagnostic name (e.g. "compressor_frequency_hz")
    table: Literal["holding", "input", "coil", "discrete"] = "holding"
    address: int | None = Field(None, ge=0, le=0xFFFF)
    type: str = "uint16"
    size: int | None = Field(None, ge=1, le=64)  # registers, for strings
    byte_order: Literal["big", "little"] = "big"
    word_order: Literal["big", "little"] = "big"
    scale: float = 1.0
    sf: int = Field(0, ge=-10, le=10)         # fixed power-of-ten exponent
    offset: float = 0.0
    invert: bool = False
    enum: dict[str, str] | None = None        # raw value (as string) -> text
    path: str | None = None                   # JSON path (HTTP profiles)
    unit: str | None = None
    combine: Literal["replace", "sum"] = "replace"   # several points summed into one metric (e.g. phases)

    @model_validator(mode="after")
    def _check(self) -> Point:
        if self.metric is None and not self.name:
            raise ValueError("point heeft een metric of een name nodig")
        if self.path is None and self.address is None:
            raise ValueError(f"{self.metric or self.name}: address (Modbus) of path (HTTP) ontbreekt")
        if self.type not in TYPES and self.type not in ("bool",):
            raise ValueError(f"onbekend type {self.type!r}")
        if self.type == "string" and not self.size:
            raise ValueError("string heeft size (aantal registers) nodig")
        return self


class ControlSpec(_B):
    """A writable function. ``values`` maps command values (e.g. hp modes) to raw register values;
    otherwise the numeric command value is scaled into the register within [min, max]."""
    action: CommandAction
    capability: Capability | None = None      # derived from the action when omitted
    table: Literal["holding", "coil"] = "holding"
    address: int | None = Field(None, ge=0, le=0xFFFF)
    type: str = "uint16"
    byte_order: Literal["big", "little"] = "big"
    word_order: Literal["big", "little"] = "big"
    scale: float = 1.0
    sf: int = 0
    offset: float = 0.0
    min: float | None = None
    max: float | None = None
    values: dict[str, int] | None = None
    fixed_value: int | None = None            # raw value for actions without a value (e.g. battery_auto)
    readback: Point | None = None             # where to verify (defaults to the same register)
    readback_tolerance: float = 0.0
    status: Literal["OFFICIAL_DOCUMENTATION", "SIMULATOR", "COMMUNITY", "UNVERIFIED"] = "UNVERIFIED"
    expires_s: int | None = Field(None, ge=10, le=86400)   # device-side revert (watchdog), if supported
    expiry_register: int | None = Field(None, ge=0, le=0xFFFF)
    note: str = ""

    @model_validator(mode="after")
    def _check(self) -> ControlSpec:
        expected = ACTION_CAPABILITY[self.action]
        if self.capability is None:
            self.capability = expected
        elif self.capability != expected:
            raise ValueError(f"{self.action}: capability moet {expected} zijn")
        if self.type not in TYPES or self.type == "string":
            raise ValueError(f"{self.action}: ongeldig type {self.type!r}")
        if self.values is None and self.fixed_value is None and self.min is None and self.max is None \
                and self.table == "holding":
            raise ValueError(f"{self.action}: numerieke control heeft min en max nodig")
        return self

    @property
    def writable(self) -> bool:
        return self.status in WRITABLE_STATUSES


class Signature(_B):
    """How discovery recognises this model. ``kind``: modbus_string | sunspec | http_json | mdns."""
    kind: Literal["modbus_string", "sunspec", "http_json", "mdns"]
    address: int | None = None
    size: int | None = None
    unit_id: int | None = None
    equals: str | None = None
    prefix: str | None = None
    manufacturer: str | None = None           # sunspec Mn
    model_regex: str | None = None            # sunspec Md / http field
    path: str | None = None                   # http path
    field: str | None = None
    service: str | None = None                # mdns service type


class DeviceProfile(_B):
    schema_version: int = SCHEMA_VERSION
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,80}$")
    driver_id: str
    manufacturer: str
    model: str
    model_aliases: list[str] = Field(default_factory=list)
    hardware_revision: str | None = None
    firmware_min: str | None = None
    firmware_max: str | None = None
    device_category: DeviceCategory
    supported_roles: list[DeviceCategory] = Field(default_factory=list)
    protocol: str
    connection_parameters: dict[str, Any] = Field(default_factory=dict)
    discovery_signatures: list[Signature] = Field(default_factory=list)
    points: list[Point] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    writable_controls: list[ControlSpec] = Field(default_factory=list)
    release_writes: list[dict[str, Any]] = Field(default_factory=list)   # restore native behaviour
    safety_constraints: dict[str, Any] = Field(default_factory=dict)
    command_timeouts: dict[str, float] = Field(default_factory=lambda: {"write_s": 5.0, "readback_s": 5.0})
    reconnect_policy: dict[str, Any] = Field(default_factory=lambda: {"backoff_s": [2, 10, 30], "max_errors": 5})
    readback_verification: bool = True
    documentation_sources: list[Source]
    integration_version: str = "1.0.0"
    test_status: Literal["HARDWARE_TESTED", "SIMULATOR_TESTED", "UNVERIFIED", "COMMUNITY", "EXPERIMENTAL"]
    known_limitations: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)   # extra hardware / settings on the device
    default_params: dict[str, Any] = Field(default_factory=dict)   # prefilled device data (limits, capacity)
    simulator_only: bool = False
    source: Literal["builtin", "community", "user"] = "builtin"
    description: str = ""

    @field_validator("protocol")
    @classmethod
    def _proto(cls, v: str) -> str:
        if v not in PROTOCOLS:
            raise ValueError(f"onbekend protocol {v!r}")
        return v

    @model_validator(mode="after")
    def _check(self) -> DeviceProfile:
        from ems.devices.capabilities import TYPE_SCHEMAS
        if self.schema_version > SCHEMA_VERSION:
            raise ValueError(f"profielschema {self.schema_version} is nieuwer dan deze software ({SCHEMA_VERSION})")
        if not self.documentation_sources:
            raise ValueError("documentation_sources is verplicht (geen profielen zonder bron)")
        allowed = set(TYPE_SCHEMAS[self.device_category].capabilities)
        for role in self.supported_roles:
            allowed |= set(TYPE_SCHEMAS[role].capabilities)
        bad = [c for c in self.capabilities if c not in allowed]
        if bad:
            raise ValueError(f"capabilities passen niet bij {self.device_category}: {', '.join(bad)}")
        for c in self.writable_controls:
            if c.capability not in allowed:
                raise ValueError(f"control {c.action} past niet bij {self.device_category}")
            if c.status == "SIMULATOR" and not self.simulator_only:
                raise ValueError(f"{c.action}: status SIMULATOR alleen voor simulatorprofielen")
        if self.source != "builtin":
            # Community/user profiles never unlock writes or full control by themselves: their claims cannot be
            # verified here. Reading works; control needs a reviewed built-in profile.
            for c in self.writable_controls:
                if c.status in WRITABLE_STATUSES:
                    c.status = "UNVERIFIED"
            if self.test_status == "HARDWARE_TESTED":
                self.test_status = "UNVERIFIED"
        return self

    @property
    def read_capabilities(self) -> set[Capability]:
        from ems.core.models import CAPABILITY_METRICS
        metrics = {p.metric for p in self.points if p.metric is not None}
        declared = set(self.capabilities)
        return {c for c, needed in CAPABILITY_METRICS.items()
                if c in declared or (needed and all(m in metrics for m in needed))}

    def control(self, action: CommandAction) -> ControlSpec | None:
        return next((c for c in self.writable_controls if c.action == action), None)

    @property
    def search_text(self) -> str:
        return " ".join([self.manufacturer, self.model, *self.model_aliases]).lower()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
