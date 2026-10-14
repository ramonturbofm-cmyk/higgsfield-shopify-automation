"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ems import __version__, buildinfo
from ems.api import routes_core, routes_devices, routes_library, routes_more, routes_nodes
from ems.api.deps import CSRF_COOKIE, resolve_token, set_session_cookies
from ems.core.config import ConfigError
from ems.security.auth import LoginRateLimiter
from ems.server.runtime import EMSRuntime

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

IMMUTABLE = "public, max-age=31536000, immutable"


class VersionedStatic(StaticFiles):
    """``/static/v/<asset-hash>/<file>``: the hash is part of the URL, so these files can be cached forever.
    A request with an old hash (a page from before an update) still gets the *current* file, but uncached;
    the page then notices the version change and reloads."""

    def __init__(self, *, directory: Path, current: str) -> None:
        super().__init__(directory=directory)
        self.current = current

    def get_path(self, scope) -> str:
        path = super().get_path(scope)
        head, sep, rest = path.partition(os.sep)
        scope["ems_current_asset"] = head == self.current
        return rest if sep else path

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        resp.headers["Cache-Control"] = IMMUTABLE if scope.get("ems_current_asset") else "no-store"
        return resp


class RevalidatedStatic(StaticFiles):
    """Unversioned ``/static/<file>`` (icon, manifest, links from older pages): always revalidated (ETag),
    never used from cache without asking the server."""

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def render_index(build: dict) -> str:
    """index.html with versioned asset URLs and the build identity the frontend checks against the server."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    base = f"/static/v/{build['asset_hash']}/"
    for asset in ("css/app.css", "js/app.js"):
        html = html.replace(f"/static/{asset}", base + asset)
    meta = (f'<meta name="ems-version" content="{build["version"]}">\n'
            f'<meta name="ems-build" content="{build["build"]}">\n'
            f'<meta name="ems-asset" content="{build["asset_hash"]}">\n')
    return html.replace("</head>", meta + "</head>", 1)


# Origins of the Windows app (Tauri) connect screen; the dashboard itself is same-origin.
TAURI_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"]


def create_app(runtime: EMSRuntime, *, start_runtime: bool = True, loops: bool = True,
               local_control_token: str | None = None) -> FastAPI:
    """``local_control_token`` enables POST /api/v1/system/local-shutdown for the
    Windows app/launcher (loopback only, token from a file only the user can read)."""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_runtime:
            await runtime.start(loops=loops)
        try:
            yield
        finally:
            if start_runtime:
                await runtime.stop()

    app = FastAPI(title="Energy Manager API", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
                  description="Lokale API van het Energy Management System. Authenticatie: browsersessie "
                              "(HttpOnly-cookie + X-CSRF-Token), of Authorization: Bearer met het token uit "
                              "POST /api/v1/auth/login of een API-token (ems_...).")
    app.state.runtime = runtime
    build = buildinfo.info()
    app.state.build = build
    app.state.login_limiter = LoginRateLimiter()
    app.add_middleware(CORSMiddleware, allow_origins=TAURI_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
                       # The app page (tauri.localhost) loads a LAN server: Private Network Access preflight.
                       allow_private_network=True)

    @app.middleware("http")
    async def security_headers(request, call_next):
        resp = await call_next(request)
        rotated = getattr(request.state, "rotate_session", None)
        if rotated:   # sliding browser session: replace the cookie, keep the CSRF token
            set_session_cookies(request, resp, rotated, request.cookies.get(CSRF_COOKIE))
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers["X-EMS-Version"] = build["version"]
        resp.headers["X-EMS-Build"] = build["build"]
        if request.url.path.startswith("/api/") or request.url.path == "/healthz":
            resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault("Content-Security-Policy", CSP)
        return resp

    @app.exception_handler(ConfigError)
    async def config_error(_request, exc: ConfigError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    app.include_router(routes_core.router)
    app.include_router(routes_devices.router)
    app.include_router(routes_more.router)
    app.include_router(routes_nodes.router)
    app.include_router(routes_library.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        ok = runtime.running and not runtime.watchdog_tripped and runtime.db.ping()
        return JSONResponse({"ok": ok, "version": __version__, "mode": runtime.config.runtime.mode,
                             "profile": "lab" if runtime.lab_mode else runtime.config.runtime.mode,
                             "build": build["build"], "asset_hash": build["asset_hash"]},
                            status_code=200 if ok else 503)

    app.state.request_shutdown = None

    @app.post("/api/v1/system/local-shutdown", include_in_schema=False)
    async def local_shutdown(request: Request):
        client = request.client.host if request.client else ""
        given = request.headers.get("x-ems-local-token", "")
        if (not local_control_token or client not in ("127.0.0.1", "::1")
                or not secrets.compare_digest(given, local_control_token)):
            return JSONResponse({"detail": "niet toegestaan"}, status_code=403)
        if app.state.request_shutdown is None:
            return JSONResponse({"detail": "stoppen niet beschikbaar"}, status_code=409)
        app.state.request_shutdown()   # graceful: lifespan stops the engine and releases devices
        return {"ok": True}

    @app.websocket("/api/v1/ws")
    async def ws(websocket: WebSocket):
        # Browser: single-use ticket from POST /auth/ws-ticket. Scripts: Authorization header.
        # Session tokens are never accepted in the URL.
        principal = None
        ticket = websocket.query_params.get("ticket")
        if ticket:
            principal = runtime.sessions.redeem(ticket)
        else:
            auth = websocket.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                principal = await resolve_token(runtime, auth[7:])
        if principal is None:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        q = runtime.subscribe()
        await websocket.send_json({"type": "hello", "data": {"version": __version__, "user": principal.username,
                                                             "role": principal.role}})
        await websocket.send_json({"type": "live", "data": runtime.live()})

        async def receiver():
            while True:
                msg = await websocket.receive_json()
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

        recv = asyncio.create_task(receiver())
        try:
            while True:
                getter = asyncio.create_task(q.get())
                done, _ = await asyncio.wait({getter, recv}, return_when=asyncio.FIRST_COMPLETED)
                if recv in done:
                    getter.cancel()
                    break
                await websocket.send_json(getter.result())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            recv.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await recv
            runtime.unsubscribe(q)

    if (WEB_DIR / "index.html").exists():
        index_html = render_index(build)
        app.mount("/static/v", VersionedStatic(directory=WEB_DIR, current=build["asset_hash"]), name="static-v")
        app.mount("/static", RevalidatedStatic(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index():
            # Never cached: it names the asset version, so it must always come from the running server.
            return HTMLResponse(index_html, headers={"Cache-Control": "no-store"})

    return app
