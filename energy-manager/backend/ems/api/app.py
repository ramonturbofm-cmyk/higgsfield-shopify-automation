"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ems import __version__
from ems.api import routes_core, routes_devices, routes_more, routes_nodes
from ems.api.deps import resolve_token
from ems.core.config import ConfigError
from ems.security.auth import LoginRateLimiter
from ems.server.runtime import EMSRuntime

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

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
                  description="Lokale API van het Energy Management System. Authenticatie: Bearer-token "
                              "(POST /api/v1/auth/login) of API-token (ems_...).")
    app.state.runtime = runtime
    app.state.login_limiter = LoginRateLimiter()
    app.add_middleware(CORSMiddleware, allow_origins=TAURI_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"],
                       # The app page (tauri.localhost) loads a LAN server: Private Network Access preflight.
                       allow_private_network=True)

    @app.middleware("http")
    async def security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
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

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        ok = runtime.running and not runtime.watchdog_tripped and runtime.db.ping()
        return JSONResponse({"ok": ok, "version": __version__, "mode": runtime.config.runtime.mode},
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
        token = websocket.query_params.get("token", "")
        principal = await resolve_token(runtime, token) if token else None
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
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app
