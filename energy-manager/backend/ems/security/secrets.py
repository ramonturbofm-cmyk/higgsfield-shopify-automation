"""Encrypted secret store for device tokens, passwords and API keys.

Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256). The key lives
in a separate file (mode 0600) next to the data, never in the config or the
database, and is created on first use. Config files only reference secrets by
name (``secret_ref``), so a config export never contains credentials.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.key_path = self.directory / "secret.key"
        self.data_path = self.directory / "secrets.enc"
        self._lock = threading.Lock()
        self._fernet = Fernet(self._load_or_create_key())
        self._cache: dict[str, str] = self._read()

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        key = Fernet.generate_key()
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        return key

    def _read(self) -> dict[str, str]:
        if not self.data_path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self.data_path.read_bytes()))
        except (InvalidToken, ValueError) as exc:
            raise RuntimeError("secrets.enc kan niet worden ontsleuteld (verkeerde secret.key?)") from exc

    def _write(self) -> None:
        tmp = self.data_path.with_suffix(".tmp")
        tmp.write_bytes(self._fernet.encrypt(json.dumps(self._cache).encode()))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.data_path)

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._cache.get(name, default)

    def set(self, name: str, value: str) -> None:
        with self._lock:
            self._cache[name] = value
            self._write()

    def delete(self, name: str) -> None:
        with self._lock:
            if self._cache.pop(name, None) is not None:
                self._write()

    def names(self) -> list[str]:
        return sorted(self._cache)

    def reload(self) -> None:
        with self._lock:
            self._fernet = Fernet(self._load_or_create_key())
            self._cache = self._read()


class MemorySecretStore:
    """In-memory variant for tests and simulations."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._cache = dict(initial or {})

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._cache.get(name, default)

    def set(self, name: str, value: str) -> None:
        self._cache[name] = value

    def delete(self, name: str) -> None:
        self._cache.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self._cache)
