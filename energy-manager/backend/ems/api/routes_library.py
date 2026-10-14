"""Device library, discovery, connection probe with capability report, Home Assistant helper and the
Testomgeving (virtual devices) API."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ems.api.deps import get_runtime, installer, viewer
from ems.api.routes_devices import DeviceTest, _check_driver, _get, _phase
from ems.core.config import ConfigError, DeviceConfig
from ems.core.models import DeviceCategory
from ems.devices import capreport
from ems.devices.base import DriverContext
from ems.devices.capabilities import TYPE_SCHEMAS
from ems.devices.discovery import scan
from ems.devices.library.catalog import ProfileError, catalog
from ems.security.auth import Principal
from ems.server.runtime import EMSRuntime
from ems.simulator.lab import PROFILE_KEY

router = APIRouter(prefix="/api/v1")

WIZARD_CATEGORIES = [  # (category, label, icon) in the order of the wizard's first screen
    ("smart_meter", "Slimme meter / P1", "⚡"), ("energy_meter", "Energiemeter", "📟"),
    ("pv_inverter", "Zonnepanelen / omvormer", "☀"), ("hybrid_inverter", "Hybride omvormer", "🔋"),
    ("battery", "Thuisbatterij", "🔋"), ("heat_pump", "Warmtepomp", "🌡"), ("heat_pump_boiler", "Warmtepompboiler", "🚿"),
    ("boiler", "Elektrische boiler", "🚿"), ("ev_charger", "Laadpaal", "🚗"), ("ev", "Elektrische auto", "🚙"),
    ("airco", "Airco", "❄"), ("ventilation", "Ventilatie", "🌀"), ("smart_plug", "Slimme stekker", "🔌"),
    ("smart_relay", "Slim relais", "🔀"), ("flexible_load", "Andere flexibele verbruiker", "⚙"),
]


def _cat(rt: EMSRuntime):
    return catalog(rt.data_dir / "profiles")


# ------------------------------------------------------------------ library
@router.get("/library/categories", tags=["library"])
async def library_categories(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    cat = _cat(rt)
    out = []
    for cid, label, icon in WIZARD_CATEGORIES:
        brands = cat.brands(cid, include_simulator=rt.lab_mode)
        supported = sum(1 for b in brands for m in b["models"] if m["support"] != "not_supported")
        out.append({"id": cid, "label": label, "icon": icon, "brands": len(brands), "supported_models": supported,
                    "phase_relevant": TYPE_SCHEMAS[DeviceCategory(cid)].phase_relevant})
    return out


@router.get("/library/brands", tags=["library"])
async def library_brands(category: DeviceCategory, _: Principal = Depends(viewer),
                         rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    brands = _cat(rt).brands(category.value, include_simulator=rt.lab_mode)
    if rt.lab_mode and rt.lab is not None:
        for b in brands:
            for m in b["models"]:
                key = m.get("lab")
                key = key or PROFILE_KEY.get(m.get("profile_id") or "")
                port = rt.lab.ports.get(key) if key else None
                if port:
                    host = f"127.0.0.1:{port}" if key in ("sgready", "boiler") else "127.0.0.1"
                    m["lab_connection"] = {"host": host, "port": port}
    return brands


@router.get("/library/routes", tags=["library"])
async def library_routes(category: DeviceCategory, _: Principal = Depends(viewer),
                         rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    """Connection routes that are not a brand: SunSpec standard, SG Ready relay, Shelly, Home Assistant."""
    routes = _cat(rt).routes(category.value)
    if rt.lab_mode and rt.lab is not None and "homeassistant" in rt.lab.ports:
        from ems.simulator.lab import HA_TOKEN
        for r in routes:   # the Testomgeving has a virtual Home Assistant: prefill it
            if r["id"] == "homeassistant":
                r["lab_connection"] = {"url": f"http://127.0.0.1:{rt.lab.ports['homeassistant']}", "token": HA_TOKEN}
    return routes


@router.get("/library/profiles", tags=["library"])
async def library_profiles(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    cat = _cat(rt)
    return {"profiles": [cat.summary(p) for p in cat.profiles.values()], "problems": cat.problems}


@router.get("/library/profiles/{profile_id}", tags=["library"])
async def library_profile(profile_id: str, _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    try:
        prof = _cat(rt).get(profile_id)
    except ProfileError as exc:
        raise HTTPException(404, str(exc)) from exc
    out = _cat(rt).summary(prof)
    out["points"] = [p.model_dump(mode="json", exclude_none=True) for p in prof.points]
    out["default_params"] = prof.default_params
    return out


def _recommission(rt: EMSRuntime, profile_id: str, previous, new) -> list[str]:
    """New write functions in an updated profile never unlock control: devices go back to shadow mode."""
    before = {c.action for c in (previous.writable_controls if previous else []) if c.writable}
    after = {c.action for c in new.writable_controls if c.writable}
    if not (after - before):
        return []
    hit = []
    for d in rt.config.devices:
        if d.connection.get("profile_id") == profile_id and d.control_level in ("limited", "full"):
            hit.append(d.id)
    return hit


@router.post("/library/profiles/import", tags=["library"])
async def import_profile(body: dict[str, Any], p: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    try:
        prof, previous = _cat(rt).import_profile(body)
    except ProfileError as exc:
        raise HTTPException(422, f"profiel ongeldig: {exc}") from exc
    downgraded = _recommission(rt, prof.profile_id, previous, prof)
    if downgraded:
        data = rt.config.model_dump(mode="json")
        for d in data["devices"]:
            if d["id"] in downgraded:
                d["control_level"] = "shadow"
        await rt.reload(data, p.username, f"profiel {prof.profile_id} bijgewerkt: opnieuw inbedrijfstellen")
    return {"profile": _cat(rt).summary(prof), "replaced": previous is not None, "recommission": downgraded}


@router.post("/library/profiles/{profile_id}/rollback", tags=["library"])
async def rollback_profile(profile_id: str, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    try:
        return _cat(rt).summary(_cat(rt).rollback(profile_id))
    except ProfileError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/library/profiles/{profile_id}", tags=["library"])
async def delete_profile(profile_id: str, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    if any(d.connection.get("profile_id") == profile_id for d in rt.config.devices):
        raise HTTPException(409, "profiel is in gebruik door een apparaat")
    try:
        _cat(rt).remove(profile_id)
    except ProfileError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


# ---------------------------------------------------------------- discovery
class ScanIn(BaseModel):
    hosts: list[str] = Field(default_factory=list, max_length=20)
    mdns: bool = True
    serial: bool = True


@router.post("/discovery/scan", tags=["library"])
async def discovery_scan(body: ScanIn, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    configured = [d.model_dump(mode="json") for d in rt.config.devices]
    return await scan(lab=rt.lab if rt.lab_mode else None, hosts=body.hosts, use_mdns=body.mdns, use_serial=body.serial,
                      configured=configured, timeout_s=2.5)


class AddressIn(BaseModel):
    host: str = Field(min_length=1, max_length=200)
    port: int | None = Field(None, ge=1, le=65535)


@router.put("/devices/{device_id}/address", tags=["library"])
async def update_address(device_id: str, body: AddressIn, p: Principal = Depends(installer),
                         rt: EMSRuntime = Depends(get_runtime)) -> dict:
    """The same device (same identity) got a new IP address: keep id, history and commissioning."""
    _get(rt, device_id)
    data = rt.config.model_dump(mode="json")
    for d in data["devices"]:
        if d["id"] == device_id:
            d["connection"]["host"] = body.host
            if body.port:
                d["connection"]["port"] = body.port
    await rt.reload(data, p.username, f"apparaat {device_id}: nieuw adres {body.host}")
    return {"ok": True}


# ------------------------------------------------------- probe + report
async def _probe(rt: EMSRuntime, cfg: DeviceConfig) -> dict:
    cls = rt.registry.get(cfg.driver)
    try:
        drv = cls(cfg, DriverContext(rt.clock, simulator=rt.site, secrets=rt.secrets))  # type: ignore[arg-type]
    except Exception as exc:
        return {"reachable": False, "checks": [{"key": "config", "label": "instellingen geldig", "ok": False,
                                                "detail": str(exc)}], "capability_report": []}
    try:
        report = await asyncio.wait_for(drv.self_test(), 25)
        ident = {}
        if report.reachable and hasattr(drv, "identify"):
            try:
                ident = await asyncio.wait_for(drv.identify(), 10)
            except Exception:
                ident = {}
        if not ident and getattr(drv, "identity", None):
            ident = dict(drv.identity)
        caps = capreport.report(drv, cfg.category, level="read_only",
                                sample=report.sample if report.reachable else None)
    except TimeoutError:
        return {"reachable": False, "checks": [{"key": "reachable", "label": "apparaat bereikbaar", "ok": False,
                                                "detail": "time-out"}], "capability_report": []}
    finally:
        try:
            await drv.disconnect()
        except Exception:
            pass
    return {"reachable": report.reachable, "checks": [c.__dict__ for c in report.checks], "sample": report.sample,
            "identity": {k: v for k, v in (ident or {}).items() if isinstance(v, (str, int, float))},
            "capability_report": caps, "tested_at": datetime.now(UTC).isoformat()}


@router.post("/devices/probe", tags=["library"])
async def probe_device(body: DeviceTest, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    """Connection test of a device that is not saved yet + what it can really do (capability report)."""
    _check_driver(rt, body.driver, body.category)
    try:
        cfg = DeviceConfig(id="probe_device", name=body.name, category=body.category, driver=body.driver,
                           phase=_phase(body.category, body.phase), connection=body.connection, params=body.params)
    except (ConfigError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return await _probe(rt, cfg)


@router.get("/devices/{device_id}/capability-report", tags=["library"])
async def device_capability_report(device_id: str, _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)):
    cfg = _get(rt, device_id)
    md = rt.devices.devices.get(device_id)
    snap = rt.engine.last_snapshot
    st = None if snap is None else snap.devices.get(device_id)
    sample = None if st is None else {str(k): v for k, v in st.values.items()}
    return {"device_id": device_id, "level": cfg.control_level, "identity": cfg.params.get("identity") or {},
            "rows": capreport.report(None if md is None else md.driver, cfg.category, level=cfg.control_level,
                                     sample=sample)}


@router.get("/library/serial-ports", tags=["library"])
async def serial_ports(_: Principal = Depends(installer)) -> list[dict]:
    from ems.devices.discovery import serial_ports as sp
    return [{"port": r["host"], "description": r["model"], "manufacturer": r["manufacturer"]} for r in sp()]


# ---------------------------------------------------------- Home Assistant
class HAIn(BaseModel):
    url: str = Field(max_length=300)
    token: str = Field(max_length=2000)
    category: DeviceCategory | None = None


def ha_slots(category: DeviceCategory) -> dict:
    """What the user can map for this kind of device: one entity per EMS value, one per control.
    Mode selection (hp_mode) needs an option mapping and stays an Expert setting."""
    from ems.core.models import ACTION_CAPABILITY, CAPABILITY_METRICS, METRIC_LABELS_NL, CommandAction
    from ems.devices.capabilities import CAPABILITY_LABELS_NL
    from ems.integrations.homeassistant.bridge import SERVICES
    caps = TYPE_SCHEMAS[category].capabilities
    values, seen = [], set()
    for cap in sorted(caps, key=lambda c: c.value):
        for metric in CAPABILITY_METRICS.get(cap, ()):
            if metric in seen:
                continue
            seen.add(metric)
            label, unit = METRIC_LABELS_NL.get(metric.value, (metric.value, ""))
            m = metric.value
            kind = ("power" if m.endswith("_w") else "energy" if m.endswith("_kwh") else
                    "soc" if m.endswith("_pct") else "temperature" if m.endswith("_c") else "other")
            values.append({"metric": m, "label": label, "unit": unit, "kind": kind})
    controls = []
    for action, cap in ACTION_CAPABILITY.items():
        if cap in caps and action in SERVICES and action != CommandAction.HP_MODE:
            controls.append({"action": action.value, "label": CAPABILITY_LABELS_NL.get(cap, cap.value),
                             "domains": sorted(SERVICES[action])})
    return {"values": values, "controls": controls}


@router.post("/integrations/homeassistant/entities", tags=["library"])
async def ha_entities(body: HAIn, _: Principal = Depends(installer)) -> dict:
    from ems.devices.base import DriverError
    from ems.integrations.homeassistant.bridge import HAClient, suggest
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(422, "adres moet met http:// of https:// beginnen")
    try:
        c = HAClient(body.url, body.token)
        await c.ping()
        states = await c.states()
        out = {"entities": suggest(states),
               "all": [{"entity_id": x.get("entity_id"), "name": (x.get("attributes") or {}).get("friendly_name"),
                        "unit": (x.get("attributes") or {}).get("unit_of_measurement"), "state": x.get("state")}
                       for x in states if x.get("entity_id")]}
        if body.category is not None:
            out["slots"] = ha_slots(body.category)
        return out
    except DriverError as exc:
        raise HTTPException(422, str(exc)) from exc


# ------------------------------------------------------------- Testomgeving
@router.get("/lab", tags=["testomgeving"])
async def lab_status(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    if not rt.lab_mode or rt.lab is None:
        return {"active": False, "devices": []}
    site = rt.site
    comps = {cid: {k: getattr(c, k) for k in ("output_w", "soc_pct", "mode", "indoor_temp_c", "limit_w", "relay_on",
                                                "tank_c", "connected", "current_setpoint_a") if hasattr(c, k)}
             for cid, c in site.components.items() if cid.startswith("lab_")}
    return {"active": True, "devices": rt.lab.overview(), "house": comps,
            "meter_w": round(site.meter.power_w, 1), "sim_time": site.now.isoformat()}


class FaultIn(BaseModel):
    offline: bool = False
    exception: int | None = Field(None, ge=1, le=11)
    delay_s: float = Field(0.0, ge=0, le=30)


@router.put("/lab/faults/{key}", tags=["testomgeving"])
async def lab_fault(key: str, body: FaultIn, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    if not rt.lab_mode or rt.lab is None:
        raise HTTPException(404, "alleen in de Testomgeving")
    if key not in rt.lab.ports:
        raise HTTPException(404, "onbekend virtueel apparaat")
    f = rt.lab.fault(key)
    f.offline, f.exception, f.delay_s = body.offline, body.exception, body.delay_s
    return {"ok": True, "fault": vars(f)}
