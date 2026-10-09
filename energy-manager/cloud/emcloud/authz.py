"""Roles, permissions and tenant isolation.

* Every organisation-scoped request goes through ``org_access``: the caller needs a membership of that
  organisation (optionally limited to some sites) or an active, customer-granted support grant.
  Without either the answer is 404 — the existence of another tenant's data is not revealed.
* PLATFORM_ADMIN is a platform role (``User.is_platform_admin``): it opens the admin portal with
  aggregate data, *not* customer data or hardware. A platform admin who needs to help a customer
  needs a support grant like any support engineer.
* Role changes cannot escalate: nobody assigns a role above their own, only owners assign OWNER,
  and the last owner cannot be removed or demoted.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from emcloud.db import Membership, Organization, Site, SupportGrant, User

PLATFORM_ADMIN = "PLATFORM_ADMIN"
OWNER, ADMIN, INSTALLER, OPERATOR, VIEWER, SUPPORT = (
    "ORGANIZATION_OWNER", "ORGANIZATION_ADMIN", "INSTALLER", "OPERATOR", "VIEWER", "SUPPORT")
ORG_ROLES = (OWNER, ADMIN, INSTALLER, OPERATOR, VIEWER)
RANK = {VIEWER: 1, SUPPORT: 1, OPERATOR: 2, INSTALLER: 3, ADMIN: 4, OWNER: 5}

PERMISSIONS = ("org.read", "org.manage", "org.delete", "sites.read", "sites.manage", "devices.read",
               "devices.control", "tariffs.manage", "users.read", "users.invite", "users.manage",
               "subscriptions.read", "subscriptions.manage", "remote_access.grant", "nodes.pair", "audit.read")

_READ = {"org.read", "sites.read", "devices.read"}
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    OWNER: frozenset(PERMISSIONS),
    ADMIN: frozenset(set(PERMISSIONS) - {"org.delete", "subscriptions.manage"}),
    INSTALLER: frozenset(_READ | {"sites.manage", "devices.control", "tariffs.manage", "nodes.pair"}),
    OPERATOR: frozenset(_READ | {"devices.control"}),
    VIEWER: frozenset(_READ),
    SUPPORT: frozenset(_READ),
}


@dataclass
class Access:
    org: Organization
    role: str
    permissions: frozenset[str]
    site_ids: list[str] | None          # None = all sites
    via_support: bool = False

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    def site_allowed(self, site_id: str) -> bool:
        return self.site_ids is None or site_id in self.site_ids


def not_found() -> HTTPException:
    return HTTPException(404, "niet gevonden")


def org_access(db: Session, user: User, org_id: str, perm: str, now: float, site_id: str | None = None) -> Access:
    org = db.get(Organization, org_id)
    if org is None or org.status == "deleted":
        raise not_found()
    m = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.org_id == org_id))
    if m is not None:
        acc = Access(org, m.role, ROLE_PERMISSIONS[m.role], m.site_ids)
    else:
        grants = db.scalars(select(SupportGrant).where(
            SupportGrant.org_id == org_id, SupportGrant.grantee_user_id == user.id,
            SupportGrant.revoked.is_(False), SupportGrant.expires_ts > now)).all()
        if not grants:
            raise not_found()
        perms = set(ROLE_PERMISSIONS[SUPPORT])
        if any(g.scope == "control" for g in grants):
            perms.add("devices.control")
        sites = None if any(g.site_id is None for g in grants) else sorted({g.site_id for g in grants})
        acc = Access(org, SUPPORT, frozenset(perms), sites, via_support=True)
    if org.status == "suspended" and perm not in _READ | {"subscriptions.read", "subscriptions.manage", "users.read"}:
        raise HTTPException(403, "organisatie is opgeschort")
    if site_id is not None:
        site = db.get(Site, site_id)
        if site is None or site.org_id != org_id or not acc.site_allowed(site_id):
            raise not_found()
    if not acc.can(perm):
        raise HTTPException(403, f"geen recht: {perm}")
    return acc


def check_role_change(actor: Access, new_role: str, target_current: str | None = None) -> None:
    if new_role not in ORG_ROLES:
        raise HTTPException(422, "onbekende rol")
    if actor.via_support:
        raise HTTPException(403, "supporttoegang kan geen rollen toekennen")
    if new_role == OWNER and actor.role != OWNER:
        raise HTTPException(403, "alleen een eigenaar kan iemand eigenaar maken")
    if RANK[new_role] > RANK[actor.role]:
        raise HTTPException(403, "u kunt geen hogere rol toekennen dan uw eigen rol")
    if target_current is not None and RANK[target_current] > RANK[actor.role]:
        raise HTTPException(403, "u kunt de rol van iemand met een hogere rol niet wijzigen")
