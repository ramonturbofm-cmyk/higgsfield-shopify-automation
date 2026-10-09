"""Node pairing, node tokens and rotation, remote commands (permission, site switch, licence), licences,
subscriptions, plans without hard-coded prices, admin 'add customer'."""

import pytest
from sqlalchemy import select

from emcloud.app import maintenance
from emcloud.billing import DAY
from emcloud.db import Node, Plan


@pytest.fixture
async def site(cloud):
    o = await cloud.account("owner@example.com", org="Huis")
    org = await cloud.org_of(o)
    site = (await o.post(f"/api/v1/orgs/{org}/sites", json={"name": "Thuis"})).json()["id"]
    return o, org, site


async def pair(cloud, owner, org, site):
    code = (await owner.post(f"/api/v1/orgs/{org}/sites/{site}/pairing-code")).json()["code"]
    n = cloud.client()
    r = await n.post("/api/v1/node/pair", json={"code": code.lower().replace("-", " "), "name": "Pi", "version": "0.5.0"})
    assert r.status_code == 200, r.text
    n.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return n, r.json()


async def test_pairing_code_single_use_expiry_and_installations(cloud, site):
    o, org, s = site
    code = (await o.post(f"/api/v1/orgs/{org}/sites/{s}/pairing-code")).json()["code"]
    n = cloud.client()
    assert (await n.post("/api/v1/node/pair", json={"code": "AAAA-BBBB"})).status_code == 400
    r = await n.post("/api/v1/node/pair", json={"code": code, "name": "Pi"})
    assert r.status_code == 200 and r.json()["token"].startswith("emn_") and r.json()["site_name"] == "Thuis"
    assert (await n.post("/api/v1/node/pair", json={"code": code})).status_code == 400          # single use
    with cloud.db() as db:
        assert db.scalars(select(Node)).first().token_hash != r.json()["token"]                 # stored hashed
    code2 = (await o.post(f"/api/v1/orgs/{org}/sites/{s}/pairing-code")).json()["code"]
    cloud.clock.advance(16 * 60)
    assert (await n.post("/api/v1/node/pair", json={"code": code2})).status_code == 400          # expired
    inst = (await o.get(f"/api/v1/orgs/{org}/installations")).json()
    assert len(inst) == 1 and inst[0]["name"] == "Pi" and inst[0]["online"] is False         # 16 min without heartbeat


async def test_pairing_brute_force_is_rate_limited(cloud, site):
    n = cloud.client()
    codes = [(await n.post("/api/v1/node/pair", json={"code": f"AAAA-{i:04d}"})).status_code for i in range(12)]
    assert codes.count(400) == 10 and codes[-1] == 429


async def test_heartbeat_online_devices_and_no_usage_without_consent(cloud, site):
    o, org, s = site
    n, info = await pair(cloud, o, org, s)
    hb = {"version": "0.5.0", "mode": "automatic", "devices": [{"id": "bat", "name": "Batterij", "category": "battery", "online": True}],
          "summary": {"import_kwh_today": 4.2}}
    r = (await n.post("/api/v1/node/heartbeat", json=hb)).json()
    assert r["ok"] and r["remote_control_enabled"] is False and r["summary_stored"] is False and r["license"]["valid"]
    detail = (await o.get(f"/api/v1/orgs/{org}/sites/{s}")).json()
    assert detail["online"] and detail["devices"][0]["id"] == "bat" and "summary" not in detail
    await o.patch(f"/api/v1/orgs/{org}", json={"sync_consent": True})
    assert (await n.post("/api/v1/node/heartbeat", json=hb)).json()["summary_stored"] is True
    assert (await o.get(f"/api/v1/orgs/{org}/sites/{s}")).json()["summary"]["import_kwh_today"] == 4.2
    await o.patch(f"/api/v1/orgs/{org}", json={"sync_consent": False})          # withdrawal deletes synced data
    assert (await o.get(f"/api/v1/orgs/{org}/sites/{s}")).json().get("summary") is None
    cloud.clock.advance(181)
    assert (await o.get(f"/api/v1/orgs/{org}/sites/{s}")).json()["online"] is False


async def test_token_rotation_grace_and_revocation(cloud, site):
    o, org, s = site
    n, info = await pair(cloud, o, org, s)
    old = n.headers["Authorization"]
    new = (await n.post("/api/v1/node/rotate")).json()["token"]
    assert (await n.post("/api/v1/node/heartbeat", json={})).status_code == 200            # old token: grace period
    assert (await n.post("/api/v1/node/rotate")).status_code == 401                         # but cannot rotate again
    cloud.clock.advance(601)
    assert (await n.post("/api/v1/node/heartbeat", json={})).status_code == 401             # grace over
    n.headers["Authorization"] = f"Bearer {new}"
    assert (await n.post("/api/v1/node/heartbeat", json={})).status_code == 200
    assert old != n.headers["Authorization"]
    assert (await o.delete(f"/api/v1/orgs/{org}/nodes/{info['node_id']}")).status_code == 200
    assert (await n.post("/api/v1/node/heartbeat", json={})).status_code == 401             # revoked by the customer
    bad = cloud.client()
    bad.headers["Authorization"] = "Bearer emn_forged"
    assert (await bad.get("/api/v1/node/commands")).status_code == 401


async def test_remote_command_needs_site_switch_permission_and_licence(cloud, site):
    o, org, s = site
    n, info = await pair(cloud, o, org, s)
    await n.post("/api/v1/node/heartbeat", json={})
    cmd = {"device": "bat", "action": "set_battery_power", "value": 1500, "duration_min": 30}
    assert (await o.post(f"/api/v1/orgs/{org}/sites/{s}/commands", json=cmd)).status_code == 403   # switched off per site
    assert (await o.patch(f"/api/v1/orgs/{org}/sites/{s}", json={"remote_control_enabled": True})).status_code == 200
    r = await o.post(f"/api/v1/orgs/{org}/sites/{s}/commands", json=cmd)
    assert r.status_code == 200
    got = (await n.get("/api/v1/node/commands")).json()
    assert len(got) == 1 and got[0]["command"]["action"] == "set_battery_power"
    assert (await n.get("/api/v1/node/commands")).json() == []                               # delivered once
    r = await n.post(f"/api/v1/node/commands/{got[0]['id']}/ack", json={"status": "rejected", "reason": "SafetyValidator: limiet"})
    assert r.status_code == 200
    hist = (await o.get(f"/api/v1/orgs/{org}/sites/{s}/commands")).json()
    assert hist[0]["status"] == "rejected" and "SafetyValidator" in hist[0]["result"]["reason"]
    # Another node cannot acknowledge (or see) this node's commands.
    o2 = await cloud.account("other@example.com")
    org2 = await cloud.org_of(o2)
    s2 = (await o2.post(f"/api/v1/orgs/{org2}/sites", json={"name": "Ander"})).json()["id"]
    n2, _ = await pair(cloud, o2, org2, s2)
    assert (await n2.post(f"/api/v1/node/commands/{got[0]['id']}/ack", json={"status": "accepted"})).status_code == 404
    # Malformed / injected command fields are refused before they reach the node.
    bad = {"device": "../etc", "action": "set_battery_power", "value": 1}
    assert (await o.post(f"/api/v1/orgs/{org}/sites/{s}/commands", json=bad)).status_code == 422
    # Expired licence: no remote control (the local EMS is not affected — see the EMS tests).
    cloud.clock.advance(31 * DAY)
    maintenance(cloud.app)
    o = await cloud.login("owner@example.com")                    # (sessions end after 30 days)
    await n.post("/api/v1/node/heartbeat", json={})
    r = await o.post(f"/api/v1/orgs/{org}/sites/{s}/commands", json=cmd)
    assert r.status_code == 402
    hb = (await n.post("/api/v1/node/heartbeat", json={})).json()
    assert hb["license"]["valid"] is False and hb["entitlements"] == ["cloud_status"]
    assert (await o.get(f"/api/v1/orgs/{org}/installations")).status_code == 200            # status stays visible


async def test_switching_remote_control_off_drops_queued_commands(cloud, site):
    o, org, s = site
    n, _ = await pair(cloud, o, org, s)
    await n.post("/api/v1/node/heartbeat", json={})
    await o.patch(f"/api/v1/orgs/{org}/sites/{s}", json={"remote_control_enabled": True})
    await o.post(f"/api/v1/orgs/{org}/sites/{s}/commands", json={"device": "bat", "action": "set_battery_mode", "value": "idle"})
    await o.patch(f"/api/v1/orgs/{org}/sites/{s}", json={"remote_control_enabled": False})
    assert (await n.get("/api/v1/node/commands")).json() == []
    # Commands older than 2 minutes are never delivered.
    await o.patch(f"/api/v1/orgs/{org}/sites/{s}", json={"remote_control_enabled": True})
    await o.post(f"/api/v1/orgs/{org}/sites/{s}/commands", json={"device": "bat", "action": "set_battery_mode", "value": "idle"})
    cloud.clock.advance(121)
    await n.post("/api/v1/node/heartbeat", json={})
    assert (await n.get("/api/v1/node/commands")).json() == []


async def test_plans_have_no_hardcoded_prices_and_subscription_lifecycle(cloud, site):
    o, org, s = site
    with cloud.db() as db:
        assert all(p.price_month_cents is None and p.price_year_cents is None for p in db.scalars(select(Plan)))
    sub = (await o.get(f"/api/v1/orgs/{org}/subscription")).json()
    assert sub["subscription"]["plan"] == "PRO" and {p["key"] for p in sub["plans"]} == {"BASIC", "PRO", "BUSINESS", "ENTERPRISE"}
    assert all(p["price_set"] is False for p in sub["plans"])
    r = (await o.post(f"/api/v1/orgs/{org}/subscription/change", json={"plan": "BUSINESS"})).json()
    assert r["result"] == "upgraded" and r["subscription"]["plan"] == "BUSINESS"
    r = (await o.post(f"/api/v1/orgs/{org}/subscription/change", json={"plan": "BASIC"})).json()
    assert r["result"] == "downgrade_scheduled" and r["subscription"]["plan"] == "BUSINESS" and r["subscription"]["pending_plan"] == "BASIC"
    r = (await o.post(f"/api/v1/orgs/{org}/subscription/cancel")).json()
    assert r["subscription"]["cancel_at_period_end"] and "lokale EMS" in r["note"]
    await o.post(f"/api/v1/orgs/{org}/subscription/resume")
    # Trial ends without payment -> expired -> only free entitlements.
    cloud.clock.advance(31 * DAY)
    maintenance(cloud.app)
    o = await cloud.login("owner@example.com")
    sub = (await o.get(f"/api/v1/orgs/{org}/subscription")).json()
    assert sub["subscription"]["status"] == "expired" and not sub["license"]["valid"] and sub["entitlements"] == ["cloud_status"]


async def test_admin_adds_customer_sets_prices_and_renews(cloud):
    adm = await cloud.account("platform@example.com")
    cloud.make_admin("platform@example.com")
    secret = (await adm.post("/api/v1/auth/mfa/setup")).json()["secret"]
    cloud.clock.advance(30)
    await adm.post("/api/v1/auth/mfa/enable", json={"code": cloud.totp(secret)})
    r = await adm.post("/api/v1/admin/customers", json={"organization": "Bakkerij Jansen", "email": "jansen@example.com",
                                                      "plan": "BASIC", "max_sites": 2, "max_nodes": 2, "trial_days": 14})
    assert r.status_code == 200
    org = r.json()["organization_id"]
    tok = cloud.mail_token("jansen@example.com")
    c = cloud.client()
    info = (await c.get(f"/api/v1/auth/invitations/{tok}")).json()
    assert info["organization"] == "Bakkerij Jansen" and info["role"] == "ORGANIZATION_OWNER"
    r = await c.post("/api/v1/auth/invitations/register", json={"token": tok, "password": "jansen password 1"})
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    c.cookies.clear()
    sub = (await c.get(f"/api/v1/orgs/{org}/subscription")).json()
    assert sub["max_sites"] == 2 and sub["license"]["status"] == "trial"
    # Prices are set by the admin — never in code.
    p = (await adm.put("/api/v1/admin/plans/BASIC", json={"price_month_cents": 499})).json()
    assert p["price_month_cents"] == 499 and p["price_set"]
    r = (await adm.post(f"/api/v1/admin/organizations/{org}/subscription/renew")).json()
    assert r["subscription"]["status"] == "active" and r["invoice"]["amount_cents"] == 499
    # Suspension: read access remains, changes are blocked; no access for the admin to customer data.
    await adm.patch(f"/api/v1/admin/organizations/{org}", json={"status": "suspended"})
    assert (await c.get(f"/api/v1/orgs/{org}/sites")).status_code == 200
    assert (await c.post(f"/api/v1/orgs/{org}/sites", json={"name": "x"})).status_code == 403
    assert (await adm.get(f"/api/v1/orgs/{org}/sites")).status_code == 404
    assert any(a["action"] == "admin.customer_created" for a in (await adm.get("/api/v1/admin/audit")).json())


async def test_limits_from_licence(cloud, site):
    o, org, s = site
    r = await o.post(f"/api/v1/orgs/{org}/sites", json={"name": "Tweede"})
    assert r.status_code == 402                                                               # PRO: 1 site
    for _ in range(3):
        await pair(cloud, o, org, s)
    assert (await o.post(f"/api/v1/orgs/{org}/sites/{s}/pairing-code")).status_code == 402     # PRO: 3 nodes


def test_production_settings_refuse_insecure_start():
    from emcloud.config import ConfigError, Settings
    with pytest.raises(ConfigError) as e:
        Settings.from_env({"EMC_ENV": "production", "EMC_BASE_URL": "http://x"})
    msg = str(e.value)
    assert "EMC_ENCRYPTION_KEYS" in msg and "https" in msg and "SMTP" in msg and "PostgreSQL" in msg
