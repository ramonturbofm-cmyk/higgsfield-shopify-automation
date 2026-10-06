"""Users, roles, password hashing (scrypt) and JWT access tokens.

Roles (cumulative):  viewer < operator < admin < installer
  viewer    read everything
  operator  + manual overrides, acknowledge notifications
  admin     + settings, tariffs, automations, backup/restore, users
  installer + devices, drivers, commissioning
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

import jwt

ROLES = ("viewer", "operator", "admin", "installer")
ACCESS_TOKEN_TTL_S = 12 * 3600
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def role_at_least(role: str, required: str) -> bool:
    return ROLES.index(role) >= ROLES.index(required)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(calc.hex(), digest)
    except (ValueError, TypeError):
        return False


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_api_token() -> str:
    return "ems_" + secrets.token_urlsafe(32)


@dataclass
class Principal:
    username: str
    role: str
    kind: str = "user"   # user | api_token

    def can(self, required: str) -> bool:
        return role_at_least(self.role, required)


class TokenIssuer:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("JWT secret too short")
        self.secret = secret

    def issue(self, username: str, role: str, ttl_s: int = ACCESS_TOKEN_TTL_S) -> str:
        now = int(time.time())
        return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + ttl_s}, self.secret,
                          algorithm="HS256")

    def verify(self, token: str) -> Principal | None:
        try:
            claims = jwt.decode(token, self.secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        except jwt.PyJWTError:
            return None
        role = claims.get("role")
        if role not in ROLES:
            return None
        return Principal(claims["sub"], role)


class LoginRateLimiter:
    """Max ``attempts`` failed logins per ``window_s`` per client address."""

    def __init__(self, attempts: int = 10, window_s: float = 300.0) -> None:
        self.attempts, self.window_s = attempts, window_s
        self._failures: dict[str, list[float]] = {}

    def allowed(self, client: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._failures.get(client, []) if now - t < self.window_s]
        self._failures[client] = recent
        return len(recent) < self.attempts

    def failed(self, client: str) -> None:
        self._failures.setdefault(client, []).append(time.monotonic())

    def succeeded(self, client: str) -> None:
        self._failures.pop(client, None)
