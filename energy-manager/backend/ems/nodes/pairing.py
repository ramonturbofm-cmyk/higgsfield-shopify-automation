"""Pairing of nodes with a short-lived 6-digit code shown on the node that is added.

The code is valid for 10 minutes and for at most 5 attempts. A successful pairing
returns a long random node token; only its SHA-256 hash is stored on the node that
issued it, the controller keeps the token in its encrypted secret store.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

CODE_TTL_S = 600
MAX_ATTEMPTS = 5


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PairingManager:
    def __init__(self, clock=time.time) -> None:
        self.clock = clock
        self._code: str | None = None
        self._expires = 0.0
        self._attempts = 0

    def new_code(self) -> tuple[str, int]:
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._expires = self.clock() + CODE_TTL_S
        self._attempts = 0
        return self._code, CODE_TTL_S

    @property
    def open(self) -> bool:
        return self._code is not None and self.clock() < self._expires

    def verify(self, code: str) -> bool:
        if not self.open:
            return False
        self._attempts += 1
        ok = hmac.compare_digest(str(code).strip(), self._code or "")
        if ok or self._attempts >= MAX_ATTEMPTS:
            self._code = None               # single use / locked after too many tries
        return ok

    @staticmethod
    def new_token() -> tuple[str, str]:
        token = "emsn_" + secrets.token_urlsafe(32)
        return token, hash_token(token)
