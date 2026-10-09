"""Node protocol — always outbound from the local installation (HTTPS, no inbound ports on the router):

* ``POST /node/pair``      one-time pairing code -> node id + node token (shown once, stored hashed)
* ``POST /node/heartbeat`` status (version, mode, health, device list); returns site settings + licence
* ``GET  /node/commands``  pending remote commands (long poll up to 25 s)
* ``POST /node/commands/{id}/ack`` result of the *local* safety check and execution
* ``POST /node/rotate``    new token; the previous one stays valid for a short grace period
* ``POST /node/unpair``    the installation removes itself
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from emcloud import billing
from emcloud.audit import audit
from emcloud.billing import iso
from emcloud.db import Device, License, Node, Organization, PairingCode, RemoteCommand, Site, Telemetry
from emcloud.deps import NodeCtx, client_ip, get_db, limit, node_ctx, now
from emcloud.security import new_token, normalize_pairing_code, token_hash

router = APIRouter(prefix="/api/v1/node", tags=["node"])


class PairIn(BaseModel):
    code: str = Field(max_length=20)
    name: str = Field("", max_length=200)
    version: str = Field("", max_length=40)
    platform: str = Field("", max_length=80)


@router.post("/pair")
def pair(body: PairIn, request: Request, db: Session = Depends(get_db)) -> dict:
    t = now(request)
    limit(request, f"pair:{client_ip(request)}", 10, 600)       # brute force on codes
    pc = db.scalar(select(PairingCode).where(PairingCode.code_hash == token_hash(normalize_pairing_code(body.code))))
    if pc is None or pc.used_ts is not None or t >= pc.expires_ts:
        raise HTTPException(400, "koppelcode ongeldig of verlopen")
    pc.used_ts = t
    billing.check_node_limit(db, pc.org_id)
    token = new_token("emn_")
    node = Node(org_id=pc.org_id, site_id=pc.site_id, name=body.name, version=body.version, platform=body.platform,
                token_hash=token_hash(token), token_rotated_ts=t, paired_ts=t, last_seen_ts=t)
    db.add(node)
    site, org = db.get(Site, pc.site_id), db.get(Organization, pc.org_id)
    audit(db, t, "node.paired", actor=pc.created_by, org_id=pc.org_id, target=site.name, node_name=body.name,
          ip=client_ip(request))
    db.commit()
    s = request.app.state.settings
    return {"node_id": node.id, "token": token, "organization": org.name, "site_id": site.id, "site_name": site.name,
            "heartbeat_s": s.node_heartbeat_s, "token_rotate_days": 30}


class DeviceIn(BaseModel):
    id: str = Field(max_length=64)
    name: str = Field("", max_length=200)
    category: str = Field("", max_length=40)
    online: bool = False


class HeartbeatIn(BaseModel):
    version: str = Field("", max_length=40)
    platform: str = Field("", max_length=80)
    mode: str = Field("", max_length=40)
    health: float | None = None
    errors: list[str] = Field(default_factory=list, max_length=20)
    ems_status: str = Field("", max_length=60)
    devices: list[DeviceIn] = Field(default_factory=list, max_length=200)
    summary: dict | None = None        # only stored with the organisation's sync consent


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, n: NodeCtx = Depends(node_ctx), db: Session = Depends(get_db)) -> dict:
    node = db.get(Node, n.node.id)
    node.last_seen_ts, node.version, node.platform = n.now, body.version or node.version, body.platform or node.platform
    node.status = {"mode": body.mode, "health": body.health, "errors": [e[:200] for e in body.errors[:20]],
                   "ems_status": body.ems_status}
    existing = {d.local_id: d for d in db.scalars(select(Device).where(Device.node_id == node.id))}
    seen = set()
    for d in body.devices:
        seen.add(d.id)
        row = existing.get(d.id) or Device(org_id=node.org_id, site_id=node.site_id, node_id=node.id, local_id=d.id)
        row.name, row.category, row.online, row.updated_ts = d.name, d.category, d.online, n.now
        db.add(row)
    for lid, row in existing.items():
        if lid not in seen:
            db.delete(row)
    org = db.get(Organization, node.org_id)
    stored_summary = False
    if body.summary and org.sync_consent and "history_sync" in billing.entitlements(db, node.org_id, n.now):
        allowed = {k: v for k, v in body.summary.items() if isinstance(v, (int, float, str, bool, type(None)))}
        db.add(Telemetry(org_id=node.org_id, site_id=node.site_id, ts=n.now, data=dict(list(allowed.items())[:30])))
        stored_summary = True
    site = db.get(Site, node.site_id)
    lic = db.scalar(select(License).where(License.org_id == node.org_id))
    db.commit()
    return {"ok": True, "site_name": site.name, "remote_control_enabled": site.remote_control_enabled,
            "license": {"valid": billing.license_valid(lic, n.now), "plan": lic.plan_key if lic else None,
                        "expires": iso(lic.expires_ts) if lic else None},
            "entitlements": sorted(billing.entitlements(db, node.org_id, n.now)), "summary_stored": stored_summary,
            "sync_consent": org.sync_consent, "rotate_token": n.now - node.token_rotated_ts > 30 * 86400}


def _pending(db: Session, node_id: str, t: float) -> list[RemoteCommand]:
    rows = db.scalars(select(RemoteCommand).where(RemoteCommand.node_id == node_id, RemoteCommand.status == "queued")).all()
    out = []
    for r in rows:
        if t > r.expires_ts:
            r.status = "expired"
        else:
            r.status = "sent"
            out.append(r)
    db.commit()
    return out


@router.get("/commands")
async def commands(request: Request, wait: float = Query(0, ge=0, le=25), n: NodeCtx = Depends(node_ctx),
                   db: Session = Depends(get_db)) -> list[dict]:
    """Long poll: returns as soon as there is a command, or after ``wait`` seconds."""
    deadline = n.now + wait
    while True:
        t = now(request)
        rows = await asyncio.to_thread(_pending, db, n.node.id, t)
        site = await asyncio.to_thread(db.get, Site, n.node.site_id)
        if rows or t >= deadline:
            break
        await asyncio.sleep(1.0)
    if not site.remote_control_enabled:                  # switched off meanwhile: never deliver
        for r in rows:
            r.status, r.result = "rejected", {"reason": "bediening op afstand staat uit"}
        await asyncio.to_thread(db.commit)
        return []
    return [{"id": r.id, "command": r.command, "requested_by": r.requested_by, "created": iso(r.created_ts),
             "expires": iso(r.expires_ts)} for r in rows]


class AckIn(BaseModel):
    status: str = Field(pattern=r"^(accepted|rejected|failed)$")
    reason: str = Field("", max_length=500)
    executes: bool | None = None


@router.post("/commands/{command_id}/ack")
def ack(command_id: str, body: AckIn, n: NodeCtx = Depends(node_ctx), db: Session = Depends(get_db)) -> dict:
    r = db.get(RemoteCommand, command_id)
    if r is None or r.node_id != n.node.id:                  # a node can only acknowledge its own commands
        raise HTTPException(404, "niet gevonden")
    if r.status not in ("sent", "queued"):
        raise HTTPException(409, "opdracht is al afgehandeld")
    r.status, r.result, r.acked_ts = body.status, {"reason": body.reason, "executes": body.executes}, n.now
    audit(db, n.now, "command.ack", actor_kind="node", actor=None, org_id=r.org_id, target=f"{r.site_id}/{r.command.get('device')}",
          status=body.status, reason=body.reason, node=n.node.id)
    db.commit()
    return {"ok": True}


@router.post("/rotate")
def rotate(request: Request, n: NodeCtx = Depends(node_ctx), db: Session = Depends(get_db)) -> dict:
    if n.token_is_previous:
        raise HTTPException(401, "gebruik het actuele token om te vervangen")
    node = db.get(Node, n.node.id)
    token = new_token("emn_")
    node.prev_token_hash, node.prev_token_valid_until = node.token_hash, n.now + request.app.state.settings.node_token_grace_s
    node.token_hash, node.token_rotated_ts = token_hash(token), n.now
    audit(db, n.now, "node.token_rotated", actor_kind="node", org_id=node.org_id, target=node.id)
    db.commit()
    return {"token": token}


@router.post("/unpair")
def unpair(n: NodeCtx = Depends(node_ctx), db: Session = Depends(get_db)) -> dict:
    node = db.get(Node, n.node.id)
    node.revoked = True
    db.execute(delete(Device).where(Device.node_id == node.id))
    audit(db, n.now, "node.unpaired", actor_kind="node", org_id=node.org_id, target=node.id)
    db.commit()
    return {"ok": True}
