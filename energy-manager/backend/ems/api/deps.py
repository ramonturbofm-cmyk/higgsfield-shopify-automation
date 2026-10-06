"""Authentication dependencies: Bearer JWT (users) or API token (integrations)."""

from __future__ import annotations

import asyncio

from fastapi import Depends, HTTPException, Request, status

from ems.security.auth import Principal, hash_api_token
from ems.server.runtime import EMSRuntime


def get_runtime(request: Request) -> EMSRuntime:
    return request.app.state.runtime


async def resolve_token(runtime: EMSRuntime, token: str) -> Principal | None:
    if token.startswith("ems_"):
        row = await asyncio.to_thread(runtime.db.find_api_token, hash_api_token(token))
        return None if row is None else Principal(row["name"], row["role"], kind="api_token")
    principal = runtime.tokens.verify(token)
    if principal is None:
        return None
    user = await asyncio.to_thread(runtime.db.get_user, principal.username)
    if user is None or user["disabled"]:
        return None
    return Principal(user["username"], user["role"])


async def current_principal(request: Request) -> Principal:
    runtime = get_runtime(request)
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else None
    if token is None and request.url.path.endswith(("/export", "/backup")):
        token = request.query_params.get("token")       # file downloads opened in a new tab
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "niet ingelogd", headers={"WWW-Authenticate": "Bearer"})
    principal = await resolve_token(runtime, token)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessie verlopen of ongeldig token",
                            headers={"WWW-Authenticate": "Bearer"})
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
