"""Backup / restore.

A backup is a zip with: manifest.json, ems.yaml, ems.db (consistent SQLite snapshot
via the online backup API), secrets.enc and — only on request — secret.key.

The key file decrypts every stored password/token, so (audit P0-05):
* keys are excluded by default;
* a backup *with* keys must be protected with a password: the whole zip is then encrypted
  (scrypt key derivation + Fernet/AES-128-CBC with HMAC-SHA256). Without the password the file
  is unreadable. A password can also be used for backups without keys.
PostgreSQL deployments back up the database with pg_dump.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

from ems import __version__

REQUIRED = ("manifest.json", "ems.yaml")
MAX_BACKUP_BYTES = 512 * 1024 * 1024
MAGIC = b"EMSBACKUP1\n"            # encrypted backup: MAGIC + 16-byte salt + Fernet token
MIN_PASSWORD = 10
_KDF = {"n": 2**15, "r": 8, "p": 1}


def _fernet(password: str, salt: bytes):
    import base64

    from cryptography.fernet import Fernet
    key = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **_KDF)
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_backup(blob: bytes, password: str) -> bytes:
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"back-upwachtwoord moet minimaal {MIN_PASSWORD} tekens hebben")
    salt = os.urandom(16)
    return MAGIC + salt + _fernet(password, salt).encrypt(blob)


def is_encrypted(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def decrypt_backup(blob: bytes, password: str | None) -> bytes:
    from cryptography.fernet import InvalidToken
    if not is_encrypted(blob):
        return blob
    if not password:
        raise ValueError("deze back-up is versleuteld; vul het back-upwachtwoord in")
    salt, token = blob[len(MAGIC):len(MAGIC) + 16], blob[len(MAGIC) + 16:]
    try:
        return _fernet(password, salt).decrypt(token)
    except InvalidToken:
        raise ValueError("onjuist back-upwachtwoord of beschadigd bestand") from None


def create_backup(data_dir: Path, db_url: str, include_keys: bool = False, password: str | None = None) -> bytes:
    if include_keys and not password:
        raise ValueError("een back-up met sleutels moet met een wachtwoord worden versleuteld")
    data_dir = Path(data_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = {"format": 1, "app_version": __version__, "created": time.time(),
                    "includes_keys": include_keys, "database": "sqlite" if db_url.startswith("sqlite") else "external"}
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.write(data_dir / "ems.yaml", "ems.yaml")
        if db_url.startswith("sqlite:///"):
            src_path = db_url.removeprefix("sqlite:///")
            with tempfile.TemporaryDirectory() as tmp:
                dst_path = Path(tmp) / "ems.db"
                src = sqlite3.connect(src_path)
                dst = sqlite3.connect(dst_path)
                with dst:
                    src.backup(dst)
                src.close()
                dst.close()
                z.write(dst_path, "ems.db")
        for name in ("secrets.enc",) + (("secret.key",) if include_keys else ()):
            if (data_dir / name).exists():
                z.write(data_dir / name, name)
    blob = buf.getvalue()
    return encrypt_backup(blob, password) if password else blob


def inspect_backup(blob: bytes, password: str | None = None) -> dict:
    if len(blob) > MAX_BACKUP_BYTES:
        raise ValueError("back-up is te groot")
    blob = decrypt_backup(blob, password)
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ValueError("geen geldig back-upbestand (zip)") from exc
    names = set(z.namelist())
    for req in REQUIRED:
        if req not in names:
            raise ValueError(f"back-up mist {req}")
    for n in names:
        if n.startswith("/") or ".." in Path(n).parts:
            raise ValueError("onveilig pad in back-up")
    manifest = json.loads(z.read("manifest.json"))
    if manifest.get("format") != 1:
        raise ValueError("onbekend back-upformaat")
    return {"manifest": manifest, "files": sorted(names)}


def restore_backup(blob: bytes, data_dir: Path, db_url: str, password: str | None = None) -> dict:
    """Validate, keep a safety copy of the current files, then replace them."""
    blob = decrypt_backup(blob, password)
    info = inspect_backup(blob)
    import yaml

    from ems.core.config import config_from_dict

    z = zipfile.ZipFile(io.BytesIO(blob))
    config_from_dict(yaml.safe_load(z.read("ems.yaml")) or {}, env={k: "" for k in _env_names(z.read("ems.yaml"))})
    data_dir = Path(data_dir)
    safety = data_dir / "backups" / f"pre-restore-{int(time.time())}"
    safety.mkdir(parents=True, exist_ok=True)
    for name in ("ems.yaml", "ems.db", "secrets.enc", "secret.key"):
        if (data_dir / name).exists():
            shutil.copy2(data_dir / name, safety / name)
    for name in ("ems.yaml", "secrets.enc", "secret.key"):
        if name in info["files"]:
            (data_dir / name).write_bytes(z.read(name))
    if "ems.db" in info["files"] and db_url.startswith("sqlite:///"):
        target = Path(db_url.removeprefix("sqlite:///"))
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        target.write_bytes(z.read("ems.db"))
    return {**info, "safety_copy": str(safety)}


def _env_names(yaml_bytes: bytes) -> list[str]:
    import re
    return re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", yaml_bytes.decode("utf-8", "replace"))


def rotate_backups(directory: Path, keep: int = 7) -> None:
    files = sorted(Path(directory).glob("auto-*.zip"))
    for f in files[:-keep]:
        f.unlink(missing_ok=True)
