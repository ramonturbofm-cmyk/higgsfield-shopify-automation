"""NodeService: this node's identity plus its relations with other Energy Manager nodes.

As **owner** of local devices (every node): grants the control lease, accepts commands
from the lease holder only (epoch fencing, max. command age), runs them through its own
CommandGate (commissioning level + SafetyValidator) and releases the devices when the
controller disappears.

As **controller** (PRIMARY_CONTROLLER role): keeps a NodeLink per paired gateway node,
renews the lease with a heartbeat, refreshes the remote device list and fills history
gaps after an outage from the data buffered on the gateway.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ems.control.safety import is_release
from ems.core.journal import JournalEntry
from ems.core.models import Command, CommandAction, Decision
from ems.nodes.discovery import NodeAdvertiser
from ems.nodes.identity import load_identity
from ems.nodes.lease import LeaseManager
from ems.nodes.link import NodeLink, NodeLinkError
from ems.nodes.pairing import PairingManager, hash_token

if TYPE_CHECKING:
    from ems.server.runtime import EMSRuntime

log = logging.getLogger(__name__)
HEARTBEAT_S = 5.0
DEVICE_REFRESH_S = 60.0


class NodeService:
    def __init__(self, rt: EMSRuntime) -> None:
        self.rt = rt
        cfg = rt.config.node
        self.identity = load_identity(rt.data_dir, cfg.name, cfg.role_preset)
        self.lease = LeaseManager(cfg.lease_ttl_s, load_epoch=lambda: int(rt.db.kv_get("node.lease_epoch", 0) or 0),
                                  save_epoch=lambda e: rt.db.kv_set("node.lease_epoch", e))
        self.pairing = PairingManager()
        self.links: dict[str, NodeLink] = {}
        self.remote_controlled: set[str] = set()
        self.advertiser: NodeAdvertiser | None = None
        self._last_device_refresh: dict[str, float] = {}
        self.transport = None   # tests inject an in-process transport for links

    # -------------------------------------------------------------- set-up
    def load_links(self) -> None:
        for row in self.rt.db.list_nodes("gateway"):
            token = self.rt.secrets.get(f"node.{row['node_id']}.token")
            if token and row["node_id"] not in self.links:
                self.links[row["node_id"]] = NodeLink(row["node_id"], row["name"], row["address"], token,
                                                      self.identity.node_id, transport=self.transport)

    def reconfigure(self) -> None:
        cfg = self.rt.config.node
        new = load_identity(self.rt.data_dir, cfg.name, cfg.role_preset)
        self.identity.name, self.identity.roles = new.name, new.roles
        self.lease.ttl = cfg.lease_ttl_s

    async def start_advertising(self, port: int) -> None:
        if self.rt.config.node.advertise:
            self.advertiser = NodeAdvertiser(self.identity, port)
            await self.advertiser.start()

    async def stop(self) -> None:
        if self.advertiser:
            await self.advertiser.stop()
        for link in self.links.values():
            await link.close()

    # --------------------------------------------------- owner: lease guard
    def owner_guard(self, cmd: Command, requester: str | None) -> str | None:
        """Called by the CommandGate before a command reaches a local driver."""
        if requester is not None:
            return None            # remote request: lease + epoch already checked by the node API
        if not self.identity.is_controller:
            return "deze node is alleen gateway; een andere node regelt de apparaten"
        ok, st = self.lease.acquire(self.identity.node_id)
        if not ok:
            holder = self.rt.db.get_node(st.holder) if st.holder else None
            return f"andere controller heeft het regelrecht ({(holder or {}).get('name') or st.holder})"
        return None

    # ------------------------------------------- owner: requests from peers
    def peer_from_token(self, token: str) -> dict | None:
        return self.rt.db.find_node_by_token(hash_token(token)) if token else None

    def accept_pairing(self, code: str, peer: dict, address: str | None) -> str | None:
        if not self.pairing.verify(code):
            return None
        token, token_hash = PairingManager.new_token()
        self.rt.db.upsert_node({
            "node_id": peer["node_id"], "name": peer.get("name") or peer["node_id"][:8], "relation": "controller",
            "address": address, "platform": peer.get("platform"), "version": peer.get("version"),
            "roles": peer.get("roles") or [], "token_hash": token_hash, "paired_ts": time.time(),
            "last_seen_ts": time.time(), "info": {}})
        log.info("node paired as controller", extra={"peer": peer["node_id"], "peer_name": peer.get("name")})
        return token

    def lease_request(self, peer: dict) -> dict:
        ok, st = self.lease.acquire(peer["node_id"])
        self.rt.db.upsert_node({"node_id": peer["node_id"], "name": peer["name"], "relation": "controller",
                                "paired_ts": peer["paired_ts"], "last_seen_ts": time.time()})
        holder = self.rt.db.get_node(st.holder) if st.holder and st.holder != peer["node_id"] else None
        if st.holder == self.identity.node_id:
            holder = {"name": f"{self.identity.name} (lokaal)"}
        return {"granted": ok, "epoch": st.epoch if ok else None, "holder": st.holder,
                "holder_name": (holder or {}).get("name"), **st.to_dict(time.time())}

    async def remote_command(self, peer: dict, device_id: str, body: dict) -> dict:
        reason = self.lease.check(peer["node_id"], int(body.get("epoch") or -1))
        if reason:
            return {"outcome": "rejected", "error": reason}
        age = time.time() - float(body.get("issued_ts") or 0)
        if age > float(body.get("ttl_s") or 30) or age < -30:
            return {"outcome": "rejected", "error": f"verouderde opdracht ({age:.0f} s oud) — niet uitgevoerd"}
        try:
            cmd = Command(device_id, CommandAction(body["action"]), body.get("value"))
        except (KeyError, ValueError):
            return {"outcome": "rejected", "error": "ongeldige opdracht"}
        decision = Decision(cmd, f"Opdracht van {peer['name']}: {cmd.describe_nl()}",
                            [body.get("reason") or f"Gepland door controller {peer['name']}"],
                            source=f"node:{peer['name']}")
        now = self.rt.clock.now()
        gr = await self.rt.engine.gate.submit(decision, now, requester=peer["node_id"])
        if gr.outcome.value in ("sent", "refreshed") and not is_release(cmd):
            self.remote_controlled.add(device_id)
        if gr.outcome.value not in ("skipped", "refreshed"):
            self.rt.engine._journal(gr, f"node-{peer['node_id'][:8]}", now)
        return {"outcome": gr.outcome.value, "error": gr.error, "command": {"action": gr.decision.command.action.value,
                                                                            "value": gr.decision.command.value},
                "notes": gr.decision.reasons}

    async def release_remote(self, device_ids: list[str] | None = None, why: str = "") -> None:
        ids = sorted(self.remote_controlled if device_ids is None else set(device_ids))
        for dev in ids:
            try:
                await self.rt.devices.driver(dev).release_control()
            except Exception as exc:
                log.warning("release failed", extra={"device": dev, "error": str(exc)})
            self.rt.engine.gate.reset(dev)
            self.remote_controlled.discard(dev)
        if ids and why:
            self.rt.journal.record(JournalEntry(
                timestamp=self.rt.clock.now(), site_id=self.rt.config.site.id, run_id="node", device=",".join(ids),
                action="release", outcome="released", old_value=None, new_value=None,
                summary="Apparaten terug naar eigen regeling", reasons=[why], source="safety"))

    # ------------------------------------------------- controller: pairing
    async def pair_with(self, address: str, code: str) -> dict:
        import httpx

        address = address.strip().rstrip("/")
        if not address.startswith(("http://", "https://")):
            address = f"http://{address}"
        if address.count(":") < 2:
            address += ":8080"
        me = self.identity
        async with httpx.AsyncClient(base_url=address, timeout=8.0, transport=self.transport) as client:
            r = await client.post("/api/v1/node/pair", json={"code": code, "node": {
                "node_id": me.node_id, "name": me.name, "platform": me.platform, "version": me.version,
                "roles": me.roles}})
        if r.status_code != 200:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            raise NodeLinkError(detail or f"koppelen mislukt (HTTP {r.status_code})")
        res = r.json()
        info = res["node"]
        if info["node_id"] == me.node_id:
            raise NodeLinkError("dit is deze node zelf")
        self.rt.secrets.set(f"node.{info['node_id']}.token", res["token"])
        self.rt.db.upsert_node({"node_id": info["node_id"], "name": info["name"], "relation": "gateway",
                                "address": address, "platform": info.get("platform"), "version": info.get("version"),
                                "roles": info.get("roles") or [], "paired_ts": time.time(),
                                "last_seen_ts": time.time(), "info": info})
        old = self.links.pop(info["node_id"], None)
        if old:
            await old.close()
        link = NodeLink(info["node_id"], info["name"], address, res["token"], me.node_id, transport=self.transport)
        self.links[info["node_id"]] = link
        await self.heartbeat(link)
        return {"node": info, "link": link.to_dict()}

    async def unpair(self, node_id: str) -> bool:
        link = self.links.pop(node_id, None)
        if link:
            await link.close()
        self.rt.secrets.delete(f"node.{node_id}.token")
        return self.rt.db.delete_node(node_id)

    # ------------------------------------------------------ periodic work
    async def heartbeat(self, link: NodeLink) -> None:
        try:
            await link.heartbeat(want_lease=self.identity.is_controller)
            if time.monotonic() - self._last_device_refresh.get(link.node_id, 0) > DEVICE_REFRESH_S or not link.devices:
                await link.refresh_devices()
                self._last_device_refresh[link.node_id] = time.monotonic()
            self.rt.db.upsert_node({"node_id": link.node_id, "name": link.info.get("name") or link.name,
                                    "relation": "gateway", "paired_ts": (self.rt.db.get_node(link.node_id) or {}).get(
                                        "paired_ts", time.time()), "last_seen_ts": time.time(), "info": link.info,
                                    "version": link.info.get("version"), "platform": link.info.get("platform"),
                                    "roles": link.info.get("roles")})
        except NodeLinkError as exc:
            log.warning("node heartbeat failed", extra={"node": link.name, "error": str(exc)})

    async def sync_gaps(self, link: NodeLink) -> int:
        if not link.gaps or not link.online:
            return 0
        mapping = {d.connection.get("remote_id"): d.id for d in self.rt.config.devices
                   if d.driver == "node.remote" and d.connection.get("node_id") == link.node_id}
        if not mapping:
            link.gaps.clear()
            return 0
        start, end = link.gaps.pop(0)
        try:
            data = await link.history(list(mapping), start, end)
        except NodeLinkError:
            link.gaps.insert(0, (start, end))
            return 0
        n = 0
        for rid, rows in data.items():
            local = mapping.get(rid)
            if local:
                n += await asyncio.to_thread(self.rt.db.replace_device_samples, local, start, end, [
                    {"ts": r["ts"], "device_id": local, "status": r["status"], "values": r["values"]} for r in rows])
        log.info("history gap synced from node", extra={"node": link.name, "rows": n})
        return n

    async def step(self) -> None:
        """One round: release devices of a vanished controller; heartbeat + gap sync per gateway."""
        expired = self.lease.expired_holder()
        if expired and expired != self.identity.node_id and self.remote_controlled:
            peer = self.rt.db.get_node(expired) or {"name": expired}
            await self.release_remote(why=f"Controller {peer['name']} onbereikbaar: regelrecht verlopen")
        for link in list(self.links.values()):
            await self.heartbeat(link)
            await self.sync_gaps(link)

    async def loop(self) -> None:
        while True:
            try:
                await self.step()
            except Exception:
                log.exception("node loop failed")
            await asyncio.sleep(HEARTBEAT_S)

    # ------------------------------------------------------------ overview
    def overview(self) -> dict[str, Any]:
        me = self.identity
        lease = self.lease.state.to_dict(time.time())
        holder = lease.get("holder")
        nodes = [{**me.to_dict(), "relation": "self", "online": True, "address": None, "last_seen": time.time(),
                  "devices": [d.id for d in self.rt.config.devices if d.driver != "node.remote"]}]
        for row in self.rt.db.list_nodes():
            link = self.links.get(row["node_id"])
            entry = {"node_id": row["node_id"], "name": row["name"], "relation": row["relation"],
                     "address": row["address"], "platform": row["platform"], "version": row["version"],
                     "roles": row["roles"] or [], "last_seen": row["last_seen_ts"], "paired": row["paired_ts"]}
            if link is not None:
                entry.update(online=link.online, error=link.error, latency_ms=link.latency_ms,
                             lease=link.to_dict()["lease"], remote_devices=list(link.devices.values()))
            else:
                recent = row["last_seen_ts"] and time.time() - row["last_seen_ts"] < 3 * self.lease.ttl
                entry.update(online=bool(recent), holds_lease=holder == row["node_id"])
            nodes.append(entry)
        return {"self": me.to_dict(), "lease": {**lease, "holder_is_self": holder == me.node_id},
                "pairing_open": self.pairing.open, "nodes": nodes}
