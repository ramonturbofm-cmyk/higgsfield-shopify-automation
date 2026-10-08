"""Authentication dependencies.

* Browser: HttpOnly session cookie (``ems_session``) plus a CSRF double-submit token: every
  state-changing request must send header ``X-CSRF-Token`` equal to the ``ems_csrf`` cookie.
  The session is rotated (fresh cookie) when older than ``SESSION_ROTATE_S``; logout revokes it.
* Scripts / integrations: ``Authorization: Bearer <token>`` (JWT from login, or an ``ems_`` API
  token). Not sent automatically by browsers, so no CSRF check is needed.
Tokens are never accepted in URLs.
"""

from __future__ import annotations

import asyncio
import secrets
import time

from fastapi import Depends, HTTPException, Request, Response, status

from ems.security.auth import ACCESS_TOKEN_TTL_S, SESSION_ROTATE_S, Principal, hash_api_token
from ems.server.runtime import EMSRuntime


def get_runtime(request: Request) -> EMSRuntime:
    return request.app.state.runtime


async def resolve_token(runtime: EMSRuntime, token: str) -> Principal | None:
    if token.startswith("ems_"):
        row = await asyncio.to_thread(runtime.db.find_api_token, hash_api_token(token))
        return None if row is None else Principal(row["name"], row["role"], kind="api_token")
    principal = runtime.tokens.verify(token)
    if principal is None or runtime.sessions.revoked(principal):
        return None
    user = await asyncio.to_thread(runtime.db.get_user, principal.username)
    if user is None or user["disabled"]:
        return None
    return Principal(user["username"], user["role"], session_id=principal.session_id,
                     issued_at=principal.issued_at, expires_at=principal.expires_at)


SESSION_COOKIE = "ems_session"
CSRF_COOKIE = "ems_csrf"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def set_session_cookies(request: Request, response: Response, token: str, csrf: str | None = None) -> str:
    """Session cookie (HttpOnly, SameSite=Strict, Secure on HTTPS) and a readable CSRF cookie."""
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    csrf = csrf or secrets.token_urlsafe(24)
    response.set_cookie(SESSION_COOKIE, token, max_age=ACCESS_TOKEN_TTL_S, httponly=True, secure=secure,
                        samesite="strict", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=ACCESS_TOKEN_TTL_S, httponly=False, secure=secure,
                        samesite="strict", path="/")
    return csrf


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


async def current_principal(request: Request) -> Principal:
    runtime = get_runtime(request)
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else None
    via_cookie = False
    if token is None:
        token = request.cookies.get(SESSION_COOKIE)
        via_cookie = token is not None
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "niet ingelogd", headers={"WWW-Authenticate": "Bearer"})
    principal = await resolve_token(runtime, token)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessie verlopen of ongeldig token",
                            headers={"WWW-Authenticate": "Bearer"})
    if via_cookie:
        if request.method not in SAFE_METHODS:
            sent = request.headers.get("x-csrf-token", "")
            cookie = request.cookies.get(CSRF_COOKIE, "")
            if not sent or not cookie or not secrets.compare_digest(sent, cookie):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF-controle mislukt; herlaad de pagina")
        if principal.issued_at and time.time() - principal.issued_at > SESSION_ROTATE_S:
            # Sliding session: the middleware sets the fresh cookie on this response.
            request.state.rotate_session = runtime.tokens.issue(principal.username, principal.role)
    return principal


def require(role: str):
    async def dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"onvoldoende rechten (vereist: {role})")
        return principal
    return dep


viewer = require("viewer")
operator = require("operator")
admin = require("admin")
installer = require("installer")
