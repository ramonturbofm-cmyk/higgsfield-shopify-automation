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
ACCESS_TOKEN_TTL_S = 8 * 3600        # browser session lifetime (sliding, see SESSION_ROTATE_S)
SESSION_ROTATE_S = 15 * 60           # a session cookie older than this is replaced by a fresh one
WS_TICKET_TTL_S = 30                 # single-use WebSocket ticket
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
    session_id: str | None = None
    issued_at: int | None = None
    expires_at: int | None = None

    def can(self, required: str) -> bool:
        return role_at_least(self.role, required)


class TokenIssuer:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("JWT secret too short")
        self.secret = secret

    def issue(self, username: str, role: str, ttl_s: int = ACCESS_TOKEN_TTL_S) -> str:
        now = int(time.time())
        return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + ttl_s,
                           "jti": secrets.token_hex(12)}, self.secret, algorithm="HS256")

    def verify(self, token: str) -> Principal | None:
        try:
            claims = jwt.decode(token, self.secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        except jwt.PyJWTError:
            return None
        role = claims.get("role")
        if role not in ROLES:
            return None
        return Principal(claims["sub"], role, session_id=claims.get("jti"), issued_at=claims.get("iat"),
                         expires_at=claims.get("exp"))


class SessionRegistry:
    """Logged-out sessions (until they expire) and single-use WebSocket tickets, in memory.

    After a server restart revoked sessions are forgotten; they still expire after
    ``ACCESS_TOKEN_TTL_S``. WebSocket tickets keep tokens out of URLs and logs."""

    def __init__(self) -> None:
        self._revoked: dict[str, float] = {}
        self._tickets: dict[str, tuple[Principal, float]] = {}

    def revoke(self, p: Principal) -> None:
        if p.session_id:
            self._revoked[p.session_id] = float(p.expires_at or time.time() + ACCESS_TOKEN_TTL_S)

    def revoked(self, p: Principal) -> bool:
        now = time.time()
        for k in [k for k, exp in self._revoked.items() if exp < now]:
            del self._revoked[k]
        return bool(p.session_id and p.session_id in self._revoked)

    def ticket(self, p: Principal) -> str:
        now = time.monotonic()
        for k in [k for k, (_, exp) in self._tickets.items() if exp < now]:
            del self._tickets[k]
        t = secrets.token_urlsafe(24)
        self._tickets[t] = (p, now + WS_TICKET_TTL_S)
        return t

    def redeem(self, ticket: str) -> Principal | None:
        entry = self._tickets.pop(ticket, None)
        if entry is None or entry[1] < time.monotonic():
            return None
        return entry[0]


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
