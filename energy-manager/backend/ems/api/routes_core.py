"""Core API: auth, users, system, live energy, settings, tariff, prices, forecast,
optimizer, profiles, overrides, decisions, notifications, history, finance."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from ems import __version__
from ems.api.deps import admin, clear_session_cookies, current_principal, get_runtime, operator, set_session_cookies, viewer
from ems.core.config import ConfigError, StrategyProfile, dump_config, settings_schema
from ems.core.models import Command, CommandAction
from ems.prices.providers import parse_manual_prices
from ems.security.auth import ROLES, Principal, hash_api_token, hash_password, new_api_token, verify_password
from ems.server.history import day_bounds
from ems.server.runtime import EMSRuntime
from ems.services.finance import finance_summary
from ems.services.settlement import compare, rules_for, settle
from ems.tariffs.taxes import TAX_TABLE

router = APIRouter(prefix="/api/v1")

PROFILE_LABELS = {
    "lowest_cost": "Laagste kosten", "maximum_profit": "Maximale opbrengst",
    "maximum_self_consumption": "Maximale zelfconsumptie", "zero_export": "Geen teruglevering",
    "battery_saver": "Batterij sparen", "peak_shaving": "Piekbegrenzing", "comfort": "Comfort", "eco": "Eco",
    "backup_priority": "Noodstroom eerst", "custom": "Aangepast", "balanced": "Gebalanceerd",
}
PROFILE_HELP = {
    "lowest_cost": "Laagste totale energiekosten; batterij en apparaten worden ingezet waar dat geld oplevert.",
    "maximum_profit": "Ook actief handelen met de batterij als het prijsverschil groot genoeg is (meer cycli).",
    "maximum_self_consumption": "Zoveel mogelijk eigen zonnestroom zelf gebruiken; niet laden uit het net.",
    "balanced": "Kosten besparen met beperkte batterijslijtage — aanbevolen standaard.",
    "zero_export": "Niets terugleveren; PV wordt zo nodig begrensd (vereist een primaire netmeter).",
    "battery_saver": "Batterij zo min mogelijk belasten (max. 1 cyclus per dag).",
    "peak_shaving": "Afnamepieken beperken tot de ingestelde piekgrens.",
    "comfort": "Comfort gaat voor: warmtepomp wijkt minder af van de gewenste temperatuur.",
    "eco": "Meer flexibiliteit in temperatuur voor meer besparing.",
    "backup_priority": "Altijd minimaal de helft van de batterij vol houden voor stroomuitval.",
    "custom": "Eigen instellingen uit Instellingen → Batterij / Strategie.",
}
SETTINGS_SECTIONS = ("runtime", "site", "grid", "control", "optimizer", "battery", "heatpump", "strategy", "tariff",
                     "prices", "forecast", "notifications", "node")


def _err(exc: Exception, code: int = 422) -> HTTPException:
    return HTTPException(code, str(exc))


# ------------------------------------------------------------------ public
@router.get("/system/info", tags=["system"])
async def system_info(rt: EMSRuntime = Depends(get_runtime)) -> dict:
    users = await asyncio.to_thread(rt.db.count_users)
    return {"product": "Energy Manager", "version": __version__, "mode": rt.config.runtime.mode,
            "site": rt.config.site.name, "setup_required": users == 0,
            "demo_login": {"username": "demo", "password": "demo"} if rt.demo else None}


# -------------------------------------------------------------------- auth
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=1, max_length=256)


@router.post("/auth/setup", tags=["auth"])
async def setup(body: Credentials, request: Request, response: Response,
                rt: EMSRuntime = Depends(get_runtime)) -> dict:
    if await asyncio.to_thread(rt.db.count_users) > 0:
        raise HTTPException(409, "er bestaat al een gebruiker; log in")
    if len(body.password) < 8:
        raise HTTPException(422, "wachtwoord moet minimaal 8 tekens hebben")
    await asyncio.to_thread(rt.db.create_user, body.username, hash_password(body.password), "installer")
    return _session(request, response, rt, body.username, "installer")


@router.post("/auth/login", tags=["auth"])
async def login(body: Credentials, request: Request, response: Response,
                rt: EMSRuntime = Depends(get_runtime)) -> dict:
    limiter = request.app.state.login_limiter
    client = request.client.host if request.client else "?"
    if not limiter.allowed(client):
        raise HTTPException(429, "te veel mislukte pogingen; probeer het over enkele minuten opnieuw")
    user = await asyncio.to_thread(rt.db.get_user, body.username)
    ok = user is not None and not user["disabled"] and await asyncio.to_thread(
        verify_password, body.password, user["password_hash"])
    if not ok:
        limiter.failed(client)
        raise HTTPException(401, "onjuiste gebruikersnaam of wachtwoord")
    limiter.succeeded(client)
    return _session(request, response, rt, user["username"], user["role"])


def _session(request: Request, response: Response, rt: EMSRuntime, username: str, role: str) -> dict:
    """Browser: HttpOnly session cookie + CSRF cookie. The token in the body is for scripts and
    integrations that use the Authorization header; the web interface never stores it."""
    token = rt.tokens.issue(username, role)
    csrf = set_session_cookies(request, response, token)
    return {"token": token, "username": username, "role": role, "csrf": csrf}


@router.post("/auth/logout", tags=["auth"])
async def logout(response: Response, p: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    rt.sessions.revoke(p)
    clear_session_cookies(response)
    return {"ok": True}


@router.post("/auth/ws-ticket", tags=["auth"])
async def ws_ticket(p: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    """Single-use ticket (30 s) to open the WebSocket; keeps session tokens out of URLs."""
    return {"ticket": rt.sessions.ticket(p)}


@router.get("/auth/session", tags=["auth"])
async def session(request: Request) -> dict:
    """Is there a valid browser session? Never 401, so the web UI can probe it at start-up."""
    try:
        p = await current_principal(request)
    except HTTPException:
        return {"authenticated": False}
    return {"authenticated": True, "username": p.username, "role": p.role, "kind": p.kind}


@router.get("/auth/me", tags=["auth"])
async def me(p: Principal = Depends(viewer)) -> dict:
    return {"username": p.username, "role": p.role, "kind": p.kind}


class NewUser(Credentials):
    role: Literal["viewer", "operator", "admin", "installer"] = "viewer"


@router.get("/users", tags=["auth"])
async def list_users(_: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    return await asyncio.to_thread(rt.db.list_users)


@router.post("/users", tags=["auth"])
async def create_user(body: NewUser, p: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    if ROLES.index(body.role) > ROLES.index(p.role):
        raise HTTPException(403, "u kunt geen gebruiker met meer rechten aanmaken")
    if len(body.password) < 8:
        raise HTTPException(422, "wachtwoord moet minimaal 8 tekens hebben")
    if await asyncio.to_thread(rt.db.get_user, body.username):
        raise HTTPException(409, "gebruikersnaam bestaat al")
    await asyncio.to_thread(rt.db.create_user, body.username, hash_password(body.password), body.role)
    return {"username": body.username, "role": body.role}


class PasswordChange(BaseModel):
    password: str = Field(min_length=8, max_length=256)


@router.put("/users/{username}/password", tags=["auth"])
async def change_password(username: str, body: PasswordChange, p: Principal = Depends(viewer),
                          rt: EMSRuntime = Depends(get_runtime)) -> dict:
    if p.username != username and not p.can("admin"):
        raise HTTPException(403, "alleen beheerders kunnen andermans wachtwoord wijzigen")
    if not await asyncio.to_thread(rt.db.update_user, username, password_hash=hash_password(body.password)):
        raise HTTPException(404, "gebruiker niet gevonden")
    return {"ok": True}


@router.delete("/users/{username}", tags=["auth"])
async def delete_user(username: str, p: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    if username == p.username:
        raise HTTPException(409, "u kunt uzelf niet verwijderen")
    if not await asyncio.to_thread(rt.db.delete_user, username):
        raise HTTPException(404, "gebruiker niet gevonden")
    return {"ok": True}


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role: Literal["viewer", "operator", "admin"] = "viewer"


@router.get("/auth/tokens", tags=["auth"])
async def list_tokens(_: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    return await asyncio.to_thread(rt.db.list_api_tokens)


@router.post("/auth/tokens", tags=["auth"])
async def create_token(body: NewToken, _: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    token = new_api_token()
    tid = await asyncio.to_thread(rt.db.create_api_token, body.name, hash_api_token(token), body.role)
    return {"id": tid, "name": body.name, "role": body.role, "token": token,
            "note": "Bewaar dit token nu; het wordt niet opnieuw getoond."}


@router.delete("/auth/tokens/{token_id}", tags=["auth"])
async def delete_token(token_id: int, _: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    if not await asyncio.to_thread(rt.db.delete_api_token, token_id):
        raise HTTPException(404, "token niet gevonden")
    return {"ok": True}


# ------------------------------------------------------------------ system
@router.get("/system/status", tags=["system"])
async def system_status(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return await asyncio.to_thread(rt.system_status)


@router.get("/system/logs", tags=["system"])
async def system_logs(limit: int = Query(200, le=2000), level: str | None = None, _: Principal = Depends(admin),
                      rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    return rt.log_buffer.tail(limit, level)


@router.get("/system/update", tags=["system"])
async def update_info(_: Principal = Depends(viewer)) -> dict:
    from pathlib import Path
    changelog = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    return {"current_version": __version__, "update_available": None,
            "how_to_update": "Raspberry Pi: ./install.sh update (maakt eerst een back-up; configuratie en historie "
                             "blijven behouden).",
            "changelog": changelog.read_text(encoding="utf-8") if changelog.exists() else ""}


# ------------------------------------------------------------------ energy
@router.get("/energy/live", tags=["energy"])
async def energy_live(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return rt.live()


# ---------------------------------------------------------------- settings
ENTSOE_SECRET = "prices.entsoe_token"


@router.get("/settings", tags=["settings"])
async def get_settings(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    data = rt.config.model_dump(mode="json")
    data.pop("devices", None)
    if data.get("prices", {}).get("entsoe_token"):
        data["prices"]["entsoe_token"] = "********"
    return data


@router.get("/settings/schema", tags=["settings"])
async def get_schema(_: Principal = Depends(viewer)) -> dict:
    return settings_schema()


@router.put("/settings", tags=["settings"])
async def put_settings(body: dict[str, dict[str, Any]], p: Principal = Depends(admin),
                       rt: EMSRuntime = Depends(get_runtime)) -> dict:
    unknown = set(body) - set(SETTINGS_SECTIONS)
    if unknown:
        raise HTTPException(422, f"onbekende sectie(s): {sorted(unknown)}")
    if "runtime" in body and not p.can("installer"):
        raise HTTPException(403, "bedrijfsmodus wijzigen vereist installateursrechten")
    data = rt.config.model_dump(mode="json")
    for section, values in body.items():
        if section == "prices" and "entsoe_token" in values:
            token = str(values["entsoe_token"] or "").strip()
            if token == "********":                      # masked value: keep the stored token
                values = {k: v for k, v in values.items() if k != "entsoe_token"}
            elif token and not token.startswith(("${", "secret:")):
                rt.secrets.set(ENTSOE_SECRET, token)        # never written to the YAML
                values = {**values, "entsoe_token": f"secret:{ENTSOE_SECRET}"}
            elif not token:
                rt.secrets.delete(ENTSOE_SECRET)
                values = {**values, "entsoe_token": ""}
        data[section] = {**data[section], **values}
    try:
        await rt.reload(data, p.username, f"instellingen: {', '.join(body)}")
    except (ConfigError, ValueError) as exc:
        raise _err(exc) from exc
    return await get_settings(p, rt)


@router.get("/settings/versions", tags=["settings"])
async def config_versions(_: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    return await asyncio.to_thread(rt.db.list_config_versions)


@router.post("/settings/versions/{version_id}/restore", tags=["settings"])
async def restore_version(version_id: int, p: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)):
    row = await asyncio.to_thread(rt.db.get_config_version, version_id)
    if row is None:
        raise HTTPException(404, "versie niet gevonden")
    try:
        await rt.reload(yaml.safe_load(row["yaml"]), p.username, f"versie {version_id} teruggezet")
    except (ConfigError, ValueError) as exc:
        raise _err(exc) from exc
    return {"ok": True}


@router.get("/config/export", tags=["settings"])
async def export_config(_: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> Response:
    return Response(dump_config(rt.config), media_type="application/x-yaml",
                    headers={"Content-Disposition": 'attachment; filename="ems.yaml"'})


class ConfigImport(BaseModel):
    yaml: str = Field(max_length=1_000_000)


@router.post("/config/import", tags=["settings"])
async def import_config(body: ConfigImport, p: Principal = Depends(admin),
                        rt: EMSRuntime = Depends(get_runtime)) -> dict:
    try:
        data = yaml.safe_load(body.yaml)
        if not isinstance(data, dict):
            raise ValueError("geen geldige configuratie")
        await rt.reload(data, p.username, "configuratie geïmporteerd")
    except (ConfigError, ValueError, yaml.YAMLError) as exc:
        raise _err(exc) from exc
    return {"ok": True}


# ------------------------------------------------------------------ tariff
@router.get("/tariff", tags=["tariff"])
async def get_tariff(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return rt.config.tariff.model_dump(mode="json")


@router.put("/tariff", tags=["tariff"])
async def put_tariff(body: dict[str, Any], p: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    data = rt.config.model_dump(mode="json")
    data["tariff"] = {**data["tariff"], **body}
    try:
        await rt.reload(data, p.username, "tarief gewijzigd")
    except (ConfigError, ValueError) as exc:
        raise _err(exc) from exc
    return rt.config.tariff.model_dump(mode="json")


@router.get("/tariff/preview", tags=["tariff"])
async def tariff_preview(spot: float | None = None, at: datetime | None = None, _: Principal = Depends(viewer),
                         rt: EMSRuntime = Depends(get_runtime)) -> dict:
    ts = at or rt.now()
    b = rt.tariff.breakdown_with_spot(ts, spot) if spot is not None else rt.tariff.breakdown(ts)
    return {**b.to_dict(), "fixed_costs_per_day": round(rt.tariff.fixed_costs_per_day(), 4)}


# ------------------------------------------------------------------ prices
@router.get("/prices", tags=["prices"])
async def get_prices(hours: float = Query(36, ge=1, le=168), past_hours: float = Query(12, ge=0, le=168),
                     _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    now = rt.now()
    start = (now - timedelta(hours=past_hours)).replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    out = []
    for p in rt.prices.series(start, now + timedelta(hours=hours), estimate=True, include_missing=True):
        spot = None if p.spot is None else rt.tariff.contract_spot(p.start, p.spot)
        b = rt.tariff.breakdown_with_spot(p.start, spot) if spot is not None else None
        out.append({"start": p.start.isoformat(), "end": (p.start + timedelta(minutes=p.resolution_min)).isoformat(),
                    "spot": p.spot, "import": None if b is None else b.import_price,
                    "export": None if b is None else b.export_price, "estimated": p.estimated, "status": p.status})
    cur = rt.prices.current(now)
    current = None
    if cur is not None:
        b = rt.tariff.breakdown_with_spot(now, rt.tariff.contract_spot(now, cur["spot"]))
        current = {**cur, "import": b.import_price, "export": b.export_price}
    return {"status": rt.prices.status(now), "points": out, "now": now.isoformat(), "current": current,
            "current_reason": None if current else "geen gepubliceerde prijs voor het huidige interval",
            "market_resolution_min": 15, "contract_resolution_min": rt.config.tariff.price_resolution_min,
            "timezone": rt.config.site.timezone}


@router.post("/prices/refresh", tags=["prices"])
async def refresh_prices(_: Principal = Depends(operator), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    n = await rt.prices.refresh(rt.now())
    if n:
        rt.optimizer.request("prijzen ververst")
    return {"fetched": n, "status": rt.prices.status()}


class ManualPrices(BaseModel):
    points: list[dict] = Field(max_length=5000)
    dry_run: bool = False          # only validate and report gaps


@router.post("/prices/manual", tags=["prices"])
async def manual_prices(body: ManualPrices, _: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    try:
        pts, report = parse_manual_prices(body.points, rt.config.site.timezone)
    except (KeyError, ValueError, TypeError) as exc:
        raise _err(exc) from exc
    if body.dry_run:
        return {"stored": 0, **report}
    n = await asyncio.to_thread(rt.prices.add_points, pts, "manual")
    rt.optimizer.request("handmatige prijzen")
    return {"stored": n, **report}


# ---------------------------------------------------------------- forecast
@router.get("/forecast", tags=["forecast"])
async def get_forecast(hours: float = Query(36, ge=1, le=72), _: Principal = Depends(viewer),
                       rt: EMSRuntime = Depends(get_runtime)) -> dict:
    snap = rt.engine.last_snapshot
    fc = rt.forecast.build(rt.now(), hours, None if snap is None else snap.outdoor_temp_c)
    return {"status": rt.forecast.status(), **fc.to_dict()}


# --------------------------------------------------------------- optimizer
@router.get("/optimizer/plan", tags=["optimizer"])
async def get_plan(hours: float = Query(36, ge=1, le=72), _: Principal = Depends(viewer),
                   rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return rt.plan_view(hours)


@router.post("/optimizer/run", tags=["optimizer"])
async def run_optimizer(p: Principal = Depends(operator), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    snap = rt.engine.last_snapshot
    if snap is None:
        raise HTTPException(409, "nog geen meetgegevens")
    await rt.optimizer.run(snap, rt.now(), f"handmatig door {p.username}")
    return rt.plan_view(36)


# ---------------------------------------------------------------- profiles
@router.get("/profiles", tags=["settings"])
async def profiles(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return {"active": rt.config.strategy.profile.value,
            "profiles": [{"id": p.value, "label": PROFILE_LABELS.get(p.value, p.value),
                          "help": PROFILE_HELP.get(p.value, "")} for p in StrategyProfile]}


class ProfileChoice(BaseModel):
    profile: StrategyProfile


@router.put("/profiles/active", tags=["settings"])
async def set_profile(body: ProfileChoice, p: Principal = Depends(operator), rt: EMSRuntime = Depends(get_runtime)):
    data = rt.config.model_dump(mode="json")
    data["strategy"]["profile"] = body.profile.value
    await rt.reload(data, p.username, f"profiel {body.profile.value}")
    return {"active": body.profile.value}


# --------------------------------------------------------------- overrides
class OverrideIn(BaseModel):
    device: str
    action: CommandAction
    value: float | str | None = None
    duration_min: float | None = Field(60, gt=0, le=7 * 24 * 60)   # null = until ended manually


@router.get("/overrides", tags=["control"])
async def list_overrides(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    return [{"device": o.command.device_id, "action": o.command.action.value, "value": o.command.value,
             "group": o.command.group_key[1], "created": o.created.isoformat(),
             "expires": None if o.expires is None else o.expires.isoformat(), "user": o.user,
             "description": o.command.describe_nl()} for o in rt.engine.overrides.active()]


@router.post("/overrides", tags=["control"])
async def create_override(body: OverrideIn, p: Principal = Depends(operator), rt: EMSRuntime = Depends(get_runtime)):
    try:
        rt.config.device(body.device)
    except KeyError:
        raise HTTPException(404, "apparaat niet gevonden") from None
    chk = rt.engine.check(Command(body.device, body.action, body.value), p)
    if not chk.allowed:
        raise HTTPException(422, "; ".join(chk.reasons) or "opdracht niet toegestaan")
    value = chk.value
    ov = rt.engine.overrides.set(Command(body.device, body.action, value), body.duration_min, user=p.username)
    rt.optimizer.request("handmatige bediening")
    return {"device": body.device, "description": ov.command.describe_nl(),
            "expires": None if ov.expires is None else ov.expires.isoformat(),
            "executes": chk.executes, "notes": chk.reasons, "control_state": chk.state}


@router.delete("/overrides/{device}", tags=["control"])
async def clear_override(device: str, group: str | None = None, _: Principal = Depends(operator),
                         rt: EMSRuntime = Depends(get_runtime)) -> dict:
    n = rt.engine.overrides.clear(device, group)
    if n:
        try:
            await rt.devices.driver(device).release_control()
        except Exception:
            pass
        rt.engine.gate.reset(device)
        rt.optimizer.request("handmatige bediening beëindigd")
    return {"cleared": n}


# --------------------------------------------------------------- decisions
@router.get("/decisions", tags=["logs"])
async def decisions(limit: int = Query(100, le=1000), device: str | None = None, _: Principal = Depends(viewer),
                    rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    rows = await asyncio.to_thread(rt.db.recent_decisions, limit, device)
    for r in rows:
        r["timestamp"] = datetime.fromtimestamp(r["ts"], UTC).isoformat()
    return rows


# ----------------------------------------------------------- notifications
@router.get("/notifications", tags=["notifications"])
async def notifications(unacknowledged: bool = False, limit: int = Query(100, le=500), _: Principal = Depends(viewer),
                        rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    rows = await asyncio.to_thread(rt.db.list_notifications, limit, unacknowledged)
    for r in rows:
        r["timestamp"] = datetime.fromtimestamp(r["ts"], UTC).isoformat()
    return rows


@router.post("/notifications/{notification_id}/ack", tags=["notifications"])
async def ack(notification_id: int, _: Principal = Depends(operator), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return {"acknowledged": await asyncio.to_thread(rt.db.acknowledge_notification, notification_id)}


@router.post("/notifications/ack-all", tags=["notifications"])
async def ack_all(_: Principal = Depends(operator), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return {"acknowledged": await asyncio.to_thread(rt.db.acknowledge_notification, None)}


# ----------------------------------------------------------------- history
def _range(rt: EMSRuntime, start: datetime | None, end: datetime | None, hours: float) -> tuple[float, float]:
    end = end or rt.now()
    start = start or end - timedelta(hours=hours)
    if end <= start:
        raise HTTPException(422, "eindtijd moet na begintijd liggen")
    if (end - start) > timedelta(days=400):
        raise HTTPException(422, "maximaal 400 dagen per opvraging")
    return start.timestamp(), end.timestamp()


@router.get("/history", tags=["history"])
async def history(start: datetime | None = None, end: datetime | None = None, hours: float = Query(24, le=24 * 400),
                  resolution: Literal["raw", "15m"] = "15m", _: Principal = Depends(viewer),
                  rt: EMSRuntime = Depends(get_runtime)) -> dict:
    a, b = _range(rt, start, end, hours)
    if resolution == "raw":
        if b - a > 3 * 86400:
            raise HTTPException(422, "ruwe data maximaal 3 dagen per opvraging; gebruik 15m")
        rows = await asyncio.to_thread(rt.db.samples_between, rt.config.site.id, a, b)
        rows = [{k: v for k, v in r.items() if k not in ("id", "site_id")} for r in rows]
    else:
        rows = await asyncio.to_thread(rt.db.slots_between, rt.config.site.id, a, b)
        rows = [{k: v for k, v in r.items() if k != "site_id"} for r in rows]
    return {"resolution": resolution, "rows": rows}


@router.get("/history/export", tags=["history"])
async def history_export(start: datetime | None = None, end: datetime | None = None,
                         hours: float = Query(24 * 7, le=24 * 400), fmt: Literal["csv", "json", "excel"] = "csv",
                         resolution: Literal["raw", "15m"] = "15m", _: Principal = Depends(viewer),
                         rt: EMSRuntime = Depends(get_runtime)) -> Response:
    data = await history(start, end, hours, resolution, _, rt)
    rows = data["rows"]
    tz = ZoneInfo(rt.config.site.timezone)
    for r in rows:
        ts = r.get("ts", r.get("slot_ts"))
        r["local_time"] = datetime.fromtimestamp(ts, UTC).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
    name = f"energy-manager-{resolution}-{datetime.now(UTC):%Y%m%d}"
    if fmt == "json":
        return Response(json.dumps(rows, ensure_ascii=False), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    buf = io.StringIO()
    if rows:
        fields = ["local_time"] + [k for k in rows[0] if k != "local_time"]
        if fmt == "excel":  # Dutch Excel: semicolon separator, decimal comma, UTF-8 BOM
            w = csv.writer(buf, delimiter=";")
            w.writerow(fields)
            for r in rows:
                w.writerow([("" if r.get(f) is None else str(r[f]).replace(".", ",") if isinstance(r.get(f), float)
                             else r[f]) for f in fields])
        else:
            w = csv.DictWriter(buf, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    body = ("﻿" if fmt == "excel" else "") + buf.getvalue()
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


# ----------------------------------------------------------------- finance
@router.get("/finance/summary", tags=["finance"])
async def finance(period: str = Query("today", pattern=r"^(today|month|year|\d{1,3}d)$"),
                  _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    tz = ZoneInfo(rt.config.site.timezone)
    try:
        start, end = day_bounds(rt.now(), tz, period)
    except ValueError as exc:
        raise _err(exc) from exc
    rows = await asyncio.to_thread(rt.db.slots_between, rt.config.site.id, start.timestamp(), end.timestamp())
    days = max(0.0, (min(end, rt.now()) - start).total_seconds() / 86400)    # elapsed part of the period
    out = finance_summary(rows, rt.config, rt.tariff.fixed_costs_per_day(), days)
    caps = [float(d.params["capacity_kwh"]) for d in rt.config.devices if d.enabled and d.params.get("capacity_kwh")]
    if out.get("available") and caps:
        e = out["energy"]
        out["battery_trading"]["equivalent_full_cycles"] = round(
            (e["battery_charge_kwh"] + e["battery_discharge_kwh"]) / 2 / sum(caps), 2)
    return {"period": period, "start": start.isoformat(), "end": end.isoformat(), **out}


@router.get("/health", tags=["system"])
async def health(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return await asyncio.to_thread(rt.health)


@router.get("/installation", tags=["system"])
async def installation(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    """"Mijn installatie": every part of the site with ONLINE / CONFIGURED / NOT_CONFIGURED / ERROR."""
    return await asyncio.to_thread(rt.installation)


@router.get("/settlement/compare", tags=["finance"])
async def settlement_compare(period: str = Query("30d", pattern=r"^(month|year|\d{1,3}d)$"),
                             _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    """Same measured history under different settlement rules (e.g. NL 2026 with netting vs 2027 without)."""
    tz = ZoneInfo(rt.config.site.timezone)
    start, end = day_bounds(rt.now(), tz, period)
    rows = await asyncio.to_thread(rt.db.slots_between, rt.config.site.id, start.timestamp(), end.timestamp())
    days = max(1e-6, (min(end, rt.now()) - start).total_seconds() / 86400)
    out = compare(rows, rt.config.tariff, rt.config.site.timezone, rt.tariff.fixed_costs_per_day(), days)
    current = rules_for(rt.config.tariff.tax_country, rt.now().astimezone(tz).date())
    return {"period": period, "start": start.isoformat(), "end": end.isoformat(),
            "current_rules": current.id if current else None, **out}


@router.get("/settlement/estimate", tags=["finance"])
async def settlement_estimate(period: str = Query("30d", pattern=r"^(month|year|\d{1,3}d)$"),
                              _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    """Estimated bill for the measured period with the rules and tax rates valid on each date
    (split at a year boundary). An estimate, not the supplier's invoice."""
    tz = ZoneInfo(rt.config.site.timezone)
    start, end = day_bounds(rt.now(), tz, period)
    rows = await asyncio.to_thread(rt.db.slots_between, rt.config.site.id, start.timestamp(), end.timestamp())
    days = max(1e-6, (min(end, rt.now()) - start).total_seconds() / 86400)
    out = settle(rows, rt.config.tariff, None, rt.config.site.timezone, rt.tariff.fixed_costs_per_day(), days)
    return {"period": period, "start": start.isoformat(), "end": end.isoformat(), **out}


@router.get("/tariff/taxes", tags=["tariff"])
async def tax_table(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return {"mode": rt.config.tariff.energy_tax_mode, "country": rt.config.tariff.tax_country,
            "current": rt.tariff.energy_tax(rt.now()),
            "table": [{"country": r.country, "year": r.year, "energy_tax_eur_kwh": r.energy_tax_eur_kwh,
                       "source": r.source, "verify": r.verify,
                       "brackets": [{"upto_kwh": b.upto_kwh, "rate_eur_kwh": b.rate_eur_kwh, "verify": b.verify}
                                    for b in r.bracket_table()]} for r in TAX_TABLE.values()]}


def safe_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)[:64]
