"""Tenant isolation, privilege escalation, site-scoped access, platform admin limits, support access."""

import pytest


@pytest.fixture
async def two_tenants(cloud):
    a = await cloud.account("owner-a@example.com", org="A")
    b = await cloud.account("owner-b@example.com", org="B")
    org_a, org_b = await cloud.org_of(a), await cloud.org_of(b)
    site_a = (await a.post(f"/api/v1/orgs/{org_a}/sites", json={"name": "A-huis"})).json()["id"]
    site_b = (await b.post(f"/api/v1/orgs/{org_b}/sites", json={"name": "B-huis"})).json()["id"]
    return a, b, org_a, org_b, site_a, site_b


async def test_no_cross_tenant_access(cloud, two_tenants):
    a, b, org_a, org_b, site_a, site_b = two_tenants
    for path in (f"/api/v1/orgs/{org_b}", f"/api/v1/orgs/{org_b}/sites", f"/api/v1/orgs/{org_b}/sites/{site_b}",
                 f"/api/v1/orgs/{org_b}/members", f"/api/v1/orgs/{org_b}/installations", f"/api/v1/orgs/{org_b}/audit",
                 f"/api/v1/orgs/{org_b}/subscription", f"/api/v1/orgs/{org_b}/sites/{site_b}/commands"):
        r = await a.get(path)
        assert r.status_code == 404, (path, r.status_code)           # not even "forbidden": existence not revealed
    # Mixing an own org id with another tenant's site id.
    assert (await a.get(f"/api/v1/orgs/{org_a}/sites/{site_b}")).status_code == 404
    assert (await a.patch(f"/api/v1/orgs/{org_a}/sites/{site_b}", json={"name": "x"})).status_code == 404
    assert (await a.post(f"/api/v1/orgs/{org_a}/sites/{site_b}/pairing-code")).status_code == 404
    assert (await a.post(f"/api/v1/orgs/{org_b}/invitations", json={"email": "evil@example.com", "role": "VIEWER"})).status_code == 404
    assert (await a.get(f"/api/v1/orgs/{org_a}/sites")).json()[0]["name"] == "A-huis"
    assert all(s["name"] != "B-huis" for s in (await a.get(f"/api/v1/orgs/{org_a}/sites")).json())


async def invite(cloud, owner, org, email, role, site_ids=None):
    r = await owner.post(f"/api/v1/orgs/{org}/invitations", json={"email": email, "role": role, "site_ids": site_ids})
    assert r.status_code == 200, r.text
    tok = cloud.mail_token(email)
    c = cloud.client()
    r = await c.post("/api/v1/auth/invitations/register", json={"token": tok, "password": "member password 1"})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    c.cookies.clear()
    return c


async def test_roles_and_privilege_escalation(cloud, two_tenants):
    a, _, org_a, _, site_a, _ = two_tenants
    viewer = await invite(cloud, a, org_a, "viewer@example.com", "VIEWER")
    admin = await invite(cloud, a, org_a, "admin@example.com", "ORGANIZATION_ADMIN")
    me_admin = (await admin.get("/api/v1/auth/me")).json()["id"]
    me_viewer = (await viewer.get("/api/v1/auth/me")).json()["id"]
    # Viewer: read yes, change no.
    assert (await viewer.get(f"/api/v1/orgs/{org_a}/sites")).status_code == 200
    assert (await viewer.post(f"/api/v1/orgs/{org_a}/sites", json={"name": "x"})).status_code == 403
    assert (await viewer.post(f"/api/v1/orgs/{org_a}/invitations", json={"email": "z@example.com", "role": "VIEWER"})).status_code == 403
    assert (await viewer.patch(f"/api/v1/orgs/{org_a}/members/{me_viewer}", json={"role": "ORGANIZATION_OWNER"})).status_code == 403
    assert (await viewer.post(f"/api/v1/orgs/{org_a}/sites/{site_a}/commands",
                              json={"device": "bat", "action": "set_battery_power", "value": 1000})).status_code == 403
    # Admin cannot create owners, cannot change their own role, cannot touch the owner.
    assert (await admin.post(f"/api/v1/orgs/{org_a}/invitations",
                             json={"email": "o2@example.com", "role": "ORGANIZATION_OWNER"})).status_code == 403
    assert (await admin.patch(f"/api/v1/orgs/{org_a}/members/{me_admin}", json={"role": "ORGANIZATION_OWNER"})).status_code == 403
    owner_id = (await a.get("/api/v1/auth/me")).json()["id"]
    assert (await admin.delete(f"/api/v1/orgs/{org_a}/members/{owner_id}")).status_code == 403
    assert (await admin.patch(f"/api/v1/orgs/{org_a}/members/{owner_id}", json={"role": "VIEWER"})).status_code == 403
    assert (await admin.post(f"/api/v1/orgs/{org_a}/subscription/change", json={"plan": "ENTERPRISE"})).status_code == 403
    # Admin can promote the viewer to operator (not above themselves).
    assert (await admin.patch(f"/api/v1/orgs/{org_a}/members/{me_viewer}", json={"role": "OPERATOR"})).status_code == 200
    # The last owner cannot leave or be demoted.
    assert (await a.delete(f"/api/v1/orgs/{org_a}/members/{owner_id}")).status_code == 409
    # Invitation for another e-mail address cannot be accepted.
    await a.post(f"/api/v1/orgs/{org_a}/invitations", json={"email": "someone@example.com", "role": "VIEWER"})
    tok = cloud.mail_token("someone@example.com")
    assert (await viewer.post("/api/v1/auth/invitations/accept", json={"token": tok})).status_code == 403


async def test_site_scoped_member_cannot_see_other_sites(cloud, two_tenants):
    a, _, org_a, _, site_a, _ = two_tenants
    site_2 = (await a.post(f"/api/v1/orgs/{org_a}/sites", json={"name": "Tweede"})).json()
    if "id" not in site_2:                     # trial plan allows one site: upgrade for this test
        await a.post(f"/api/v1/orgs/{org_a}/subscription/change", json={"plan": "BUSINESS"})
        site_2 = (await a.post(f"/api/v1/orgs/{org_a}/sites", json={"name": "Tweede"})).json()
    inst = await invite(cloud, a, org_a, "installer@example.com", "INSTALLER", [site_a])
    names = [s["name"] for s in (await inst.get(f"/api/v1/orgs/{org_a}/sites")).json()]
    assert names == ["A-huis"]
    assert (await inst.get(f"/api/v1/orgs/{org_a}/sites/{site_2['id']}")).status_code == 404
    assert (await inst.post(f"/api/v1/orgs/{org_a}/sites/{site_2['id']}/pairing-code")).status_code == 404
    assert (await inst.post(f"/api/v1/orgs/{org_a}/sites/{site_a}/pairing-code")).status_code == 200
    # Installer may not switch remote control on (owner/admin decision).
    assert (await inst.patch(f"/api/v1/orgs/{org_a}/sites/{site_a}", json={"remote_control_enabled": True})).status_code == 403


async def test_platform_admin_sees_aggregates_not_customer_data(cloud, two_tenants):
    a, _, org_a, _, site_a, _ = two_tenants
    adm = await cloud.account("platform@example.com")
    cloud.make_admin("platform@example.com")
    secret = (await adm.post("/api/v1/auth/mfa/setup")).json()["secret"]
    cloud.clock.advance(30)
    await adm.post("/api/v1/auth/mfa/enable", json={"code": cloud.totp(secret)})
    ov = (await adm.get("/api/v1/admin/overview")).json()
    assert ov["organizations"] >= 3 and "installations" in ov and "Geen verbruiks" in ov["privacy"]
    assert (await adm.get("/api/v1/admin/organizations")).status_code == 200
    # ... but no access to a customer's sites, devices or commands.
    assert (await adm.get(f"/api/v1/orgs/{org_a}/sites")).status_code == 404
    assert (await adm.get(f"/api/v1/orgs/{org_a}/sites/{site_a}")).status_code == 404
    assert (await adm.post(f"/api/v1/orgs/{org_a}/sites/{site_a}/commands",
                           json={"device": "bat", "action": "set_battery_power", "value": 1})).status_code == 404
    # Ordinary users cannot reach the admin portal.
    assert (await a.get("/api/v1/admin/overview")).status_code == 403


async def test_support_access_is_temporary_and_granted_by_customer(cloud, two_tenants):
    a, _, org_a, _, site_a, _ = two_tenants
    sup = await cloud.account("support@example.com")
    assert (await sup.get(f"/api/v1/orgs/{org_a}/sites")).status_code == 404
    r = await a.post(f"/api/v1/orgs/{org_a}/support-grants", json={"email": "support@example.com", "hours": 2})
    assert r.status_code == 402                                       # trial PRO has no support_access
    await a.post(f"/api/v1/orgs/{org_a}/subscription/change", json={"plan": "BUSINESS"})
    r = await a.post(f"/api/v1/orgs/{org_a}/support-grants", json={"email": "support@example.com", "hours": 2})
    assert r.status_code == 200
    assert (await sup.get(f"/api/v1/orgs/{org_a}/sites")).status_code == 200
    assert (await sup.get(f"/api/v1/orgs/{org_a}")).json()["via_support"]
    assert (await sup.post(f"/api/v1/orgs/{org_a}/sites", json={"name": "x"})).status_code == 403     # read only
    assert (await sup.post(f"/api/v1/orgs/{org_a}/support-grants", json={"email": "support@example.com"})).status_code == 403
    assert (await sup.get(f"/api/v1/orgs/{org_a}/members")).status_code == 403
    cloud.clock.advance(2 * 3600 + 1)                                 # expired automatically
    assert (await sup.get(f"/api/v1/orgs/{org_a}/sites")).status_code == 404
    g = (await a.post(f"/api/v1/orgs/{org_a}/support-grants", json={"email": "support@example.com", "hours": 1})).json()
    assert (await sup.get(f"/api/v1/orgs/{org_a}/sites")).status_code == 200
    await a.delete(f"/api/v1/orgs/{org_a}/support-grants/{g['id']}")  # revoked by the customer
    assert (await sup.get(f"/api/v1/orgs/{org_a}/sites")).status_code == 404
    audit = [x["action"] for x in (await a.get(f"/api/v1/orgs/{org_a}/audit")).json()]
    assert "support.granted" in audit and "support.revoked" in audit
