"""Platform admin portal: aggregate numbers, customers, plans and licences.

Deliberately *not* here: customers' sites, devices, measurements, consumption or remote control. A
platform admin who has to help a customer needs a support grant from that customer like anyone else.
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emcloud import billing
from emcloud.audit import audit
from emcloud.authz import OWNER
from emcloud.billing import DAY, iso
from emcloud.db import AuditLog, Invitation, License, Membership, Node, Organization, Plan, Site, Subscription, User
from emcloud.deps import Ctx, get_db, platform_admin
from emcloud.security import new_token, token_hash

router = APIRouter(prefix="/api/v1/admin", tags=["platform admin"])


@router.get("/overview")
def overview(request: Request, c: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    t = c.now
    hb = request.app.state.settings.node_heartbeat_s * 3
    nodes = db.scalars(select(Node).where(Node.revoked.is_(False))).all()
    subs = db.scalars(select(Subscription)).all()
    lics = db.scalars(select(License)).all()
    users = db.scalars(select(User).where(User.deleted_ts.is_(None))).all()
    online = [n for n in nodes if t - n.last_seen_ts <= hb]
    valid = [x for x in lics if billing.license_valid(x, t)]
    latest = request.app.state.latest_ems_version
    return {
        "organizations": db.scalar(select(func.count()).select_from(Organization).where(Organization.status != "deleted")),
        "users": len(users), "sites": db.scalar(select(func.count()).select_from(Site)),
        "installations": {"total": len(nodes), "online": len(online), "offline": len(nodes) - len(online)},
        "versions": dict(Counter(n.version or "onbekend" for n in nodes)),
        "updates": {"latest_known": latest, "outdated": sum(1 for n in nodes if latest and n.version and n.version != latest)},
        "subscriptions": {"by_status": dict(Counter(s.status for s in subs)), "by_plan": dict(Counter(s.plan_key for s in subs))},
        "licenses": {"valid": len(valid), "invalid": len(lics) - len(valid),
                     "expiring_30d": sum(1 for x in valid if x.expires_ts and x.expires_ts - t < 30 * DAY)},
        "registrations": {"7d": sum(1 for u in users if t - u.created_ts < 7 * DAY),
                          "30d": sum(1 for u in users if t - u.created_ts < 30 * DAY)},
        "installations_with_errors": sum(1 for n in nodes if (n.status or {}).get("errors")),
        "privacy": "Geen verbruiks- of meetgegevens van klanten in dit overzicht.",
    }


@router.get("/organizations")
def organizations(c: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> list[dict]:
    out = []
    for o in db.scalars(select(Organization).where(Organization.status != "deleted").order_by(Organization.created_ts.desc())):
        sub = db.scalar(select(Subscription).where(Subscription.org_id == o.id))
        lic = db.scalar(select(License).where(License.org_id == o.id))
        owner = db.scalar(select(User.email).join(Membership, Membership.user_id == User.id)
                          .where(Membership.org_id == o.id, Membership.role == OWNER).limit(1))
        out.append({"id": o.id, "name": o.name, "status": o.status, "owner_email": owner, "created": iso(o.created_ts),
                    "plan": sub.plan_key if sub else None, "subscription_status": sub.status if sub else None,
                    "license": billing.license_dict(lic, c.now),
                    "sites": db.scalar(select(func.count()).select_from(Site).where(Site.org_id == o.id)),
                    "installations": db.scalar(select(func.count()).select_from(Node).where(Node.org_id == o.id, Node.revoked.is_(False)))})
    return out


class CustomerIn(BaseModel):
    organization: str = Field(min_length=1, max_length=200)
    email: EmailStr
    plan: str = "BASIC"
    max_sites: int | None = Field(None, ge=1, le=10000)
    max_nodes: int | None = Field(None, ge=1, le=100000)
    trial_days: int = Field(0, ge=0, le=365)
    status: str = Field("active", pattern=r"^(active|suspended)$")


@router.post("/customers")
def add_customer(body: CustomerIn, request: Request, c: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    """New customer: organisation + licence/subscription + a secure invitation for the owner."""
    org = Organization(name=body.organization, status=body.status, created_ts=c.now)
    db.add(org)
    db.flush()
    billing.start(db, org.id, body.plan, c.now, trial_days=body.trial_days, max_sites=body.max_sites, max_nodes=body.max_nodes)
    token = new_token()
    email = body.email.strip().lower()
    db.add(Invitation(org_id=org.id, email=email, role=OWNER, token_hash=token_hash(token), invited_by=c.user.id,
                      expires_ts=c.now + 7 * DAY))
    request.app.state.mailer.send(db, c.now, email, "Uw Energy Manager Cloud-account",
                                  f"Er is een account voor '{body.organization}' voor u klaargezet.\n\n"
                                  f"{request.app.state.settings.base_url}/#/invite/{token}\n\nDe link is 7 dagen geldig.")
    audit(db, c.now, "admin.customer_created", actor=c.user.id, actor_kind="platform_admin", org_id=org.id,
          target=body.organization, plan=body.plan, ip=c.ip)
    db.commit()
    return {"organization_id": org.id}


class OrgAdminPatch(BaseModel):
    status: str | None = Field(None, pattern=r"^(active|suspended)$")


@router.patch("/organizations/{org_id}")
def patch_org(org_id: str, body: OrgAdminPatch, c: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    org = db.get(Organization, org_id)
    if org is None or org.status == "deleted":
        raise HTTPException(404, "niet gevonden")
    if body.status:
        org.status = body.status
    audit(db, c.now, "admin.organization_updated", actor=c.user.id, actor_kind="platform_admin", org_id=org_id,
          status=body.status, ip=c.ip)
    db.commit()
    return {"ok": True}


class LicensePatch(BaseModel):
    plan: str | None = None
    status: str | None = Field(None, pattern=r"^(active|trial|suspended|expired)$")
    expires_days: float | None = Field(None, gt=0, le=3650)
    max_sites: int | None = Field(None, ge=1)
    max_nodes: int | None = Field(None, ge=1)
    renewal: str | None = Field(None, pattern=r"^(subscription|manual)$")
    extra_entitlements: list[str] | None = None


@router.patch("/organizations/{org_id}/license")
def patch_license(org_id: str, body: LicensePatch, c: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    lic = db.scalar(select(License).where(License.org_id == org_id))
    if lic is None:
        raise HTTPException(404, "niet gevonden")
    if body.plan:
        if db.get(Plan, body.plan) is None:
            raise HTTPException(422, "onbekend abonnement")
        lic.plan_key = body.plan
    for k in ("status", "max_sites", "max_nodes", "renewal", "extra_entitlements"):
        if getattr(body, k) is not None:
            setattr(lic, k, getattr(body, k))
    if body.expires_days:
        lic.expires_ts = c.now + body.expires_days * DAY
    audit(db, c.now, "admin.license_updated", actor=c.user.id, actor_kind="platform_admin", org_id=org_id,
          **body.model_dump(exclude_none=True))
    db.commit()
    return billing.license_dict(lic, c.now)


@router.post("/organizations/{org_id}/subscription/renew")
def renew(org_id: str, c: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    """Manual payment received (until a payment provider is connected)."""
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org_id))
    if sub is None:
        raise HTTPException(404, "niet gevonden")
    inv = billing.renew(db, sub, c.now, provider_ref="manual")
    audit(db, c.now, "admin.subscription_renewed", actor=c.user.id, actor_kind="platform_admin", org_id=org_id)
    db.commit()
    return {"subscription": billing.subscription_dict(sub),
            "invoice": None if inv is None else {"number": inv.number, "amount_cents": inv.amount_cents}}


@router.get("/plans")
def plans(c: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> list[dict]:
    return [billing.plan_dict(p) for p in db.scalars(select(Plan).order_by(Plan.rank))]


class PlanPatch(BaseModel):
    name: str | None = Field(None, max_length=100)
    price_month_cents: int | None = Field(None, ge=0, le=10_000_000)
    price_year_cents: int | None = Field(None, ge=0, le=100_000_000)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    entitlements: list[str] | None = None
    max_sites: int | None = Field(None, ge=1)
    max_nodes: int | None = Field(None, ge=1)
    active: bool | None = None


@router.put("/plans/{key}")
def put_plan(key: str, body: PlanPatch, c: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    p = db.get(Plan, key)
    if p is None:
        raise HTTPException(404, "niet gevonden")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    audit(db, c.now, "admin.plan_updated", actor=c.user.id, actor_kind="platform_admin", target=key,
          **body.model_dump(exclude_unset=True))
    db.commit()
    return billing.plan_dict(p)


@router.get("/audit")
def admin_audit(c: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> list[dict]:
    """Platform-level actions only (admin actions, logins of admins); customer audit logs stay with the customer."""
    rows = db.scalars(select(AuditLog).where(AuditLog.actor_kind == "platform_admin").order_by(AuditLog.ts.desc()).limit(200))
    return [{"ts": iso(a.ts), "action": a.action, "target": a.target, "org_id": a.org_id} for a in rows]
