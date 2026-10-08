"""Manual overrides that expire automatically and return to AUTO."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from ems.core.clock import Clock
from ems.core.models import Command, Decision

# Durations offered by the UI; None = until ended manually.
OVERRIDE_DURATIONS_MIN = (30, 60, 120, 240, None)


@dataclass
class Override:
    command: Command
    created: datetime
    expires: datetime | None
    user: str = "local"
    note: str = ""
    source: str = "override"     # "automation" for overrides created by automations (lower priority)

    def active(self, now: datetime) -> bool:
        return self.expires is None or now < self.expires


class OverrideManager:
    def __init__(self, clock: Clock, tz: tzinfo | None = None) -> None:
        self.clock = clock
        self.tz = tz
        self._items: dict[tuple[str, str], Override] = {}

    def set(self, command: Command, duration_min: float | None, user: str = "local", note: str = "",
            source: str = "override") -> Override:
        now = self.clock.now()
        expires = None if duration_min is None else now + timedelta(minutes=duration_min)
        ov = Override(command, now, expires, user, note, source)
        self._items[command.group_key] = ov
        return ov

    def clear(self, device_id: str, group: str | None = None) -> int:
        keys = [k for k in self._items if k[0] == device_id and (group is None or k[1] == group)]
        for k in keys:
            del self._items[k]
        return len(keys)

    def expire(self) -> list[Override]:
        """Remove and return overrides that ran out."""
        now = self.clock.now()
        gone = [k for k, o in self._items.items() if not o.active(now)]
        return [self._items.pop(k) for k in gone]

    def active(self) -> list[Override]:
        now = self.clock.now()
        return [o for o in self._items.values() if o.active(now)]

    def apply(self, decisions: list[Decision]) -> list[Decision]:
        """Add active overrides as decisions; the priority scheduler picks one winner per group."""
        active = {o.command.group_key: o for o in self.active()}
        result = list(decisions)
        for o in active.values():
            until = ("tot handmatig beëindigd" if o.expires is None
                     else f"tot {o.expires.astimezone(self.tz):%H:%M}" if self.tz
                     else f"tot {o.expires:%H:%M} UTC")
            result.append(Decision(
                command=o.command,
                summary=(f"Handmatig: {o.command.describe_nl()}" if o.source == "override"
                         else f"Automatisering: {o.command.describe_nl()}"),
                reasons=[f"{'Handmatige bediening' if o.source == 'override' else 'Actie'} door {o.user} actief {until}"]
                + ([o.note] if o.note else []),
                source=o.source,
            ))
        return result
