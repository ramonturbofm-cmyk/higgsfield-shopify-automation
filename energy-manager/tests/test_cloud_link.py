"""Local EMS <-> Energy Manager Cloud, end to end (cloud app in-process via ASGI): pairing from the local UI,
heartbeat, remote commands only with *both* switches on and only through the local checks, and the local
EMS keeps regulating when the cloud is offline, the licence expires or the pairing is revoked."""

import sys
from pathlib import Path

import httpx
import pytest

pytest.importorskip("argon2")
pytest.importorskip("pyotp")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cloud"))

from emcloud.app import create_app as create_cloud  # noqa: E402
from emcloud.app import maintenance
from emcloud.config import Settings  # noqa: E402

from ems.api.app import create_app  # noqa: E402
from ems.cloud.link import validate_cloud_url  # noqa: E402
from ems.server.runtime import EMSRuntime  # noqa: E402

CLOUD = "https://cloud.example.com"


class Clock:
    def __init__(self):
        import time
        self.t = time.time()

    def __call__(self):
        self.t += 0.001          # strictly increasing: stable ordering of commands
        return self.t


@pytest.fixture
async def setup(tmp_path):
    clock = Clock()
    cloud = create_cloud(Settings(database_url=f"sqlite:///{tmp_path}/cloud.db", base_url=CLOUD), clock=clock, background=False)
    cc = httpx.AsyncClient(transport=httpx.ASGITransport(app=cloud), base_url=CLOUD)
    await cc.post("/api/v1/auth/register", json={"email": "eigenaar@example.com", "password": "correct horse battery"})
    from emcloud.db import Outbox
    from sqlalchemy import select
    with cloud.state.sessionmaker() as db:
        body = db.scalars(select(Outbox)).first().body
    await cc.post("/api/v1/auth/verify", json={"token": body.split("#/verify/")[1].split()[0]})
    r = await cc.post("/api/v1/auth/login", json={"email": "eigenaar@example.com", "password": "correct horse battery"})
    cc.headers["Authorization"] = f"Bearer {r.json()['token']}"
    cc.cookies.clear()
    org = (await cc.get("/api/v1/auth/me")).json()["organizations"][0]["id"]
    site = (await cc.post(f"/api/v1/orgs/{org}/sites", json={"name": "Demo-woning"})).json()["id"]

    rt = EMSRuntime(tmp_path / "ems", mode="demo", env={})
    await rt.start(loops=False)
    for _ in range(3):
        await rt.tick_once()
    rt.cloud.transport = httpx.ASGITransport(app=cloud)
    app = create_app(rt, start_runtime=False)
    local = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ems")
    r = await local.post("/api/v1/auth/login", json={"username": "demo", "password": "demo"})
    local.headers["Authorization"] = f"Bearer {r.json()['token']}"
    yield cloud, cc, org, site, rt, local, clock
    await local.aclose()
    await cc.aclose()
    await rt.stop()


def test_cloud_url_validation():
    assert validate_cloud_url("https://cloud.example.com/") == "https://cloud.example.com"
    for bad in ("http://cloud.example.com", "https://user:pw@cloud.example.com", "https://cloud.example.com/x?y=1", "ftp://x"):
        with pytest.raises(ValueError):
            validate_cloud_url(bad)


async def test_pair_heartbeat_and_remote_commands_through_local_safety(setup):
    cloud, cc, org, site, rt, local, _ = setup
    assert (await local.get("/api/v1/cloud")).json()["paired"] is False
    code = (await cc.post(f"/api/v1/orgs/{org}/sites/{site}/pairing-code")).json()["code"]
    r = await local.post("/api/v1/cloud/pair", json={"url": CLOUD, "code": "FOUT-CODE"})
    assert r.status_code == 422 and "koppelen mislukt" in r.json()["detail"]
    r = await local.post("/api/v1/cloud/pair", json={"url": CLOUD, "code": code})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["paired"] and st["connected"] and st["site_name"] == "Demo-woning" and rt.config.cloud.enabled
    assert "emn_" not in rt.config_path.read_text()                       # token only in the secret store
    inst = (await cc.get(f"/api/v1/orgs/{org}/installations")).json()
    assert inst[0]["online"] and inst[0]["version"]
    devices = (await cc.get(f"/api/v1/orgs/{org}/sites/{site}")).json()["devices"]
    assert {"battery", "p1"} <= {d["id"] for d in devices}
    assert "summary" not in (await cc.get(f"/api/v1/orgs/{org}/sites/{site}")).json()   # no values without consent

    # Remote control enabled in the cloud, but not allowed locally: rejected by the installation.
    await cc.patch(f"/api/v1/orgs/{org}/sites/{site}", json={"remote_control_enabled": True})
    await rt.cloud.heartbeat_once()
    cmd = {"device": "battery", "action": "battery_charge", "value": 2000, "duration_min": 30}
    assert (await cc.post(f"/api/v1/orgs/{org}/sites/{site}/commands", json=cmd)).status_code == 200
    assert await rt.cloud.poll_commands_once() == 1
    hist = (await cc.get(f"/api/v1/orgs/{org}/sites/{site}/commands")).json()
    assert hist[0]["status"] == "rejected" and "op deze installatie uit" in hist[0]["result"]["reason"]
    assert not rt.engine.overrides.active()

    # Both switches on: accepted via the normal override path (-> SafetyValidator in the engine tick).
    data = rt.config.model_dump(mode="json")
    data["cloud"]["remote_control_allowed"] = True
    r = await local.put("/api/v1/settings", json={"cloud": data["cloud"]})
    assert r.status_code == 200, r.text
    assert rt.config.cloud.remote_control_allowed, (r.json().get("cloud"), rt.config.cloud)
    await cc.post(f"/api/v1/orgs/{org}/sites/{site}/commands", json=cmd)
    assert await rt.cloud.poll_commands_once() == 1
    hist = (await cc.get(f"/api/v1/orgs/{org}/sites/{site}/commands")).json()
    assert hist[0]["status"] == "accepted", hist[0]
    ov = rt.engine.overrides.active()
    assert len(ov) == 1 and ov[0].user.startswith("cloud:") and ov[0].command.device_id == "battery"
    await rt.tick_once()                                                  # executed by the engine like a local override
    assert rt.ems_status()["state"] == "MANUAL_OVERRIDE"

    # Commands the local checks refuse: a meter cannot be controlled, unknown device/action.
    for bad in ({"device": "p1", "action": "battery_charge", "value": 1000},
                {"device": "nope", "action": "battery_charge", "value": 1000},
                {"device": "battery", "action": "self_destruct"}):
        await cc.post(f"/api/v1/orgs/{org}/sites/{site}/commands", json=bad)
        await rt.cloud.poll_commands_once()
        assert (await cc.get(f"/api/v1/orgs/{org}/sites/{site}/commands")).json()[0]["status"] == "rejected"

    # Switched off in the cloud again: the installation refuses even if a command slips through.
    await cc.patch(f"/api/v1/orgs/{org}/sites/{site}", json={"remote_control_enabled": False})
    await rt.cloud.heartbeat_once()
    assert rt.cloud.remote_enabled_cloud is False
    status, reason, _ = await rt.cloud.execute({"id": "x", "command": cmd})
    assert status == "rejected" and "locatie uit" in reason


async def test_local_keeps_working_cloud_offline_expired_and_revoked(setup):
    cloud, cc, org, site, rt, local, clock = setup
    code = (await cc.post(f"/api/v1/orgs/{org}/sites/{site}/pairing-code")).json()["code"]
    await local.post("/api/v1/cloud/pair", json={"url": CLOUD, "code": code})

    def offline(request):
        raise httpx.ConnectError("cloud down")

    rt.cloud.transport = httpx.MockTransport(offline)
    assert await rt.cloud.heartbeat_once() is None
    st = (await local.get("/api/v1/cloud")).json()
    assert st["connected"] is False and "niet bereikbaar" in st["last_error"]
    for _ in range(5):
        await rt.tick_once()                                             # local regulation continues
    assert rt.ems_status()["state"] not in ("ERROR", "SAFE_MODE")
    assert (await local.get("/api/v1/energy/live")).status_code == 200
    plan = await rt.optimizer.run(rt.engine.last_snapshot, rt.now(), "test")
    assert plan.ok

    # Licence expired: cloud features off, local unchanged.
    rt.cloud.transport = httpx.ASGITransport(app=cloud)
    clock.t += 31 * 86400
    maintenance(cloud)
    hb = await rt.cloud.heartbeat_once()
    assert hb["license"]["valid"] is False and hb["entitlements"] == ["cloud_status"]
    await rt.tick_once()
    assert rt.ems_status()["state"] not in ("ERROR", "SAFE_MODE")

    # Revoked in the portal: the installation notices and keeps running locally.
    r = await cc.post("/api/v1/auth/login", json={"email": "eigenaar@example.com", "password": "correct horse battery"})
    cc.headers["Authorization"] = f"Bearer {r.json()['token']}"
    node_id = (await local.get("/api/v1/cloud")).json()["node_id"]
    assert (await cc.delete(f"/api/v1/orgs/{org}/nodes/{node_id}")).status_code == 200
    assert await rt.cloud.heartbeat_once() is None
    assert "ingetrokken" in rt.cloud.status()["last_error"]
    await rt.tick_once()
    assert rt.ems_status()["state"] not in ("ERROR", "SAFE_MODE")
    # Unpair locally: secret removed, cloud switched off in the configuration.
    st = (await local.post("/api/v1/cloud/unpair")).json()
    assert st["paired"] is False and rt.config.cloud.enabled is False and rt.secrets.get("cloud.node_token") is None


async def test_viewer_cannot_pair(setup):
    _, _, _, _, rt, local, _ = setup
    await local.post("/api/v1/users", json={"username": "kijker", "password": "kijker-wachtwoord", "role": "viewer"})
    v = httpx.AsyncClient(transport=local._transport, base_url="http://ems")
    r = await v.post("/api/v1/auth/login", json={"username": "kijker", "password": "kijker-wachtwoord"})
    v.headers["Authorization"] = f"Bearer {r.json()['token']}"
    assert (await v.post("/api/v1/cloud/pair", json={"url": CLOUD, "code": "ABCD-EFGH"})).status_code == 403
    assert (await v.get("/api/v1/cloud")).status_code == 200
    await v.aclose()
