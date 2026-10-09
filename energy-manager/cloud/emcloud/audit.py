"""Audit log: who did what, when, from where. Stored per organisation; retention ``audit_retention_days``."""

from __future__ import annotations

from sqlalchemy.orm import Session

from emcloud.db import AuditLog


def audit(db: Session, now: float, action: str, *, actor: str | None = None, actor_kind: str = "user",
          org_id: str | None = None, target: str = "", ip: str = "", **details) -> None:
    db.add(AuditLog(ts=now, actor_user_id=actor, actor_kind=actor_kind, org_id=org_id, action=action,
                    target=target[:200], ip=ip[:64], details=details))
