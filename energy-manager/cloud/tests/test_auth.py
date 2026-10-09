"""Accounts: registration, verification, login, cookie session + CSRF, brute force, reset, passwordless,
MFA (incl. replay and 'required by role'), sessions, export and deletion."""

from sqlalchemy import select

from emcloud.db import AuthSession, Organization, User


async def test_registration_requires_verification_and_creates_org_with_trial(cloud):
    c = cloud.client()
    r = await c.post("/api/v1/auth/register", json={"email": "Anna@Example.com", "password": "correct horse battery"})
    assert r.status_code == 200 and "bekend" in r.json()["message"]
    r = await c.post("/api/v1/auth/login", json={"email": "anna@example.com", "password": "correct horse battery"})
    assert r.status_code == 403 and "bevestig" in r.json()["detail"]
    assert (await c.post("/api/v1/auth/verify", json={"token": "nope"})).status_code == 400
    tok = cloud.mail_token("anna@example.com")
    assert (await c.post("/api/v1/auth/verify", json={"token": tok})).status_code == 200
    assert (await c.post("/api/v1/auth/verify", json={"token": tok})).status_code == 400          # single use
    r = await c.post("/api/v1/auth/login", json={"email": "anna@example.com", "password": "correct horse battery"})
    assert r.status_code == 200
    me = (await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})).json()
    assert me["organizations"][0]["role"] == "ORGANIZATION_OWNER"
    sub = (await c.get(f"/api/v1/orgs/{me['organizations'][0]['id']}/subscription",
                       headers={"Authorization": f"Bearer {r.json()['token']}"})).json()
    assert sub["subscription"]["status"] == "trialing" and sub["license"]["valid"]
    # Duplicate registration: same answer (no account enumeration), existing owner notified.
    r = await c.post("/api/v1/auth/register", json={"email": "anna@example.com", "password": "another password 1"})
    assert r.status_code == 200 and cloud.last_mail("anna@example.com").subject == "Registratiepoging"


async def test_password_policy_and_self_registration_switch(cloud):
    c = cloud.client()
    r = await c.post("/api/v1/auth/register", json={"email": "b@example.com", "password": "short"})
    assert r.status_code == 422
    cloud.settings.self_registration = False
    r = await c.post("/api/v1/auth/register", json={"email": "c@example.com", "password": "correct horse battery"})
    assert r.status_code == 403


async def test_cookie_session_needs_csrf_and_logout_revokes(cloud):
    await cloud.account("d@example.com")
    c = cloud.client()
    r = await c.post("/api/v1/auth/login", json={"email": "d@example.com", "password": "correct horse battery"})
    csrf = r.json()["csrf"]
    set_cookie = r.headers.get_list("set-cookie")
    assert any("emc_session=" in h and "HttpOnly" in h and "SameSite=strict" in h for h in set_cookie)
    assert (await c.get("/api/v1/auth/me")).status_code == 200                       # cookie works for reads
    org = (await c.get("/api/v1/auth/me")).json()["organizations"][0]["id"]
    assert (await c.post(f"/api/v1/orgs/{org}/sites", json={"name": "Thuis"})).status_code == 403       # no CSRF
    assert (await c.post(f"/api/v1/orgs/{org}/sites", json={"name": "Thuis"},
                         headers={"X-CSRF-Token": "fout"})).status_code == 403
    assert (await c.post(f"/api/v1/orgs/{org}/sites", json={"name": "Thuis"},
                         headers={"X-CSRF-Token": csrf})).status_code == 200
    assert (await c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})).status_code == 200
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_brute_force_lockout_and_rate_limit(cloud):
    await cloud.account("e@example.com")
    c = cloud.client()
    codes = [(await c.post("/api/v1/auth/login", json={"email": "e@example.com", "password": f"wrong-{i}-xxxxx"})).status_code
             for i in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429                  # locked after 5 failures
    r = await c.post("/api/v1/auth/login", json={"email": "e@example.com", "password": "correct horse battery"})
    assert r.status_code == 429                                         # even the right password while locked
    cloud.clock.advance(16 * 60)
    cloud.app.state.limiter.reset()
    r = await c.post("/api/v1/auth/login", json={"email": "e@example.com", "password": "correct horse battery"})
    assert r.status_code == 200
    # Unknown accounts get the same answer as a wrong password.
    r = await c.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "correct horse battery"})
    assert r.status_code == 401 and r.json()["detail"] == "e-mailadres of wachtwoord onjuist"
    # Per-IP limit across many accounts.
    cloud.app.state.limiter.reset()
    statuses = [(await c.post("/api/v1/auth/login", json={"email": f"x{i}@example.com", "password": "whatever-123"})).status_code
                for i in range(35)]
    assert 429 in statuses


async def test_password_reset_ends_all_sessions(cloud):
    old = await cloud.account("f@example.com")
    c = cloud.client()
    assert (await c.post("/api/v1/auth/password/forgot", json={"email": "f@example.com"})).status_code == 200
    assert (await c.post("/api/v1/auth/password/forgot", json={"email": "unknown@example.com"})).json()["ok"]
    tok = cloud.mail_token("f@example.com")
    assert (await c.post("/api/v1/auth/password/reset", json={"token": tok, "password": "kort"})).status_code == 422
    assert (await c.post("/api/v1/auth/password/reset", json={"token": tok, "password": "a new long password"})).status_code == 200
    assert (await c.post("/api/v1/auth/password/reset", json={"token": tok, "password": "again new password"})).status_code == 400
    assert (await old.get("/api/v1/auth/me")).status_code == 401           # old session revoked
    r = await c.post("/api/v1/auth/login", json={"email": "f@example.com", "password": "a new long password"})
    assert r.status_code == 200
    # Reset links expire after an hour.
    await c.post("/api/v1/auth/password/forgot", json={"email": "f@example.com"})
    tok = cloud.mail_token("f@example.com")
    cloud.clock.advance(3601)
    assert (await c.post("/api/v1/auth/password/reset", json={"token": tok, "password": "yet another password"})).status_code == 400


async def test_passwordless_magic_link(cloud):
    c = cloud.client()
    await c.post("/api/v1/auth/register", json={"email": "g@example.com"})       # no password at all
    await c.post("/api/v1/auth/magic-link", json={"email": "g@example.com"})
    r = await c.post("/api/v1/auth/magic-link/consume", json={"token": cloud.mail_token("g@example.com")})
    assert r.status_code == 200
    me = (await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})).json()
    assert me["passwordless"] and me["email_verified"]
    r = await c.post("/api/v1/auth/login", json={"email": "g@example.com", "password": "anything goes here"})
    assert r.status_code == 401                                            # no password = no password login


async def test_mfa_enable_login_replay_and_required_for_platform_admin(cloud):
    c = await cloud.account("h@example.com")
    secret = (await c.post("/api/v1/auth/mfa/setup")).json()["secret"]
    assert (await c.post("/api/v1/auth/mfa/enable", json={"code": "000000"})).status_code == 401
    assert (await c.post("/api/v1/auth/mfa/enable", json={"code": cloud.totp(secret)})).status_code == 200
    with cloud.db() as db:
        assert db.scalar(select(User).where(User.email == "h@example.com")).mfa_secret_enc != secret   # encrypted at rest
    n = cloud.client()
    r = await n.post("/api/v1/auth/login", json={"email": "h@example.com", "password": "correct horse battery"})
    assert r.json()["mfa_required"]
    n.headers["Authorization"] = f"Bearer {r.json()['token']}"
    n.cookies.clear()
    assert (await n.get("/api/v1/auth/me")).status_code == 401              # second factor still missing
    assert (await n.post("/api/v1/auth/mfa/verify", json={"code": cloud.totp(secret)})).status_code == 401  # replay of same step
    cloud.clock.advance(30)
    assert (await n.post("/api/v1/auth/mfa/verify", json={"code": cloud.totp(secret)})).status_code == 200
    assert (await n.get("/api/v1/auth/me")).status_code == 200
    # A platform admin without MFA can only reach the MFA setup.
    a = await cloud.account("admin@example.com")
    cloud.make_admin("admin@example.com")
    assert (await a.get("/api/v1/admin/overview")).status_code == 403
    assert (await a.get("/api/v1/auth/me")).json()["mfa_setup_required"]
    s2 = (await a.post("/api/v1/auth/mfa/setup")).json()["secret"]
    cloud.clock.advance(30)
    await a.post("/api/v1/auth/mfa/enable", json={"code": cloud.totp(s2)})
    assert (await a.get("/api/v1/admin/overview")).status_code == 200


async def test_org_can_require_mfa_for_admins(cloud):
    c = await cloud.account("i@example.com")
    org = await cloud.org_of(c)
    assert (await c.patch(f"/api/v1/orgs/{org}", json={"require_mfa": True})).status_code == 200
    assert (await c.get(f"/api/v1/orgs/{org}/sites")).status_code == 403


async def test_sessions_list_revoke_and_expiry(cloud):
    c = await cloud.account("j@example.com")
    other = cloud.client()
    r = await other.post("/api/v1/auth/login", json={"email": "j@example.com", "password": "correct horse battery"})
    other.headers["Authorization"] = f"Bearer {r.json()['token']}"
    other.cookies.clear()
    sessions = (await c.get("/api/v1/auth/sessions")).json()
    assert len(sessions) == 2
    sid = next(s["id"] for s in sessions if not s["current"])
    assert (await c.delete(f"/api/v1/auth/sessions/{sid}")).status_code == 200
    assert (await other.get("/api/v1/auth/me")).status_code == 401
    cloud.clock.advance(13 * 3600)                                           # idle timeout (12 h)
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_absolute_session_expiry(cloud):
    c = await cloud.account("k@example.com")
    for _ in range(31):                                                      # active every day, still ends after 30 days
        cloud.clock.advance(86400 - 1)
        await c.get("/api/v1/auth/me")
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_export_and_account_deletion(cloud):
    c = await cloud.account("l@example.com")
    org = await cloud.org_of(c)
    await c.post(f"/api/v1/orgs/{org}/sites", json={"name": "Thuis"})
    data = (await c.get("/api/v1/auth/me/export")).json()
    assert data["account"]["email"] == "l@example.com" and data["sites"][0]["name"] == "Thuis" and data["audit_log"]
    assert "password_hash" not in str(data) and "mfa_secret" not in str(data)
    r = await c.request("DELETE", "/api/v1/auth/me", json={"password": "correct horse battery", "confirm_email": "x@example.com"})
    assert r.status_code == 422
    r = await c.request("DELETE", "/api/v1/auth/me", json={"password": "correct horse battery", "confirm_email": "l@example.com"})
    assert r.status_code == 200 and r.json()["deleted_organizations"] == [org]
    with cloud.db() as db:
        assert db.get(Organization, org) is None
        u = db.scalar(select(User).where(User.email == "l@example.com"))
        assert u is None
        assert db.scalars(select(AuthSession)).all() == [] or all(s.user_id for s in db.scalars(select(AuthSession)))
    assert (await c.get("/api/v1/auth/me")).status_code == 401
