"""Test harness: a fresh cloud app per test on a temporary SQLite file, a controllable clock, and helpers
that go through the real flows (registration -> e-mail in the outbox -> verification -> login)."""

from __future__ import annotations

import os
import re

import httpx
import pyotp
import pytest
from sqlalchemy import select

from emcloud.app import create_app
from emcloud.config import Settings
from emcloud.db import Outbox, User


class Clock:
    def __init__(self, t: float = 1_790_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Cloud:
    def __init__(self, tmp_path, **settings):
        self.clock = Clock()
        url = os.environ.get("EMC_TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/cloud.db"
        if url.startswith("postgresql"):          # same suite on PostgreSQL: a clean schema per test
            from sqlalchemy import create_engine, text
            eng = create_engine(url)
            with eng.begin() as c:
                c.execute(text("DROP SCHEMA public CASCADE"))
                c.execute(text("CREATE SCHEMA public"))
            eng.dispose()
        self.settings = Settings(database_url=url, base_url="https://cloud.example.com", **settings)
        self.app = create_app(self.settings, clock=self.clock, background=False)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://cloud.example.com")

    def db(self):
        return self.app.state.sessionmaker()

    def last_mail(self, to: str) -> Outbox:
        with self.db() as db:
            return db.scalars(select(Outbox).where(Outbox.to == to).order_by(Outbox.id.desc())).first()

    def mail_token(self, to: str) -> str:
        m = self.last_mail(to)
        return re.search(r"#/\w+/([A-Za-z0-9_-]+)", m.body).group(1)

    async def account(self, email: str, password: str = "correct horse battery", org: str = "") -> httpx.AsyncClient:
        """Registered, verified and logged-in client (Bearer session token)."""
        c = self.client()
        r = await c.post("/api/v1/auth/register", json={"email": email, "password": password, "organization": org or email})
        assert r.status_code == 200, r.text
        r = await c.post("/api/v1/auth/verify", json={"token": self.mail_token(email)})
        assert r.status_code == 200, r.text
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        c.cookies.clear()
        return c

    async def login(self, email: str, password: str = "correct horse battery") -> httpx.AsyncClient:
        c = self.client()
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        c.cookies.clear()
        return c

    async def org_of(self, c: httpx.AsyncClient) -> str:
        return (await c.get("/api/v1/auth/me")).json()["organizations"][0]["id"]

    def make_admin(self, email: str) -> None:
        with self.db() as db:
            db.scalar(select(User).where(User.email == email)).is_platform_admin = True
            db.commit()

    def totp(self, secret: str) -> str:
        return pyotp.TOTP(secret).at(self.clock())


@pytest.fixture
def cloud(tmp_path):
    return Cloud(tmp_path)
