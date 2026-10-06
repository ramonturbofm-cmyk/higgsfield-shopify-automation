"""mDNS discovery (docs: Discovery): _hwenergy._tcp (API v1, HTTP) and
_homewizard._tcp (API v2+, HTTPS). Requires the EMS to be on the same LAN
(Docker: network_mode host)."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

SERVICES = ("_homewizard._tcp.local.", "_hwenergy._tcp.local.")


def _props(info) -> dict:
    out = {}
    for k, v in (info.properties or {}).items():
        key = k.decode() if isinstance(k, bytes) else str(k)
        out[key] = v.decode() if isinstance(v, bytes) else v
    return out


async def discover(timeout_s: float = 3.0) -> list[dict]:
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    found: dict[str, dict] = {}
    pending: list[tuple[str, str]] = []

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            pending.append((service_type, name))

    azc = AsyncZeroconf()
    try:
        browser = AsyncServiceBrowser(azc.zeroconf, list(SERVICES), handlers=[on_change])
        await asyncio.sleep(timeout_s)
        for service_type, name in pending:
            info = AsyncServiceInfo(service_type, name)
            if not await info.async_request(azc.zeroconf, 2000):
                continue
            props = _props(info)
            addresses = info.parsed_addresses()
            if not addresses:
                continue
            api = "v2" if service_type.startswith("_homewizard") else "v1"
            serial = (props.get("serial") or "").lower()
            entry = found.setdefault(serial or name, {
                "name": props.get("product_name") or name.split(".")[0], "serial": serial,
                "product_type": props.get("product_type"), "host": addresses[0], "apis": [],
            })
            entry["apis"].append(api)
            if api == "v2":
                entry["v2_id"] = props.get("id")
                entry["api_version"] = props.get("api_version")
                entry["port_v2"] = info.port
            else:
                entry["api_v1_enabled"] = props.get("api_enabled") == "1"
        await browser.async_cancel()
    finally:
        await azc.async_close()
    for e in found.values():
        e["recommended_api"] = "v2" if "v2" in e["apis"] else "v1"
        e["is_p1"] = e.get("product_type") == "HWE-P1"
    return sorted(found.values(), key=lambda e: (not e["is_p1"], e["name"]))
