"""One authoritative answer to "may this command run on this device, and what happens if it does?"

* :class:`ControlState` — the single control state of a device (audit P0-04). Every screen shows this
  value; nothing derives its own.
* :func:`can_execute` — central per-action check used by manual overrides, automations and the
  ``/devices/{id}/actions`` endpoint (audit P0-02/P0-03). It checks capability (type schema ∩ driver),
  value range against the device's own limits, user role, and the control state. It says whether the
  command would really reach the device or only be shown as "EMS zou …".
* :class:`ConfirmationTracker` — after a command is sent, compares the next measurements with what
  was asked: confirmed / unconfirmed / no_feedback (audit P0-04: desired → validated → sent →
  confirmed → measured).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from ems.core.models import ACTION_CAPABILITY, Command, CommandAction, Metric
from ems.devices.capabilities import ACTION_LABELS, check_value, type_capabilities, value_spec


class ControlState(StrEnum):
    READ_ONLY = "read_only"
    SHADOW = "shadow"
    LIMITED_CONTROL = "limited_control"
    FULL_CONTROL = "full_control"
    MANUAL_OVERRIDE = "manual_override"
    SAFE_MODE = "safe_mode"
    OFFLINE = "offline"
    ERROR = "error"


STATE_LABELS = {
    ControlState.READ_ONLY: "Alleen lezen — het EMS stuurt niets",
    ControlState.SHADOW: "Schaduwmodus — het EMS laat zien wat het zou doen, maar stuurt niets",
    ControlState.LIMITED_CONTROL: "Beperkte regeling — het EMS stuurt met begrensd vermogen",
    ControlState.FULL_CONTROL: "Volledige regeling — het EMS stuurt dit apparaat",
    ControlState.MANUAL_OVERRIDE: "Handmatige bediening actief",
    ControlState.SAFE_MODE: "Veilige stand — apparaat op eigen regeling (EMS-fallback)",
    ControlState.OFFLINE: "Niet verbonden",
    ControlState.ERROR: "Fout — apparaat of driver werkt niet",
}

# States in which a command really reaches the device (subject to the gate mode).
EXECUTING = {ControlState.LIMITED_CONTROL, ControlState.FULL_CONTROL, ControlState.MANUAL_OVERRIDE}

LEVEL_STATE = {"connection_test": ControlState.READ_ONLY, "read_only": ControlState.READ_ONLY,
               "shadow": ControlState.SHADOW, "limited": ControlState.LIMITED_CONTROL,
               "full": ControlState.FULL_CONTROL}


def control_state(md, *, failsafe: bool = False, override_active: bool = False) -> ControlState:
    """The control state of a managed device. Order matters: hardware problems first, then the
    system fallback, then commissioning level, with a manual override on top of a writable level."""
    if md is None or md.driver is None:
        return ControlState.ERROR
    if not md.connected:
        return ControlState.OFFLINE if md.error is None or md.last_ok is not None else ControlState.ERROR
    level = LEVEL_STATE.get(md.config.control_level, ControlState.READ_ONLY)
    if not any(c.value.startswith("control_") for c in md.driver.device_capabilities()):
        return ControlState.READ_ONLY
    if failsafe and level in EXECUTING:
        return ControlState.SAFE_MODE
    if override_active and level in EXECUTING:
        return ControlState.MANUAL_OVERRIDE
    return level


@dataclass
class ExecCheck:
    allowed: bool                    # accepted (it will run, or be shown as "EMS zou …")
    executes: bool                   # really sent to the device
    action: str
    value: Any = None                # normalised value
    state: str = ""
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def can_execute(md, command: Command, user, state: ControlState, *, config=None,
                gate_mode: str = "live") -> ExecCheck:
    """Central validation of one command for one device (manual override, automation, UI)."""
    reasons: list[str] = []
    action = command.action
    out = ExecCheck(False, False, action.value, command.value, state.value, reasons)
    if user is not None and not user.can("operator"):
        reasons.append("uw rol mag geen apparaten bedienen (minimaal operator)")
        return out
    if md is None or md.driver is None:
        reasons.append("apparaat of driver niet beschikbaar")
        return out
    cat = md.config.category
    cap = ACTION_CAPABILITY[action]
    if cap not in type_capabilities(cat):
        reasons.append(f"'{ACTION_LABELS.get(action, action.value)}' bestaat niet voor dit apparaattype")
        return out
    if cap not in md.driver.device_capabilities():
        reasons.append(f"dit apparaat ondersteunt '{ACTION_LABELS.get(action, action.value)}' niet")
        return out
    spec = value_spec(action, cat, md.config.params, config)
    value, err = check_value(spec, command.value)
    if err:
        reasons.append(err)
        return out
    out.value = value
    if state in (ControlState.OFFLINE, ControlState.ERROR):
        reasons.append("apparaat niet verbonden — opdracht kan niet worden uitgevoerd")
        return out
    out.allowed = True
    if state == ControlState.SAFE_MODE:
        reasons.append("EMS-fallback actief: apparaten staan op eigen regeling; de opdracht wordt pas "
                       "uitgevoerd als de netmeting weer betrouwbaar is")
    elif state in (ControlState.READ_ONLY, ControlState.SHADOW):
        reasons.append("apparaat staat op " + ("alleen lezen" if state == ControlState.READ_ONLY else
                                               "schaduwmodus") + ": wordt alleen getoond als 'EMS zou …', "
                       "niet uitgevoerd")
    elif gate_mode == "dry_run":
        reasons.append("EMS staat in DRY RUN: niets wordt uitgevoerd")
    elif gate_mode == "simulation" and not getattr(md.driver.manifest, "simulated", False):
        reasons.append("simulatiemodus: echte apparatuur wordt niet aangestuurd")
    else:
        out.executes = True
        if state == ControlState.LIMITED_CONTROL:
            reasons.append(f"beperkte regeling: maximaal {md.config.limited_fraction:.0%} van het gevraagde")
    return out


# ------------------------------------------------------------- confirmation
@dataclass
class Execution:
    device_id: str
    desired: dict | None = None      # what the controller / user asked
    validated: dict | None = None    # after limits, safety and commissioning
    sent: dict | None = None         # what was written, and when
    measured: dict | None = None     # relevant reading after the write
    status: str = "idle"             # idle | shadow | rejected | sent | confirmed | unconfirmed | no_feedback
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _cmd(c: Command, ts: datetime | None = None) -> dict:
    d = {"action": c.action.value, "value": c.value, "description": c.describe_nl()}
    if ts is not None:
        d["ts"] = ts.isoformat()
    return d


def _confirm(cmd: Command, values: dict) -> tuple[str | None, dict | None, str]:
    """(status or None if not yet decidable, measured, detail) for a sent command."""
    a, v = cmd.action, cmd.value
    get = values.get
    match a:
        case CommandAction.BATTERY_CHARGE | CommandAction.BATTERY_DISCHARGE:
            p = get(Metric.BATTERY_POWER_W)
            if p is None:
                return "no_feedback", None, "apparaat meldt geen batterijvermogen"
            want = float(v or 0) * (1 if a == CommandAction.BATTERY_CHARGE else -1)
            ok = abs(want) < 50 or (p * want > 0 and abs(p) >= 0.5 * abs(want))
            return ("confirmed" if ok else None), {"battery_power_w": p}, f"gemeten {p:.0f} W"
        case CommandAction.BATTERY_AUTO | CommandAction.BATTERY_STANDBY:
            m = get(Metric.BATTERY_MODE)
            if m is None:
                return "no_feedback", None, "apparaat meldt zijn modus niet"
            want = "auto" if a == CommandAction.BATTERY_AUTO else "standby"
            return ("confirmed" if str(m) == want else None), {"battery_mode": m}, f"modus {m}"
        case CommandAction.EV_CURRENT:
            lim = get(Metric.EV_CURRENT_LIMIT_A)
            if lim is None:
                return "no_feedback", None, "laadpaal meldt geen ingestelde stroom"
            return ("confirmed" if abs(float(lim) - float(v or 0)) < 1 else None), \
                {"ev_current_limit_a": lim}, f"ingesteld {lim} A"
        case CommandAction.PV_LIMIT:
            p = get(Metric.PV_POWER_W)
            if p is None:
                return "no_feedback", None, "omvormer meldt geen vermogen"
            ok = v is None or p <= float(v) * 1.05 + 50
            return ("confirmed" if ok else None), {"pv_power_w": p}, f"gemeten {p:.0f} W"
        case CommandAction.HP_MODE:
            m = get(Metric.HP_MODE)
            if m is None:
                return "no_feedback", None, "warmtepomp meldt haar modus niet"
            return ("confirmed" if str(m) == str(v) else None), {"hp_mode": m}, f"modus {m}"
        case CommandAction.TEMP_SETPOINT:
            s = get(Metric.HP_SETPOINT_C)
            if s is None:
                return "no_feedback", None, "apparaat meldt geen setpoint"
            return ("confirmed" if abs(float(s) - float(v)) < 0.3 else None), {"setpoint_c": s}, f"setpoint {s} °C"
        case CommandAction.BATTERY_SOC_LIMIT:
            s = get(Metric.BATTERY_MAX_SOC_PCT)
            if s is None:
                return "no_feedback", None, "batterij meldt geen SOC-limiet"
            return ("confirmed" if abs(float(s) - float(v)) < 1 else None), {"max_soc_pct": s}, f"limiet {s}%"
    return "no_feedback", None, "geen terugmelding voor deze opdracht"


class ConfirmationTracker:
    """Per device and command group: desired → validated → sent → confirmed/unconfirmed."""

    def __init__(self, timeout_s: float = 90.0) -> None:
        self.timeout = timedelta(seconds=timeout_s)
        self._x: dict[tuple[str, str], Execution] = {}
        self._sent_at: dict[tuple[str, str], datetime] = {}

    def record(self, desired: Command, validated: Command, outcome: str, now: datetime,
               error: str | None = None) -> None:
        key = validated.group_key
        x = self._x.setdefault(key, Execution(validated.device_id))
        x.desired, x.validated = _cmd(desired), _cmd(validated)
        if outcome in ("sent",):
            x.sent, x.status, x.detail, x.measured = _cmd(validated, now), "sent", "wacht op terugmelding", None
            self._sent_at[key] = now
        elif outcome == "refreshed":
            pass                                  # keep the confirmation of the original write
        elif outcome in ("shadow", "not_commissioned", "dry_run", "blocked"):
            x.sent, x.status, x.detail = None, "shadow", error or "niet uitgevoerd"
            self._sent_at.pop(key, None)
        elif outcome in ("rejected", "failed"):
            x.status, x.detail = outcome, error or ""
            self._sent_at.pop(key, None)

    def evaluate(self, values_by_device: dict[str, dict], now: datetime) -> None:
        for key, at in list(self._sent_at.items()):
            x = self._x[key]
            cmd = Command(x.device_id, CommandAction(x.sent["action"]), x.sent["value"])
            status, measured, detail = _confirm(cmd, values_by_device.get(x.device_id, {}))
            x.measured, x.detail = measured, detail
            if status is not None:
                x.status = status
                del self._sent_at[key]
            elif now - at > self.timeout:
                x.status, x.detail = "unconfirmed", f"apparaat reageert niet zoals gevraagd ({detail})"
                del self._sent_at[key]

    def for_device(self, device_id: str) -> list[dict]:
        return [x.to_dict() for (d, _), x in self._x.items() if d == device_id]

    def clear(self, device_id: str) -> None:
        for k in [k for k in self._x if k[0] == device_id]:
            self._x.pop(k, None)
            self._sent_at.pop(k, None)
