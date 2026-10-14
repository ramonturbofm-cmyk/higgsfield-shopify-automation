"""Central build identity: one place that says *which* Energy Manager is running.

* ``VERSION``     — the release version (``ems.__version__``).
* ``BUILD``       — version + git commit + build time, written to ``BUILD_INFO.json`` by the release build
                     (CI / ``windows/build.ps1``); ``dev`` when running from a source checkout.
* ``ASSET_HASH``  — SHA-256 over every file of the web interface (``web/``), computed at start-up.
                     Every asset URL contains it (``/static/v/<hash>/…``), so a new or changed frontend is
                     always a new URL: a browser or the Windows app (WebView2) can never combine old
                     JavaScript with a new server, whatever it has cached.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from ems import __version__

PKG = Path(__file__).resolve().parent
WEB_DIR = PKG / "web"
BUILD_FILE = PKG / "BUILD_INFO.json"
VERSION = __version__


def _web_hash(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


@lru_cache(maxsize=1)
def info() -> dict:
    data: dict = {}
    try:
        data = json.loads(BUILD_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    asset = _web_hash(WEB_DIR) if WEB_DIR.exists() else "none"
    commit = str(data.get("commit") or "dev")[:12]
    return {
        "version": VERSION,
        "commit": commit,
        "built_at": data.get("built_at"),
        "build": f"{VERSION}+{commit}",
        "asset_hash": asset,
        # A build file from another version means a broken/mixed installation.
        "consistent": data.get("version") in (None, VERSION),
    }


def asset_hash() -> str:
    return info()["asset_hash"]
