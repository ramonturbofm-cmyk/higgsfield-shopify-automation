"""Assigning the primary grid meter (audit P1-26/P1-37).

The primary grid meter drives zero-export, peak limiting and phase protection, so it is never
replaced silently: when another meter already has (or was automatically given) the role, the
caller must pass ``replace=True`` after the user explicitly confirmed the switch.
"""

from __future__ import annotations

ROLES = ("primary_grid_meter", "grid_reference")


class PrimaryMeterConflict(Exception):
    def __init__(self, current_id: str, current_name: str) -> None:
        super().__init__(f"{current_name} is al de primaire netmeter")
        self.current_id, self.current_name = current_id, current_name

    def detail(self) -> dict:
        return {"message": f"{self.current_name} is al de primaire netmeter. Bevestig dat u deze wilt vervangen.",
                "code": "primary_meter_exists", "current": {"id": self.current_id, "name": self.current_name}}


def claim_primary(data: dict, new_id: str, current_id: str | None, replace: bool) -> None:
    """Give ``new_id`` the primary role in config ``data`` (in place). ``current_id`` is the meter the
    engine currently uses (explicit or auto-selected)."""
    devices = data["devices"]
    holders = [d for d in devices if d.get("role") in ROLES and d["id"] != new_id]
    current = current_id if current_id and current_id != new_id else (holders[0]["id"] if holders else None)
    if current and not replace:
        name = next((d["name"] for d in devices if d["id"] == current), current)
        raise PrimaryMeterConflict(current, name)
    for d in devices:
        if d["id"] == new_id:
            d["role"] = "primary_grid_meter"
        elif d.get("role") in ROLES:
            d["role"] = None
