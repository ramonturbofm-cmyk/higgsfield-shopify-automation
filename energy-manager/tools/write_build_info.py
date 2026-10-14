"""Write backend/ems/BUILD_INFO.json (version, git commit, build time) for a release build.

Run before PyInstaller / packaging: ``python tools/write_build_info.py``. The running server reports it in
``/healthz``, ``/api/v1/system/info`` and the ``X-EMS-Build`` header; the web interface shows it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from ems import __version__  # noqa: E402


def commit() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


if __name__ == "__main__":
    info = {"version": __version__, "commit": commit()[:12], "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "run": os.environ.get("GITHUB_RUN_ID")}
    out = ROOT / "backend" / "ems" / "BUILD_INFO.json"
    out.write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    print(f"{out}: {info}")
