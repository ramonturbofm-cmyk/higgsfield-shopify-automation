"""Single version source: ``backend/ems/__init__.py`` (``__version__``).

    python tools/set_version.py 0.4.0     # write the version everywhere
    python tools/set_version.py --check   # fail if any file differs (also a unit test)

Files: pyproject.toml, windows-app/package.json + package-lock.json, src-tauri/tauri.conf.json,
src-tauri/Cargo.toml, windows/installer.iss (default AppVersion). The release workflow builds the
installer with the version from ems.__version__.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "backend" / "ems" / "__init__.py"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def source_version() -> str:
    return re.search(r'__version__ = "([^"]+)"', INIT.read_text(encoding="utf-8")).group(1)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def found_versions() -> dict[str, str]:
    out = {"backend/ems/__init__.py": source_version()}
    out["pyproject.toml"] = re.search(r'(?m)^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8")).group(1)
    app = ROOT / "windows-app"
    out["windows-app/package.json"] = json.loads((app / "package.json").read_text(encoding="utf-8"))["version"]
    lock = json.loads((app / "package-lock.json").read_text(encoding="utf-8"))
    out["windows-app/package-lock.json"] = lock["version"]
    out["windows-app/package-lock.json (root package)"] = lock["packages"][""]["version"]
    out["src-tauri/tauri.conf.json"] = json.loads((app / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    out["src-tauri/Cargo.toml"] = re.search(r'(?m)^version = "([^"]+)"', _read(app / "src-tauri" / "Cargo.toml")).group(1)
    lockfile = app / "src-tauri" / "Cargo.lock"
    if lockfile.exists():
        out["src-tauri/Cargo.lock"] = re.search(r'name = "energy-manager"\nversion = "([^"]+)"', _read(lockfile)).group(1)
    out["windows/installer.iss"] = re.search(r'#define AppVersion "([^"]+)"', _read(ROOT / "windows" / "installer.iss")).group(1)
    return out


def set_version(v: str) -> None:
    if not SEMVER.match(v):
        raise SystemExit(f"geen geldige versie: {v}")
    def sub(p: Path, pat: str, rep: str) -> None:
        p.write_text(re.sub(pat, rep, p.read_text(encoding="utf-8"), count=1), encoding="utf-8")
    sub(INIT, r'__version__ = "[^"]+"', f'__version__ = "{v}"')
    sub(ROOT / "pyproject.toml", r'(?m)^version = "[^"]+"', f'version = "{v}"')
    app = ROOT / "windows-app"
    for name in ("package.json", "src-tauri/tauri.conf.json"):
        p = app / name
        d = json.loads(p.read_text(encoding="utf-8"))
        d["version"] = v
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    p = app / "package-lock.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["version"] = v
    d["packages"][""]["version"] = v
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sub(app / "src-tauri" / "Cargo.toml", r'(?m)^version = "[^"]+"', f'version = "{v}"')
    lockfile = app / "src-tauri" / "Cargo.lock"
    if lockfile.exists():
        sub(lockfile, r'(name = "energy-manager"\nversion = )"[^"]+"', rf'\g<1>"{v}"')
    sub(ROOT / "windows" / "installer.iss", r'#define AppVersion "[^"]+"', f'#define AppVersion "{v}"')


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        vs = found_versions()
        bad = {k: v for k, v in vs.items() if v != vs["backend/ems/__init__.py"]}
        print("\n".join(f"{k}: {v}" for k, v in vs.items()))
        sys.exit(1 if bad else 0)
    set_version(sys.argv[1])
    print(f"versie {sys.argv[1]} gezet")
