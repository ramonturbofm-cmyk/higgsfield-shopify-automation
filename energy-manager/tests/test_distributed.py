"""Distributed acceptance test: controller node + device-gateway node over the real node API.

Gateway ("Meterkast Pi"): demo devices (simulated P1 meter, battery, PV ...), role gateway.
Controller ("Mini-PC"): no local devices, role controller, imports the gateway's devices.
Chain: P1 data gateway -> controller; command controller -> gateway; safety validation on
the gateway; lease fencing against a second controller; partition -> devices released.
"""

import time

import httpx
import pytest

from ems.api.app import create_app
from ems.core.models import Metric
from ems.server.runtime import EMSRuntime


async def _runtime(path, mode, preset, name):
    rt = EMSRuntime(path, mode=mode, env={})
    await rt.start(loops=False)
    data = rt.config.model_dump(mode="json")
    data["node"] = {**data["node"], "role_preset": preset, "name": name, "advertise": False}
    await rt.reload(data, "test", "rol instellen")
    app = create_app(rt, start_runtime=False)
    return rt, app


async def _login(app, username, password, setup=False):
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    path = "/api/v1/auth/setup" if setup else "/api/v1/auth/login"
    r = await c.post(path, json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return c


@pytest.fixture
async def cluster(tmp_path):
    gw, gw_app = await _runtime(tmp_path / "pi", "demo", "gateway", "Meterkast Pi")
    ctl, ctl_app = await _runtime(tmp_path / "ctl", "production", "controller", "Mini-PC")
    transport = httpx.ASGITransport(app=gw_app)
    ctl.nodes.transport = transport
    for _ in range(3):
        await gw.tick_once()
    g = await _login(gw_app, "demo", "demo")
    c = await _login(ctl_app, "beheer", "geheim-wachtwoord", setup=True)
    yield gw, ctl, g, c, gw_app, transport
    await g.aclose()
    await c.aclose()
    await ctl.stop()
    await gw.stop()


async def _pair(g, c, address="http://pi.local:8080"):
    code = (await g.post("/api/v1/nodes/pairing-code")).json()["code"]
    r = await c.post("/api/v1/nodes/pair", json={"address": address, "code": code})
    assert r.status_code == 200, r.text
    return r.json()["node"]["node_id"]


async def test_full_chain_controller_gateway(cluster):
    gw, ctl, g, c, _, _ = cluster
    assert gw.nodes.identity.is_controller is False and ctl.nodes.identity.is_controller
    # Wrong code is refused, the right one pairs.
    bad = await c.post("/api/v1/nodes/pair", json={"address": "http://pi.local:8080", "code": "000000"})
    assert bad.status_code == 422
    gid = await _pair(g, c)
    nodes = (await c.get("/api/v1/nodes")).json()
    pi = next(n for n in nodes["nodes"] if n["node_id"] == gid)
    assert pi["online"] and pi["lease"]["granted"] and pi["platform"] in ("LINUX", "WINDOWS", "RASPBERRY_PI", "OTHER")
    remote = {d["id"]: d for d in pi["remote_devices"]}
    assert {"p1", "battery"} <= set(remote) and remote["p1"]["is_primary_grid_meter"]
    # The gateway sees the controller as its controller.
    gnodes = (await g.get("/api/v1/nodes")).json()
    assert any(n["relation"] == "controller" and n["name"] == "Mini-PC" for n in gnodes["nodes"])

    # Import the P1 meter (as primary grid meter) and the battery into the controller.
    r = await c.post(f"/api/v1/nodes/{gid}/devices/p1/import", json={"primary_grid_meter": True})
    assert r.status_code == 200, r.text
    meter_id = r.json()["device_id"]
    r = await c.post(f"/api/v1/nodes/{gid}/devices/battery/import", json={})
    batt_id = r.json()["device_id"]
    assert ctl.config.device(batt_id).control_level == "read_only"

    # P1 data: Pi -> controller.
    await gw.tick_once()
    await ctl.tick_once()
    snap = ctl.engine.last_snapshot
    assert snap.grid_device_id == meter_id and snap.grid_valid
    pi_power = gw.engine.last_snapshot.grid_power_w
    assert snap.grid_power_w == pytest.approx(pi_power, abs=1.0)
    assert snap.battery_soc_pct is not None

    # Commissioning: test -> shadow (nothing written) -> full (pushed to the gateway as well).
    assert (await c.post(f"/api/v1/devices/{batt_id}/test")).json()["reachable"]
    assert (await c.put(f"/api/v1/devices/{batt_id}/commissioning", json={"level": "shadow"})).status_code == 200
    assert gw.config.device("battery").control_level == "shadow"
    await gw.tick_once()
    await ctl.tick_once()
    assert (await c.post(f"/api/v1/devices/{batt_id}/commissioning/write-test")).json()["ok"]
    r = await c.put(f"/api/v1/devices/{batt_id}/commissioning",
                    json={"level": "full", "confirm_text": ctl.config.device(batt_id).name})
    assert r.status_code == 200, r.text
    assert gw.config.device("battery").control_level == "full"

    # Battery command: controller -> Pi, validated on the Pi, executed by the Pi's driver.
    gw.site.component("battery").soc_pct = 50.0
    await gw.tick_once()
    await ctl.nodes.step()           # heartbeat renews the lease
    # A manual value above the battery's own limit is refused up front (backend range validation).
    r = await c.post("/api/v1/overrides", json={"device": batt_id, "action": "battery_charge", "value": 20000,
                                                "duration_min": 30})
    assert r.status_code == 422 and "6000" in r.text
    r = await c.post("/api/v1/overrides", json={"device": batt_id, "action": "battery_charge", "value": 6000,
                                                "duration_min": 30})
    assert r.status_code == 200, r.text
    assert r.json()["executes"] is True
    await ctl.tick_once()
    last = ctl.engine.last_decisions[batt_id]
    assert last["outcome"] == "sent", last
    assert batt_id in ctl.engine.last_decisions
    pi_dec = gw.db.recent_decisions(10, device="battery")
    assert pi_dec and pi_dec[0]["outcome"] == "sent" and pi_dec[0]["source"] == "node:Mini-PC"
    assert pi_dec[0]["new_value"] == 6000
    # The Pi validates on its own, even if a controller sends an out-of-range value directly.
    link = ctl.nodes.links[gid]
    from ems.core.models import Command, CommandAction
    res = await link.command("battery", Command("x", CommandAction.BATTERY_CHARGE, 50000))
    assert res["command"]["value"] == 6000 and any("Veiligheidscontrole" in n for n in res["notes"])
    for _ in range(3):
        await gw.tick_once()
    assert gw.engine.last_snapshot.battery_power_w == pytest.approx(6000, rel=0.1)
    # Result back: Pi -> controller.
    await ctl.tick_once()
    assert ctl.engine.last_snapshot.battery_power_w == pytest.approx(6000, rel=0.1)
    assert batt_id in {k for k in ctl.engine.last_decisions}


async def test_split_brain_and_partition(cluster, tmp_path):
    gw, ctl, g, c, gw_app, transport = cluster
    gid = await _pair(g, c)
    r = await c.post(f"/api/v1/nodes/{gid}/devices/battery/import", json={})
    batt_id = r.json()["device_id"]
    await c.post(f"/api/v1/devices/{batt_id}/test")
    assert (await c.put(f"/api/v1/devices/{batt_id}/commissioning", json={"level": "shadow"})).status_code == 200
    await gw.tick_once()
    await ctl.tick_once()
    assert (await c.post(f"/api/v1/devices/{batt_id}/commissioning/write-test")).json()["ok"]
    r = await c.put(f"/api/v1/devices/{batt_id}/commissioning",
                    json={"level": "full", "confirm_text": ctl.config.device(batt_id).name})
    assert r.status_code == 200, r.text
    gw.site.component("battery").soc_pct = 50.0
    await gw.tick_once()

    # A second controller pairs with the same gateway: no lease while the first one holds it.
    ctl2, ctl2_app = await _runtime(tmp_path / "ctl2", "production", "controller", "Laptop")
    ctl2.nodes.transport = transport
    c2 = await _login(ctl2_app, "beheer", "geheim-wachtwoord", setup=True)
    try:
        await _pair(g, c2)
        link2 = ctl2.nodes.links[gid]
        assert link2.lease_ok is False and "Mini-PC" in (link2.lease_error or "")
        # Forged command with a guessed epoch is refused by the gateway.
        link2.lease_ok, link2.epoch = True, gw.nodes.lease.state.epoch
        from ems.core.models import Command, CommandAction
        from ems.nodes.link import NodeLinkError
        with pytest.raises(NodeLinkError, match="regelrecht"):
            await link2.command("battery", Command("x", CommandAction.BATTERY_DISCHARGE, 3000))
    finally:
        await c2.aclose()
        await ctl2.stop()

    # The real controller drives the battery.
    await ctl.nodes.step()
    await c.post("/api/v1/overrides", json={"device": batt_id, "action": "battery_charge", "value": 3000,
                                            "duration_min": 30})
    await ctl.tick_once()
    await gw.tick_once()
    await gw.tick_once()
    assert "battery" in gw.nodes.remote_controlled
    assert gw.engine.last_snapshot.battery_power_w == pytest.approx(3000, rel=0.1)

    # Stale command (issued long ago) is never executed.
    link = ctl.nodes.links[gid]
    res = await link._req("POST", "/devices/battery/command", json={
        "command_id": "stale-command-0001", "action": "battery_discharge", "value": 1000, "epoch": link.epoch,
        "issued_ts": time.time() - 600})
    assert res["outcome"] == "rejected" and "verouderde" in res["error"]

    # Replay: the same command id is executed at most once.
    body = {"command_id": "replay-test-0000001", "action": "battery_auto", "value": None, "epoch": link.epoch,
            "issued_ts": time.time()}
    first = await link._req("POST", "/devices/battery/command", json=body)
    assert first["outcome"] != "rejected", first
    again = await link._req("POST", "/devices/battery/command", json=body)
    assert again["outcome"] == "rejected" and "replay" in again["error"]

    # Clock skew between controller and gateway: no commands until time is synchronised.
    link.clock_offset_s = 45.0
    from ems.nodes.link import NodeLinkError as _NLE
    with pytest.raises(_NLE, match="klokverschil"):
        await link.command("battery", Command("x", CommandAction.BATTERY_AUTO))
    link.clock_offset_s = 0.0

    # Partition: no more heartbeats -> lease expires on the Pi -> battery back to its own control.
    gw.nodes.lease.clock = lambda: time.time() + 3600
    await gw.nodes.step()
    assert not gw.nodes.remote_controlled
    for _ in range(2):
        await gw.tick_once()
    assert gw.site.component("battery").mode == "auto"
    released = gw.db.recent_decisions(5, device="battery")
    assert any("onbereikbaar" in " ".join(r["reasons"]) for r in released)
    gw.nodes.lease.clock = time.time

    # The controller cannot reach the Pi: remote reads fail -> device offline, no crash.
    link._client._transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    await ctl.nodes.step()
    assert link.online is False and link.down_since is not None
    await ctl.tick_once()
    assert ctl.engine.last_snapshot.devices[batt_id].status.value in ("offline", "stale")

    # Back online -> the outage window is filled from the Pi's buffered history, without duplicates.
    link._client._transport = transport
    await gw.tick_once()
    await gw.recorder.record(gw.engine.last_snapshot)
    link.down_since = time.time() - 120
    await ctl.nodes.step()
    assert link.online and not link.gaps
    rows = ctl.db.device_samples_between(batt_id, time.time() - 3600, time.time() + 60)
    assert rows and all(r["device_id"] == batt_id for r in rows)
    n_before = len(rows)
    link.gaps.append((time.time() - 3600, time.time()))
    await ctl.nodes.step()
    assert len(ctl.db.device_samples_between(batt_id, time.time() - 3600, time.time() + 60)) == n_before


async def test_local_controller_and_remote_cannot_both_drive(cluster):
    """An all-in-one node that is also paired as gateway: local engine and remote controller share one lease."""
    gw, ctl, g, c, _, _ = cluster
    data = gw.config.model_dump(mode="json")
    data["node"]["role_preset"] = "all_in_one"
    await gw.reload(data, "test", "all in one")
    gw.site.component("battery").soc_pct = 50.0
    r = await g.post("/api/v1/overrides", json={"device": "battery", "action": "battery_charge", "value": 2000})
    assert r.status_code == 200
    await gw.tick_once()                              # the local engine drives the battery -> holds the lease
    assert gw.nodes.lease.state.holder == gw.nodes.identity.node_id
    gid = await _pair(g, c)
    link = ctl.nodes.links[gid]
    assert link.lease_ok is False and "lokaal" in (link.lease_error or "")
    # The other way round: once the remote controller holds the lease, the local engine is refused.
    gw.nodes.lease.state.expires_at = 0
    await ctl.nodes.step()
    assert link.lease_ok
    await gw.tick_once()
    dec = gw.engine.last_decisions["battery"]
    assert dec["outcome"] == "rejected" or gw.nodes.lease.state.holder == link.own_node_id
    assert Metric.BATTERY_SOC_PCT  # (sanity: import used)
