"""Customer side: organisation, members and invitations, sites, installations (nodes), pairing codes,
remote commands, temporary support access, subscription/licence and audit log. Every endpoint resolves
access through ``org_access`` (tenant isolation)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emcloud import billing
from emcloud.audit import audit
from emcloud.authz import OWNER, RANK, check_role_change, org_access
from emcloud.billing import iso
from emcloud.db import (
    AuditLog,
    Device,
    Invitation,
    License,
    Membership,
    Node,
    PairingCode,
    Plan,
    RemoteCommand,
    Site,
    Subscription,
    SupportGrant,
    Telemetry,
    User,
)
from emcloud.deps import Ctx, ctx, get_db, limit
from emcloud.security import new_pairing_code, new_token, token_hash

router = APIRouter(prefix="/api/v1/orgs", tags=["organizations"])

ONLINE_FACTOR = 3                # online = heartbeat seen within 3 intervals


def node_online(request: Request, n: Node, now: float) -> bool:
    return not n.revoked and now - n.last_seen_ts <= ONLINE_FACTOR * request.app.state.settings.node_heartbeat_s


def node_dict(request: Request, n: Node, now: float) -> dict:
    return {"id": n.id, "site_id": n.site_id, "name": n.name, "version": n.version, "platform": n.platform,
            "online": node_online(request, n, now), "last_seen": iso(n.last_seen_ts) if n.last_seen_ts else None,
            "paired": iso(n.paired_ts), "revoked": n.revoked,
            "status": {k: v for k, v in (n.status or {}).items() if k in ("mode", "health", "errors", "ems_status")}}


# ----------------------------------------------------------- organisation
@router.get("")
def my_orgs(c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> list[dict]:
    from emcloud.api.auth import user_dict
    return user_dict(db, c.user, c.now)["organizations"]


@router.get("/{org_id}")
def get_org(org_id: str, request: Request, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    acc = org_access(db, c.user, org_id, "org.read", c.now)
    o = acc.org
    return {"id": o.id, "name": o.name, "status": o.status, "require_mfa": o.require_mfa, "sync_consent": o.sync_consent,
            "share_usage_with_platform": o.share_usage_with_platform, "data_region": o.data_region,
            "my_role": acc.role, "my_permissions": sorted(acc.permissions), "via_support": acc.via_support,
            "entitlements": sorted(billing.entitlements(db, org_id, c.now))}


class OrgPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    require_mfa: bool | None = None
    sync_consent: bool | None = None
    share_usage_with_platform: bool | None = None


@router.patch("/{org_id}")
def patch_org(org_id: str, body: OrgPatch, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    acc = org_access(db, c.user, org_id, "org.manage", c.now)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(acc.org, k, v)
    if changes.get("sync_consent") is False:            # consent withdrawn: delete synced data
        for row in db.scalars(select(Telemetry).where(Telemetry.org_id == org_id)).all():
            db.delete(row)
    audit(db, c.now, "org.updated", actor=c.user.id, org_id=org_id, ip=c.ip, **changes)
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------- members
@router.get("/{org_id}/members")
def members(org_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    org_access(db, c.user, org_id, "users.read", c.now)
    rows = db.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.org_id == org_id)).all()
    invites = db.scalars(select(Invitation).where(Invitation.org_id == org_id, Invitation.accepted_ts.is_(None),
                                                  Invitation.revoked.is_(False), Invitation.expires_ts > c.now)).all()
    return {"members": [{"user_id": u.id, "email": u.email, "name": u.name, "role": m.role, "site_ids": m.site_ids,
                         "mfa_enabled": u.mfa_enabled} for m, u in rows],
            "invitations": [{"id": i.id, "email": i.email, "role": i.role, "expires": iso(i.expires_ts)} for i in invites]}


class InviteIn(BaseModel):
    email: EmailStr
    role: str
    site_ids: list[str] | None = None


@router.post("/{org_id}/invitations")
def invite(org_id: str, body: InviteIn, request: Request, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    acc = org_access(db, c.user, org_id, "users.invite", c.now)
    limit(request, f"invite-send:{org_id}", 30, 3600)
    check_role_change(acc, body.role)
    if body.role != "VIEWER" and "multi_user" not in billing.entitlements(db, org_id, c.now):
        raise HTTPException(402, "meerdere gebruikers met bedieningsrechten vallen niet in uw abonnement")
    if body.site_ids:
        valid = set(db.scalars(select(Site.id).where(Site.org_id == org_id, Site.id.in_(body.site_ids))))
        if valid != set(body.site_ids):
            raise HTTPException(404, "locatie niet gevonden")
    email = body.email.strip().lower()
    token = new_token()
    db.add(Invitation(org_id=org_id, email=email, role=body.role, site_ids=body.site_ids, token_hash=token_hash(token),
                      invited_by=c.user.id, expires_ts=c.now + 7 * 86400))
    request.app.state.mailer.send(db, c.now, email, f"Uitnodiging voor {acc.org.name} — Energy Manager Cloud",
                                  f"U bent uitgenodigd als {body.role} voor '{acc.org.name}'.\n\n"
                                  f"{request.app.state.settings.base_url}/#/invite/{token}\n\nDe uitnodiging is 7 dagen geldig.")
    audit(db, c.now, "member.invited", actor=c.user.id, org_id=org_id, target=email, role=body.role, ip=c.ip)
    db.commit()
    return {"ok": True}


@router.delete("/{org_id}/invitations/{invitation_id}")
def revoke_invitation(org_id: str, invitation_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    org_access(db, c.user, org_id, "users.invite", c.now)
    inv = db.get(Invitation, invitation_id)
    if inv is None or inv.org_id != org_id:
        raise HTTPException(404, "niet gevonden")
    inv.revoked = True
    db.commit()
    return {"ok": True}


class MemberPatch(BaseModel):
    role: str
    site_ids: list[str] | None = None


def _owners(db: Session, org_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == org_id, Membership.role == OWNER))


@router.patch("/{org_id}/members/{user_id}")
def change_member(org_id: str, user_id: str, body: MemberPatch, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    acc = org_access(db, c.user, org_id, "users.manage", c.now)
    m = db.scalar(select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id))
    if m is None:
        raise HTTPException(404, "niet gevonden")
    if user_id == c.user.id:
        raise HTTPException(403, "u kunt uw eigen rol niet wijzigen")
    check_role_change(acc, body.role, m.role)
    if m.role == OWNER and body.role != OWNER and _owners(db, org_id) <= 1:
        raise HTTPException(409, "er moet minstens één eigenaar blijven")
    m.role, m.site_ids = body.role, body.site_ids
    audit(db, c.now, "member.role_changed", actor=c.user.id, org_id=org_id, target=user_id, role=body.role, ip=c.ip)
    db.commit()
    return {"ok": True}


@router.delete("/{org_id}/members/{user_id}")
def remove_member(org_id: str, user_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    acc = org_access(db, c.user, org_id, "users.read" if user_id == c.user.id else "users.manage", c.now)
    m = db.scalar(select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id))
    if m is None:
        raise HTTPException(404, "niet gevonden")
    if user_id != c.user.id and RANK[m.role] > RANK[acc.role]:
        raise HTTPException(403, "u kunt iemand met een hogere rol niet verwijderen")
    if m.role == OWNER and _owners(db, org_id) <= 1:
        raise HTTPException(409, "er moet minstens één eigenaar blijven")
    db.delete(m)
    audit(db, c.now, "member.removed", actor=c.user.id, org_id=org_id, target=user_id, ip=c.ip)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------- sites
def site_dict(request: Request, db: Session, s: Site, now: float) -> dict:
    nodes = db.scalars(select(Node).where(Node.site_id == s.id, Node.revoked.is_(False))).all()
    return {"id": s.id, "name": s.name, "timezone": s.timezone, "remote_control_enabled": s.remote_control_enabled,
            "online": any(node_online(request, n, now) for n in nodes), "nodes": [node_dict(request, n, now) for n in nodes],
            "created": iso(s.created_ts)}


@router.get("/{org_id}/sites")
def sites(org_id: str, request: Request, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> list[dict]:
    acc = org_access(db, c.user, org_id, "sites.read", c.now)
    rows = db.scalars(select(Site).where(Site.org_id == org_id).order_by(Site.created_ts)).all()
    return [site_dict(request, db, s, c.now) for s in rows if acc.site_allowed(s.id)]


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field("Europe/Amsterdam", max_length=64)


@router.post("/{org_id}/sites")
def create_site(org_id: str, body: SiteIn, request: Request, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    acc = org_access(db, c.user, org_id, "sites.manage", c.now)
    if acc.site_ids is not None:
        raise HTTPException(403, "alleen gebruikers met toegang tot alle locaties kunnen locaties toevoegen")
    billing.check_site_limit(db, org_id)
    s = Site(org_id=org_id, name=body.name, timezone=body.timezone, created_ts=c.now)
    db.add(s)
    audit(db, c.now, "site.created", actor=c.user.id, org_id=org_id, target=body.name, ip=c.ip)
    db.commit()
    return site_dict(request, db, s, c.now)


@router.get("/{org_id}/sites/{site_id}")
def get_site(org_id: str, site_id: str, request: Request, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    acc = org_access(db, c.user, org_id, "sites.read", c.now, site_id)
    s = db.get(Site, site_id)
    out = site_dict(request, db, s, c.now)
    if acc.can("devices.read"):
        out["devices"] = [{"id": d.local_id, "name": d.name, "category": d.category, "online": d.online}
                          for d in db.scalars(select(Device).where(Device.site_id == site_id))]
    if acc.org.sync_consent and "history_sync" in billing.entitlements(db, org_id, c.now):
        last = db.scalar(select(Telemetry).where(Telemetry.site_id == site_id).order_by(Telemetry.ts.desc()).limit(1))
        out["summary"] = None if last is None else {"ts": iso(last.ts), **last.data}
    return out


class SitePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    remote_control_enabled: bool | None = None


@router.patch("/{org_id}/sites/{site_id}")
def patch_site(org_id: str, site_id: str, body: SitePatch, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    acc = org_access(db, c.user, org_id, "sites.manage", c.now, site_id)
    s = db.get(Site, site_id)
    if body.remote_control_enabled is not None and body.remote_control_enabled != s.remote_control_enabled:
        if not acc.can("remote_access.grant"):
            raise HTTPException(403, "alleen eigenaar of beheerder kan bediening op afstand aan- of uitzetten")
        if body.remote_control_enabled:
            billing.require(db, org_id, "remote_control", c.now)
        s.remote_control_enabled = body.remote_control_enabled
        audit(db, c.now, "site.remote_control", actor=c.user.id, org_id=org_id, target=site_id,
              enabled=body.remote_control_enabled, ip=c.ip)
    if body.name:
        s.name = body.name
    db.commit()
    return {"ok": True}


@router.delete("/{org_id}/sites/{site_id}")
def delete_site(org_id: str, site_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    org_access(db, c.user, org_id, "org.manage", c.now, site_id)
    db.delete(db.get(Site, site_id))
    audit(db, c.now, "site.deleted", actor=c.user.id, org_id=org_id, target=site_id, ip=c.ip)
    db.commit()
    return {"ok": True}


# -------------------------------------------------------- installations
@router.get("/{org_id}/installations")
def installations(org_id: str, request: Request, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> list[dict]:
    """'Mijn installaties': every paired node with online/offline, per site."""
    acc = org_access(db, c.user, org_id, "sites.read", c.now)
    rows = db.execute(select(Node, Site).join(Site, Site.id == Node.site_id).where(Node.org_id == org_id,
                                                                                   Node.revoked.is_(False))).all()
    return [{**node_dict(request, n, c.now), "site_name": s.name} for n, s in rows if acc.site_allowed(s.id)]


@router.post("/{org_id}/sites/{site_id}/pairing-code")
def pairing_code(org_id: str, site_id: str, request: Request, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    """One-time code (valid 15 min) to enter in the local Energy Manager: 'Deze installatie koppelen'."""
    org_access(db, c.user, org_id, "nodes.pair", c.now, site_id)
    limit(request, f"paircode:{org_id}", 20, 3600)
    billing.check_node_limit(db, org_id)
    code = new_pairing_code()
    ttl = request.app.state.settings.pairing_code_minutes * 60
    db.add(PairingCode(code_hash=token_hash(code), org_id=org_id, site_id=site_id, created_by=c.user.id,
                       expires_ts=c.now + ttl))
    audit(db, c.now, "node.pairing_code", actor=c.user.id, org_id=org_id, target=site_id, ip=c.ip)
    db.commit()
    return {"code": code, "expires": iso(c.now + ttl)}


@router.delete("/{org_id}/nodes/{node_id}")
def revoke_node(org_id: str, node_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    n = db.get(Node, node_id)
    if n is None or n.org_id != org_id:
        raise HTTPException(404, "niet gevonden")
    org_access(db, c.user, org_id, "nodes.pair", c.now, n.site_id)
    n.revoked = True
    audit(db, c.now, "node.revoked", actor=c.user.id, org_id=org_id, target=node_id, ip=c.ip)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------- remote commands
class CommandIn(BaseModel):
    device: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    action: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
    value: float | str | None = None
    duration_min: float | None = Field(60, gt=0, le=24 * 60)


@router.post("/{org_id}/sites/{site_id}/commands")
def send_command(org_id: str, site_id: str, body: CommandIn, request: Request, c: Ctx = Depends(ctx),
                 db: Session = Depends(get_db)) -> dict:
    """Queue a command for the site's node. The node executes it only through its own local safety
    validation (``can_execute`` + SafetyValidator) and only if remote control is also allowed locally."""
    org_access(db, c.user, org_id, "devices.control", c.now, site_id)
    s = db.get(Site, site_id)
    if not s.remote_control_enabled:
        raise HTTPException(403, "bediening op afstand staat uit voor deze locatie")
    billing.require(db, org_id, "remote_control", c.now)
    limit(request, f"cmd:{site_id}", 10, 60)
    if isinstance(body.value, str) and len(body.value) > 40:
        raise HTTPException(422, "waarde te lang")
    node = db.scalar(select(Node).where(Node.site_id == site_id, Node.revoked.is_(False)).order_by(Node.last_seen_ts.desc()))
    if node is None or not node_online(request, node, c.now):
        raise HTTPException(409, "installatie is offline; opdracht niet verstuurd")
    cmd = RemoteCommand(org_id=org_id, site_id=site_id, node_id=node.id, requested_by=c.user.id,
                        command=body.model_dump(), created_ts=c.now, expires_ts=c.now + 120)
    db.add(cmd)
    audit(db, c.now, "command.queued", actor=c.user.id, org_id=org_id, target=f"{site_id}/{body.device}",
          action_name=body.action, value=body.value, ip=c.ip)
    db.commit()
    return {"id": cmd.id, "status": cmd.status}


@router.get("/{org_id}/sites/{site_id}/commands")
def list_commands(org_id: str, site_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> list[dict]:
    org_access(db, c.user, org_id, "devices.read", c.now, site_id)
    rows = db.scalars(select(RemoteCommand).where(RemoteCommand.site_id == site_id).order_by(RemoteCommand.created_ts.desc())
                      .limit(50))
    return [{"id": r.id, "command": r.command, "status": r.status if not (r.status in ("queued", "sent") and c.now > r.expires_ts)
             else "expired", "result": r.result, "created": iso(r.created_ts), "acked": iso(r.acked_ts)} for r in rows]


# ------------------------------------------------------------ support access
class SupportIn(BaseModel):
    email: EmailStr
    site_id: str | None = None
    hours: float = Field(24, gt=0, le=72)
    scope: str = Field("read", pattern=r"^(read|control)$")


@router.post("/{org_id}/support-grants")
def grant_support(org_id: str, body: SupportIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    """Customer grants temporary support access (max. 72 h). 'control' also requires remote control on the site."""
    acc = org_access(db, c.user, org_id, "remote_access.grant", c.now, body.site_id)
    if acc.via_support:
        raise HTTPException(403, "supporttoegang kan geen supporttoegang verlenen")
    billing.require(db, org_id, "support_access", c.now)
    grantee = db.scalar(select(User).where(User.email == body.email.strip().lower(), User.deleted_ts.is_(None)))
    if grantee is None:
        raise HTTPException(404, "geen account met dit e-mailadres")
    g = SupportGrant(org_id=org_id, site_id=body.site_id, grantee_user_id=grantee.id, scope=body.scope, granted_by=c.user.id,
                     created_ts=c.now, expires_ts=c.now + body.hours * 3600)
    db.add(g)
    audit(db, c.now, "support.granted", actor=c.user.id, org_id=org_id, target=grantee.email, scope=body.scope,
          hours=body.hours, site_id=body.site_id, ip=c.ip)
    db.commit()
    return {"id": g.id, "expires": iso(g.expires_ts)}


@router.get("/{org_id}/support-grants")
def list_support(org_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> list[dict]:
    org_access(db, c.user, org_id, "remote_access.grant", c.now)
    rows = db.execute(select(SupportGrant, User).join(User, User.id == SupportGrant.grantee_user_id)
                      .where(SupportGrant.org_id == org_id).order_by(SupportGrant.created_ts.desc())).all()
    return [{"id": g.id, "email": u.email, "scope": g.scope, "site_id": g.site_id, "expires": iso(g.expires_ts),
             "active": not g.revoked and g.expires_ts > c.now} for g, u in rows]


@router.delete("/{org_id}/support-grants/{grant_id}")
def revoke_support(org_id: str, grant_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    org_access(db, c.user, org_id, "remote_access.grant", c.now)
    g = db.get(SupportGrant, grant_id)
    if g is None or g.org_id != org_id:
        raise HTTPException(404, "niet gevonden")
    g.revoked = True
    audit(db, c.now, "support.revoked", actor=c.user.id, org_id=org_id, target=grant_id, ip=c.ip)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------ subscription / licence
@router.get("/{org_id}/subscription")
def subscription(org_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    org_access(db, c.user, org_id, "subscriptions.read", c.now)
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
    lic = db.scalar(select(License).where(License.org_id == org_id))
    ms, mn = billing.limits(db, org_id)
    return {"subscription": billing.subscription_dict(sub), "license": billing.license_dict(lic, c.now),
            "entitlements": sorted(billing.entitlements(db, org_id, c.now)), "max_sites": ms, "max_nodes": mn,
            "plans": [billing.plan_dict(p) for p in db.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.rank))]}


class PlanChange(BaseModel):
    plan: str = Field(max_length=32)
    interval: str | None = Field(None, pattern=r"^(month|year)$")


@router.post("/{org_id}/subscription/change")
def change_subscription(org_id: str, body: PlanChange, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    org_access(db, c.user, org_id, "subscriptions.manage", c.now)
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
    if sub is None:
        raise HTTPException(404, "geen abonnement")
    result = billing.change_plan(db, sub, body.plan, body.interval, c.now)
    audit(db, c.now, "subscription.changed", actor=c.user.id, org_id=org_id, plan=body.plan, result=result, ip=c.ip)
    db.commit()
    return {"result": result, "subscription": billing.subscription_dict(sub)}


@router.post("/{org_id}/subscription/cancel")
def cancel_subscription(org_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    org_access(db, c.user, org_id, "subscriptions.manage", c.now)
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
    sub.cancel_at_period_end = True
    audit(db, c.now, "subscription.cancel_requested", actor=c.user.id, org_id=org_id, ip=c.ip)
    db.commit()
    return {"subscription": billing.subscription_dict(sub),
            "note": "Het abonnement loopt tot het einde van de periode. Het lokale EMS blijft daarna gewoon werken."}


@router.post("/{org_id}/subscription/resume")
def resume_subscription(org_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    org_access(db, c.user, org_id, "subscriptions.manage", c.now)
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
    if sub.status == "expired":
        raise HTTPException(409, "abonnement is verlopen; verlengen via betaling")
    sub.cancel_at_period_end = False
    db.commit()
    return {"subscription": billing.subscription_dict(sub)}


# ------------------------------------------------------------- audit
@router.get("/{org_id}/audit")
def org_audit(org_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> list[dict]:
    org_access(db, c.user, org_id, "audit.read", c.now)
    rows = db.execute(select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_user_id)
                      .where(AuditLog.org_id == org_id).order_by(AuditLog.ts.desc()).limit(200)).all()
    return [{"ts": iso(a.ts), "actor": email or a.actor_kind, "action": a.action, "target": a.target, "ip": a.ip,
             "details": a.details} for a, email in rows]
