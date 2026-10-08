"""API v1 against a real Demo Mode runtime (loops off; the test drives the clock)."""

import asyncio
import io
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from ems.api.app import create_app
from ems.server.runtime import EMSRuntime


@pytest.fixture
async def env(tmp_path):
    rt = EMSRuntime(tmp_path / "data", mode="demo", env={})
    await rt.start(loops=False)
    for _ in range(3):
        await rt.tick_once()
    app = create_app(rt, start_runtime=False)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    r = await client.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})
    client.headers["Authorization"] = f"Bearer {r.json()['token']}"
    yield rt, client
    await client.aclose()
    await rt.stop()


async def test_public_info_and_auth(env):
    rt, c = env
    anon = httpx.AsyncClient(transport=c._transport, base_url="http://test")
    info = (await anon.get("/api/v1/system/info")).json()
    assert info["mode"] == "demo" and info["setup_required"] is False and info["demo_login"]
    assert (await anon.get("/api/v1/energy/live")).status_code == 401
    assert (await anon.post("/api/v1/auth/login", json={"username": "demo", "password": "x"})).status_code == 401
    for _ in range(10):
        await anon.post("/api/v1/auth/login", json={"username": "demo", "password": "x"})
    assert (await anon.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})).status_code == 429
    assert (await c.get("/api/v1/auth/me")).json()["role"] == "installer"
    assert (await anon.get("/healthz")).json()["ok"] is True
    assert (await anon.get("/api/openapi.json")).status_code == 200
    # Windows app (tauri.localhost) may probe the LAN server; other origins get no CORS grant.
    pre = await anon.options("/api/v1/system/info", headers={
        "Origin": "http://tauri.localhost", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Private-Network": "true"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-private-network"] == "true"
    other = await anon.get("/api/v1/system/info", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    await anon.aclose()


async def test_roles_enforced(env):
    rt, c = env
    assert (await c.post("/api/v1/users", json={"username": "kijker", "password": "geheim123",
                                                "role": "viewer"})).status_code == 200
    v = httpx.AsyncClient(transport=c._transport, base_url="http://test")
    tok = (await v.post("/api/v1/auth/login", json={"username": "kijker", "password": "geheim123"})).json()["token"]
    v.headers["Authorization"] = f"Bearer {tok}"
    assert (await v.get("/api/v1/energy/live")).status_code == 200
    assert (await v.put("/api/v1/settings", json={"battery": {"min_soc": 20}})).status_code == 403
    assert (await v.post("/api/v1/overrides", json={"device": "battery", "action": "battery_standby"})).status_code == 403
    await v.aclose()
    t = (await c.post("/api/v1/auth/tokens", json={"name": "ha", "role": "viewer"})).json()["token"]
    api = httpx.AsyncClient(transport=c._transport, base_url="http://test", headers={"Authorization": f"Bearer {t}"})
    assert (await api.get("/api/v1/devices")).status_code == 200
    await api.aclose()


async def test_live_shows_real_simulated_values(env):
    rt, c = env
    live = (await c.get("/api/v1/energy/live")).json()
    assert live["mode"] == "demo" and live["flows"]["soc_pct"] is not None
    assert live["grid_meter"]["device_id"] == "p1" and live["grid_meter"]["status"] == "primary_explicit"
    assert live["features"]["zero_export_closed_loop"]["available"] is True
    assert set(live["devices"]) == {"p1", "pv_roof", "pv_garage", "battery", "heatpump", "ev"}
    assert live["price"]["import"] is not None


async def test_devices_crud_test_and_commissioning(env):
    rt, c = env
    devs = (await c.get("/api/v1/devices")).json()
    assert any(d["is_primary_grid_meter"] for d in devs)
    drivers = (await c.get("/api/v1/drivers")).json()
    hw = next(d for d in drivers if d["driver_id"] == "homewizard.p1")
    assert hw["grid_meter_kind"] == "homewizard_p1" and hw["verified"] is False
    r = await c.post("/api/v1/devices/test", json={"driver": "mock.battery", "category": "battery"})
    assert r.status_code == 200
    r = await c.post("/api/v1/devices", json={"name": "Batterij 2", "category": "battery", "driver": "mock.battery",
                                              "params": {"capacity_kwh": 5}})
    assert r.status_code == 200          # demo: simulated devices can be added, the demo world keeps its state
    assert "batterij_2" in rt.live()["devices"]
    r = await c.post("/api/v1/devices", json={"name": "Garage P1", "category": "smart_meter",
                                              "driver": "homewizard.p1", "connection": {"host": "192.0.2.10"}})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["control_level"] == "read_only" and new["status"] in ("offline", "unknown")
    com = (await c.get(f"/api/v1/devices/{new['id']}/commissioning")).json()
    assert com["levels"]["shadow"]["allowed"] is False and "niet bestuurbaar" in com["levels"]["shadow"]["reason"]
    assert (await c.delete(f"/api/v1/devices/{new['id']}")).status_code == 200
    com = (await c.get("/api/v1/devices/battery/commissioning")).json()
    assert com["level"] == "full" and com["levels"]["shadow"]["allowed"]
    r = await c.put("/api/v1/devices/battery/commissioning", json={"level": "shadow"})
    assert r.status_code == 200 and r.json()["level"] == "shadow"
    # Full control needs the real procedure: a shadow decision, a write test and the typed device name.
    com = r.json()
    assert com["levels"]["full"]["allowed"] is False and {s["id"] for s in com["procedure"]} >= {
        "connection", "safety_params", "shadow", "write_test"}
    await rt.tick_once()
    assert (await c.post("/api/v1/devices/battery/commissioning/write-test")).json()["ok"]
    name = rt.config.device("battery").name
    assert (await c.put("/api/v1/devices/battery/commissioning", json={"level": "full"})).status_code == 409
    r = await c.put("/api/v1/devices/battery/commissioning", json={"level": "full", "confirm_text": "ja"})
    assert r.status_code == 409 and name in r.text
    r = await c.put("/api/v1/devices/battery/commissioning", json={"level": "full", "confirm_text": name.upper()})
    assert r.status_code == 200, r.text


async def test_shadow_mode_reports_without_writing(env):
    rt, c = env
    await c.put("/api/v1/devices/battery/commissioning", json={"level": "shadow"})
    await rt.optimizer.run(rt.engine.last_snapshot, rt.now(), "test")
    rt.site.components["battery"].mode = "auto"
    applied_before = len(rt.devices.driver("battery").applied)
    for _ in range(3):
        await rt.tick_once()
    assert len(rt.devices.driver("battery").applied) == applied_before
    com = (await c.get("/api/v1/devices/battery/commissioning")).json()
    assert com["ems_would_do"]["outcome"] in ("shadow", "skipped") and com["ems_would_do"]["reasons"]
    assert "battery_soc_pct" in com["actual"]


async def test_demo_world_survives_settings_change(env):
    rt, c = env
    rt.site.components["battery"].soc_pct = 77.0
    t_before = rt.now()
    await c.put("/api/v1/settings", json={"battery": {"min_soc": 11}})
    assert rt.site.components["battery"].soc_pct == pytest.approx(77.0, abs=0.1)
    assert rt.now() >= t_before


async def test_override_is_applied_and_expires(env):
    rt, c = env
    r = await c.post("/api/v1/overrides", json={"device": "battery", "action": "battery_charge", "value": 3000,
                                                "duration_min": 30})
    assert r.status_code == 200
    await rt.tick_once()
    assert rt.site.components["battery"].mode == "charge"
    assert len((await c.get("/api/v1/overrides")).json()) == 1
    assert (await c.post("/api/v1/overrides", json={"device": "p1", "action": "battery_charge"})).status_code == 422
    assert (await c.delete("/api/v1/overrides/battery")).json()["cleared"] == 1


async def test_prices_tariff_plan(env):
    rt, c = env
    prices = (await c.get("/api/v1/prices?hours=24")).json()
    assert len(prices["points"]) > 50 and prices["status"]["provider"] == "demo"
    p0 = prices["points"][0]
    assert p0["import"] > p0["spot"]
    prev = (await c.get("/api/v1/tariff/preview?spot=0.10")).json()
    assert prev["import_price"] == pytest.approx((0.10 + 0.02 + 0.10) * 1.21)
    r = await c.put("/api/v1/tariff", json={"import_markup_eur_kwh": 0.03})
    assert r.status_code == 200 and r.json()["import_markup_eur_kwh"] == 0.03
    assert (await c.put("/api/v1/tariff", json={"vat_pct": 500})).status_code == 422
    plan = (await c.post("/api/v1/optimizer/run")).json()
    assert plan["status"] == "optimal" and len(plan["slots"]) > 50
    assert plan["expected_benefit"] is not None
    fc = (await c.get("/api/v1/forecast?hours=24")).json()
    assert fc["sources"]["pv"].startswith("demo")


async def test_settings_validation_and_versions(env):
    rt, c = env
    bad = await c.put("/api/v1/settings", json={"battery": {"min_soc": 50, "reserve_soc": 20}})
    assert bad.status_code == 422 and "min_soc" in bad.json()["detail"]
    ok = await c.put("/api/v1/settings", json={"battery": {"min_soc": 12}})
    assert ok.status_code == 200 and ok.json()["battery"]["min_soc"] == 12
    assert rt.config.battery.min_soc == 12
    versions = (await c.get("/api/v1/settings/versions")).json()
    assert versions and versions[0]["comment"].startswith("instellingen")
    exported = (await c.get("/api/v1/config/export")).text
    assert "Demo Home" in exported
    assert (await c.get("/api/v1/settings/schema")).json()["$defs"]["TariffConfig"]


async def test_history_export_finance(env):
    rt, c = env
    for _ in range(400):         # ~33 simulated minutes at 5 s per tick
        await rt.tick_once()
    await asyncio.to_thread(rt.recorder.aggregate, rt.now())
    raw = (await c.get("/api/v1/history?hours=1&resolution=raw")).json()["rows"]
    assert len(raw) > 100 and raw[-1]["pv_w"] is not None
    slots = (await c.get("/api/v1/history?hours=2&resolution=15m")).json()["rows"]
    assert slots and slots[0]["import_kwh"] is not None
    csv_text = (await c.get("/api/v1/history/export?hours=2&fmt=csv&resolution=15m")).text
    assert csv_text.startswith("local_time,")
    excel = (await c.get("/api/v1/history/export?hours=2&fmt=excel&resolution=15m")).text
    assert excel.startswith("﻿local_time;")
    fin = (await c.get("/api/v1/finance/summary?period=2d")).json()
    assert fin["available"] and "with_ems_eur" in fin["comparison"]


async def test_automation_fires_override(env):
    rt, c = env
    bad = await c.post("/api/v1/automations", json={"name": "x", "definition": {"if": {"metric": "nope", "op": "<",
                                                                                       "value": 1}}})
    assert bad.status_code == 422
    rule = {"name": "Goedkoop laden", "definition": {
        "if": {"all": [{"metric": "price.import", "op": ">", "value": -10},
                       {"metric": "battery.soc", "op": "<", "value": 100}]},
        "then": [{"type": "override", "device": "battery", "action": "battery_charge", "value": 2000,
                  "duration_min": 15}, {"type": "notify", "message": "Laden gestart"}]}}
    r = await c.post("/api/v1/automations", json=rule)
    assert r.status_code == 200
    aid = r.json()["id"]
    ev = (await c.post(f"/api/v1/automations/{aid}/evaluate")).json()
    assert ev["result"] is True
    rt._last_automation = 0
    await rt.tick_once()
    assert any(o.user.startswith("automatisering") for o in rt.engine.overrides.active())
    notes = (await c.get("/api/v1/notifications")).json()
    assert any(n["message"] == "Laden gestart" for n in notes)


async def test_backtest_and_autotune_jobs(env):
    rt, c = env
    job = (await c.post("/api/v1/backtest", json={"days": 1, "overrides": {"battery": {"min_arbitrage_spread_eur": 0.2}}})).json()
    for _ in range(200):
        j = (await c.get(f"/api/v1/jobs/{job['job_id']}")).json()
        if j["status"] != "running":
            break
        await asyncio.sleep(0.1)
    assert j["status"] == "done", j.get("error")
    res = j["result"]
    assert res["source"].startswith("demowereld") and res["current"]["days"] == 1
    assert "difference_eur" in res
    assert (await c.post("/api/v1/backtest", json={"days": 3})).status_code == 422


async def test_backup_restore_roundtrip(env):
    rt, c = env
    await c.put("/api/v1/settings", json={"site": {"name": "Voor back-up"}})
    # Default: no keys (audit P0-05).
    plain = (await c.post("/api/v1/backup", json={})).content
    names = set(zipfile.ZipFile(io.BytesIO(plain)).namelist())
    assert {"manifest.json", "ems.yaml", "ems.db", "secrets.enc"} <= names and "secret.key" not in names
    # Keys only with a password; the file is then encrypted as a whole.
    assert (await c.post("/api/v1/backup", json={"include_keys": True})).status_code == 422
    assert (await c.post("/api/v1/backup", json={"include_keys": True, "password": "kort"})).status_code == 422
    pw = "lang-genoeg-wachtwoord"
    blob = (await c.post("/api/v1/backup", json={"include_keys": True, "password": pw})).content
    assert blob.startswith(b"EMSBACKUP1") and b"secret.key" not in blob
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(blob))
    await c.put("/api/v1/settings", json={"site": {"name": "Na back-up"}})
    files = {"file": ("b.emsbackup", blob, "application/octet-stream")}
    assert (await c.post("/api/v1/backup/restore", files=files)).status_code == 422                 # no password
    assert (await c.post("/api/v1/backup/restore", files=files, data={"password": "fout-wachtwoord"})).status_code == 422
    r = await c.post("/api/v1/backup/restore", files=files, data={"password": pw})
    assert r.status_code == 200, r.text
    assert "secret.key" in r.json()["restored"]
    assert rt.config.site.name == "Voor back-up"
    assert (await c.get("/api/v1/auth/me")).status_code == 200      # same jwt secret restored
    bad = await c.post("/api/v1/backup/restore", files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert bad.status_code == 422


def test_websocket_live_updates(tmp_path):
    rt = EMSRuntime(tmp_path / "ws", mode="demo", env={})
    app = create_app(rt, loops=False)
    with TestClient(app) as client:
        client.portal.call(rt.tick_once)
        r = client.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})
        csrf = r.json()["csrf"]
        ticket = client.post("/api/v1/auth/ws-ticket", headers={"X-CSRF-Token": csrf}).json()["ticket"]
        with client.websocket_connect(f"/api/v1/ws?ticket={ticket}") as ws:
            assert ws.receive_json()["type"] == "hello"
            first = ws.receive_json()
            assert first["type"] == "live" and first["data"]["flows"] is not None
            client.portal.call(rt.tick_once)
            msg = ws.receive_json()
            while msg["type"] != "live":          # decisions may arrive before the live update
                msg = ws.receive_json()
            assert msg["data"]["timestamp"] > first["data"]["timestamp"]
        from starlette.websockets import WebSocketDisconnect
        token = r.json()["token"]
        for url in (f"/api/v1/ws?ticket={ticket}",      # tickets are single-use
                    f"/api/v1/ws?token={token}",        # tokens are never accepted in the URL
                    "/api/v1/ws?ticket=wrong"):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(url) as ws:
                    ws.receive_json()


async def test_production_mode_has_no_fake_data(tmp_path):
    rt = EMSRuntime(tmp_path / "prod", env={})
    await rt.start(loops=False)
    await rt.tick_once()
    live = rt.live()
    assert live["mode"] == "production" and live["grid_meter"]["status"] == "no_primary_grid_meter"
    assert live["price"]["import"] is None and live["flows"]["grid_w"] is None
    assert live["features"]["zero_export_closed_loop"]["available"] is False
    app = create_app(rt, start_runtime=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    assert (await c.get("/api/v1/system/info")).json()["setup_required"] is True
    r = await c.post("/api/v1/auth/setup", json={"username": "ramon", "password": "kort"})
    assert r.status_code == 422
    r = await c.post("/api/v1/auth/setup", json={"username": "ramon", "password": "voldoende-lang"})
    assert r.status_code == 200
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert (await c.post("/api/v1/auth/setup", json={"username": "x", "password": "voldoende-lang"})).status_code == 409
    r = await c.post("/api/v1/devices", json={"name": "Nep", "category": "battery", "driver": "mock.battery"})
    assert r.status_code == 422 and "Demo Mode" in r.json()["detail"]
    await c.aclose()
    await rt.stop()


async def test_generic_device_password_goes_to_secret_store(env):
    rt, c = env
    body = {"name": "MQTT meter", "category": "smart_meter", "driver": "generic.mqtt",
            "connection": {"host": "127.0.0.1", "port": 1, "username": "ems", "password": "s3cret-pw",
                           "values": [{"metric": "grid_power_w", "topic": "p1/power"}]}}
    r = await c.post("/api/v1/devices", json=body)
    assert r.status_code == 200, r.text
    dev = r.json()
    assert dev["connection"]["password"] == "********" and dev["control_level"] == "read_only"
    assert "s3cret-pw" not in rt.config_path.read_text()
    assert rt.secrets.get(f"device.{dev['id']}.password") == "s3cret-pw"
    # Saving the masked value keeps the stored secret.
    r = await c.put(f"/api/v1/devices/{dev['id']}", json={"connection": {"password": "********", "port": 2}})
    assert r.status_code == 200 and rt.secrets.get(f"device.{dev['id']}.password") == "s3cret-pw"
    assert (await c.delete(f"/api/v1/devices/{dev['id']}")).status_code == 200
    assert rt.secrets.get(f"device.{dev['id']}.password") is None


async def test_entsoe_token_is_stored_encrypted(env):
    rt, c = env
    r = await c.put("/api/v1/settings", json={"prices": {"provider": "entsoe", "entsoe_token": "abc-123-token"}})
    assert r.status_code == 200, r.text
    assert r.json()["prices"]["entsoe_token"] == "********"
    assert "abc-123-token" not in rt.config_path.read_text()
    assert rt.secrets.get("prices.entsoe_token") == "abc-123-token"
    assert rt.prices.provider is not None and rt.prices.provider.token == "abc-123-token"
    # Saving again with the masked value keeps the token; an empty value removes it.
    await c.put("/api/v1/settings", json={"prices": {"provider": "entsoe", "entsoe_token": "********"}})
    assert rt.secrets.get("prices.entsoe_token") == "abc-123-token"
    await c.put("/api/v1/settings", json={"prices": {"provider": "manual", "entsoe_token": ""}})
    assert rt.secrets.get("prices.entsoe_token") is None


async def test_energyzero_selectable_without_token(env):
    rt, c = env
    r = await c.put("/api/v1/settings", json={"prices": {"provider": "energyzero", "energyzero_interval": "hour"}})
    assert r.status_code == 200, r.text
    assert type(rt.prices.provider).__name__ == "EnergyZeroProvider" and rt.prices.provider.resolution == 60
    assert "token" not in (rt.prices.last_error or "")   # (a fetch error without network is fine here)


async def test_health_installation_status_and_quality(env):
    rt, c = env
    for _ in range(2):
        await rt.tick_once()
    live = (await c.get("/api/v1/energy/live")).json()
    assert live["quality"]["grid"] == "GOOD" and live["quality"]["house"] == "CALCULATED"
    assert live["balance"]["ok"] is True
    assert live["ems_status"]["state"] in ("AUTOMATIC", "DEGRADED", "SHADOW_MODE")
    h = (await c.get("/api/v1/health")).json()
    assert 0 <= h["score"] <= 100 and any(c_["key"] == "grid_meter" and c_["state"] == "ok" for c_ in h["checks"])
    inst = (await c.get("/api/v1/installation")).json()
    keys = {i["key"]: i for i in inst["items"]}
    assert keys["grid_meter"]["state"] == "ONLINE" and keys["battery"]["state"] == "ONLINE"
    r = await c.post("/api/v1/overrides", json={"device": "battery", "action": "battery_standby", "duration_min": 30})
    assert r.status_code == 200
    await rt.tick_once()
    live = (await c.get("/api/v1/energy/live")).json()
    assert live["ems_status"]["state"] == "MANUAL_OVERRIDE"
    assert live["now"]["what"] and live["now"]["why"] and live["now"]["limits"]
    s = (await c.get("/api/v1/settlement/compare?period=7d")).json()
    assert [x["rules"] for x in s["scenarios"]] == ["NL-2026", "NL-2027"]
    taxes = (await c.get("/api/v1/tariff/taxes")).json()
    assert any(t["year"] == 2025 for t in taxes["table"])
