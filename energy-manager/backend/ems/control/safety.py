"""SafetyValidator: the last check before a command reaches a driver.

Runs on the node that owns the device (locally in a standalone installation, on the
device gateway in a distributed one), so it also protects against a faulty or
partitioned central optimizer. Checks:

* device known, enabled, connected and its data fresh (no decisions on stale data);
* the driver really has the needed capability;
* value within the device's own limits (power, current) — clamped, never exceeded;
* battery SOC limits: no charging above max SOC, no discharging below the reserve;
* rate limit: a changed command for the same function at most every ``min_interval_s``.

"Safe" commands that hand control back to the device (battery AUTO, PV unlimited,
heat pump normal) only need a connected device.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from ems.core.models import HP_MODES, Command, CommandAction, Metric


class SafetyRejected(Exception):
    """The command is not allowed; ``str(exc)`` explains why (Dutch, for the journal/UI)."""


@dataclass
class SafetyResult:
    command: Command
    notes: list[str] = field(default_factory=list)

    @property
    def adjusted(self) -> bool:
        return bool(self.notes)


def _param(params: dict, key: str, default: Any = None) -> Any:
    if key in params:
        return params[key]
    return (params.get("sim") or {}).get(key, default)


def is_release(cmd: Command) -> bool:
    return (cmd.action == CommandAction.BATTERY_AUTO
            or (cmd.action == CommandAction.PV_LIMIT and cmd.value is None)
            or (cmd.action == CommandAction.HP_MODE and cmd.value == "normal"))


class SafetyValidator:
    def __init__(self, *, stale_after_s: float = 30.0, min_interval_s: float = 5.0,
                 min_soc: float = 10.0, reserve_soc: float = 15.0, max_soc: float = 95.0) -> None:
        self.stale_after = timedelta(seconds=stale_after_s)
        self.min_interval = timedelta(seconds=min_interval_s)
        self.min_soc, self.reserve_soc, self.max_soc = min_soc, reserve_soc, max_soc
        self._last_change: dict[tuple[str, str], tuple[Command, datetime]] = {}

    @classmethod
    def from_config(cls, config) -> SafetyValidator:
        return cls(stale_after_s=config.control.grid_stale_after_s, min_interval_s=config.control.min_command_interval_s,
                   min_soc=config.battery.min_soc, reserve_soc=config.battery.reserve_soc,
                   max_soc=config.battery.max_soc)

    def validate(self, cmd: Command, device, now: datetime, *, record: bool = True) -> SafetyResult:
        """``device`` is a ``ManagedDevice``. Raises SafetyRejected or returns the (clamped) command."""
        if device is None:
            raise SafetyRejected("onbekend apparaat")
        if not device.config.enabled or device.driver is None:
            raise SafetyRejected("apparaat uitgeschakeld of zonder driver")
        if not device.connected:
            raise SafetyRejected("apparaat niet verbonden")
        if not device.driver.supports(cmd):
            raise SafetyRejected(f"apparaat ondersteunt '{cmd.action.value}' niet")
        notes: list[str] = []
        if not is_release(cmd):
            if device.last_ok is None or now - device.last_ok > self.stale_after:
                raise SafetyRejected("apparaatgegevens verouderd — geen stuuropdracht op oude data")
            cmd = self._limits(cmd, device.config.params, device.last_values, notes)
            prev = self._last_change.get(cmd.group_key)
            if prev is not None and prev[0] != cmd and now - prev[1] < self.min_interval:
                raise SafetyRejected(f"te snel na vorige wijziging (minimaal {self.min_interval.total_seconds():.0f} s)")
        if record:
            prev = self._last_change.get(cmd.group_key)
            if prev is None or prev[0] != cmd:
                self._last_change[cmd.group_key] = (cmd, now)
        return SafetyResult(cmd, notes)

    def _limits(self, cmd: Command, params: dict, values: dict, notes: list[str]) -> Command:
        a, v = cmd.action, cmd.value
        if a in (CommandAction.BATTERY_CHARGE, CommandAction.BATTERY_DISCHARGE):
            if v is None or float(v) < 0:
                raise SafetyRejected("ongeldig batterijvermogen")
            soc = values.get(Metric.BATTERY_SOC_PCT)
            if soc is None:
                raise SafetyRejected("laadtoestand (SOC) onbekend — laden/ontladen niet toegestaan")
            if a == CommandAction.BATTERY_CHARGE and float(soc) >= self.max_soc:
                raise SafetyRejected(f"batterij vol ({float(soc):.0f}% ≥ max {self.max_soc:.0f}%)")
            floor = max(self.min_soc, self.reserve_soc)
            if a == CommandAction.BATTERY_DISCHARGE and float(soc) <= floor:
                raise SafetyRejected(f"batterij op reserve ({float(soc):.0f}% ≤ {floor:.0f}%)")
            key = "max_charge_w" if a == CommandAction.BATTERY_CHARGE else "max_discharge_w"
            limit = _param(params, key)
            if limit is not None and float(v) > float(limit):
                notes.append(f"vermogen begrensd tot apparaatlimiet {float(limit) / 1000:.1f} kW")
                return replace(cmd, value=float(limit))
        elif a == CommandAction.PV_LIMIT and v is not None:
            if float(v) < 0:
                raise SafetyRejected("negatieve PV-limiet")
            peak = _param(params, "peak_power_kw")
            if peak is not None and float(v) > float(peak) * 1000:
                return replace(cmd, value=None)
        elif a == CommandAction.EV_CURRENT:
            amps = float(v or 0)
            if amps < 0:
                raise SafetyRejected("negatieve laadstroom")
            if amps > 0:
                lo, hi = float(_param(params, "min_current_a", 6)), float(_param(params, "max_current_a", 16))
                if amps < lo:
                    raise SafetyRejected(f"laadstroom onder minimum {lo:.0f} A")
                if amps > hi:
                    notes.append(f"laadstroom begrensd tot {hi:.0f} A")
                    return replace(cmd, value=hi)
        elif a == CommandAction.HP_MODE and v not in HP_MODES:
            raise SafetyRejected(f"onbekende warmtepompmodus {v!r}")
        elif a == CommandAction.SWITCH and v not in ("on", "off"):
            raise SafetyRejected(f"ongeldige schakelopdracht {v!r}")
        return cmd

    def forget(self, device_id: str | None = None) -> None:
        for k in [k for k in self._last_change if device_id is None or k[0] == device_id]:
            del self._last_change[k]
