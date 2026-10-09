"""Request context: database session, clock, current user (session) and current node (node token)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from emcloud.authz import ADMIN, OWNER
from emcloud.db import AuthSession, Membership, Node, Organization, User
from emcloud.security import same, token_hash

SESSION_COOKIE = "emc_session"
CSRF_COOKIE = "emc_csrf"
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.sessionmaker()
    try:
        yield db
    finally:
        db.close()


def now(request: Request) -> float:
    return request.app.state.clock()


def client_ip(request: Request) -> str:
    if request.app.state.settings.trusted_proxies:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def limit(request: Request, key: str, n: int, window_s: float) -> None:
    if not request.app.state.limiter.hit(key, n, window_s):
        raise HTTPException(429, "te veel pogingen; probeer het later opnieuw")


@dataclass
class Ctx:
    user: User
    session: AuthSession
    now: float
    ip: str


def mfa_required(db: Session, user: User, settings) -> bool:
    if user.is_platform_admin and settings.require_mfa_platform_admin:
        return True
    return db.scalar(select(Membership.id).join(Organization, Organization.id == Membership.org_id).where(
        Membership.user_id == user.id, Membership.role.in_((OWNER, ADMIN)), Organization.require_mfa.is_(True))) is not None


def _session(request: Request, db: Session, t: float) -> tuple[AuthSession, bool]:
    auth = request.headers.get("authorization", "")
    via_cookie = False
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    else:
        token = request.cookies.get(SESSION_COOKIE, "")
        via_cookie = True
    if not token:
        raise HTTPException(401, "niet ingelogd")
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    s = request.app.state.settings
    if (sess is None or sess.revoked or t >= sess.expires_ts
            or t - sess.last_seen_ts > s.session_idle_hours * 3600):
        raise HTTPException(401, "sessie verlopen; log opnieuw in")
    return sess, via_cookie


def _ctx(request: Request, db: Session, allow_pending: bool, allow_mfa_setup: bool) -> Ctx:
    t = now(request)
    sess, via_cookie = _session(request, db, t)
    if via_cookie and request.method in UNSAFE:
        hdr = request.headers.get("x-csrf-token", "")
        if not hdr or not same(token_hash(hdr), sess.csrf_hash):
            raise HTTPException(403, "CSRF-controle mislukt")
    user = db.get(User, sess.user_id)
    if user is None or user.deleted_ts is not None:
        raise HTTPException(401, "account bestaat niet meer")
    if sess.mfa_pending and not allow_pending:
        raise HTTPException(401, "tweede stap (MFA) nog niet voltooid")
    if not allow_mfa_setup and not user.mfa_enabled and mfa_required(db, user, request.app.state.settings):
        raise HTTPException(403, "MFA verplicht: stel eerst tweestapsverificatie in")
    if t - sess.last_seen_ts > 60:
        sess.last_seen_ts = t
        db.commit()
    return Ctx(user, sess, t, client_ip(request))


def ctx(request: Request, db: Session = Depends(get_db)) -> Ctx:
    """Logged in, MFA done, MFA set up if required."""
    return _ctx(request, db, allow_pending=False, allow_mfa_setup=False)


def ctx_mfa_setup(request: Request, db: Session = Depends(get_db)) -> Ctx:
    """Logged in (MFA done) but MFA setup may still be outstanding: account and MFA setup endpoints."""
    return _ctx(request, db, allow_pending=False, allow_mfa_setup=True)


def ctx_pending(request: Request, db: Session = Depends(get_db)) -> Ctx:
    """Password accepted, second factor still needed."""
    return _ctx(request, db, allow_pending=True, allow_mfa_setup=True)


def platform_admin(c: Ctx = Depends(ctx)) -> Ctx:
    if not c.user.is_platform_admin:
        raise HTTPException(403, "alleen voor platformbeheer")
    return c


@dataclass
class NodeCtx:
    node: Node
    now: float
    ip: str
    token_is_previous: bool


def node_ctx(request: Request, db: Session = Depends(get_db)) -> NodeCtx:
    """A node authenticates with its own token (``Authorization: Bearer emn_...``); the previous token
    stays valid for a short grace period after a rotation."""
    t = now(request)
    limit(request, f"node:{client_ip(request)}", 600, 60)
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token.startswith("emn_"):
        raise HTTPException(401, "node-token ontbreekt")
    h = token_hash(token)
    node = db.scalar(select(Node).where(or_(Node.token_hash == h, Node.prev_token_hash == h)))
    if node is None or node.revoked:
        raise HTTPException(401, "node onbekend of ontkoppeld")
    previous = node.token_hash != h
    if previous and t > node.prev_token_valid_until:
        raise HTTPException(401, "node-token verlopen (vervangen)")
    org = db.get(Organization, node.org_id)
    if org is None or org.status == "deleted":
        raise HTTPException(401, "organisatie bestaat niet meer")
    return NodeCtx(node, t, client_ip(request), previous)
