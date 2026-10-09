"""Plans, licences and subscriptions.

* Plans and prices are data (table ``plans``), seeded without prices from ``plans_default.json``.
* A licence is valid while ``status`` is active/trial and it has not expired. An invalid licence only
  removes paid cloud features (``FREE_ENTITLEMENTS`` stay: see whether the installation is online).
  The local EMS — safety, regulation, optimizer, fallback — never depends on the licence.
* Subscription lifecycle: trialing → active → (past_due → grace) → expired, with cancel at period end,
  renew, upgrade (immediate) and downgrade (at the end of the period). Payments: ``PaymentProvider``;
  ``ManualPaymentProvider`` until a professional provider (e.g. Mollie or Stripe) is connected — no
  card data is ever stored here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emcloud.db import Invoice, License, Node, Plan, Site, Subscription

FREE_ENTITLEMENTS = frozenset({"cloud_status"})
DAY = 86400.0
PERIOD = {"month": 30 * DAY, "year": 365 * DAY}
PAST_DUE_GRACE = 14 * DAY


def seed_plans(db: Session) -> None:
    data = json.loads((Path(__file__).with_name("plans_default.json")).read_text(encoding="utf-8"))
    for p in data["plans"]:
        if db.get(Plan, p["key"]) is None:
            db.add(Plan(key=p["key"], name=p["name"], rank=p["rank"], max_sites=p["max_sites"], max_nodes=p["max_nodes"],
                        entitlements=p["entitlements"], price_month_cents=None, price_year_cents=None))
    db.commit()


def plan_dict(p: Plan) -> dict:
    return {"key": p.key, "name": p.name, "rank": p.rank, "price_month_cents": p.price_month_cents,
            "price_year_cents": p.price_year_cents, "currency": p.currency, "entitlements": p.entitlements,
            "max_sites": p.max_sites, "max_nodes": p.max_nodes, "active": p.active,
            "price_set": p.price_month_cents is not None or p.price_year_cents is not None}


def license_valid(lic: License | None, now: float) -> bool:
    return lic is not None and lic.status in ("active", "trial") and (lic.expires_ts is None or lic.expires_ts > now)


def entitlements(db: Session, org_id: str, now: float) -> frozenset[str]:
    lic = db.scalar(select(License).where(License.org_id == org_id))
    if not license_valid(lic, now):
        return FREE_ENTITLEMENTS
    plan = db.get(Plan, lic.plan_key)
    return frozenset(set(plan.entitlements if plan else []) | set(lic.extra_entitlements or []) | FREE_ENTITLEMENTS)


def require(db: Session, org_id: str, feature: str, now: float) -> None:
    if feature not in entitlements(db, org_id, now):
        raise HTTPException(402, f"uw licentie of abonnement omvat '{feature}' niet (of is verlopen)")


def limits(db: Session, org_id: str) -> tuple[int | None, int | None]:
    lic = db.scalar(select(License).where(License.org_id == org_id))
    plan = db.get(Plan, lic.plan_key) if lic else None
    ms = lic.max_sites if lic and lic.max_sites is not None else (plan.max_sites if plan else 1)
    mn = lic.max_nodes if lic and lic.max_nodes is not None else (plan.max_nodes if plan else 1)
    return ms, mn


def check_site_limit(db: Session, org_id: str) -> None:
    ms, _ = limits(db, org_id)
    n = db.scalar(select(func.count()).select_from(Site).where(Site.org_id == org_id))
    if ms is not None and n >= ms:
        raise HTTPException(402, f"maximaal {ms} locatie(s) in uw abonnement")


def check_node_limit(db: Session, org_id: str) -> None:
    _, mn = limits(db, org_id)
    n = db.scalar(select(func.count()).select_from(Node).where(Node.org_id == org_id, Node.revoked.is_(False)))
    if mn is not None and n >= mn:
        raise HTTPException(402, f"maximaal {mn} installatie(s) in uw abonnement")


def license_dict(lic: License | None, now: float) -> dict | None:
    if lic is None:
        return None
    return {"license_id": lic.license_id, "organization_id": lic.org_id, "plan": lic.plan_key, "status": lic.status,
            "valid": license_valid(lic, now), "activated": iso(lic.activated_ts), "expires": iso(lic.expires_ts),
            "max_sites": lic.max_sites, "max_nodes": lic.max_nodes, "extra_entitlements": lic.extra_entitlements,
            "renewal": lic.renewal}


def iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).isoformat()


# ------------------------------------------------------------- subscriptions
def start(db: Session, org_id: str, plan_key: str, now: float, *, trial_days: int = 0, interval: str = "month",
          status: str | None = None, max_sites=None, max_nodes=None) -> Subscription:
    if db.get(Plan, plan_key) is None:
        raise HTTPException(422, "onbekend abonnement")
    trial = trial_days > 0
    end = now + (trial_days * DAY if trial else PERIOD[interval])
    sub = Subscription(org_id=org_id, plan_key=plan_key, interval=interval, status=status or ("trialing" if trial else "active"),
                       trial_end_ts=end if trial else None, current_period_end_ts=end, created_ts=now)
    db.add(sub)
    db.add(License(org_id=org_id, plan_key=plan_key, status="trial" if trial else "active", activated_ts=now,
                   expires_ts=end, max_sites=max_sites, max_nodes=max_nodes, extra_entitlements=[]))
    return sub


def sync_license(db: Session, sub: Subscription) -> License:
    lic = db.scalar(select(License).where(License.org_id == sub.org_id))
    if lic.renewal == "manual":
        return lic
    lic.plan_key = sub.plan_key
    lic.status = {"trialing": "trial", "active": "active", "past_due": "active", "canceled": "active",
                  "expired": "expired"}[sub.status]
    lic.expires_ts = sub.current_period_end_ts + (PAST_DUE_GRACE if sub.status == "past_due" else 0)
    return lic


def change_plan(db: Session, sub: Subscription, plan_key: str, interval: str | None, now: float) -> str:
    new, old = db.get(Plan, plan_key), db.get(Plan, sub.plan_key)
    if new is None or not new.active:
        raise HTTPException(422, "onbekend of niet beschikbaar abonnement")
    if interval:
        sub.interval = interval
    if new.rank >= old.rank:                       # upgrade (or same plan): immediately
        sub.plan_key, sub.pending_plan_key = plan_key, None
        result = "upgraded"
    else:                                          # downgrade: at the end of the paid period
        sub.pending_plan_key = plan_key
        result = "downgrade_scheduled"
    sub.cancel_at_period_end = False
    sync_license(db, sub)
    return result


def renew(db: Session, sub: Subscription, now: float, provider_ref: str = "") -> Invoice | None:
    """A paid renewal (payment provider webhook or manual): new period, apply a scheduled downgrade."""
    if sub.pending_plan_key:
        sub.plan_key, sub.pending_plan_key = sub.pending_plan_key, None
    start_ts = max(now, sub.current_period_end_ts) if sub.status != "expired" else now
    sub.current_period_end_ts = start_ts + PERIOD[sub.interval]
    sub.status, sub.past_due_since_ts, sub.trial_end_ts, sub.cancel_at_period_end = "active", None, None, False
    sync_license(db, sub)
    plan = db.get(Plan, sub.plan_key)
    amount = plan.price_month_cents if sub.interval == "month" else plan.price_year_cents
    if amount is None:                             # no price configured: no invoice is invented
        return None
    n = db.scalar(select(func.count()).select_from(Invoice)) + 1
    inv = Invoice(org_id=sub.org_id, number=f"EMC-{datetime.fromtimestamp(now, UTC):%Y}-{n:06d}", amount_cents=amount,
                  currency=plan.currency, period_start_ts=start_ts, period_end_ts=sub.current_period_end_ts,
                  status="paid" if provider_ref else "open", provider_ref=provider_ref, created_ts=now)
    db.add(inv)
    return inv


def mark_past_due(db: Session, sub: Subscription, now: float) -> None:
    sub.status, sub.past_due_since_ts = "past_due", now
    sync_license(db, sub)


def tick(db: Session, now: float) -> int:
    """Process period ends: trial over or cancellation effective -> expired; a paid period that was not
    renewed -> past_due (arrears) with a grace period; grace over -> expired."""
    changed = 0
    for sub in db.scalars(select(Subscription).where(Subscription.status.in_(("trialing", "active", "past_due", "canceled")))):
        if sub.status == "past_due":
            if now >= sub.current_period_end_ts + PAST_DUE_GRACE:
                sub.status = "expired"
            else:
                continue
        elif now >= sub.current_period_end_ts:
            if sub.status in ("trialing", "canceled") or sub.cancel_at_period_end:
                sub.status = "expired"
            else:
                sub.status, sub.past_due_since_ts = "past_due", now
        else:
            continue
        sync_license(db, sub)
        changed += 1
    return changed


def subscription_dict(sub: Subscription | None) -> dict | None:
    if sub is None:
        return None
    return {"plan": sub.plan_key, "pending_plan": sub.pending_plan_key, "interval": sub.interval, "status": sub.status,
            "trial_end": iso(sub.trial_end_ts), "current_period_end": iso(sub.current_period_end_ts),
            "cancel_at_period_end": sub.cancel_at_period_end, "payment_provider": sub.provider}


class PaymentProvider:
    """Interface for a professional payment provider (hosted checkout + signed webhooks)."""
    name = "abstract"

    def checkout_url(self, sub: Subscription, plan: Plan) -> str | None:
        raise NotImplementedError


class ManualPaymentProvider(PaymentProvider):
    """No online payments: the platform admin renews/marks invoices. Stores no payment data."""
    name = "manual"

    def checkout_url(self, sub: Subscription, plan: Plan) -> str | None:
        return None
