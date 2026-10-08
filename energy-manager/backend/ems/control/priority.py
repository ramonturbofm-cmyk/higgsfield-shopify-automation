"""Central command scheduling: one winner per device function, by priority.

  1 SAFETY  2 EMERGENCY  3 GRID_PROTECTION  4 COMMISSIONING  5 MANUAL_OVERRIDE
  6 OPTIMIZER  7 AUTOMATION  8 DEVICE_DEFAULT

Automations are supplementary: they act only where the optimizer has nothing planned
for that function. The GridGuard runs after scheduling and limits whatever won (also a
manual command) so the grid connection is never overloaded.
"""

from __future__ import annotations

from dataclasses import replace
from enum import IntEnum

from ems.core.models import Command, CommandAction, Decision, DeviceCategory


class CommandPriority(IntEnum):
    SAFETY = 1
    EMERGENCY = 2
    GRID_PROTECTION = 3
    COMMISSIONING = 4
    MANUAL_OVERRIDE = 5
    OPTIMIZER = 6
    AUTOMATION = 7
    DEVICE_DEFAULT = 8


PRIORITY_LABELS_NL = {
    CommandPriority.SAFETY: "veiligheid", CommandPriority.EMERGENCY: "noodsituatie",
    CommandPriority.GRID_PROTECTION: "netbeveiliging", CommandPriority.COMMISSIONING: "inbedrijfstelling",
    CommandPriority.MANUAL_OVERRIDE: "handmatige bediening", CommandPriority.OPTIMIZER: "optimizer",
    CommandPriority.AUTOMATION: "automatisering", CommandPriority.DEVICE_DEFAULT: "apparaatstandaard",
}


def priority_of(source: str) -> CommandPriority:
    head = (source or "").split(":", 1)[0]
    return {
        "safety": CommandPriority.SAFETY, "engine": CommandPriority.SAFETY, "emergency": CommandPriority.EMERGENCY,
        "grid_protection": CommandPriority.GRID_PROTECTION, "commissioning": CommandPriority.COMMISSIONING,
        "override": CommandPriority.MANUAL_OVERRIDE, "controller": CommandPriority.OPTIMIZER,
        "optimizer": CommandPriority.OPTIMIZER, "automation": CommandPriority.AUTOMATION,
    }.get(head, CommandPriority.DEVICE_DEFAULT)


def schedule(decisions: list[Decision]) -> list[Decision]:
    """Keep the highest-priority decision per (device, function); explain what it overruled."""
    best: dict[tuple[str, str], Decision] = {}
    beaten: dict[tuple[str, str], list[Decision]] = {}
    for d in decisions:
        key = d.command.group_key
        cur = best.get(key)
        if cur is None or priority_of(d.source) < priority_of(cur.source):
            if cur is not None:
                beaten.setdefault(key, []).append(cur)
            best[key] = d
        else:
            beaten.setdefault(key, []).append(d)
    out = []
    for key, d in best.items():
        if key in beaten:
            lost = ", ".join(sorted({PRIORITY_LABELS_NL[priority_of(b.source)] for b in beaten[key]}))
            d = replace(d, reasons=d.reasons + [f"Voorrang ({PRIORITY_LABELS_NL[priority_of(d.source)]}) boven: {lost}"])
        out.append(d)
    return out


class GridGuard:
    """Projected grid import must stay below the connection limit; flexible loads give way first."""

    def __init__(self, config) -> None:
        g = config.grid
        self.limit_w = (g.max_import_kw * 1000 if g.max_import_kw else
                        g.connection_kw * 1000 * (1 - g.phase_safety_margin_pct / 100))
        self.volt = g.voltage_v
        self.phases = g.phases
        self.config = config

    def _ev_w(self, cmd: Command) -> float:
        dev = self.config.device(cmd.device_id)
        ph = 3 if dev.phase == "3P" and self.phases == 3 else 1
        return float(cmd.value or 0) * self.volt * ph, ph

    def apply(self, decisions: list[Decision], snap) -> list[Decision]:
        if not snap.grid_valid or snap.grid_power_w is None:
            return decisions
        # Extra import caused by the new commands compared with what the devices do now.
        extra: dict[int, float] = {}
        for i, d in enumerate(decisions):
            c = d.command
            if c.action == CommandAction.BATTERY_CHARGE:
                cur = max(0.0, float((snap.devices[c.device_id].values.get("battery_power_w") or 0)
                                     if c.device_id in snap.devices else 0))
                extra[i] = float(c.value or 0) - cur
            elif c.action == CommandAction.EV_CURRENT and c.device_id in snap.devices:
                st = snap.devices[c.device_id]
                if st.category == DeviceCategory.EV_CHARGER:
                    extra[i] = self._ev_w(c)[0] - float(st.values.get("ev_power_w") or 0)
        over = snap.grid_power_w + sum(v for v in extra.values() if v > 0) - self.limit_w
        if over <= 0:
            return decisions
        out = list(decisions)
        # Flexible loads give way in this order: EV first, then battery charging.
        for action in (CommandAction.EV_CURRENT, CommandAction.BATTERY_CHARGE):
            for i, d in enumerate(out):
                if over <= 0 or d.command.action != action or extra.get(i, 0) <= 0:
                    continue
                c = d.command
                cut = min(extra[i], over)
                if action == CommandAction.EV_CURRENT:
                    w, ph = self._ev_w(c)
                    amps = max(0.0, (w - cut) / (self.volt * ph))
                    min_a = float(self.config.device(c.device_id).params.get("min_current_a", 6))
                    new_value = float(int(amps)) if amps >= min_a else 0.0
                    removed = w - new_value * self.volt * ph
                else:
                    new_value = max(0.0, round(float(c.value or 0) - cut, -1))
                    removed = float(c.value or 0) - new_value
                over -= removed
                out[i] = replace(d, command=replace(c, value=new_value), source="grid_protection",
                                 reasons=d.reasons + [
                                     f"Netbeveiliging: aansluiting ({self.limit_w / 1000:.1f} kW) zou overbelast raken"
                                     f" — begrensd tot {new_value:g}{' A' if action == CommandAction.EV_CURRENT else ' W'}"])
        return out
