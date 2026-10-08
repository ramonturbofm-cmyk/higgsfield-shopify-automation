"""Devices, drivers, primary grid meter, commissioning and the HomeWizard pairing flow."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ems.api.deps import get_runtime, installer, viewer
from ems.control.authority import EXECUTING, STATE_LABELS
from ems.core.config import ConfigError, DeviceConfig
from ems.core.models import CAPABILITY_LABELS_NL, Command, CommandAction, DeviceCategory
from ems.devices.base import DriverContext
from ems.devices.capabilities import TYPE_SCHEMAS, actions_for, capability_view, missing_control_params, schema_view
from ems.integrations.homewizard import client as hw
from ems.integrations.homewizard.discovery import discover
from ems.security.auth import Principal
from ems.server.runtime import EMSRuntime

router = APIRouter(prefix="/api/v1")

LEVELS = ["connection_test", "read_only", "shadow", "limited", "full"]
LEVEL_LABELS = {"connection_test": "1. Verbindingstest", "read_only": "2. Alleen lezen", "shadow": "3. Schaduwmodus",
                "limited": "4. Beperkte regeling", "full": "5. Volledige regeling"}


def _slug(text: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "apparaat"
    slug, n = base, 2
    while slug in existing:
        slug, n = f"{base}_{n}", n + 1
    return slug


def _manifest_dict(cls) -> dict:
    m = cls.manifest
    return {"driver_id": m.driver_id, "name": m.display_name, "vendor": m.vendor,
            "categories": [c.value for c in m.categories],
            "capabilities": [{"id": c.value, "label": CAPABILITY_LABELS_NL.get(c, c.value)} for c in
                             sorted(m.capabilities, key=lambda c: c.value)],
            "connection_types": list(m.connection_types), "models": list(m.models), "simulated": m.simulated,
            "verified": m.verified, "documentation": m.documentation, "connection_schema": m.connection_schema,
            "grid_meter_kind": m.grid_meter_kind, "write_capable": m.write_capable, "notes": m.notes}


@router.get("/drivers", tags=["devices"])
async def drivers(category: DeviceCategory | None = None, _: Principal = Depends(viewer),
                  rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    out = []
    for cls in rt.registry.list(category):
        d = _manifest_dict(cls)
        # node.remote devices are added via Systeem -> Nodes, not via the device wizard
        d["available_in_mode"] = ((not cls.manifest.simulated) or rt.demo) and cls.manifest.driver_id != "node.remote"
        out.append(d)
    return out


@router.get("/device-categories", tags=["devices"])
async def categories(_: Principal = Depends(viewer)) -> list[dict]:
    return [{"id": c.value, "label": TYPE_SCHEMAS[c].label} for c in DeviceCategory]


@router.get("/device-schemas", tags=["devices"])
async def device_schemas(_: Principal = Depends(viewer)) -> list[dict]:
    """Type-bound capability and parameter schemas (single source for UI forms and validation)."""
    return [schema_view(c) for c in DeviceCategory]


@router.get("/device-schemas/{category}", tags=["devices"])
async def device_schema(category: DeviceCategory, _: Principal = Depends(viewer)) -> dict:
    return schema_view(category)


def _device_view(rt: EMSRuntime, cfg: DeviceConfig) -> dict:
    snap = rt.engine.last_snapshot
    st = None if snap is None else snap.devices.get(cfg.id)
    md = rt.devices.devices.get(cfg.id)
    drv = None if md is None else md.driver
    sel = rt.engine.grid_selection
    data = cfg.model_dump(mode="json")
    data["connection"] = {k: ("********" if _is_secret_key(k) and v else v)
                          for k, v in data["connection"].items()}
    data.update({
        "status": "disabled" if not cfg.enabled else (st.status.value if st else "unknown"),
        "error": None if st is None else st.error,
        "values": {} if st is None else {str(k): v for k, v in st.values.items()},
        "health": rt.devices.health(cfg.id, rt.now()) if md else None,
        "diagnostics": drv.diagnostics() if drv is not None and hasattr(drv, "diagnostics") else None,
        "driver_info": None if drv is None else _manifest_dict(type(drv)),
        "is_primary_grid_meter": sel.device_id == cfg.id,
        "primary_grid_meter_status": sel.status.value if sel.device_id == cfg.id else None,
        "last_decision": rt.engine.last_decisions.get(cfg.id),
        "capabilities": [] if drv is None else capability_view(drv.device_capabilities()),
        "missing_control_params": missing_control_params(cfg.category, cfg.params),
    })
    data.update(_control_view(rt, cfg.id))
    return data


def _control_view(rt: EMSRuntime, device_id: str) -> dict:
    """Control state plus "EMS zou doen" (decision) versus "EMS doet nu" (sent and confirmed)."""
    state = rt.engine.control_state(device_id)
    executions = rt.engine.executions.for_device(device_id)
    doing = [x for x in executions if x["status"] in ("sent", "confirmed", "unconfirmed", "no_feedback")]
    return {"control_state": state.value, "control_state_label": STATE_LABELS[state],
            "ems_would_do": rt.engine.last_decisions.get(device_id),
            "ems_does_now": doing if state in EXECUTING else [],
            "executions": executions}


@router.get("/devices/{device_id}/actions", tags=["control"])
async def device_actions(device_id: str, p: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)):
    """Exactly the actions this device supports, with value ranges from its own limits and, per
    action, whether this user may run it now and whether it would really be executed."""
    cfg = _get(rt, device_id)
    md = rt.devices.devices.get(device_id)
    caps = frozenset() if md is None or md.driver is None else md.driver.device_capabilities()
    out = []
    for spec in actions_for(cfg.category, caps, cfg.params, rt.config):
        probe = spec.value.get("min") if spec.value and spec.value.get("type") == "number" else (
            spec.value["options"][0] if spec.value and spec.value.get("type") == "enum" else None)
        if spec.value and spec.value.get("type") == "number" and spec.value.get("max") is None:
            probe = 0 if spec.value.get("zero_allowed") else probe
        chk = rt.engine.check(Command(device_id, CommandAction(spec.action), probe), p)
        d = spec.to_dict()
        d["executes"] = chk.executes
        d["reasons"] = list(chk.reasons)
        if spec.value and spec.value.get("type") == "number" and spec.value.get("max") is None \
                and spec.value.get("unit") in ("W", "A"):
            d["available"] = False
            d["reasons"].append("apparaatlimiet niet ingesteld — stel eerst het maximum in bij het apparaat")
        elif not chk.allowed:
            d["available"] = False
        out.append(d)
    return {"device_id": device_id, **_control_view(rt, device_id), "actions": out}


@router.get("/devices", tags=["devices"])
async def list_devices(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    return [_device_view(rt, d) for d in rt.config.devices]


def _get(rt: EMSRuntime, device_id: str) -> DeviceConfig:
    try:
        return rt.config.device(device_id)
    except KeyError:
        raise HTTPException(404, "apparaat niet gevonden") from None


@router.get("/devices/{device_id}", tags=["devices"])
async def get_device(device_id: str, _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return _device_view(rt, _get(rt, device_id))


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    category: DeviceCategory
    driver: str
    id: str | None = Field(None, pattern=r"^[a-z0-9_-]{1,64}$")
    phase: Literal["L1", "L2", "L3", "3P"] = "3P"
    connection: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    primary_grid_meter: bool = False


def _check_driver(rt: EMSRuntime, driver: str, category: DeviceCategory):
    try:
        cls = rt.registry.get(driver)
    except KeyError:
        raise HTTPException(422, f"onbekende driver {driver!r}") from None
    if category not in cls.manifest.categories:
        raise HTTPException(422, f"driver {driver} ondersteunt categorie {category.value} niet")
    if cls.manifest.simulated and not rt.demo:
        raise HTTPException(422, "gesimuleerde apparaten zijn alleen beschikbaar in Demo Mode")
    return cls


def _is_secret_key(key: str) -> bool:
    return key != "token_ref" and ("password" in key or "token" in key)


def _store_connection_secrets(rt: EMSRuntime, dev_id: str, conn: dict[str, Any]) -> dict[str, Any]:
    """Typed passwords/tokens go to the encrypted SecretStore; the YAML only keeps a reference.
    ``${VAR}`` references (.env) and existing ``secret:`` references are kept as they are."""
    out = dict(conn)
    for k, v in conn.items():
        if _is_secret_key(k) and isinstance(v, str) and v and v != "********" \
                and not v.startswith(("${", "secret:")):
            ref = f"device.{dev_id}.{k}"
            rt.secrets.set(ref, v)
            out[k] = f"secret:{ref}"
    return out


@router.post("/devices", tags=["devices"])
async def add_device(body: DeviceIn, p: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    cls = _check_driver(rt, body.driver, body.category)
    data = rt.config.model_dump(mode="json")
    existing = {d["id"] for d in data["devices"]}
    dev_id = body.id or _slug(body.name, existing)
    if dev_id in existing:
        raise HTTPException(409, f"apparaat-id {dev_id!r} bestaat al")
    dev = {"id": dev_id, "name": body.name, "category": body.category.value, "driver": body.driver,
           "phase": body.phase, "connection": _store_connection_secrets(rt, dev_id, body.connection),
           "params": body.params,
           "control_level": "full" if cls.manifest.simulated else "read_only"}
    if body.primary_grid_meter:
        for d in data["devices"]:
            if d.get("role") in ("primary_grid_meter", "grid_reference"):
                d["role"] = None
        dev["role"] = "primary_grid_meter"
    data["devices"].append(dev)
    try:
        await rt.reload(data, p.username, f"apparaat {dev_id} toegevoegd")
    except (ConfigError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return _device_view(rt, rt.config.device(dev_id))


class DevicePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    enabled: bool | None = None
    phase: Literal["L1", "L2", "L3", "3P"] | None = None
    connection: dict[str, Any] | None = None
    params: dict[str, Any] | None = None


@router.put("/devices/{device_id}", tags=["devices"])
async def update_device(device_id: str, body: DevicePatch, p: Principal = Depends(installer),
                        rt: EMSRuntime = Depends(get_runtime)) -> dict:
    _get(rt, device_id)
    data = rt.config.model_dump(mode="json")
    for d in data["devices"]:
        if d["id"] == device_id:
            for k, v in body.model_dump(exclude_none=True).items():
                if k in ("connection", "params"):
                    merged = {**d[k], **v}
                    d[k] = {kk: vv for kk, vv in merged.items() if vv is not None}
                    if k == "connection":   # masked secrets are never written back
                        for kk, vv in v.items():
                            if vv == "********":
                                d[k][kk] = rt.config.device(device_id).connection.get(kk)
                        d[k] = _store_connection_secrets(rt, device_id, d[k])
                else:
                    d[k] = v
    try:
        await rt.reload(data, p.username, f"apparaat {device_id} gewijzigd")
    except (ConfigError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return _device_view(rt, rt.config.device(device_id))


@router.delete("/devices/{device_id}", tags=["devices"])
async def delete_device(device_id: str, p: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)):
    cfg = _get(rt, device_id)
    data = rt.config.model_dump(mode="json")
    data["devices"] = [d for d in data["devices"] if d["id"] != device_id]
    await rt.reload(data, p.username, f"apparaat {device_id} verwijderd")
    for v in cfg.connection.values():
        if isinstance(v, str) and v.startswith("secret:device."):
            rt.secrets.delete(v.removeprefix("secret:"))
    ref = cfg.connection.get("token_ref")
    if ref and not any(d.connection.get("token_ref") == ref for d in rt.config.devices):
        rt.secrets.delete(ref)
    return {"ok": True}


class DeviceTest(BaseModel):
    driver: str
    category: DeviceCategory
    name: str = "test"
    phase: Literal["L1", "L2", "L3", "3P"] = "3P"
    connection: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


async def _run_test(rt: EMSRuntime, cfg: DeviceConfig) -> dict:
    cls = rt.registry.get(cfg.driver)
    drv = cls(cfg, DriverContext(rt.clock, simulator=rt.site, secrets=rt.secrets))  # type: ignore[arg-type]
    try:
        report = await asyncio.wait_for(drv.self_test(), 25)
    except TimeoutError:
        return {"reachable": False, "checks": [{"key": "reachable", "label": "apparaat bereikbaar", "ok": False,
                                                "detail": "timeout"}], "text": "✗ apparaat bereikbaar — timeout"}
    finally:
        try:
            await drv.disconnect()
        except Exception:
            pass
    return {"reachable": report.reachable, "checks": [c.__dict__ for c in report.checks], "sample": report.sample,
            "available": report.available, "unavailable": report.unavailable, "text": report.render(),
            "tested_at": datetime.now(UTC).isoformat()}


@router.post("/devices/test", tags=["devices"])
async def test_new_device(body: DeviceTest, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)):
    _check_driver(rt, body.driver, body.category)
    cfg = DeviceConfig(id="test_device", name=body.name, category=body.category, driver=body.driver,
                       phase=body.phase, connection=body.connection, params=body.params)
    return await _run_test(rt, cfg)


@router.post("/devices/{device_id}/test", tags=["devices"])
async def test_device(device_id: str, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)):
    cfg = _get(rt, device_id)
    if rt.devices.devices[device_id].driver is not None and rt.devices.devices[device_id].connected:
        # Already connected: test through the live driver (no second connection to the device).
        report = await rt.devices.driver(device_id).self_test()
        result = {"reachable": report.reachable, "checks": [c.__dict__ for c in report.checks],
                  "sample": report.sample, "text": report.render(), "tested_at": datetime.now(UTC).isoformat()}
    else:
        result = await _run_test(rt, cfg)
    await asyncio.to_thread(rt.db.kv_set, f"commissioning.{device_id}.test", result)
    return result


@router.get("/devices/{device_id}/health", tags=["devices"])
async def device_health(device_id: str, hours: float = Query(24, le=24 * 14), _: Principal = Depends(viewer),
                        rt: EMSRuntime = Depends(get_runtime)) -> dict:
    _get(rt, device_id)
    now = rt.now()
    rows = await asyncio.to_thread(rt.db.device_samples_between, device_id,
                                   (now - timedelta(hours=hours)).timestamp(), now.timestamp())
    online = sum(1 for r in rows if r["status"] == "online")
    view = _device_view(rt, rt.config.device(device_id))
    return {"device_id": device_id, "status": view["status"], "error": view["error"], "health": view["health"],
            "diagnostics": view["diagnostics"], "availability_pct": round(100 * online / len(rows), 1) if rows else None,
            "samples": len(rows), "status_timeline": [{"ts": r["ts"], "status": r["status"]} for r in rows[-500:]]}


@router.get("/devices/{device_id}/history", tags=["devices"])
async def device_history(device_id: str, hours: float = Query(24, le=24 * 14), _: Principal = Depends(viewer),
                         rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    _get(rt, device_id)
    now = rt.now()
    return await asyncio.to_thread(rt.db.device_samples_between, device_id,
                                   (now - timedelta(hours=hours)).timestamp(), now.timestamp())


@router.post("/devices/{device_id}/identify", tags=["devices"])
async def identify(device_id: str, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    _get(rt, device_id)
    drv = rt.devices.devices[device_id].driver
    if drv is None or not hasattr(drv, "identify"):
        raise HTTPException(422, "dit apparaat kan zich niet identificeren")
    try:
        await drv.identify()
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True}


# ----------------------------------------------------------- grid meter
@router.get("/gridmeter", tags=["devices"])
async def gridmeter(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    live = rt.live()
    sel = rt.engine.grid_selection
    return {**sel.to_dict(), "live": live["grid_meter"], "flows": live["flows"], "features": live["features"],
            "message": None if sel.device_id else "Geen primaire netmeter ingesteld. Sommige EMS-functies zijn beperkt."}


@router.post("/devices/{device_id}/primary-grid-meter", tags=["devices"])
async def set_primary(device_id: str, p: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)):
    cfg = _get(rt, device_id)
    cls = rt.registry.get(cfg.driver)
    if cls.manifest.grid_meter_kind is None:
        raise HTTPException(422, "dit apparaat kan geen netmeting leveren")
    data = rt.config.model_dump(mode="json")
    for d in data["devices"]:
        d["role"] = "primary_grid_meter" if d["id"] == device_id else (
            None if d.get("role") in ("primary_grid_meter", "grid_reference") else d.get("role"))
    await rt.reload(data, p.username, f"{device_id} ingesteld als primaire netmeter")
    return rt.engine.grid_selection.to_dict()


@router.delete("/devices/{device_id}/primary-grid-meter", tags=["devices"])
async def unset_primary(device_id: str, p: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)):
    _get(rt, device_id)
    data = rt.config.model_dump(mode="json")
    for d in data["devices"]:
        if d["id"] == device_id and d.get("role") in ("primary_grid_meter", "grid_reference"):
            d["role"] = None
    await rt.reload(data, p.username, f"{device_id} niet langer primaire netmeter")
    return rt.engine.grid_selection.to_dict()


# --------------------------------------------------------- commissioning
def _commissioning_view(rt: EMSRuntime, device_id: str, test: dict | None) -> dict:
    cfg = rt.config.device(device_id)
    md = rt.devices.devices[device_id]
    manifest = None if md.driver is None else md.driver.manifest
    controls = [] if md.driver is None else [c.value for c in md.driver.device_capabilities() if c.value.startswith("control_")]
    allowed = {}
    for lvl in LEVELS:
        ok, why = True, ""
        if lvl != "connection_test" and not (manifest and manifest.simulated) and not (test and test.get("reachable")):
            ok, why = False, "eerst een geslaagde verbindingstest"
        if lvl in ("shadow", "limited", "full") and not controls:
            ok, why = False, "dit apparaat is niet bestuurbaar (alleen meten)"
        remote = cfg.driver == "node.remote"
        writes = (md.driver is not None and md.driver.write_capable) if remote else \
            bool(manifest and (manifest.write_capable or manifest.simulated))
        if lvl in ("limited", "full") and manifest and not writes:
            ok, why = False, "de driver ondersteunt nog geen schrijfopdrachten voor dit apparaat"
        allowed[lvl] = {"label": LEVEL_LABELS[lvl], "allowed": ok, "reason": why}
    snap = rt.engine.last_snapshot
    st = None if snap is None else snap.devices.get(device_id)
    return {"device_id": device_id, "name": cfg.name, "level": cfg.control_level,
            "limited_fraction": cfg.limited_fraction, "levels": allowed, "control_capabilities": controls,
            "last_test": test, "actual": {} if st is None else {str(k): v for k, v in st.values.items()},
            "ems_would_do": rt.engine.last_decisions.get(device_id)}


@router.get("/devices/{device_id}/commissioning", tags=["commissioning"])
async def get_commissioning(device_id: str, _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)):
    _get(rt, device_id)
    test = await asyncio.to_thread(rt.db.kv_get, f"commissioning.{device_id}.test")
    return _commissioning_view(rt, device_id, test)


class CommissioningIn(BaseModel):
    level: Literal["connection_test", "read_only", "shadow", "limited", "full"]
    limited_fraction: float | None = Field(None, gt=0, le=1)
    confirm: bool = False


@router.put("/devices/{device_id}/commissioning", tags=["commissioning"])
async def set_commissioning(device_id: str, body: CommissioningIn, p: Principal = Depends(installer),
                            rt: EMSRuntime = Depends(get_runtime)) -> dict:
    _get(rt, device_id)
    test = await asyncio.to_thread(rt.db.kv_get, f"commissioning.{device_id}.test")
    view = _commissioning_view(rt, device_id, test)
    info = view["levels"][body.level]
    if not info["allowed"]:
        raise HTTPException(409, f"{info['label']} niet mogelijk: {info['reason']}")
    if body.level == "full" and not body.confirm:
        raise HTTPException(409, "bevestig volledige regeling expliciet (confirm=true)")
    cfg = rt.config.device(device_id)
    if cfg.driver == "node.remote":   # the owner node enforces the level as well
        link = rt.nodes.links.get(cfg.connection.get("node_id"))
        if link is None:
            raise HTTPException(409, "eigenaar-node niet gekoppeld")
        try:
            await link.set_control_level(cfg.connection.get("remote_id"), body.level,
                                         body.limited_fraction or cfg.limited_fraction)
        except Exception as exc:
            raise HTTPException(502, f"node {link.name}: {exc}") from exc
    data = rt.config.model_dump(mode="json")
    for d in data["devices"]:
        if d["id"] == device_id:
            d["control_level"] = body.level
            if body.limited_fraction is not None:
                d["limited_fraction"] = body.limited_fraction
    await rt.reload(data, p.username, f"inbedrijfstelling {device_id}: {body.level}")
    return _commissioning_view(rt, device_id, test)


# -------------------------------------------------------------- HomeWizard
class HostIn(BaseModel):
    host: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9.:\-\[\]]+$")
    serial: str | None = Field(None, pattern=r"^[0-9a-fA-F]{12}$")
    api: Literal["v2", "v1"] = "v2"


@router.post("/integrations/homewizard/discover", tags=["homewizard"])
async def hw_discover(timeout: float = Query(3.0, ge=1, le=10), _: Principal = Depends(installer)) -> dict:
    try:
        return {"devices": await discover(timeout)}
    except Exception as exc:
        return {"devices": [], "error": f"automatisch zoeken mislukt: {exc}. Voer het IP-adres handmatig in."}


@router.post("/integrations/homewizard/identity", tags=["homewizard"])
async def hw_identity(body: HostIn, _: Principal = Depends(installer)) -> dict:
    if body.api == "v1":
        c = hw.HomeWizardV1Client(body.host)
        try:
            info = await c.device_info()
        except hw.HomeWizardError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            await c.close()
        return {"api": "v1", "serial": info.get("serial"), "product_type": info.get("product_type"),
                "firmware_version": info.get("firmware_version")}
    try:
        ident = await hw.probe_identity(body.host)
    except hw.HomeWizardError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"api": "v2", **ident}


@router.post("/integrations/homewizard/pair", tags=["homewizard"])
async def hw_pair(body: HostIn, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    """One pairing attempt (v2). Call repeatedly while the user presses the button
    (the docs allow repeated requests; the button opens a 30 s window)."""
    if body.api == "v1":
        return {"status": "paired", "api": "v1", "note": "API v1 heeft geen koppeling nodig; zet 'Local API' aan "
                                                          "in de HomeWizard-app."}
    serial = body.serial
    if serial is None:
        try:
            serial = (await hw.probe_identity(body.host))["serial"]
        except hw.HomeWizardError as exc:
            raise HTTPException(502, str(exc)) from exc
    c = hw.HomeWizardV2Client(body.host, serial)
    try:
        token = await c.create_user()
        info = await c.device_info()
    except hw.ButtonPressRequired:
        return {"status": "press_button", "serial": serial,
                "message": "Druk nu op de knop van de HomeWizard P1 Meter (u heeft daarna 30 seconden)."}
    except hw.HomeWizardError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        await c.close()
    ref = f"homewizard:{serial.lower()}"
    rt.secrets.set(ref, token)
    return {"status": "paired", "api": "v2", "serial": serial.lower(), "token_ref": ref,
            "product_type": info.get("product_type"), "firmware_version": info.get("firmware_version")}


class HomeWizardAdd(HostIn):
    name: str = "HomeWizard P1 Meter"
    primary_grid_meter: bool = True


@router.post("/integrations/homewizard/add", tags=["homewizard"])
async def hw_add(body: HomeWizardAdd, p: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)):
    conn: dict[str, Any] = {"host": body.host, "api": body.api}
    if body.api == "v2":
        if not body.serial:
            raise HTTPException(422, "serienummer ontbreekt (eerst koppelen)")
        ref = f"homewizard:{body.serial.lower()}"
        if not rt.secrets.get(ref):
            raise HTTPException(409, "nog niet gekoppeld: gebruik eerst 'Koppelen'")
        conn.update({"serial": body.serial.lower(), "token_ref": ref})
    existing = [d for d in rt.config.devices if d.driver == "homewizard.p1" and d.connection.get("host") == body.host]
    if existing:
        raise HTTPException(409, f"deze P1 Meter is al toegevoegd als {existing[0].name}")
    has_primary = rt.engine.grid_selection.status.value == "primary_explicit"
    result = await add_device(DeviceIn(name=body.name, category=DeviceCategory.SMART_METER, driver="homewizard.p1",
                                       connection=conn, primary_grid_meter=body.primary_grid_meter or not has_primary),
                              p, rt)
    rt.optimizer.request("nieuwe netmeter")
    return result


def _ts() -> float:
    return time.time()
