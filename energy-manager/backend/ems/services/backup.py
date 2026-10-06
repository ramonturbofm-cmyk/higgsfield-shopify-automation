"""Backup / restore.

A backup is a zip with: manifest.json, ems.yaml, ems.db (consistent SQLite snapshot
via the online backup API), secrets.enc and — unless excluded — secret.key.
With secret.key included the backup can restore paired devices on a new Raspberry Pi;
keep such a backup private. PostgreSQL deployments back up the database with pg_dump.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

from ems import __version__

REQUIRED = ("manifest.json", "ems.yaml")
MAX_BACKUP_BYTES = 512 * 1024 * 1024


def create_backup(data_dir: Path, db_url: str, include_keys: bool = True) -> bytes:
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
    return buf.getvalue()


def inspect_backup(blob: bytes) -> dict:
    if len(blob) > MAX_BACKUP_BYTES:
        raise ValueError("back-up is te groot")
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


def restore_backup(blob: bytes, data_dir: Path, db_url: str) -> dict:
    """Validate, keep a safety copy of the current files, then replace them."""
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
