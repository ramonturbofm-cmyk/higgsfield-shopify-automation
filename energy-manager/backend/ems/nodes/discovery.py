"""Find other Energy Manager nodes on the LAN via mDNS / DNS-SD (no network scans).

Service type ``_energymanager._tcp.local.`` with TXT records node_id, name, platform,
version and roles. Advertising is passive; browsing only listens a few seconds.
"""

from __future__ import annotations

import asyncio
import logging
import socket

log = logging.getLogger(__name__)
SERVICE = "_energymanager._tcp.local."


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))      # TEST-NET, nothing is sent
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class NodeAdvertiser:
    def __init__(self, identity, port: int) -> None:
        self.identity, self.port = identity, port
        self._zc = None
        self._info = None

    async def start(self) -> None:
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf

            ident = self.identity
            safe = "".join(c if c.isalnum() or c in "-" else "-" for c in ident.name)[:40] or "node"
            self._info = ServiceInfo(
                SERVICE, f"{safe}-{ident.node_id[:8]}.{SERVICE}", port=self.port,
                addresses=[socket.inet_aton(_local_ip())],
                properties={"node_id": ident.node_id, "name": ident.name, "platform": ident.platform,
                            "version": ident.version, "roles": ",".join(ident.roles)})
            self._zc = AsyncZeroconf()
            await self._zc.async_register_service(self._info)
        except Exception as exc:  # discovery is a convenience, never fatal
            log.warning("mDNS advertising unavailable", extra={"error": str(exc)})
            self._zc = None

    async def stop(self) -> None:
        if self._zc is not None:
            try:
                await self._zc.async_unregister_service(self._info)
                await self._zc.async_close()
            except Exception:
                pass
            self._zc = None


async def browse(timeout_s: float = 3.0, own_node_id: str | None = None) -> list[dict]:
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    found: dict[str, dict] = {}
    names: set[str] = set()
    azc = AsyncZeroconf()

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            names.add(name)

    browser = AsyncServiceBrowser(azc.zeroconf, [SERVICE], handlers=[on_change])
    try:
        await asyncio.sleep(timeout_s)
        for name in list(names):
            info = AsyncServiceInfo(SERVICE, name)
            if not await info.async_request(azc.zeroconf, 1500):
                continue
            props = {k.decode(): (v.decode() if isinstance(v, bytes) else v) for k, v in (info.properties or {}).items()}
            if props.get("node_id") in (None, own_node_id):
                continue
            addrs = info.parsed_addresses()
            found[props["node_id"]] = {
                "node_id": props["node_id"], "name": props.get("name"), "platform": props.get("platform"),
                "version": props.get("version"), "roles": [r for r in (props.get("roles") or "").split(",") if r],
                "address": f"http://{addrs[0]}:{info.port}" if addrs else None}
    finally:
        await browser.async_cancel()
        await azc.async_close()
    return sorted(found.values(), key=lambda n: n.get("name") or "")
