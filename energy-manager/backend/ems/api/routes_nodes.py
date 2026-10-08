"""Node API.

``/api/v1/node/...``  — this node as device owner, called by other nodes (node token).
``/api/v1/nodes/...`` — managing the nodes of this installation (user login).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ems.api.deps import admin, get_runtime, installer, viewer
from ems.core.config import ConfigError
from ems.nodes.identity import ROLE_LABELS_NL
from ems.nodes.link import NodeLinkError
from ems.security.auth import Principal
from ems.server.commissioning import node_level_problem
from ems.server.runtime import EMSRuntime

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


# ------------------------------------------------------------- peer access
async def peer_node(x_ems_node_token: str = Header(""), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    peer = await asyncio.to_thread(rt.nodes.peer_from_token, x_ems_node_token)
    if peer is None:
        raise HTTPException(401, "onbekende of ingetrokken node")
    return peer


@router.get("/node/info", tags=["nodes"])
async def node_info(rt: EMSRuntime = Depends(get_runtime)) -> dict:
    me = rt.nodes.identity
    return {**me.to_dict(), "site": rt.config.site.name, "mode": rt.config.runtime.mode,
            "pairing_open": rt.nodes.pairing.open}


class PairBody(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    node: dict[str, Any]


@router.post("/node/pair", tags=["nodes"])
async def node_pair(body: PairBody, request: Request, rt: EMSRuntime = Depends(get_runtime)) -> dict:
    limiter = request.app.state.login_limiter
    client = request.client.host if request.client else "?"
    if not limiter.allowed(client):
        raise HTTPException(429, "te veel pogingen; probeer het later opnieuw")
    peer = body.node
    if not isinstance(peer.get("node_id"), str) or len(peer["node_id"]) > 64:
        raise HTTPException(422, "ongeldige node")
    token = await asyncio.to_thread(rt.nodes.accept_pairing, body.code, peer, client)
    if token is None:
        limiter.failed(client)
        raise HTTPException(403, "koppelcode onjuist of verlopen — maak een nieuwe code aan op de node")
    me = rt.nodes.identity
    return {"token": token, "node": {**me.to_dict(), "site": rt.config.site.name}}


@router.post("/node/lease", tags=["nodes"])
async def node_lease(peer: dict = Depends(peer_node), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return await asyncio.to_thread(rt.nodes.lease_request, peer)


def _local_devices(rt: EMSRuntime):
    return [d for d in rt.config.devices if d.driver != "node.remote"]


def _device_entry(rt: EMSRuntime, cfg) -> dict:
    md = rt.devices.devices.get(cfg.id)
    snap = rt.engine.last_snapshot
    st = None if snap is None else snap.devices.get(cfg.id)
    drv = None if md is None else md.driver
    manifest = None if drv is None else type(drv).manifest
    return {"id": cfg.id, "name": cfg.name, "category": cfg.category.value, "driver": cfg.driver,
            "phase": cfg.phase, "params": cfg.params, "control_level": cfg.control_level,
            "capabilities": sorted(c.value for c in drv.device_capabilities()) if drv else [],
            "write_capable": bool(manifest and (manifest.write_capable or manifest.simulated)),
            "documented": bool(manifest and (manifest.simulated or (manifest.write_capable and manifest.documentation))),
            "grid_meter_kind": manifest.grid_meter_kind if manifest else None,
            "is_primary_grid_meter": rt.engine.grid_selection.device_id == cfg.id,
            "status": st.status.value if st else "unknown"}


@router.get("/node/devices", tags=["nodes"])
async def node_devices(_: dict = Depends(peer_node), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    return [_device_entry(rt, d) for d in _local_devices(rt)]


def _local(rt: EMSRuntime, device_id: str):
    try:
        cfg = rt.config.device(device_id)
    except KeyError:
        raise HTTPException(404, "apparaat niet gevonden") from None
    if cfg.driver == "node.remote":
        raise HTTPException(404, "apparaat is niet van deze node")
    return cfg


@router.get("/node/devices/{device_id}/state", tags=["nodes"])
async def node_device_state(device_id: str, _: dict = Depends(peer_node), rt: EMSRuntime = Depends(get_runtime)):
    _local(rt, device_id)
    snap = rt.engine.last_snapshot
    st = None if snap is None else snap.devices.get(device_id)
    health = rt.devices.health(device_id, rt.now())
    if st is None:
        return {"status": "unknown", "values": {}, "health": health}
    return {"status": st.status.value, "error": st.error, "values": {str(k): v for k, v in st.values.items()},
            "ts": snap.timestamp.timestamp(), "health": health}


class RemoteCommand(BaseModel):
    action: str
    value: float | str | None = None
    epoch: int
    issued_ts: float
    ttl_s: float = Field(30.0, gt=0, le=300)
    reason: str = Field("", max_length=500)


@router.post("/node/devices/{device_id}/command", tags=["nodes"])
async def node_device_command(device_id: str, body: RemoteCommand, peer: dict = Depends(peer_node),
                              rt: EMSRuntime = Depends(get_runtime)) -> dict:
    _local(rt, device_id)
    return await rt.nodes.remote_command(peer, device_id, body.model_dump())


@router.post("/node/devices/{device_id}/release", tags=["nodes"])
async def node_device_release(device_id: str, peer: dict = Depends(peer_node),
                              rt: EMSRuntime = Depends(get_runtime)) -> dict:
    _local(rt, device_id)
    await rt.nodes.release_remote([device_id], why=f"Vrijgegeven door controller {peer['name']}")
    return {"ok": True}


class LevelBody(BaseModel):
    control_level: Literal["connection_test", "read_only", "shadow", "limited", "full"]
    limited_fraction: float = Field(0.3, gt=0, le=1)


@router.put("/node/devices/{device_id}/control-level", tags=["nodes"])
async def node_device_level(device_id: str, body: LevelBody, peer: dict = Depends(peer_node),
                            rt: EMSRuntime = Depends(get_runtime)) -> dict:
    _local(rt, device_id)
    problem = node_level_problem(rt, device_id, body.control_level)   # this node checks its own device too
    if problem:
        raise HTTPException(409, problem)
    data = rt.config.model_dump(mode="json")
    for d in data["devices"]:
        if d["id"] == device_id:
            d["control_level"], d["limited_fraction"] = body.control_level, body.limited_fraction
    try:
        await rt.reload(data, f"node:{peer['name']}", f"inbedrijfstelling {device_id}: {body.control_level}")
    except (ConfigError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "control_level": body.control_level}


@router.get("/node/history", tags=["nodes"])
async def node_history(device_ids: str, start: float, end: float, _: dict = Depends(peer_node),
                       rt: EMSRuntime = Depends(get_runtime)) -> dict[str, list[dict]]:
    if end - start > 31 * 86400:
        raise HTTPException(422, "maximaal 31 dagen per aanvraag")
    out = {}
    for dev in [d for d in device_ids.split(",") if d][:50]:
        _local(rt, dev)
        rows = await asyncio.to_thread(rt.db.device_samples_between, dev, start, end, 20000)
        out[dev] = [{"ts": r["ts"], "status": r["status"], "values": r["values"]} for r in rows]
    return out


# ------------------------------------------------------- user management
@router.get("/nodes", tags=["nodes"])
async def list_nodes(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    data = await asyncio.to_thread(rt.nodes.overview)
    data["role_labels"] = ROLE_LABELS_NL
    return data


@router.get("/nodes/discover", tags=["nodes"])
async def discover_nodes(_: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    from ems.nodes.discovery import browse

    try:
        found = await browse(3.0, rt.nodes.identity.node_id)
    except Exception as exc:
        raise HTTPException(503, f"zoeken via mDNS niet mogelijk: {exc}") from exc
    paired = {r["node_id"] for r in await asyncio.to_thread(rt.db.list_nodes)}
    return [{**n, "paired": n["node_id"] in paired} for n in found]


@router.post("/nodes/pairing-code", tags=["nodes"])
async def new_pairing_code(_: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    code, ttl = rt.nodes.pairing.new_code()
    return {"code": code, "expires_in_s": ttl, "node": rt.nodes.identity.to_dict()}


class PairWith(BaseModel):
    address: str = Field(min_length=3, max_length=200)
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/nodes/pair", tags=["nodes"])
async def pair_with(body: PairWith, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    try:
        return await rt.nodes.pair_with(body.address, body.code)
    except NodeLinkError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        log.warning("pairing failed", exc_info=True)
        raise HTTPException(502, f"node niet bereikbaar: {type(exc).__name__}") from exc


@router.delete("/nodes/{node_id}", tags=["nodes"])
async def unpair(node_id: str, _: Principal = Depends(installer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    used = [d.id for d in rt.config.devices if d.driver == "node.remote" and d.connection.get("node_id") == node_id]
    if used:
        raise HTTPException(409, f"verwijder eerst de apparaten van deze node: {', '.join(used)}")
    return {"ok": await rt.nodes.unpair(node_id)}


class ImportDevice(BaseModel):
    name: str | None = Field(None, max_length=80)
    primary_grid_meter: bool = False


@router.post("/nodes/{node_id}/devices/{remote_id}/import", tags=["nodes"])
async def import_device(node_id: str, remote_id: str, body: ImportDevice, p: Principal = Depends(installer),
                        rt: EMSRuntime = Depends(get_runtime)) -> dict:
    link = rt.nodes.links.get(node_id)
    if link is None:
        raise HTTPException(404, "node niet gekoppeld")
    try:
        devices = await link.refresh_devices()
    except NodeLinkError as exc:
        raise HTTPException(502, str(exc)) from exc
    remote = devices.get(remote_id)
    if remote is None:
        raise HTTPException(404, "apparaat niet gevonden op de node")
    data = rt.config.model_dump(mode="json")
    if any(d["driver"] == "node.remote" and d["connection"].get("node_id") == node_id
           and d["connection"].get("remote_id") == remote_id for d in data["devices"]):
        raise HTTPException(409, "apparaat is al toegevoegd")
    existing = {d["id"] for d in data["devices"]}
    base = f"{remote_id}-{link.name}".lower()
    dev_id = "".join(c if c.isalnum() or c in "_-" else "-" for c in base)[:56].strip("-") or "remote"
    n, candidate = 1, dev_id
    while candidate in existing:
        n += 1
        candidate = f"{dev_id}-{n}"
    dev = {"id": candidate, "name": body.name or f"{remote['name']} ({link.name})", "category": remote["category"],
           "driver": "node.remote", "phase": remote.get("phase", "3P"),
           "connection": {"node_id": node_id, "remote_id": remote_id},
           "params": remote.get("params") or {}, "control_level": "read_only"}
    if body.primary_grid_meter:
        for d in data["devices"]:
            if d.get("role") in ("primary_grid_meter", "grid_reference"):
                d["role"] = None
        dev["role"] = "primary_grid_meter"
    data["devices"].append(dev)
    try:
        await rt.reload(data, p.username, f"apparaat {candidate} van node {link.name} toegevoegd")
    except (ConfigError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "device_id": candidate}


@router.get("/nodes/self/lease", tags=["nodes"])
async def own_lease(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return rt.nodes.lease.state.to_dict(time.time())

