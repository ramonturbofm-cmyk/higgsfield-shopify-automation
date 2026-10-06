"""Rule engine.

Definition (JSON):
{
  "if":   {"all": [ {"metric": "price.import", "op": "<", "value": 0},
                    {"metric": "battery.soc", "op": "<", "value": 90} ]},
  "then": [ {"type": "override", "device": "battery_1", "action": "battery_charge",
             "value": 8000, "duration_min": 30} ],
  "else": [],
  "cooldown_s": 300
}
Conditions: {"all": [...]}, {"any": [...]}, {"not": cond}, or a comparison
{"metric", "op", "value"} with op in < <= > >= == != between in.
Actions: override, clear_override, notify, set_profile, replan.
Rules are edge-triggered: THEN runs when the condition becomes true, ELSE when it
becomes false. Overrides created by automations always expire automatically.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from ems.core.models import HP_MODES, CommandAction

log = logging.getLogger(__name__)

METRICS = {
    "price.import": "Importprijs (EUR/kWh)", "price.export": "Terugleverprijs (EUR/kWh)",
    "price.spot": "Marktprijs (EUR/kWh)", "grid.power_w": "Netvermogen (W, + = afname)",
    "grid.import_w": "Netafname (W)", "grid.export_w": "Teruglevering (W)", "pv.power_w": "PV-vermogen (W)",
    "battery.soc": "Batterij-SOC (%)", "battery.power_w": "Batterijvermogen (W, + = laden)",
    "house.load_w": "Huisverbruik (W)", "hp.power_w": "Warmtepomp (W)", "ev.power_w": "Laadpaal (W)",
    "temp.outdoor": "Buitentemperatuur (°C)", "temp.indoor": "Binnentemperatuur (°C)",
    "time": "Tijd (HH:MM)", "weekday": "Weekdag (0=ma .. 6=zo)",
    "forecast.pv_next_24h_kwh": "PV-verwachting komende 24 uur (kWh)",
    "forecast.pv_remaining_today_kwh": "PV-verwachting rest van vandaag (kWh)",
}
OPS = ("<", "<=", ">", ">=", "==", "!=", "between", "in")
ACTION_TYPES = ("override", "clear_override", "notify", "set_profile", "replan")


def _metric_ok(name: str) -> bool:
    return name in METRICS or (name.startswith("device.") and name.count(".") >= 2)


def validate_definition(defn: dict) -> None:
    if not isinstance(defn, dict) or "if" not in defn:
        raise ValueError("automatisering heeft een 'if' (ALS) nodig")

    def check(c: Any, depth: int = 0) -> None:
        if depth > 8:
            raise ValueError("voorwaarden te diep genest")
        if not isinstance(c, dict):
            raise ValueError("voorwaarde moet een object zijn")
        if "all" in c or "any" in c:
            items = c.get("all", c.get("any"))
            if not isinstance(items, list) or not items:
                raise ValueError("EN/OF heeft minstens één voorwaarde nodig")
            for i in items:
                check(i, depth + 1)
        elif "not" in c:
            check(c["not"], depth + 1)
        else:
            if not _metric_ok(c.get("metric", "")):
                raise ValueError(f"onbekende meetwaarde {c.get('metric')!r}")
            if c.get("op") not in OPS:
                raise ValueError(f"onbekende vergelijking {c.get('op')!r}")
            if c["op"] in ("between", "in") and not isinstance(c.get("value"), list):
                raise ValueError("'tussen'/'in' verwacht een lijst")
            if "value" not in c:
                raise ValueError("vergelijking zonder waarde")

    check(defn["if"])
    for branch in ("then", "else"):
        for a in defn.get(branch, []) or []:
            t = a.get("type")
            if t not in ACTION_TYPES:
                raise ValueError(f"onbekende actie {t!r}")
            if t == "override":
                try:
                    action = CommandAction(a.get("action"))
                except ValueError:
                    raise ValueError(f"onbekende apparaatactie {a.get('action')!r}") from None
                if not a.get("device"):
                    raise ValueError("override zonder apparaat")
                if action == CommandAction.HP_MODE and a.get("value") not in HP_MODES:
                    raise ValueError("warmtepompmodus moet normal, boost of eco zijn")
                d = a.get("duration_min", 60)
                if d is None or not 1 <= float(d) <= 24 * 60:
                    raise ValueError("automatisering-overrides moeten verlopen (1 min .. 24 uur)")
            if t == "notify" and not a.get("message"):
                raise ValueError("melding zonder tekst")
    if not defn.get("then") and not defn.get("else"):
        raise ValueError("automatisering zonder acties (DAN/ANDERS)")


def _hhmm(v: str) -> int:
    h, m = str(v).split(":")
    return int(h) * 60 + int(m)


def evaluate_condition(c: dict, metrics: dict[str, Any]) -> bool | None:
    """True/False, or None when a needed value is unavailable (rule not evaluated)."""
    if "all" in c:
        res = [evaluate_condition(x, metrics) for x in c["all"]]
        return None if None in res else all(res)
    if "any" in c:
        res = [evaluate_condition(x, metrics) for x in c["any"]]
        if any(r is True for r in res):
            return True
        return None if None in res else False
    if "not" in c:
        r = evaluate_condition(c["not"], metrics)
        return None if r is None else not r
    v = metrics.get(c["metric"])
    if v is None:
        return None
    op, target = c["op"], c["value"]
    if c["metric"] == "time":
        v = _hhmm(v)
        target = [_hhmm(x) for x in target] if isinstance(target, list) else _hhmm(target)
        if op == "between":
            lo, hi = target
            return lo <= v < hi if lo <= hi else (v >= lo or v < hi)
    if op == "between":
        lo, hi = target
        return lo <= v <= hi
    if op == "in":
        return v in target
    try:
        a, b = (float(v), float(target)) if not isinstance(v, str) else (v, str(target))
    except (TypeError, ValueError):
        a, b = str(v), str(target)
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "==": a == b, "!=": a != b}[op]


ActionRunner = Callable[[dict, dict], Awaitable[str]]


class AutomationEngine:
    def __init__(self, db, run_action: ActionRunner) -> None:
        self.db = db
        self.run_action = run_action
        self._state: dict[int, bool] = {}
        self._last_fire: dict[int, float] = {}

    async def evaluate(self, metrics: dict[str, Any], now: datetime) -> list[dict]:
        rules = await asyncio.to_thread(self.db.list_automations)
        fired = []
        for r in rules:
            if not r["enabled"]:
                continue
            rid, defn = r["id"], r["definition"]
            try:
                result = evaluate_condition(defn["if"], metrics)
            except Exception as exc:
                log.warning("automation evaluation failed", extra={"automation": rid, "error": str(exc)})
                continue
            if result is None:
                continue
            previous = self._state.get(rid, r.get("last_state"))
            self._state[rid] = result
            if previous == result:
                continue
            branch = "then" if result else "else"
            actions = defn.get(branch) or []
            cooldown = float(defn.get("cooldown_s", 300))
            if not actions or (previous is None and not result):
                await asyncio.to_thread(self.db.set_automation_state, rid, result, None)
                continue
            if time.time() - self._last_fire.get(rid, 0) < cooldown:
                continue
            self._last_fire[rid] = time.time()
            outcomes = []
            for a in actions:
                try:
                    outcomes.append(await self.run_action(a, {"automation_id": rid, "name": r["name"]}))
                except Exception as exc:
                    outcomes.append(f"mislukt: {exc}")
            await asyncio.to_thread(self.db.set_automation_state, rid, result, time.time())
            fired.append({"id": rid, "name": r["name"], "branch": branch, "outcomes": outcomes})
            log.info("automation fired", extra={"automation": rid, "branch": branch})
        return fired
