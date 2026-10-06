"""CommandGate: the single path from decisions to hardware.

* DRY_RUN       -> nothing is executed, everything journaled as 'EMS zou ...'
* SIMULATION    -> only drivers whose manifest says ``simulated`` are written
* LIVE          -> real hardware
Also de-duplicates commands (devices such as Modbus inverters should not be
hammered with identical writes) and refreshes them periodically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from ems.core.models import Command, CommandAction, Decision
from ems.devices.manager import DeviceManager

log = logging.getLogger(__name__)

# Numeric changes smaller than this are not worth a new write.
DEADBAND: dict[CommandAction, float] = {
    CommandAction.BATTERY_CHARGE: 100.0,
    CommandAction.BATTERY_DISCHARGE: 100.0,
    CommandAction.PV_LIMIT: 100.0,
    CommandAction.EV_CURRENT: 0.5,
}


class GateMode(StrEnum):
    LIVE = "live"
    SIMULATION = "simulation"
    DRY_RUN = "dry_run"


class Outcome(StrEnum):
    SENT = "sent"
    REFRESHED = "refreshed"  # identical command re-sent as keep-alive
    DRY_RUN = "dry_run"
    SKIPPED = "skipped"      # identical to what the device already has
    BLOCKED = "blocked"      # simulation mode protecting real hardware
    FAILED = "failed"


@dataclass
class GateResult:
    decision: Decision
    outcome: Outcome
    old_value: object = None
    error: str | None = None


class CommandGate:
    def __init__(self, devices: DeviceManager, mode: GateMode, refresh_s: float) -> None:
        self.devices = devices
        self.mode = mode
        self.refresh = timedelta(seconds=refresh_s)
        self._last: dict[tuple[str, str], tuple[Command, datetime]] = {}

    def reset(self, device_id: str | None = None) -> None:
        if device_id is None:
            self._last.clear()
        else:
            for k in [k for k in self._last if k[0] == device_id]:
                del self._last[k]

    def last_command(self, device_id: str, group: str) -> Command | None:
        entry = self._last.get((device_id, group))
        return entry[0] if entry else None

    def _unchanged(self, cmd: Command, now: datetime) -> bool:
        prev = self._last.get(cmd.group_key)
        if prev is None or now - prev[1] >= self.refresh:
            return False
        old = prev[0]
        if old.action != cmd.action:
            return False
        if isinstance(cmd.value, (int, float)) and isinstance(old.value, (int, float)):
            return abs(cmd.value - old.value) < DEADBAND.get(cmd.action, 1e-9)
        return old.value == cmd.value

    async def submit(self, decision: Decision, now: datetime) -> GateResult:
        cmd = decision.command
        prev = self._last.get(cmd.group_key)
        old_value = None if prev is None else (prev[0].action.value, prev[0].value)
        if self._unchanged(cmd, now):
            return GateResult(decision, Outcome.SKIPPED, old_value)
        refresh = prev is not None and prev[0] == cmd
        if self.mode == GateMode.DRY_RUN:
            self._last[cmd.group_key] = (cmd, now)
            return GateResult(decision, Outcome.SKIPPED if refresh else Outcome.DRY_RUN, old_value)
        if self.mode == GateMode.SIMULATION and not self.devices.is_simulated(cmd.device_id):
            return GateResult(decision, Outcome.BLOCKED, old_value,
                              "simulatiemodus: echte apparatuur wordt niet aangestuurd")
        try:
            await self.devices.apply(cmd)
        except Exception as exc:
            self._last.pop(cmd.group_key, None)
            log.warning("command failed", extra={"device": cmd.device_id, "action": cmd.action, "error": str(exc)})
            return GateResult(decision, Outcome.FAILED, old_value, f"{type(exc).__name__}: {exc}")
        self._last[cmd.group_key] = (cmd, now)
        return GateResult(decision, Outcome.REFRESHED if refresh else Outcome.SENT, old_value)
