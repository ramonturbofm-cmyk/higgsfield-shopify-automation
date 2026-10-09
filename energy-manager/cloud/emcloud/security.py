"""Security primitives — all from maintained libraries, nothing home-made:

* passwords: Argon2id via ``argon2-cffi`` (parameters per RFC 9106, rehash on parameter change);
* MFA: TOTP (RFC 6238) via ``pyotp``, with replay protection per time step;
* MFA secrets at rest: Fernet (``cryptography``), ``MultiFernet`` for key rotation;
* session/node/e-mail tokens and pairing codes: ``secrets`` (CSPRNG); only their SHA-256 is stored
  (the tokens are high-entropy random values, so a fast hash is the standard choice);
* constant-time comparisons with ``hmac.compare_digest``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

_ph = PasswordHasher()
# A real hash to verify against when the account does not exist (same timing as a wrong password).
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))

PASSWORD_MIN = 10
PASSWORD_MAX = 256
PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O/1/I/L


def check_password_policy(password: str, email: str = "") -> str | None:
    if len(password) < PASSWORD_MIN:
        return f"wachtwoord moet minstens {PASSWORD_MIN} tekens hebben"
    if len(password) > PASSWORD_MAX:
        return "wachtwoord is te lang"
    if email and password.lower() == email.lower():
        return "wachtwoord mag niet gelijk zijn aan het e-mailadres"
    if len(set(password)) < 4:
        return "wachtwoord is te eenvoudig"
    return None


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    try:
        return _ph.verify(stored or _DUMMY_HASH, password) and stored is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return _ph.check_needs_rehash(stored)
    except InvalidHashError:
        return True


def new_token(prefix: str = "", nbytes: int = 32) -> str:
    return prefix + secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_pairing_code() -> str:
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_pairing_code(code: str) -> str:
    raw = "".join(c for c in code.upper() if c.isalnum())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else raw


# ------------------------------------------------------------------- MFA
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Energy Manager Cloud")


def verify_totp(secret: str, code: str, last_step: int, now: float) -> int | None:
    """Return the accepted time step (store it) or None. One step of clock drift either way; a code
    whose step is not newer than ``last_step`` is a replay and is refused."""
    code = "".join(c for c in (code or "") if c.isdigit())
    if len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    step = int(now // totp.interval)
    for s in (step - 1, step, step + 1):
        if s > last_step and hmac.compare_digest(totp.at(s * totp.interval), code):
            return s
    return None


class Crypto:
    """Encryption of small secrets at rest (MFA). Keys: ``EMC_ENCRYPTION_KEYS`` (first = current)."""

    def __init__(self, keys: list[str]) -> None:
        self.ephemeral = not keys
        self._f = MultiFernet([Fernet(k) for k in (keys or [Fernet.generate_key().decode()])])

    def encrypt(self, value: str) -> str:
        return self._f.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._f.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("versleutelde waarde kan niet worden gelezen (verkeerde sleutel?)") from exc


# --------------------------------------------------------------- rate limiting
class RateLimiter:
    """Sliding-window limiter per key (in-process). A multi-instance deployment needs a shared store
    (e.g. Redis) — see KNOWN_LIMITATIONS."""

    def __init__(self, clock=time.time) -> None:
        self.clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
