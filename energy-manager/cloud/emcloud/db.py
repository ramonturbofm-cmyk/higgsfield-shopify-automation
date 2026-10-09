"""Multi-tenant data model. Every tenant-owned row carries ``org_id``; queries for tenant data always
filter on it (see ``emcloud.authz``). Times are UTC epoch seconds. Secrets are stored only as hashes
(session/node/e-mail tokens, pairing codes) or encrypted (MFA secrets)."""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

SCHEMA_VERSION = 1


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")          # active | suspended | deleted
    require_mfa: Mapped[bool] = mapped_column(Boolean, default=False)           # MFA for owners/admins
    sync_consent: Mapped[bool] = mapped_column(Boolean, default=False)          # telemetry sync allowed (AVG)
    share_usage_with_platform: Mapped[bool] = mapped_column(Boolean, default=False)
    data_region: Mapped[str] = mapped_column(String(20), default="eu")
    created_ts: Mapped[float] = mapped_column(Float)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)   # lower-case
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)   # None = passwordless only
    email_verified_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfa_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_last_step: Mapped[int] = mapped_column(Integer, default=0)            # replay protection
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[float] = mapped_column(Float, default=0)
    created_ts: Mapped[float] = mapped_column(Float)
    deleted_ts: Mapped[float | None] = mapped_column(Float, nullable=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    site_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)        # None = all sites of the org
    created_ts: Mapped[float] = mapped_column(Float)


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Amsterdam")
    remote_control_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_ts: Mapped[float] = mapped_column(Float)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(40), default="")
    platform: Mapped[str] = mapped_column(String(80), default="")
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    prev_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    prev_token_valid_until: Mapped[float] = mapped_column(Float, default=0)
    token_rotated_ts: Mapped[float] = mapped_column(Float)
    paired_ts: Mapped[float] = mapped_column(Float)
    last_seen_ts: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[dict] = mapped_column(JSON, default=dict)                  # mode, health, errors (no usage data)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Device(Base):
    """Device metadata reported by a node (name/category/online) — no measurements."""
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("node_id", "local_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    local_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(40), default="")
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_ts: Mapped[float] = mapped_column(Float, default=0)


class Telemetry(Base):
    """Optional summary data, only stored when the organisation consented (``sync_consent``)."""
    __tablename__ = "telemetry"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class PairingCode(Base):
    __tablename__ = "pairing_codes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    created_by: Mapped[str] = mapped_column(String(32))
    expires_ts: Mapped[float] = mapped_column(Float)
    used_ts: Mapped[float | None] = mapped_column(Float, nullable=True)


class Plan(Base):
    """Configurable plan. Prices are data (set by the platform admin), never constants in code."""
    __tablename__ = "plans"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)            # BASIC | PRO | BUSINESS | ENTERPRISE
    name: Mapped[str] = mapped_column(String(100))
    price_month_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_year_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    entitlements: Mapped[list] = mapped_column(JSON, default=list)
    max_sites: Mapped[int | None] = mapped_column(Integer, nullable=True)     # None = unlimited
    max_nodes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    plan_key: Mapped[str] = mapped_column(ForeignKey("plans.key"))
    pending_plan_key: Mapped[str | None] = mapped_column(String(32), nullable=True)   # downgrade at period end
    interval: Mapped[str] = mapped_column(String(10), default="month")      # month | year
    status: Mapped[str] = mapped_column(String(20))                          # trialing|active|past_due|canceled|expired
    trial_end_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_period_end_ts: Mapped[float] = mapped_column(Float)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    past_due_since_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(30), default="manual")      # payment provider (later: mollie/stripe)
    provider_ref: Mapped[str] = mapped_column(String(120), default="")
    created_ts: Mapped[float] = mapped_column(Float)


class License(Base):
    __tablename__ = "licenses"
    license_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    plan_key: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20))                          # active | trial | suspended | expired
    activated_ts: Mapped[float] = mapped_column(Float)
    expires_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_sites: Mapped[int | None] = mapped_column(Integer, nullable=True)     # overrides of the plan
    max_nodes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_entitlements: Mapped[list] = mapped_column(JSON, default=list)
    renewal: Mapped[str] = mapped_column(String(20), default="subscription")  # subscription | manual


class Invoice(Base):
    """Invoice record only — payment details stay with the payment provider (no card data here)."""
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    period_start_ts: Mapped[float] = mapped_column(Float)
    period_end_ts: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="open")          # open | paid | void
    provider_ref: Mapped[str] = mapped_column(String(120), default="")
    created_ts: Mapped[float] = mapped_column(Float)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(32))
    site_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expires_ts: Mapped[float] = mapped_column(Float)
    accepted_ts: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_ts: Mapped[float] = mapped_column(Float)
    last_seen_ts: Mapped[float] = mapped_column(Float)
    expires_ts: Mapped[float] = mapped_column(Float)
    mfa_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailToken(Base):
    __tablename__ = "email_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))                         # verify | reset | magic
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_ts: Mapped[float] = mapped_column(Float)
    used_ts: Mapped[float | None] = mapped_column(Float, nullable=True)


class SupportGrant(Base):
    """Temporary support access, explicitly granted by the customer. Expires automatically."""
    __tablename__ = "support_grants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[str | None] = mapped_column(String(32), nullable=True)     # None = all sites
    grantee_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(10), default="read")          # read | control
    granted_by: Mapped[str] = mapped_column(String(32))
    created_ts: Mapped[float] = mapped_column(Float)
    expires_ts: Mapped[float] = mapped_column(Float)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class RemoteCommand(Base):
    __tablename__ = "remote_commands"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    requested_by: Mapped[str] = mapped_column(String(32))
    command: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")        # queued|sent|accepted|rejected|failed|expired
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_ts: Mapped[float] = mapped_column(Float)
    expires_ts: Mapped[float] = mapped_column(Float)
    acked_ts: Mapped[float | None] = mapped_column(Float, nullable=True)


class Outbox(Base):
    """Development mail outbox (``EMC_MAIL_MODE=outbox``). Production uses SMTP."""
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    to: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    ts: Mapped[float] = mapped_column(Float)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(20), default="user")      # user | node | system | platform_admin
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str] = mapped_column(String(200), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Meta(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


def make_engine(url: str):
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    engine = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def init_db(engine) -> sessionmaker:
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker() as s:
        row = s.get(Meta, "schema_version")
        if row is None:
            s.add(Meta(key="schema_version", value=str(SCHEMA_VERSION)))
            s.commit()
        elif int(row.value) > SCHEMA_VERSION:
            raise RuntimeError(f"database schema {row.value} is newer than this software ({SCHEMA_VERSION})")
    return maker
