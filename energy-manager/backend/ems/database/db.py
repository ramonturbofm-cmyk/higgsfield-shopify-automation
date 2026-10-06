"""Database access. Synchronous SQLAlchemy Core; async callers use asyncio.to_thread."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

from sqlalchemy import create_engine, delete, event, func, insert, select, update
from sqlalchemy.engine import Engine

from ems.database import schema as s

log = logging.getLogger(__name__)

# Migrations: version -> callable(engine). Version 1 = initial schema.
# Later versions add ALTER/CREATE statements; never edit an applied migration.
SCHEMA_VERSION = 1


def _migrate_1(engine: Engine) -> None:
    s.metadata.create_all(engine)


MIGRATIONS = {1: _migrate_1}

RETENTION_DAYS = {"samples": 14, "device_samples": 14, "decisions": 365, "notifications": 180, "plans": 30}


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        kwargs: dict[str, Any] = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragma(conn, _record):  # pragma: no cover - trivial
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()

    # ------------------------------------------------------------ migrations
    def current_version(self) -> int:
        from sqlalchemy import inspect
        if not inspect(self.engine).has_table("schema_version"):
            return 0
        with self.engine.connect() as c:
            v = c.execute(select(func.max(s.schema_version.c.version))).scalar()
        return int(v or 0)

    def migrate(self) -> int:
        current = self.current_version()
        for version in range(current + 1, SCHEMA_VERSION + 1):
            log.info("applying database migration", extra={"version": version})
            MIGRATIONS[version](self.engine)
            with self.engine.begin() as c:
                c.execute(insert(s.schema_version).values(version=version))
        return self.current_version()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as c:
                c.execute(select(1))
            return True
        except Exception:
            return False

    def close(self) -> None:
        self.engine.dispose()

    # ----------------------------------------------------------------- users
    def count_users(self) -> int:
        with self.engine.connect() as c:
            return int(c.execute(select(func.count()).select_from(s.users)).scalar() or 0)

    def create_user(self, username: str, password_hash: str, role: str) -> int:
        with self.engine.begin() as c:
            res = c.execute(insert(s.users).values(username=username, password_hash=password_hash, role=role,
                                                   created_ts=time.time(), disabled=False))
            return int(res.inserted_primary_key[0])

    def get_user(self, username: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(s.users).where(s.users.c.username == username)).mappings().first()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(s.users.c.id, s.users.c.username, s.users.c.role, s.users.c.created_ts,
                                    s.users.c.disabled)).mappings().all()
        return [dict(r) for r in rows]

    def update_user(self, username: str, **values: Any) -> bool:
        with self.engine.begin() as c:
            return c.execute(update(s.users).where(s.users.c.username == username).values(**values)).rowcount > 0

    def delete_user(self, username: str) -> bool:
        with self.engine.begin() as c:
            return c.execute(delete(s.users).where(s.users.c.username == username)).rowcount > 0

    def create_api_token(self, name: str, token_hash: str, role: str) -> int:
        with self.engine.begin() as c:
            res = c.execute(insert(s.api_tokens).values(name=name, token_hash=token_hash, role=role,
                                                        created_ts=time.time()))
            return int(res.inserted_primary_key[0])

    def find_api_token(self, token_hash: str) -> dict | None:
        with self.engine.begin() as c:
            row = c.execute(select(s.api_tokens).where(s.api_tokens.c.token_hash == token_hash)).mappings().first()
            if row:
                c.execute(update(s.api_tokens).where(s.api_tokens.c.id == row["id"]).values(last_used_ts=time.time()))
        return dict(row) if row else None

    def list_api_tokens(self) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(s.api_tokens.c.id, s.api_tokens.c.name, s.api_tokens.c.role,
                                    s.api_tokens.c.created_ts, s.api_tokens.c.last_used_ts)).mappings().all()
        return [dict(r) for r in rows]

    def delete_api_token(self, token_id: int) -> bool:
        with self.engine.begin() as c:
            return c.execute(delete(s.api_tokens).where(s.api_tokens.c.id == token_id)).rowcount > 0

    # --------------------------------------------------------------- samples
    def insert_sample(self, row: dict) -> None:
        with self.engine.begin() as c:
            c.execute(insert(s.samples).values(**row))

    def insert_device_samples(self, rows: list[dict]) -> None:
        if rows:
            with self.engine.begin() as c:
                c.execute(insert(s.device_samples), rows)

    def samples_between(self, site_id: str, start: float, end: float) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(s.samples).where(s.samples.c.site_id == site_id, s.samples.c.ts >= start,
                                                     s.samples.c.ts < end).order_by(s.samples.c.ts)).mappings().all()
        return [dict(r) for r in rows]

    def last_slot_ts(self, site_id: str) -> float | None:
        with self.engine.connect() as c:
            return c.execute(select(func.max(s.samples_15m.c.slot_ts))
                             .where(s.samples_15m.c.site_id == site_id)).scalar()

    def first_sample_ts(self, site_id: str) -> float | None:
        with self.engine.connect() as c:
            return c.execute(select(func.min(s.samples.c.ts)).where(s.samples.c.site_id == site_id)).scalar()

    def upsert_slots(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self.engine.begin() as c:
            for row in rows:
                c.execute(delete(s.samples_15m).where(s.samples_15m.c.site_id == row["site_id"],
                                                      s.samples_15m.c.slot_ts == row["slot_ts"]))
            c.execute(insert(s.samples_15m), rows)

    def slots_between(self, site_id: str, start: float, end: float) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(s.samples_15m).where(
                s.samples_15m.c.site_id == site_id, s.samples_15m.c.slot_ts >= start,
                s.samples_15m.c.slot_ts < end).order_by(s.samples_15m.c.slot_ts)).mappings().all()
        return [dict(r) for r in rows]

    def device_samples_between(self, device_id: str, start: float, end: float, limit: int = 5000) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(s.device_samples).where(
                s.device_samples.c.device_id == device_id, s.device_samples.c.ts >= start,
                s.device_samples.c.ts < end).order_by(s.device_samples.c.ts).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- decisions
    def insert_decision(self, row: dict) -> None:
        with self.engine.begin() as c:
            c.execute(insert(s.decisions).values(**row))

    def recent_decisions(self, limit: int = 200, device: str | None = None, since: float | None = None) -> list[dict]:
        q = select(s.decisions).order_by(s.decisions.c.ts.desc(), s.decisions.c.id.desc()).limit(limit)
        if device:
            q = q.where(s.decisions.c.device == device)
        if since is not None:
            q = q.where(s.decisions.c.ts >= since)
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings().all()]

    # ---------------------------------------------------------------- prices
    def upsert_prices(self, area: str, source: str, points: Iterable[tuple[float, float, int]]) -> int:
        now, n = time.time(), 0
        with self.engine.begin() as c:
            for ts, price, res in points:
                c.execute(delete(s.prices).where(s.prices.c.area == area, s.prices.c.ts == ts))
                c.execute(insert(s.prices).values(area=area, ts=ts, source=source, spot_eur_kwh=price,
                                                  resolution_min=res, fetched_ts=now))
                n += 1
        return n

    def prices_between(self, area: str, start: float, end: float) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(s.prices).where(s.prices.c.area == area, s.prices.c.ts >= start,
                                                    s.prices.c.ts < end).order_by(s.prices.c.ts)).mappings().all()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------------- plans
    def insert_plan(self, row: dict) -> int:
        with self.engine.begin() as c:
            return int(c.execute(insert(s.plans).values(**row)).inserted_primary_key[0])

    def latest_plan(self, site_id: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(s.plans).where(s.plans.c.site_id == site_id)
                            .order_by(s.plans.c.created_ts.desc()).limit(1)).mappings().first()
        return dict(row) if row else None

    # ----------------------------------------------------------- automations
    def list_automations(self) -> list[dict]:
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(select(s.automations).order_by(s.automations.c.id)).mappings().all()]

    def get_automation(self, automation_id: int) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(s.automations).where(s.automations.c.id == automation_id)).mappings().first()
        return dict(row) if row else None

    def save_automation(self, name: str, enabled: bool, definition: dict, automation_id: int | None = None) -> int:
        now = time.time()
        with self.engine.begin() as c:
            if automation_id is None:
                res = c.execute(insert(s.automations).values(name=name, enabled=enabled, definition=definition,
                                                             created_ts=now, updated_ts=now))
                return int(res.inserted_primary_key[0])
            c.execute(update(s.automations).where(s.automations.c.id == automation_id).values(
                name=name, enabled=enabled, definition=definition, updated_ts=now, last_state=None))
            return automation_id

    def set_automation_state(self, automation_id: int, state: bool, fired_ts: float | None) -> None:
        values: dict[str, Any] = {"last_state": state}
        if fired_ts is not None:
            values["last_fired_ts"] = fired_ts
        with self.engine.begin() as c:
            c.execute(update(s.automations).where(s.automations.c.id == automation_id).values(**values))

    def delete_automation(self, automation_id: int) -> bool:
        with self.engine.begin() as c:
            return c.execute(delete(s.automations).where(s.automations.c.id == automation_id)).rowcount > 0

    # --------------------------------------------------------- notifications
    def insert_notification(self, level: str, code: str, message: str, data: dict | None = None) -> int:
        with self.engine.begin() as c:
            res = c.execute(insert(s.notifications).values(ts=time.time(), level=level, code=code, message=message,
                                                           data=data or {}, acknowledged=False))
            return int(res.inserted_primary_key[0])

    def list_notifications(self, limit: int = 100, unacknowledged_only: bool = False) -> list[dict]:
        q = select(s.notifications).order_by(s.notifications.c.ts.desc()).limit(limit)
        if unacknowledged_only:
            q = q.where(s.notifications.c.acknowledged.is_(False))
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings().all()]

    def acknowledge_notification(self, notification_id: int | None = None) -> int:
        q = update(s.notifications).values(acknowledged=True)
        if notification_id is not None:
            q = q.where(s.notifications.c.id == notification_id)
        with self.engine.begin() as c:
            return c.execute(q).rowcount

    # ------------------------------------------------------- config versions
    def add_config_version(self, yaml_text: str, username: str | None, comment: str = "") -> int:
        with self.engine.begin() as c:
            res = c.execute(insert(s.config_versions).values(ts=time.time(), username=username, comment=comment,
                                                             yaml=yaml_text))
            return int(res.inserted_primary_key[0])

    def list_config_versions(self, limit: int = 50) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(s.config_versions.c.id, s.config_versions.c.ts, s.config_versions.c.username,
                                    s.config_versions.c.comment).order_by(s.config_versions.c.id.desc())
                             .limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    def get_config_version(self, version_id: int) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(s.config_versions).where(s.config_versions.c.id == version_id)).mappings().first()
        return dict(row) if row else None

    # ------------------------------------------------------------------ jobs
    def create_job(self, kind: str, params: dict) -> int:
        with self.engine.begin() as c:
            res = c.execute(insert(s.jobs).values(kind=kind, status="running", created_ts=time.time(),
                                                  params=params, progress=0.0))
            return int(res.inserted_primary_key[0])

    def update_job(self, job_id: int, **values: Any) -> None:
        with self.engine.begin() as c:
            c.execute(update(s.jobs).where(s.jobs.c.id == job_id).values(**values))

    def get_job(self, job_id: int) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(s.jobs).where(s.jobs.c.id == job_id)).mappings().first()
        return dict(row) if row else None

    def list_jobs(self, kind: str | None = None, limit: int = 20) -> list[dict]:
        q = select(s.jobs).order_by(s.jobs.c.id.desc()).limit(limit)
        if kind:
            q = q.where(s.jobs.c.kind == kind)
        with self.engine.connect() as c:
            return [dict(r) for r in c.execute(q).mappings().all()]

    # -------------------------------------------------------------------- kv
    def kv_get(self, key: str, default: Any = None) -> Any:
        with self.engine.connect() as c:
            v = c.execute(select(s.kv.c.value).where(s.kv.c.key == key)).first()
        return default if v is None else v[0]

    def kv_set(self, key: str, value: Any) -> None:
        with self.engine.begin() as c:
            c.execute(delete(s.kv).where(s.kv.c.key == key))
            c.execute(insert(s.kv).values(key=key, value=value, updated_ts=time.time()))

    # ------------------------------------------------------------- retention
    def apply_retention(self, now: float | None = None) -> dict[str, int]:
        now = now or time.time()
        removed = {}
        with self.engine.begin() as c:
            for table, col in ((s.samples, s.samples.c.ts), (s.device_samples, s.device_samples.c.ts),
                               (s.decisions, s.decisions.c.ts), (s.notifications, s.notifications.c.ts),
                               (s.plans, s.plans.c.created_ts)):
                cutoff = now - RETENTION_DAYS[table.name] * 86400
                removed[table.name] = c.execute(delete(table).where(col < cutoff)).rowcount
        return removed
