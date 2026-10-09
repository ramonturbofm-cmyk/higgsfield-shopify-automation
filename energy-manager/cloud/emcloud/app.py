"""FastAPI application factory for Energy Manager Cloud."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, text

from emcloud import __version__, billing
from emcloud.api import admin, auth, nodes, orgs
from emcloud.config import Settings
from emcloud.db import AuditLog, AuthSession, EmailToken, PairingCode, Telemetry, init_db, make_engine
from emcloud.mailer import Mailer
from emcloud.security import Crypto, RateLimiter

log = logging.getLogger(__name__)
WEB = Path(__file__).with_name("web")

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}


def maintenance(app: FastAPI) -> dict:
    """Period ends of subscriptions, expired tokens/sessions, retention of audit log and telemetry."""
    s, t = app.state.settings, app.state.clock()
    with app.state.sessionmaker() as db:
        changed = billing.tick(db, t)
        db.execute(delete(AuthSession).where(AuthSession.expires_ts < t - 86400))
        db.execute(delete(EmailToken).where(EmailToken.expires_ts < t - 86400))
        db.execute(delete(PairingCode).where(PairingCode.expires_ts < t - 86400))
        db.execute(delete(AuditLog).where(AuditLog.ts < t - s.audit_retention_days * 86400))
        db.execute(delete(Telemetry).where(Telemetry.ts < t - s.telemetry_retention_days * 86400))
        db.commit()
    return {"subscriptions_changed": changed}


def create_app(settings: Settings | None = None, clock=time.time, background: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    maker = init_db(engine)
    with maker() as db:
        billing.seed_plans(db)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if background:
            async def loop():
                while True:
                    try:
                        await asyncio.to_thread(maintenance, app)
                    except Exception:
                        log.exception("maintenance failed")
                    await asyncio.sleep(600)
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Energy Manager Cloud", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if settings.environment != "production" else None, redoc_url=None)
    app.state.settings, app.state.clock, app.state.sessionmaker = settings, clock, maker
    app.state.limiter = RateLimiter(clock)
    app.state.crypto = Crypto(settings.encryption_keys)
    app.state.mailer = Mailer(settings)
    app.state.latest_ems_version = None
    if app.state.crypto.ephemeral:
        log.warning("EMC_ENCRYPTION_KEYS not set: using a temporary key (development only; MFA secrets are lost on restart)")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if settings.secure_cookies:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    for r in (auth.router, orgs.router, nodes.router, admin.router):
        app.include_router(r)

    @app.get("/api/v1/health", tags=["system"])
    def health() -> JSONResponse:
        try:
            with maker() as db:
                db.execute(text("SELECT 1"))
            ok = True
        except Exception:
            ok = False
        return JSONResponse({"status": "ok" if ok else "degraded", "version": __version__,
                             "environment": settings.environment}, status_code=200 if ok else 503)

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB / "index.html")

    return app
