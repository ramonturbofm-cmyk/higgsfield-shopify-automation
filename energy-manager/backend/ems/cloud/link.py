"""Link between this installation and Energy Manager Cloud (optional).

* Only outbound HTTPS from this installation: no port forwarding, no inbound connections.
* Pairing: the user creates a one-time code in the cloud portal ('Installatie koppelen') and enters it here
  ('Deze installatie aan mijn account koppelen'). The node token is kept in the encrypted secret store.
* Heartbeat: status, version and device names; measured values only with ``share_summary`` (and the
  organisation's consent in the cloud).
* Remote commands: executed only when remote control is enabled for the site in the cloud *and* allowed
  locally (``cloud.remote_control_allowed``), and only through ``engine.check`` (``can_execute``) plus the
  normal override path — so every command passes the same SafetyValidator as local manual control.
* A cloud outage, an expired licence or a revoked pairing never affects local control.
"""

from __future__ import annotations

import asyncio
import logging
import platform
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from ems import __version__
from ems.core.models import Command, CommandAction
from ems.security.auth import Principal

log = logging.getLogger(__name__)

TOKEN_SECRET = "cloud.node_token"
LINK_KV = "cloud.link"


def validate_cloud_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname:
        raise ValueError("het cloud-adres moet met https:// beginnen")
    if p.username or p.password or "@" in p.netloc:
        raise ValueError("geen gebruikersnaam of wachtwoord in het adres")
    if p.path not in ("", "/") or p.query or p.fragment:
        raise ValueError("alleen het basisadres, zonder pad (bijv. https://cloud.voorbeeld.nl)")
    return url


class CloudLink:
    def __init__(self, runtime) -> None:
        self.rt = runtime
        self.transport: httpx.AsyncBaseTransport | None = None     # tests: ASGI transport to the cloud app
        self.connected = False
        self.last_ok: datetime | None = None
        self.last_error: str | None = None
        self.remote_enabled_cloud = False
        self.license: dict | None = None
        self.entitlements: list[str] = []
        self.executed: list[dict] = []          # last remote commands (for the local status screen)

    # ------------------------------------------------------------ state
    @property
    def cfg(self):
        return self.rt.config.cloud

    @property
    def info(self) -> dict:
        return self.rt.db.kv_get(LINK_KV) or {}

    @property
    def paired(self) -> bool:
        return bool(self.info.get("node_id")) and bool(self.rt.secrets.get(TOKEN_SECRET))

    def status(self) -> dict:
        info = self.info
        return {"enabled": self.cfg.enabled, "url": info.get("url") or self.cfg.url, "paired": self.paired,
                "organization": info.get("organization"), "site_name": info.get("site_name"), "node_id": info.get("node_id"),
                "paired_at": info.get("paired_at"), "connected": self.connected,
                "last_ok": None if self.last_ok is None else self.last_ok.isoformat(), "last_error": self.last_error,
                "remote_control_allowed_locally": self.cfg.remote_control_allowed,
                "remote_control_enabled_in_cloud": self.remote_enabled_cloud, "license": self.license,
                "recent_commands": self.executed[-10:],
                "local_note": "Lokale regeling, veiligheid en optimalisatie werken altijd, ook zonder cloud."}

    def _client(self, url: str, token: str | None = None) -> httpx.AsyncClient:
        headers = {"User-Agent": f"energy-manager/{__version__}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.AsyncClient(base_url=url, headers=headers, timeout=httpx.Timeout(30, connect=10),
                                 follow_redirects=False, transport=self.transport)

    @staticmethod
    def _error(resp: httpx.Response) -> str:
        try:
            return str(resp.json().get("detail") or resp.status_code)
        except ValueError:
            return f"HTTP {resp.status_code}"

    # ------------------------------------------------------------ pairing
    async def pair(self, url: str, code: str) -> dict:
        url = validate_cloud_url(url)
        body = {"code": code.strip()[:20], "name": self.rt.config.node.name or platform.node(), "version": __version__,
                "platform": f"{platform.system()} {platform.machine()}"}
        async with self._client(url) as c:
            try:
                r = await c.post("/api/v1/node/pair", json=body)
            except httpx.HTTPError as exc:
                raise ValueError(f"cloud niet bereikbaar ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise ValueError(f"koppelen mislukt: {self._error(r)}")
        data = r.json()
        self.rt.secrets.set(TOKEN_SECRET, data["token"])
        await asyncio.to_thread(self.rt.db.kv_set, LINK_KV, {
            "url": url, "node_id": data["node_id"], "organization": data.get("organization"), "site_id": data.get("site_id"),
            "site_name": data.get("site_name"), "paired_at": datetime.now(UTC).isoformat()})
        self.last_error = None
        return {k: v for k, v in data.items() if k != "token"}

    async def unpair(self) -> None:
        info, token = self.info, self.rt.secrets.get(TOKEN_SECRET)
        if info.get("url") and token:
            try:
                async with self._client(info["url"], token) as c:
                    await c.post("/api/v1/node/unpair")
            except httpx.HTTPError:
                pass                          # offline: the customer can also remove it in the portal
        self.rt.secrets.delete(TOKEN_SECRET)
        await asyncio.to_thread(self.rt.db.kv_set, LINK_KV, {})
        self.connected, self.remote_enabled_cloud, self.license = False, False, None

    # ----------------------------------------------------------- heartbeat
    def heartbeat_body(self) -> dict:
        rt = self.rt
        live = rt.live() if rt.engine.last_snapshot is not None else {}
        st = live.get("ems_status") or {}
        devices = [{"id": d.id, "name": d.name, "category": d.category.value,
                    "online": bool(live.get("devices", {}).get(d.id, {}).get("status") == "online")}
                   for d in rt.config.devices if d.enabled]
        bad = st.get("state") in ("ERROR", "SAFE_MODE", "DEGRADED") and st.get("reason")
        body: dict[str, Any] = {"version": __version__, "platform": f"{platform.system()} {platform.machine()}",
                                "mode": rt.config.runtime.mode, "ems_status": st.get("state", ""),
                                "errors": [st["reason"]] if bad else [], "devices": devices}
        if self.cfg.share_summary and live.get("flows"):
            f = live["flows"]
            body["summary"] = {"grid_w": f.get("grid_w"), "pv_w": f.get("pv_w"), "battery_soc_pct": f.get("soc_pct")}
        return body

    async def heartbeat_once(self) -> dict | None:
        info, token = self.info, self.rt.secrets.get(TOKEN_SECRET)
        if not info.get("url") or not token:
            return None
        try:
            async with self._client(info["url"], token) as c:
                r = await c.post("/api/v1/node/heartbeat", json=self.heartbeat_body())
        except httpx.HTTPError as exc:
            self.connected, self.last_error = False, f"cloud niet bereikbaar ({type(exc).__name__})"
            return None
        if r.status_code == 401:
            self.connected, self.last_error = False, "koppeling ingetrokken of token ongeldig — koppel opnieuw"
            self.remote_enabled_cloud = False
            return None
        if r.status_code != 200:
            self.connected, self.last_error = False, f"cloud: {self._error(r)}"
            return None
        data = r.json()
        self.connected, self.last_ok, self.last_error = True, datetime.now(UTC), None
        self.remote_enabled_cloud = bool(data.get("remote_control_enabled"))
        self.license, self.entitlements = data.get("license"), data.get("entitlements", [])
        if data.get("rotate_token"):
            await self.rotate_token()
        return data

    async def rotate_token(self) -> bool:
        info, token = self.info, self.rt.secrets.get(TOKEN_SECRET)
        try:
            async with self._client(info["url"], token) as c:
                r = await c.post("/api/v1/node/rotate")
        except httpx.HTTPError:
            return False
        if r.status_code != 200:
            return False
        self.rt.secrets.set(TOKEN_SECRET, r.json()["token"])
        return True

    # ------------------------------------------------------------ commands
    async def execute(self, item: dict) -> tuple[str, str, bool | None]:
        """Run one remote command through the local checks. Returns (status, reason, executes)."""
        rt = self.rt
        if not self.cfg.remote_control_allowed:
            return "rejected", "bediening op afstand staat op deze installatie uit", None
        if not self.remote_enabled_cloud:
            return "rejected", "bediening op afstand staat voor deze locatie uit", None
        cmd = item.get("command") or {}
        try:
            action = CommandAction(str(cmd.get("action")))
            rt.config.device(str(cmd.get("device")))
        except (ValueError, KeyError):
            return "rejected", "onbekend apparaat of onbekende actie", None
        duration = cmd.get("duration_min")
        if duration is not None and not 0 < float(duration) <= 24 * 60:
            return "rejected", "ongeldige duur", None
        who = f"cloud:{str(item.get('requested_by', ''))[:32]}"
        principal = Principal(username=who, role="operator", kind="cloud")
        command = Command(str(cmd["device"]), action, cmd.get("value"))
        chk = rt.engine.check(command, principal)                   # can_execute: capabilities, level, limits
        if not chk.allowed:
            return "rejected", "; ".join(chk.reasons) or "niet toegestaan", False
        # Same path as local manual control: an override that the engine runs through the SafetyValidator.
        rt.engine.overrides.set(Command(command.device_id, action, chk.value), duration, user=who,
                                note="Bediening op afstand via Energy Manager Cloud")
        rt.optimizer.request("bediening op afstand")
        await rt.notifications.notify("info", "remote_command", f"Bediening op afstand: {command.describe_nl()}",
                                      key=str(item.get("id")), cooldown_s=0)
        return "accepted", "; ".join(chk.reasons), chk.executes

    async def poll_commands_once(self, wait: float = 0) -> int:
        info, token = self.info, self.rt.secrets.get(TOKEN_SECRET)
        if not info.get("url") or not token:
            return 0
        try:
            async with self._client(info["url"], token) as c:
                r = await c.get("/api/v1/node/commands", params={"wait": wait})
                if r.status_code != 200:
                    return 0
                items = r.json()
                for item in items:
                    try:
                        status, reason, executes = await self.execute(item)
                    except Exception as exc:                   # never let a remote command crash the EMS
                        log.exception("remote command failed")
                        status, reason, executes = "failed", type(exc).__name__, None
                    self.executed.append({"id": item.get("id"), "command": item.get("command"), "status": status,
                                          "reason": reason, "at": datetime.now(UTC).isoformat()})
                    self.executed = self.executed[-50:]
                    await c.post(f"/api/v1/node/commands/{item['id']}/ack",
                                 json={"status": status, "reason": reason[:500], "executes": executes})
                return len(items)
        except httpx.HTTPError as exc:
            self.connected, self.last_error = False, f"cloud niet bereikbaar ({type(exc).__name__})"
            return 0

    # --------------------------------------------------------------- loop
    async def loop(self) -> None:
        """Runs while the runtime runs; does nothing unless enabled and paired. Errors only back off."""
        backoff = 5.0
        while True:
            if not (self.cfg.enabled and self.paired):
                self.connected = False
                await asyncio.sleep(30)
                continue
            data = await self.heartbeat_once()
            if data is None:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300.0)
                continue
            backoff = 5.0
            deadline = asyncio.get_running_loop().time() + self.cfg.heartbeat_s
            while asyncio.get_running_loop().time() < deadline:
                if self.remote_enabled_cloud and self.cfg.remote_control_allowed:
                    await self.poll_commands_once(wait=20)
                else:
                    await asyncio.sleep(min(20, self.cfg.heartbeat_s))
