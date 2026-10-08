"""Windows sleep check: a controller node that goes to sleep cannot control anything.

Read-only: we query ``powercfg`` and only warn; Windows settings are never changed
without the user. ``powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE`` prints the
minimum, maximum, increment, AC and DC values as hex numbers (labels are localised,
so we take the last two hex values: AC index, DC index — in seconds, 0 = never).
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

_cache: tuple[float, dict | None] = (0.0, None)


def parse_powercfg(output: str) -> dict | None:
    values = re.findall(r"0x([0-9a-fA-F]{8})", output)
    if len(values) < 2:
        return None
    ac, dc = int(values[-2], 16), int(values[-1], 16)
    return {"ac_sleep_after_s": ac, "dc_sleep_after_s": dc}


def windows_sleep_settings() -> dict | None:
    """None when not on Windows or when powercfg is unavailable. Cached for 10 minutes."""
    global _cache
    if not sys.platform.startswith("win"):
        return None
    if time.monotonic() - _cache[0] < 600:
        return _cache[1]
    try:
        out = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        result = parse_powercfg(out)
    except (OSError, subprocess.SubprocessError):
        result = None
    _cache = (time.monotonic(), result)
    return result
