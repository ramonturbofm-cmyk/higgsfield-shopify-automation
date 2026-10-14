"""Device library catalogue: built-in profiles (``profiles/*.json``), user/community profile packages
(``<data_dir>/profiles/*.json``) and the manufacturer/model index (``manufacturers.json``).

Profiles are validated before loading; an invalid file is reported, never half-loaded. User profiles
cannot override a built-in profile, never unlock writes by themselves (see ``DeviceProfile._check``) and
replacing one keeps the previous version for rollback.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ems.devices.library.schema import DeviceProfile

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
BUILTIN_DIR = HERE / "profiles"


class ProfileError(ValueError):
    pass


def _errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:6])


def parse_profile(data: Any, source: str = "builtin") -> DeviceProfile:
    if not isinstance(data, dict):
        raise ProfileError("profiel moet een JSON-object zijn")
    data = {**data, "source": source if source != "builtin" else data.get("source", "builtin")}
    try:
        return DeviceProfile.model_validate(data)
    except ValidationError as exc:
        raise ProfileError(_errors(exc)) from None


@dataclass
class Catalog:
    user_dir: Path | None = None
    profiles: dict[str, DeviceProfile] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    manufacturers: list[dict] = field(default_factory=list)

    def load(self) -> Catalog:
        self.profiles, self.problems = {}, []
        for p in sorted(BUILTIN_DIR.glob("*.json")):
            self._load_file(p, "builtin")
        if self.user_dir is not None and self.user_dir.exists():
            for p in sorted(self.user_dir.glob("*.json")):
                self._load_file(p, "user")
        self.manufacturers = json.loads((HERE / "manufacturers.json").read_text(encoding="utf-8"))["entries"]
        return self

    def _load_file(self, path: Path, source: str) -> None:
        try:
            prof = parse_profile(json.loads(path.read_text(encoding="utf-8")), source)
        except (OSError, ValueError) as exc:
            self.problems.append(f"{path.name}: {exc}")
            log.warning("invalid device profile", extra={"file": path.name, "error": str(exc)})
            return
        if prof.profile_id in self.profiles and source != "builtin":
            self.problems.append(f"{path.name}: profiel-id {prof.profile_id} bestaat al als ingebouwd profiel")
            return
        self.profiles[prof.profile_id] = prof

    def get(self, profile_id: str) -> DeviceProfile:
        try:
            return self.profiles[profile_id]
        except KeyError:
            raise ProfileError(f"onbekend apparaatprofiel {profile_id!r}") from None

    def for_category(self, category: str) -> list[DeviceProfile]:
        return [p for p in self.profiles.values()
                if p.device_category.value == category or category in [r.value for r in p.supported_roles]]

    # ---------------------------------------------------------------- import
    def import_profile(self, data: Any) -> tuple[DeviceProfile, DeviceProfile | None]:
        """Store a user/community profile package (JSON only, validated). Returns (new, previous)."""
        if self.user_dir is None:
            raise ProfileError("geen map voor eigen profielen")
        prof = parse_profile(data, "user" if (data or {}).get("source") != "community" else "community")
        if prof.profile_id in self.profiles and self.profiles[prof.profile_id].source == "builtin":
            raise ProfileError("een ingebouwd profiel kan niet worden vervangen; kies een eigen profile_id")
        self.user_dir.mkdir(parents=True, exist_ok=True)
        target = self.user_dir / f"{prof.profile_id}.json"
        previous = self.profiles.get(prof.profile_id)
        if target.exists():
            hist = self.user_dir / ".history"
            hist.mkdir(exist_ok=True)
            shutil.copy2(target, hist / f"{prof.profile_id}.{int(time.time())}.json")
        target.write_text(json.dumps(prof.model_dump(mode="json", exclude={"source"}) | {"source": prof.source},
                                     indent=1, ensure_ascii=False), encoding="utf-8")
        self.profiles[prof.profile_id] = prof
        return prof, previous

    def rollback(self, profile_id: str) -> DeviceProfile:
        hist = sorted((self.user_dir / ".history").glob(f"{profile_id}.*.json")) if self.user_dir else []
        if not hist:
            raise ProfileError("geen eerdere versie beschikbaar")
        last = hist[-1]
        data = json.loads(last.read_text(encoding="utf-8"))
        prof = parse_profile(data, "community" if data.get("source") == "community" else "user")
        (self.user_dir / f"{profile_id}.json").write_text(last.read_text(encoding="utf-8"), encoding="utf-8")
        last.unlink()
        self.profiles[profile_id] = prof
        return prof

    def remove(self, profile_id: str) -> None:
        prof = self.get(profile_id)
        if prof.source == "builtin":
            raise ProfileError("ingebouwde profielen kunnen niet worden verwijderd")
        (self.user_dir / f"{profile_id}.json").unlink(missing_ok=True)
        del self.profiles[profile_id]

    # --------------------------------------------------------------- listing
    def summary(self, prof: DeviceProfile) -> dict:
        writes = [c for c in prof.writable_controls]
        return {"profile_id": prof.profile_id, "driver_id": prof.driver_id, "manufacturer": prof.manufacturer,
                "model": prof.model, "aliases": prof.model_aliases, "category": prof.device_category.value,
                "roles": [r.value for r in prof.supported_roles], "protocol": prof.protocol,
                "test_status": prof.test_status, "source": prof.source, "simulator_only": prof.simulator_only,
                "read_capabilities": sorted(c.value for c in prof.read_capabilities),
                "controls": [{"action": c.action.value, "capability": c.capability.value, "status": c.status,
                              "writable": c.writable, "min": c.min, "max": c.max,
                              "values": sorted(c.values) if c.values else None} for c in writes],
                "requirements": prof.requirements, "known_limitations": prof.known_limitations,
                "documentation": [s.model_dump() for s in prof.documentation_sources],
                "firmware": {"min": prof.firmware_min, "max": prof.firmware_max},
                "connection_parameters": prof.connection_parameters, "description": prof.description,
                "integration_version": prof.integration_version}

    def brands(self, category: str, include_simulator: bool = False) -> list[dict]:
        """Manufacturer -> models for one category, each with its real support status."""
        out: dict[str, dict] = {}
        for e in self.manufacturers:
            if category not in e["categories"]:
                continue
            for m in e["models"]:
                if category not in m.get("categories", e["categories"]):
                    continue
                prof = self.profiles.get(m.get("profile_id") or "")
                if (m["support"] == "simulator" or (prof is not None and prof.simulator_only)) and not include_simulator:
                    continue
                b = out.setdefault(e["manufacturer"], {"manufacturer": e["manufacturer"], "models": [],
                                                      "note": e.get("note", "")})
                b["models"].append({"model": m["model"], "profile_id": m.get("profile_id"),
                                    "driver_id": m.get("driver_id") or (prof.driver_id if prof else None),
                                    "support": m["support"], "note": m.get("note", ""),
                                    "requirements": m.get("requirements", []), "lab": m.get("lab"),
                                    "test_status": prof.test_status if prof else None})
        return sorted(out.values(), key=lambda b: (b["manufacturer"].startswith("Energy Manager"), b["manufacturer"]))


_catalog: Catalog | None = None


def catalog(user_dir: Path | None = None) -> Catalog:
    global _catalog
    if _catalog is None or (user_dir is not None and _catalog.user_dir != user_dir):
        _catalog = Catalog(user_dir).load()
    return _catalog
