"""Audit test 17 (simulated part) and P1-26/P1-37: the primary grid meter is never replaced silently,
and grid functions (zero-export) only become available once that meter delivers fresh data."""

import httpx
import pytest

from ems.api.app import create_app
from ems.server.runtime import EMSRuntime


@pytest.fixture
async def demo(tmp_path):
    rt = EMSRuntime(tmp_path / "d", mode="demo", env={})
    await rt.start(loops=False)
    await rt.tick_once()
    app = create_app(rt, start_runtime=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    r = await c.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    yield rt, c
    await c.aclose()
    await rt.stop()


async def test_primary_meter_needs_explicit_replacement(demo):
    rt, c = demo
    old = rt.engine.grid_selection.device_id
    assert old is not None
    body = {"name": "P1 meterkast", "category": "smart_meter", "driver": "homewizard.p1",
            "connection": {"host": "192.0.2.20", "api": "v1"}, "primary_grid_meter": True}
    r = await c.post("/api/v1/devices", json=body)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "primary_meter_exists" and detail["current"]["id"] == old
    assert rt.engine.grid_selection.device_id == old                         # nothing changed
    # Added as an extra meter, then promoted only with explicit replace=true.
    r = await c.post("/api/v1/devices", json={**body, "primary_grid_meter": False})
    new = r.json()["id"]
    assert (await c.post(f"/api/v1/devices/{new}/primary-grid-meter")).status_code == 409
    r = await c.post(f"/api/v1/devices/{new}/primary-grid-meter?replace=true")
    assert r.status_code == 200 and rt.engine.grid_selection.device_id == new
    roles = {d.id: d.role for d in rt.config.devices}
    assert roles[new] == "primary_grid_meter" and roles[old] is None
    # The new P1 meter is offline (no hardware): zero-export closed loop stays unavailable.
    await rt.tick_once()
    feats = rt.engine.last_snapshot.features
    assert not feats["zero_export_closed_loop"]["available"]
    # Back to the working meter: grid functions return once data is fresh.
    assert (await c.post(f"/api/v1/devices/{old}/primary-grid-meter?replace=true")).status_code == 200
    for _ in range(2):
        await rt.tick_once()
    assert rt.engine.last_snapshot.features["zero_export_closed_loop"]["available"]
