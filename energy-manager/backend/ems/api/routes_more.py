"""Automations, backtesting + Auto-Tune (background jobs) and backup/restore."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from ems.api.deps import admin, get_runtime, operator, viewer
from ems.automations.engine import ACTION_TYPES, METRICS, OPS, evaluate_condition, validate_definition
from ems.control.authority import ControlState, can_execute
from ems.core.models import Command, CommandAction
from ems.devices.capabilities import actions_for
from ems.security.auth import Principal
from ems.server.runtime import EMSRuntime
from ems.services.backtest import apply_overrides, autotune, run_backtest
from ems.services.backup import create_backup, inspect_backup, restore_backup

router = APIRouter(prefix="/api/v1")


# ------------------------------------------------------------ automations
class AutomationIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    definition: dict[str, Any]


@router.get("/automations/catalog", tags=["automations"])
async def catalog(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    metrics = dict(METRICS)
    for d in rt.config.devices:
        metrics[f"device.{d.id}.status"] = f"{d.name}: status"
    devices = []
    for d in rt.config.devices:
        md = rt.devices.devices.get(d.id)
        caps = frozenset() if md is None or md.driver is None else md.driver.device_capabilities()
        devices.append({"id": d.id, "name": d.name, "category": d.category.value,
                        # only the actions this device supports, with its own value ranges
                        "actions": [a.to_dict() for a in actions_for(d.category, caps, d.params, rt.config)]})
    return {"metrics": metrics, "ops": list(OPS), "action_types": list(ACTION_TYPES), "devices": devices}


@router.get("/automations/metrics", tags=["automations"])
async def metrics_now(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    return rt.automation_metrics()


@router.get("/automations", tags=["automations"])
async def list_automations(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    return await asyncio.to_thread(rt.db.list_automations)


def _validate(body: AutomationIn, rt: EMSRuntime) -> None:
    try:
        validate_definition(body.definition)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"ongeldige automatisering: {exc}") from exc
    # Device-specific: every override must be an action this device supports, within its own limits.
    for branch in ("then", "else"):
        for a in body.definition.get(branch) or []:
            if a.get("type") not in ("override", "clear_override"):
                continue
            md = rt.devices.devices.get(a.get("device"))
            if md is None:
                raise HTTPException(422, f"ongeldige automatisering: onbekend apparaat {a.get('device')!r}")
            if a["type"] == "clear_override":
                continue
            chk = can_execute(md, Command(md.config.id, CommandAction(a["action"]), a.get("value")), None,
                              ControlState.FULL_CONTROL, config=rt.config)
            if not chk.allowed:
                raise HTTPException(422, f"ongeldige automatisering ({md.config.name}): {'; '.join(chk.reasons)}")


@router.post("/automations", tags=["automations"])
async def create_automation(body: AutomationIn, _: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)):
    _validate(body, rt)
    aid = await asyncio.to_thread(rt.db.save_automation, body.name, body.enabled, body.definition)
    return await asyncio.to_thread(rt.db.get_automation, aid)


@router.put("/automations/{automation_id}", tags=["automations"])
async def update_automation(automation_id: int, body: AutomationIn, _: Principal = Depends(admin),
                            rt: EMSRuntime = Depends(get_runtime)):
    if await asyncio.to_thread(rt.db.get_automation, automation_id) is None:
        raise HTTPException(404, "automatisering niet gevonden")
    _validate(body, rt)
    await asyncio.to_thread(rt.db.save_automation, body.name, body.enabled, body.definition, automation_id)
    rt.automations._state.pop(automation_id, None)
    return await asyncio.to_thread(rt.db.get_automation, automation_id)


@router.delete("/automations/{automation_id}", tags=["automations"])
async def delete_automation(automation_id: int, _: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)):
    if not await asyncio.to_thread(rt.db.delete_automation, automation_id):
        raise HTTPException(404, "automatisering niet gevonden")
    return {"ok": True}


@router.post("/automations/{automation_id}/evaluate", tags=["automations"])
async def evaluate_now(automation_id: int, _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)):
    row = await asyncio.to_thread(rt.db.get_automation, automation_id)
    if row is None:
        raise HTTPException(404, "automatisering niet gevonden")
    m = rt.automation_metrics()
    result = evaluate_condition(row["definition"]["if"], m)
    return {"result": result, "meaning": {True: "voorwaarde is nu WAAR (DAN)", False: "voorwaarde is nu ONWAAR (ANDERS)",
                                          None: "niet te bepalen: een benodigde waarde ontbreekt"}[result],
            "metrics": m}


# ------------------------------------------------------------------- jobs
async def _job(rt: EMSRuntime, kind: str, params: dict, fn) -> int:
    job_id = await asyncio.to_thread(rt.db.create_job, kind, params)
    loop = asyncio.get_running_loop()
    last = {"t": 0.0}

    def progress(f: float) -> None:
        if time.monotonic() - last["t"] > 1:
            last["t"] = time.monotonic()
            loop.call_soon_threadsafe(lambda: asyncio.create_task(
                asyncio.to_thread(rt.db.update_job, job_id, progress=round(f, 3))))

    async def runner():
        try:
            result = await asyncio.to_thread(fn, progress)
            await asyncio.to_thread(rt.db.update_job, job_id, status="done", result=result, progress=1.0,
                                    finished_ts=time.time())
        except Exception as exc:
            await asyncio.to_thread(rt.db.update_job, job_id, status="failed", error=str(exc), finished_ts=time.time())

    asyncio.create_task(runner())
    return job_id


@router.get("/jobs/{job_id}", tags=["backtest"])
async def get_job(job_id: int, _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> dict:
    job = await asyncio.to_thread(rt.db.get_job, job_id)
    if job is None:
        raise HTTPException(404, "taak niet gevonden")
    return job


@router.get("/jobs", tags=["backtest"])
async def list_jobs(kind: str | None = None, _: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)):
    return await asyncio.to_thread(rt.db.list_jobs, kind)


class BacktestIn(BaseModel):
    days: int = 7
    overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


@router.post("/backtest", tags=["backtest"])
async def start_backtest(body: BacktestIn, _: Principal = Depends(operator), rt: EMSRuntime = Depends(get_runtime)):
    try:
        apply_overrides(rt.config, body.overrides)
    except Exception as exc:
        raise HTTPException(422, f"ongeldige instellingen: {exc}") from exc
    if body.days not in (1, 7, 14, 30, 90, 365):
        raise HTTPException(422, "periode moet 1, 7, 14, 30, 90 of 365 dagen zijn")
    cfg, db, mode, now = rt.config, rt.db, rt.config.runtime.mode, rt.now()
    job_id = await _job(rt, "backtest", body.model_dump(),
                        lambda progress: run_backtest(cfg, db, mode, body.days, body.overrides, now, progress))
    return {"job_id": job_id}


@router.post("/autotune", tags=["backtest"])
async def start_autotune(days: int = Query(14, ge=7, le=90), _: Principal = Depends(operator),
                         rt: EMSRuntime = Depends(get_runtime)) -> dict:
    cfg, db, mode, now = rt.config, rt.db, rt.config.runtime.mode, rt.now()

    def work(progress):
        suggestions = autotune(cfg, db, mode, now, days, progress)
        db.kv_set("autotune.suggestions", suggestions)
        return {"suggestions": suggestions}

    return {"job_id": await _job(rt, "autotune", {"days": days}, work)}


@router.get("/autotune/suggestions", tags=["backtest"])
async def suggestions(_: Principal = Depends(viewer), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    return await asyncio.to_thread(rt.db.kv_get, "autotune.suggestions", []) or []


@router.post("/autotune/suggestions/{suggestion_id}/{action}", tags=["backtest"])
async def act_on_suggestion(suggestion_id: str, action: str, p: Principal = Depends(admin),
                            rt: EMSRuntime = Depends(get_runtime)) -> dict:
    """IGNORE / TEST / APPLY — Auto-Tune never changes settings on its own."""
    items = await asyncio.to_thread(rt.db.kv_get, "autotune.suggestions", []) or []
    item = next((s for s in items if s["id"] == suggestion_id), None)
    if item is None:
        raise HTTPException(404, "advies niet gevonden")
    overrides = {item["section"]: {item["key"]: item["suggested"]}}
    result: dict = {}
    if action == "ignore":
        item["status"] = "ignored"
    elif action == "test":
        result = await start_backtest(BacktestIn(days=30, overrides=overrides), p, rt)
        item["status"] = "testing"
    elif action == "apply":
        data = rt.config.model_dump(mode="json")
        data[item["section"]][item["key"]] = item["suggested"]
        await rt.reload(data, p.username, f"Auto-Tune advies toegepast: {item['label']}")
        item["status"] = "applied"
    else:
        raise HTTPException(422, "actie moet ignore, test of apply zijn")
    await asyncio.to_thread(rt.db.kv_set, "autotune.suggestions", items)
    return {"suggestion": item, **result}


# ------------------------------------------------------------------ backup
@router.get("/backup", tags=["backup"])
async def download_backup(include_keys: bool = True, _: Principal = Depends(admin),
                          rt: EMSRuntime = Depends(get_runtime)) -> Response:
    blob = await asyncio.to_thread(create_backup, rt.data_dir, rt.db_url, include_keys)
    name = f"energy-manager-backup-{datetime.now(UTC):%Y%m%d-%H%M%S}.zip"
    return Response(blob, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/backup/restore", tags=["backup"])
async def restore(file: UploadFile = File(...), _: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)):
    blob = await file.read()
    try:
        inspect_backup(blob)
        result = await rt.restart(lambda: restore_backup(blob, rt.data_dir, rt.db_url))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "restored": result["files"], "safety_copy": result["safety_copy"]}


@router.get("/backups", tags=["backup"])
async def list_backups(_: Principal = Depends(admin), rt: EMSRuntime = Depends(get_runtime)) -> list[dict]:
    d = rt.data_dir / "backups"
    return [{"name": f.name, "size": f.stat().st_size, "created": f.stat().st_mtime}
            for f in sorted(d.glob("*.zip"), reverse=True)] if d.exists() else []
