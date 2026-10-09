"""Accounts: registration, e-mail verification, login (password or e-mail link), MFA (TOTP), password reset,
sessions, data export and account deletion."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from emcloud import billing
from emcloud.audit import audit
from emcloud.authz import OWNER
from emcloud.billing import iso
from emcloud.db import AuditLog, AuthSession, EmailToken, Invitation, Membership, Organization, Site, SupportGrant, User
from emcloud.deps import CSRF_COOKIE, SESSION_COOKIE, Ctx, client_ip, ctx, ctx_mfa_setup, ctx_pending, get_db, limit, mfa_required, now
from emcloud.security import (
    check_password_policy,
    hash_password,
    needs_rehash,
    new_token,
    new_totp_secret,
    token_hash,
    totp_uri,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

TOKEN_TTL = {"verify": 48 * 3600, "reset": 3600, "magic": 15 * 60}
GENERIC_SENT = {"ok": True, "message": "Als dit adres bij ons bekend is, is er een e-mail verstuurd."}


def norm_email(email: str) -> str:
    return email.strip().lower()


def send_token(request: Request, db: Session, user: User, purpose: str, t: float) -> None:
    token = new_token()
    db.add(EmailToken(user_id=user.id, purpose=purpose, token_hash=token_hash(token), expires_ts=t + TOKEN_TTL[purpose]))
    base = request.app.state.settings.base_url
    subject, path = {"verify": ("Bevestig uw e-mailadres", "verify"), "reset": ("Wachtwoord opnieuw instellen", "reset"),
                     "magic": ("Inloggen bij Energy Manager Cloud", "magic")}[purpose]
    body = (f"Open deze link om door te gaan:\n\n{base}/#/{path}/{token}\n\n"
            f"De link is {TOKEN_TTL[purpose] // 60} minuten geldig en werkt één keer. "
            "Heeft u dit niet aangevraagd? Dan kunt u dit bericht negeren.")
    request.app.state.mailer.send(db, t, user.email, subject, body)


def use_token(db: Session, token: str, purpose: str, t: float) -> User:
    row = db.scalar(select(EmailToken).where(EmailToken.token_hash == token_hash(token or ""), EmailToken.purpose == purpose))
    if row is None or row.used_ts is not None or t >= row.expires_ts:
        raise HTTPException(400, "link ongeldig of verlopen")
    row.used_ts = t
    user = db.get(User, row.user_id)
    if user is None or user.deleted_ts is not None:
        raise HTTPException(400, "link ongeldig of verlopen")
    return user


def start_session(request: Request, response: Response, db: Session, user: User, t: float, mfa_pending: bool) -> dict:
    s = request.app.state.settings
    token, csrf = new_token("ems_"), new_token()
    sess = AuthSession(user_id=user.id, token_hash=token_hash(token), csrf_hash=token_hash(csrf), created_ts=t,
                       last_seen_ts=t, expires_ts=t + s.session_absolute_days * 86400, mfa_pending=mfa_pending,
                       ip=client_ip(request), user_agent=request.headers.get("user-agent", "")[:300])
    db.add(sess)
    secure = s.secure_cookies
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=secure, samesite="strict",
                        max_age=int(s.session_absolute_days * 86400), path="/")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=secure, samesite="strict", path="/")
    return {"token": token, "csrf": csrf, "mfa_required": mfa_pending, "session_id": sess.id}


def user_dict(db: Session, u: User, t: float) -> dict:
    rows = db.execute(select(Membership, Organization).join(Organization, Organization.id == Membership.org_id)
                      .where(Membership.user_id == u.id, Organization.status != "deleted")).all()
    return {"id": u.id, "email": u.email, "name": u.name, "email_verified": u.email_verified_ts is not None,
            "mfa_enabled": u.mfa_enabled, "platform_admin": u.is_platform_admin, "passwordless": u.password_hash is None,
            "organizations": [{"id": o.id, "name": o.name, "role": m.role, "status": o.status} for m, o in rows]}


# ------------------------------------------------------------------ register
class RegisterIn(BaseModel):
    email: EmailStr
    password: str | None = Field(None, max_length=256)    # None = passwordless (e-mail link)
    name: str = Field("", max_length=200)
    organization: str = Field("", max_length=200)


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    s = request.app.state.settings
    t = now(request)
    limit(request, f"register:{client_ip(request)}", 10, 3600)
    if not s.self_registration:
        raise HTTPException(403, "zelf registreren is uitgeschakeld; vraag een uitnodiging aan")
    email = norm_email(body.email)
    if body.password is not None and (msg := check_password_policy(body.password, email)):
        raise HTTPException(422, msg)
    if db.scalar(select(User).where(User.email == email)) is not None:
        # No account enumeration: same answer; the existing owner gets a notice instead.
        request.app.state.mailer.send(db, t, email, "Registratiepoging", "Er is geprobeerd een account aan te maken met "
                                      "uw e-mailadres. Was u dat? Gebruik dan 'wachtwoord vergeten'.")
        db.commit()
        return GENERIC_SENT
    user = User(email=email, name=body.name, password_hash=hash_password(body.password) if body.password else None,
                created_ts=t)
    db.add(user)
    db.flush()
    org = Organization(name=body.organization or (body.name or email.split("@")[0]), created_ts=t)
    db.add(org)
    db.flush()
    db.add(Membership(user_id=user.id, org_id=org.id, role=OWNER, created_ts=t))
    billing.start(db, org.id, s.trial_plan, t, trial_days=s.trial_days)
    send_token(request, db, user, "verify", t)
    audit(db, t, "user.registered", actor=user.id, org_id=org.id, ip=client_ip(request))
    db.commit()
    return GENERIC_SENT


class TokenIn(BaseModel):
    token: str = Field(max_length=200)


@router.post("/verify")
def verify_email(body: TokenIn, request: Request, db: Session = Depends(get_db)) -> dict:
    t = now(request)
    limit(request, f"verify:{client_ip(request)}", 30, 3600)
    user = use_token(db, body.token, "verify", t)
    user.email_verified_ts = user.email_verified_ts or t
    audit(db, t, "user.email_verified", actor=user.id)
    db.commit()
    return {"ok": True}


class EmailIn(BaseModel):
    email: EmailStr


@router.post("/verify/resend")
def resend_verification(body: EmailIn, request: Request, db: Session = Depends(get_db)) -> dict:
    t = now(request)
    limit(request, f"resend:{client_ip(request)}", 5, 3600)
    user = db.scalar(select(User).where(User.email == norm_email(body.email), User.deleted_ts.is_(None)))
    if user is not None and user.email_verified_ts is None:
        send_token(request, db, user, "verify", t)
        db.commit()
    return GENERIC_SENT


# --------------------------------------------------------------------- login
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    s = request.app.state.settings
    t = now(request)
    email = norm_email(body.email)
    limit(request, f"login-ip:{client_ip(request)}", 30, 300)
    limit(request, f"login-acct:{email}", 10, 300)
    user = db.scalar(select(User).where(User.email == email, User.deleted_ts.is_(None)))
    if user is not None and user.locked_until > t:
        verify_password(None, body.password)                 # same timing
        raise HTTPException(429, "account tijdelijk geblokkeerd na te veel mislukte pogingen")
    if user is None or not verify_password(user.password_hash, body.password):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= s.login_max_failures:
                user.locked_until, user.failed_logins = t + s.lockout_minutes * 60, 0
                audit(db, t, "user.locked", actor=user.id, ip=client_ip(request))
            audit(db, t, "user.login_failed", actor=user.id, ip=client_ip(request))
            db.commit()
        raise HTTPException(401, "e-mailadres of wachtwoord onjuist")
    if user.email_verified_ts is None:
        raise HTTPException(403, "bevestig eerst uw e-mailadres (zie de e-mail die we stuurden)")
    user.failed_logins = 0
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    out = start_session(request, response, db, user, t, mfa_pending=user.mfa_enabled)
    audit(db, t, "user.login", actor=user.id, ip=client_ip(request), mfa_pending=user.mfa_enabled)
    db.commit()
    return out


class CodeIn(BaseModel):
    code: str = Field(max_length=12)


@router.post("/mfa/verify")
def mfa_verify(body: CodeIn, request: Request, c: Ctx = Depends(ctx_pending), db: Session = Depends(get_db)) -> dict:
    limit(request, f"mfa:{c.user.id}", 5, 300)
    user, sess = db.get(User, c.user.id), db.get(AuthSession, c.session.id)
    if not user.mfa_enabled:
        raise HTTPException(400, "MFA staat niet aan")
    step = verify_totp(request.app.state.crypto.decrypt(user.mfa_secret_enc), body.code, user.mfa_last_step, c.now)
    if step is None:
        audit(db, c.now, "user.mfa_failed", actor=user.id, ip=c.ip)
        db.commit()
        raise HTTPException(401, "code onjuist")
    user.mfa_last_step, sess.mfa_pending = step, False
    db.commit()
    return {"ok": True}


@router.post("/magic-link")
def magic_link(body: EmailIn, request: Request, db: Session = Depends(get_db)) -> dict:
    """Passwordless login: a single-use link valid for 15 minutes."""
    t = now(request)
    email = norm_email(body.email)
    limit(request, f"magic-ip:{client_ip(request)}", 10, 3600)
    limit(request, f"magic:{email}", 3, 900)
    user = db.scalar(select(User).where(User.email == email, User.deleted_ts.is_(None)))
    if user is not None:
        send_token(request, db, user, "magic", t)
        db.commit()
    return GENERIC_SENT


@router.post("/magic-link/consume")
def magic_consume(body: TokenIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    t = now(request)
    limit(request, f"magic-use:{client_ip(request)}", 30, 3600)
    user = use_token(db, body.token, "magic", t)
    user.email_verified_ts = user.email_verified_ts or t           # the link proves control of the mailbox
    out = start_session(request, response, db, user, t, mfa_pending=user.mfa_enabled)
    audit(db, t, "user.login", actor=user.id, ip=client_ip(request), method="magic_link")
    db.commit()
    return out


@router.post("/password/forgot")
def forgot(body: EmailIn, request: Request, db: Session = Depends(get_db)) -> dict:
    t = now(request)
    email = norm_email(body.email)
    limit(request, f"forgot-ip:{client_ip(request)}", 10, 3600)
    limit(request, f"forgot:{email}", 3, 900)
    user = db.scalar(select(User).where(User.email == email, User.deleted_ts.is_(None)))
    if user is not None:
        send_token(request, db, user, "reset", t)
        db.commit()
    return GENERIC_SENT


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=256)


@router.post("/password/reset")
def reset(body: ResetIn, request: Request, db: Session = Depends(get_db)) -> dict:
    t = now(request)
    limit(request, f"reset:{client_ip(request)}", 20, 3600)
    row = db.scalar(select(EmailToken).where(EmailToken.token_hash == token_hash(body.token), EmailToken.purpose == "reset"))
    user = db.get(User, row.user_id) if row else None
    if user is not None and (msg := check_password_policy(body.password, user.email)):
        raise HTTPException(422, msg)
    user = use_token(db, body.token, "reset", t)
    user.password_hash = hash_password(body.password)
    user.failed_logins, user.locked_until = 0, 0
    user.email_verified_ts = user.email_verified_ts or t
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked.is_(False))):
        s.revoked = True                                        # every session ends after a reset
    audit(db, t, "user.password_reset", actor=user.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}


class ChangePasswordIn(BaseModel):
    current: str | None = Field(None, max_length=256)
    new: str = Field(max_length=256)


@router.post("/password/change")
def change_password(body: ChangePasswordIn, c: Ctx = Depends(ctx_mfa_setup), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, c.user.id)
    if user.password_hash is not None and not verify_password(user.password_hash, body.current or ""):
        raise HTTPException(401, "huidig wachtwoord onjuist")
    if msg := check_password_policy(body.new, user.email):
        raise HTTPException(422, msg)
    user.password_hash = hash_password(body.new)
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != c.session.id)):
        s.revoked = True
    audit(db, c.now, "user.password_changed", actor=user.id, ip=c.ip)
    db.commit()
    return {"ok": True}


@router.post("/logout")
def logout(response: Response, c: Ctx = Depends(ctx_pending), db: Session = Depends(get_db)) -> dict:
    db.get(AuthSession, c.session.id).revoked = True
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request, c: Ctx = Depends(ctx_mfa_setup), db: Session = Depends(get_db)) -> dict:
    out = user_dict(db, c.user, c.now)
    out["mfa_setup_required"] = not c.user.mfa_enabled and mfa_required(db, c.user, request.app.state.settings)
    return out


# ---------------------------------------------------------------- sessions
@router.get("/sessions")
def sessions(c: Ctx = Depends(ctx_mfa_setup), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(AuthSession).where(AuthSession.user_id == c.user.id, AuthSession.revoked.is_(False),
                                                AuthSession.expires_ts > c.now).order_by(AuthSession.last_seen_ts.desc()))
    return [{"id": s.id, "created": iso(s.created_ts), "last_seen": iso(s.last_seen_ts), "ip": s.ip,
             "user_agent": s.user_agent, "current": s.id == c.session.id} for s in rows]


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, c: Ctx = Depends(ctx_mfa_setup), db: Session = Depends(get_db)) -> dict:
    s = db.get(AuthSession, session_id)
    if s is None or s.user_id != c.user.id:
        raise HTTPException(404, "niet gevonden")
    s.revoked = True
    audit(db, c.now, "user.session_revoked", actor=c.user.id, target=session_id, ip=c.ip)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------- MFA
@router.post("/mfa/setup")
def mfa_setup(request: Request, c: Ctx = Depends(ctx_mfa_setup), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, c.user.id)
    if user.mfa_enabled:
        raise HTTPException(409, "MFA staat al aan")
    secret = new_totp_secret()
    user.mfa_secret_enc = request.app.state.crypto.encrypt(secret)
    db.commit()
    return {"secret": secret, "otpauth_uri": totp_uri(secret, user.email)}


@router.post("/mfa/enable")
def mfa_enable(body: CodeIn, request: Request, c: Ctx = Depends(ctx_mfa_setup), db: Session = Depends(get_db)) -> dict:
    limit(request, f"mfa:{c.user.id}", 5, 300)
    user = db.get(User, c.user.id)
    if not user.mfa_secret_enc:
        raise HTTPException(400, "start eerst de MFA-instelling")
    step = verify_totp(request.app.state.crypto.decrypt(user.mfa_secret_enc), body.code, user.mfa_last_step, c.now)
    if step is None:
        raise HTTPException(401, "code onjuist")
    user.mfa_enabled, user.mfa_last_step = True, step
    audit(db, c.now, "user.mfa_enabled", actor=user.id, ip=c.ip)
    db.commit()
    return {"ok": True}


@router.post("/mfa/disable")
def mfa_disable(body: CodeIn, request: Request, c: Ctx = Depends(ctx), db: Session = Depends(get_db)) -> dict:
    limit(request, f"mfa:{c.user.id}", 5, 300)
    user = db.get(User, c.user.id)
    if mfa_required(db, user, request.app.state.settings):
        raise HTTPException(403, "MFA is verplicht voor uw rol")
    step = verify_totp(request.app.state.crypto.decrypt(user.mfa_secret_enc or ""), body.code, user.mfa_last_step, c.now) \
        if user.mfa_secret_enc else None
    if step is None:
        raise HTTPException(401, "code onjuist")
    user.mfa_enabled, user.mfa_secret_enc = False, None
    audit(db, c.now, "user.mfa_disabled", actor=user.id, ip=c.ip)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------- invitations
class AcceptIn(BaseModel):
    token: str = Field(max_length=200)


@router.post("/invitations/accept")
def accept_invitation(body: AcceptIn, request: Request, c: Ctx = Depends(ctx_mfa_setup), db: Session = Depends(get_db)) -> dict:
    limit(request, f"invite:{c.user.id}", 20, 3600)
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(body.token)))
    if inv is None or inv.revoked or inv.accepted_ts is not None or c.now >= inv.expires_ts:
        raise HTTPException(400, "uitnodiging ongeldig of verlopen")
    if inv.email != c.user.email:
        raise HTTPException(403, "deze uitnodiging is voor een ander e-mailadres")
    if db.scalar(select(Membership).where(Membership.user_id == c.user.id, Membership.org_id == inv.org_id)) is None:
        db.add(Membership(user_id=c.user.id, org_id=inv.org_id, role=inv.role, site_ids=inv.site_ids, created_ts=c.now))
    inv.accepted_ts = c.now
    audit(db, c.now, "member.joined", actor=c.user.id, org_id=inv.org_id, target=c.user.email, role=inv.role, ip=c.ip)
    db.commit()
    return {"ok": True, "organization_id": inv.org_id}


@router.post("/invitations/register")
def register_by_invitation(body: ResetIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    """New user via invitation: the invitation proves the e-mail address; no self-registration needed."""
    t = now(request)
    limit(request, f"invite-reg:{client_ip(request)}", 20, 3600)
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(body.token)))
    if inv is None or inv.revoked or inv.accepted_ts is not None or t >= inv.expires_ts:
        raise HTTPException(400, "uitnodiging ongeldig of verlopen")
    if db.scalar(select(User).where(User.email == inv.email)) is not None:
        raise HTTPException(409, "er bestaat al een account; log in en accepteer de uitnodiging")
    if msg := check_password_policy(body.password, inv.email):
        raise HTTPException(422, msg)
    user = User(email=inv.email, password_hash=hash_password(body.password), email_verified_ts=t, created_ts=t)
    db.add(user)
    db.flush()
    db.add(Membership(user_id=user.id, org_id=inv.org_id, role=inv.role, site_ids=inv.site_ids, created_ts=t))
    inv.accepted_ts = t
    audit(db, t, "member.joined", actor=user.id, org_id=inv.org_id, target=user.email, role=inv.role, ip=client_ip(request))
    out = start_session(request, response, db, user, t, mfa_pending=False)
    db.commit()
    return out


# ------------------------------------------------------ export and deletion
@router.get("/me/export")
def export(c: Ctx = Depends(ctx_mfa_setup), db: Session = Depends(get_db)) -> dict:
    """AVG/GDPR: all personal data of this account (and the organisations it owns)."""
    u = c.user
    owned = [m.org_id for m in db.scalars(select(Membership).where(Membership.user_id == u.id, Membership.role == OWNER))]
    sites = db.scalars(select(Site).where(Site.org_id.in_(owned))).all() if owned else []
    return {
        "account": {"email": u.email, "name": u.name, "created": iso(u.created_ts), "email_verified": iso(u.email_verified_ts),
                    "mfa_enabled": u.mfa_enabled},
        "memberships": [{"organization_id": m.org_id, "role": m.role, "site_ids": m.site_ids, "since": iso(m.created_ts)}
                        for m in db.scalars(select(Membership).where(Membership.user_id == u.id))],
        "sessions": sessions(c, db),
        "owned_organizations": [{"id": o.id, "name": o.name, "sync_consent": o.sync_consent}
                                for o in db.scalars(select(Organization).where(Organization.id.in_(owned)))] if owned else [],
        "sites": [{"id": s.id, "org_id": s.org_id, "name": s.name, "remote_control_enabled": s.remote_control_enabled}
                  for s in sites],
        "audit_log": [{"ts": iso(a.ts), "action": a.action, "target": a.target, "ip": a.ip}
                      for a in db.scalars(select(AuditLog).where(AuditLog.actor_user_id == u.id).order_by(AuditLog.ts))],
    }


class DeleteIn(BaseModel):
    password: str | None = Field(None, max_length=256)
    confirm_email: str


@router.delete("/me")
def delete_account(body: DeleteIn, response: Response, c: Ctx = Depends(ctx_mfa_setup), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, c.user.id)
    if norm_email(body.confirm_email) != user.email:
        raise HTTPException(422, "typ uw e-mailadres ter bevestiging")
    if user.password_hash is not None and not verify_password(user.password_hash, body.password or ""):
        raise HTTPException(401, "wachtwoord onjuist")
    deleted_orgs = []
    for m in db.scalars(select(Membership).where(Membership.user_id == user.id, Membership.role == OWNER)).all():
        owners = db.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == m.org_id, Membership.role == OWNER))
        members = db.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == m.org_id))
        if owners == 1 and members > 1:
            raise HTTPException(409, "u bent de enige eigenaar van een organisatie met andere leden: draag het eigendom eerst over")
        if members == 1:
            deleted_orgs.append(m.org_id)
    for org_id in deleted_orgs:
        org = db.get(Organization, org_id)
        db.delete(org)                                           # cascades: sites, nodes, devices, telemetry, licence
    for m in db.scalars(select(Membership).where(Membership.user_id == user.id)).all():
        db.delete(m)
    for g in db.scalars(select(SupportGrant).where(SupportGrant.grantee_user_id == user.id)).all():
        db.delete(g)
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)).all():
        db.delete(s)
    for tkn in db.scalars(select(EmailToken).where(EmailToken.user_id == user.id)).all():
        db.delete(tkn)
    user.email, user.name = f"deleted-{user.id}@invalid", ""
    user.password_hash, user.mfa_secret_enc, user.mfa_enabled, user.deleted_ts = None, None, False, c.now
    audit(db, c.now, "user.deleted", actor=user.id, deleted_organizations=len(deleted_orgs))
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True, "deleted_organizations": deleted_orgs}


@router.get("/invitations/{token}")
def invitation_info(token: str, request: Request, db: Session = Depends(get_db)) -> dict:
    limit(request, f"invite-info:{client_ip(request)}", 30, 3600)
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)))
    t = now(request)
    if inv is None or inv.revoked or inv.accepted_ts is not None or t >= inv.expires_ts:
        raise HTTPException(404, "uitnodiging ongeldig of verlopen")
    org = db.get(Organization, inv.org_id)
    return {"organization": org.name, "email": inv.email, "role": inv.role,
            "account_exists": db.scalar(select(User.id).where(User.email == inv.email)) is not None}

