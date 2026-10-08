"""Device-specific commissioning procedure (audit P0/P1: no plain confirm(), no generic step list).

Levels: connection_test → read_only → shadow → limited → full. What each level needs:

* read_only  — a successful connection test (simulated Demo devices: always reachable).
* shadow     — + the device has at least one control capability (type schema ∩ driver).
* limited    — + a driver that really writes, built from documentation (``manifest.documentation``)
               or a simulated Demo driver; + all safety parameters of this device type filled in
               (e.g. maximum charge power, rated current); + shadow mode actually observed: the EMS
               produced at least one "EMS zou …" decision for this device while in shadow mode.
* full       — + a passed write test (the device was handed back to its own regulation and still
               answered afterwards); + a driver proven on real hardware (``manifest.verified``) or a
               simulated Demo device — audit P0-08: full control stays blocked while unproven;
               + typed confirmation: the installer types the device name.

A gateway node enforces the documentation/driver/safety-parameter part for its own devices as well
(``node_level_problem``), so a controller can never switch a device on another node to writes the
owner node itself would not allow.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from ems.devices.capabilities import TYPE_SCHEMAS, missing_control_params

LEVELS = ["connection_test", "read_only", "shadow", "limited", "full"]
LEVEL_LABELS = {"connection_test": "1. Verbindingstest", "read_only": "2. Alleen lezen", "shadow": "3. Schaduwmodus",
                "limited": "4. Beperkte regeling", "full": "5. Volledige regeling"}


def _kv(rt, device_id: str, what: str):
    return rt.db.kv_get(f"commissioning.{device_id}.{what}")


def driver_facts(rt, device_id: str) -> dict:
    """What the driver can do for this device, also for devices on another node."""
    cfg = rt.config.device(device_id)
    md = rt.devices.devices[device_id]
    drv = md.driver
    manifest = None if drv is None else drv.manifest
    remote = cfg.driver == "node.remote"
    info = {}
    if remote and drv is not None:
        try:
            info = drv._link().devices.get(drv.remote_id) or {}
        except Exception:
            info = {}
    simulated = bool(manifest and manifest.simulated)
    writes = bool(info.get("write_capable")) if remote else bool(manifest and (manifest.write_capable or simulated))
    documented = bool(info.get("documented", info.get("write_capable"))) if remote else \
        bool(manifest and (simulated or (manifest.write_capable and manifest.documentation)))
    controls = [] if drv is None else sorted(c.value for c in drv.device_capabilities() if c.value.startswith("control_"))
    verified = bool(info.get("verified")) if remote else bool(manifest and (manifest.verified or simulated))
    return {"remote": remote, "simulated": simulated, "writes": writes, "documented": documented,
            "controls": controls, "verified": verified,
            "documentation": None if manifest is None else manifest.documentation}


def node_level_problem(rt, device_id: str, level: str) -> str | None:
    """Checks a gateway node applies to its own device before accepting a level from a controller."""
    if level not in ("limited", "full"):
        return None
    f = driver_facts(rt, device_id)
    cfg = rt.config.device(device_id)
    if not f["controls"]:
        return "dit apparaat is niet bestuurbaar (alleen meten)"
    if not f["writes"] or not f["documented"]:
        return "de driver ondersteunt nog geen gedocumenteerde schrijfopdrachten voor dit apparaat"
    missing = missing_control_params(cfg.category, cfg.params)
    if missing:
        return "veiligheidsinstellingen ontbreken: " + ", ".join(missing)
    if level == "full" and not f["verified"]:
        return "driver niet op echte hardware bewezen; volledige regeling geblokkeerd"
    return None


def build_view(rt, device_id: str) -> dict:
    cfg = rt.config.device(device_id)
    f = driver_facts(rt, device_id)
    test = _kv(rt, device_id, "test")
    write_test = _kv(rt, device_id, "write_test")
    shadow_since = _kv(rt, device_id, "shadow_since")
    decision = rt.engine.last_decisions.get(device_id)
    shadow_seen = bool(shadow_since and decision and decision.get("ts", "") >= shadow_since
                       and decision.get("outcome") in ("shadow", "skipped", "dry_run"))
    missing = missing_control_params(cfg.category, cfg.params)
    reachable = f["simulated"] or bool(test and test.get("reachable"))

    steps = [
        {"id": "connection", "label": "Verbindingstest geslaagd", "done": reachable,
         "detail": None if test is None else ("bereikbaar" if test.get("reachable") else test.get("error"))},
        {"id": "controls", "label": "Bestuurbare functies aanwezig", "done": bool(f["controls"]),
         "detail": ", ".join(f["controls"]) or "alleen meten"},
        {"id": "driver", "label": "Driver schrijft volgens documentatie", "done": f["writes"] and f["documented"],
         "detail": "Demo (gesimuleerd)" if f["simulated"] else (f["documentation"] or "geen schrijvende driver")},
        {"id": "safety_params", "label": f"Veiligheidsinstellingen {TYPE_SCHEMAS[cfg.category].label.lower()}",
         "done": not missing, "detail": ("ontbreekt: " + ", ".join(missing)) if missing else "volledig"},
        {"id": "shadow", "label": "Schaduwmodus waargenomen (EMS zou …)", "done": shadow_seen,
         "detail": f"sinds {shadow_since}" if shadow_since else "nog niet in schaduwmodus geweest"},
        {"id": "write_test", "label": "Schrijftest geslaagd", "done": bool(write_test and write_test.get("ok")),
         "detail": None if write_test is None else write_test.get("detail")},
        {"id": "hardware_verified", "label": "Driver bewezen op echte hardware (vereist voor volledige regeling)",
         "done": f["verified"], "detail": "Demo (gesimuleerd)" if f["simulated"] else (
             "getest" if f["verified"] else "nog niet met echte hardware getest — volledige regeling geblokkeerd")},
    ]
    done = {s["id"]: s["done"] for s in steps}
    levels = {}
    for lvl in LEVELS:
        why = ""
        if lvl in ("shadow", "limited", "full") and not done["controls"]:
            why = "dit apparaat is niet bestuurbaar (alleen meten)"
        elif lvl != "connection_test" and not done["connection"]:
            why = "eerst een geslaagde verbindingstest"
        elif lvl in ("limited", "full") and not done["driver"]:
            why = "de driver ondersteunt nog geen gedocumenteerde schrijfopdrachten voor dit apparaat"
        elif lvl in ("limited", "full") and not done["safety_params"]:
            why = "vul eerst de veiligheidsinstellingen in: " + ", ".join(missing)
        elif lvl in ("limited", "full") and not done["shadow"] and cfg.control_level not in ("limited", "full"):
            why = "eerst schaduwmodus doorlopen tot het EMS een beslissing voor dit apparaat heeft getoond"
        elif lvl == "full" and not done["write_test"]:
            why = "eerst een geslaagde schrijftest"
        elif lvl == "full" and not done["hardware_verified"]:
            why = "deze driver is nog niet op echte hardware bewezen; volledige regeling blijft geblokkeerd"
        levels[lvl] = {"label": LEVEL_LABELS[lvl], "allowed": not why, "reason": why}

    snap = rt.engine.last_snapshot
    st = None if snap is None else snap.devices.get(device_id)
    return {"device_id": device_id, "name": cfg.name, "category": cfg.category.value, "level": cfg.control_level,
            "limited_fraction": cfg.limited_fraction, "levels": levels, "procedure": steps,
            "control_capabilities": f["controls"], "last_test": test, "write_test": write_test,
            "full_requires_typed_name": True,
            "actual": {} if st is None else {str(k): v for k, v in st.values.items()},
            "ems_would_do": decision}


async def run_write_test(rt, device_id: str, now: datetime) -> dict:
    """The least invasive real write: hand the device back to its own regulation, then read it again."""
    md = rt.devices.devices[device_id]
    f = driver_facts(rt, device_id)
    if not f["writes"]:
        result = {"ok": False, "ts": now.isoformat(), "detail": "driver kan niet schrijven"}
    else:
        try:
            await asyncio.wait_for(md.driver.release_control(), 15)
            values = await asyncio.wait_for(md.driver.read(), 15)
            result = {"ok": True, "ts": now.isoformat(),
                      "detail": f"teruggezet naar eigen regeling, daarna {len(values)} waarden gelezen"}
        except Exception as exc:
            result = {"ok": False, "ts": now.isoformat(), "detail": f"{type(exc).__name__}: {exc}"}
    await asyncio.to_thread(rt.db.kv_set, f"commissioning.{device_id}.write_test", result)
    return result
