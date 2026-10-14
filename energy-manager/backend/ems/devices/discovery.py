"""Automatic device discovery — no aggressive port scans.

Sources: mDNS/Zeroconf (HomeWizard ``_hwenergy._tcp``, Shelly ``_shelly._tcp``, Home Assistant
``_home-assistant._tcp``), the Testomgeving's virtual devices (127.0.0.1), addresses the user enters
(only the well-known port of each protocol is tried: HTTP 80, Modbus 502), and serial ports (USB P1
cables / RS485 adapters).

Each result has a confidence:
  EXACT_MODEL       identity read from the device matches one model profile
  MANUFACTURER      manufacturer known (e.g. SunSpec common model), exact model profile not available
  PROTOCOL          the protocol answers (e.g. a Modbus server) but the device is not identified
  UNVERIFIED        something answers, nothing more is known
An open Modbus port is never presented as a specific inverter.

Results are de-duplicated by device identity (serial number / MAC) — the same device found twice
(e.g. by mDNS and by address, or by several nodes) becomes one result with several routes; a device
already configured is reported with its device id, also when its IP address changed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from ems.devices.library.catalog import catalog
from ems.integrations.modbus.transport import ModbusTransport

log = logging.getLogger(__name__)

EXACT, MANUFACTURER, PROTOCOL, UNVERIFIED = "EXACT_MODEL", "MANUFACTURER", "PROTOCOL", "UNVERIFIED"
CONFIDENCE_LABELS = {EXACT: "Exact model herkend", MANUFACTURER: "Fabrikant herkend", PROTOCOL: "Protocol herkend",
                     UNVERIFIED: "Niet geverifieerd"}
SHELLY_CATS = ["smart_plug", "smart_relay", "boiler", "flexible_load", "heat_pump"]
ORDER = [EXACT, MANUFACTURER, PROTOCOL, UNVERIFIED]
MDNS_SERVICES = {"_hwenergy._tcp.local.": "homewizard", "_shelly._tcp.local.": "shelly",
                 "_home-assistant._tcp.local.": "homeassistant"}


def _result(**kw) -> dict:
    base = {"confidence": UNVERIFIED, "manufacturer": None, "model": None, "serial": None, "categories": [],
            "driver_id": None, "profile_id": None, "protocol": None, "host": None, "port": None, "unit_id": None,
            "routes": [], "node": "local", "notes": [], "connection": {}, "virtual": False}
    base.update(kw)
    base["confidence_label"] = CONFIDENCE_LABELS[base["confidence"]]
    return base


# ------------------------------------------------------------------ probes
async def probe_http(host: str, port: int = 80, timeout: float = 2.5, transport=None) -> list[dict]:
    out = []
    target = host if port == 80 else f"{host}:{port}"
    async with httpx.AsyncClient(base_url=f"http://{target}", timeout=timeout, follow_redirects=False,
                                 transport=transport) as c:
        try:
            r = await c.get("/api")
            if r.status_code == 200 and isinstance(j := r.json(), dict) and j.get("product_type"):
                is_p1 = j.get("product_type") == "HWE-P1"
                out.append(_result(confidence=EXACT if is_p1 else MANUFACTURER, manufacturer="HomeWizard",
                                   model=j.get("product_name") or j.get("product_type"), serial=(j.get("serial") or "").lower(),
                                   categories=["smart_meter"] if is_p1 else [], driver_id="homewizard.p1" if is_p1 else None,
                                   protocol="http", host=host, port=port,
                                   connection={"host": host, "api": "v1", "port": port,
                                               "serial": (j.get("serial") or "").lower()},
                                   notes=[] if is_p1 else ["Alleen de P1 Meter wordt in deze versie ondersteund."]))
                return out
        except (httpx.HTTPError, ValueError):
            pass
        try:
            r = await c.get("/shelly")
            if r.status_code == 200 and isinstance(j := r.json(), dict) and j.get("gen"):
                gen2 = int(j.get("gen") or 0) >= 2
                out.append(_result(confidence=EXACT if gen2 else MANUFACTURER, manufacturer="Shelly",
                                   model=f"{j.get('app') or j.get('model')} (Gen{j.get('gen')})",
                                   serial=str(j.get("id") or j.get("mac")),
                                   categories=SHELLY_CATS if gen2 else [],
                                   driver_id="shelly.switch" if gen2 else None, protocol="http", host=host, port=port,
                                   connection={"host": target},
                                   notes=["Voor SG Ready: kies de categorie Warmtepomp en 'SG Ready via relais'."] if gen2
                                   else ["Shelly Gen1 wordt niet ondersteund."]))
                return out
        except (httpx.HTTPError, ValueError):
            pass
        try:
            r = await c.get("/api/")
            if r.status_code in (200, 401) and "home" in r.text.lower() or r.status_code == 401:
                out.append(_result(confidence=PROTOCOL, manufacturer="Home Assistant", model="Home Assistant-server",
                                   driver_id="homeassistant.entities", protocol="http", host=host, port=port,
                                   connection={"url": f"http://{target}"}, categories=[],
                                   notes=["Maak in Home Assistant een long-lived access token aan om entiteiten te kiezen."]))
        except (httpx.HTTPError, ValueError):
            pass
    return out


def match_sunspec(ident: dict) -> tuple[str, Any]:
    """(confidence, profile) for a SunSpec identity."""
    mn, md = (ident.get("manufacturer") or ""), (ident.get("model") or "")
    best = None
    for prof in catalog().profiles.values():
        for sig in prof.discovery_signatures:
            if sig.kind != "sunspec":
                continue
            if sig.manufacturer and sig.manufacturer.lower() != mn.lower():
                continue
            if sig.model_regex and not re.search(sig.model_regex, md):
                continue
            specific = bool(sig.manufacturer or sig.model_regex)
            if best is None or (specific and not best[1]):
                best = (prof, specific)
    if best is None:
        return MANUFACTURER, None
    return (EXACT if best[1] else MANUFACTURER), best[0]


async def probe_modbus(host: str, port: int = 502, unit_ids=(1,), timeout: float = 2.0) -> list[dict]:
    from ems.integrations.modbus.codec import decode
    from ems.integrations.sunspec.driver import identify as sunspec_identify
    out = []
    for unit in unit_ids:
        t = ModbusTransport("tcp", host=host, port=port, timeout=timeout, retries=0)
        try:
            await t.connect()
        except Exception:
            return out
        try:
            try:
                ident = await sunspec_identify(t, unit)
                conf, prof = match_sunspec(ident)
                models = ident.get("sunspec_models", [])
                cats = (["pv_inverter"] if any(m in models for m in (101, 102, 103, 111, 112, 113)) else []) + \
                       (["smart_meter", "energy_meter"] if any(m in models for m in (201, 202, 203, 204)) else []) + \
                       (["battery"] if any(m in models for m in (124, 802)) else [])
                if prof is None:
                    prof = catalog().profiles.get("sunspec.inverter" if "pv_inverter" in cats else "sunspec.meter")
                out.append(_result(confidence=conf, manufacturer=ident.get("manufacturer"), model=ident.get("model"),
                                   serial=ident.get("serial_number"), categories=cats, driver_id="library.sunspec",
                                   profile_id=None if prof is None else prof.profile_id, protocol="sunspec", host=host,
                                   port=port, unit_id=unit, firmware=ident.get("firmware_version"),
                                   connection={"host": host, "port": port, "unit_id": unit,
                                               **({"profile_id": prof.profile_id} if prof else {})},
                                   notes=[f"SunSpec-modellen: {', '.join(str(m) for m in models)}"]))
                continue
            except Exception:
                pass
            matched = None
            for prof in catalog().profiles.values():
                for sig in prof.discovery_signatures:
                    if sig.kind != "modbus_string" or (sig.unit_id not in (None, unit)) or sig.address is None:
                        continue
                    try:
                        regs = await t.read("holding", sig.address, sig.size or 8, unit)
                    except Exception:
                        continue
                    text = decode(regs, "string")
                    if (sig.equals and text == sig.equals) or (sig.prefix and text.startswith(sig.prefix)):
                        matched = prof
                        break
                if matched:
                    break
            if matched is not None:
                serial = None
                try:
                    serial = decode(await t.read("holding", 16, 8, unit), "string")
                except Exception:
                    pass
                out.append(_result(confidence=EXACT, manufacturer=matched.manufacturer, model=matched.model, serial=serial,
                                   categories=[matched.device_category.value], driver_id=matched.driver_id,
                                   profile_id=matched.profile_id, protocol="modbus_tcp", host=host, port=port, unit_id=unit,
                                   connection={"host": host, "port": port, "unit_id": unit, "profile_id": matched.profile_id}))
                continue
            # The server answers Modbus, but nothing identifies the device: never guess a model.
            answers = False
            for table, addr in (("holding", 0), ("input", 0)):
                try:
                    await t.read(table, addr, 1, unit)
                    answers = True
                    break
                except Exception as exc:
                    answers = answers or "uitzondering" in str(exc)
            if answers:
                out.append(_result(confidence=PROTOCOL, manufacturer=None, model="Onbekend Modbus-apparaat",
                                   protocol="modbus_tcp", host=host, port=port, unit_id=unit,
                                   driver_id="generic.modbus_tcp", connection={"host": host, "port": port, "unit_id": unit},
                                   notes=["Modbus antwoordt, maar het apparaat is niet herkend. Kies het merk en model, of "
                                          "gebruik Expert met registers uit de handleiding."]))
        finally:
            await t.close()
    return out


async def mdns(timeout_s: float = 3.0) -> list[dict]:
    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    except ImportError:
        return []
    pending: list[tuple[str, str]] = []

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            pending.append((service_type, name))

    hosts: list[tuple[str, int, str]] = []
    try:
        azc = AsyncZeroconf()
    except Exception as exc:          # no multicast-capable interface
        log.info("mdns unavailable", extra={"error": str(exc)})
        return []
    try:
        browser = AsyncServiceBrowser(azc.zeroconf, list(MDNS_SERVICES), handlers=[on_change])
        await asyncio.sleep(timeout_s)
        for service_type, name in pending:
            info = AsyncServiceInfo(service_type, name)
            if await info.async_request(azc.zeroconf, 1500) and info.parsed_addresses():
                hosts.append((info.parsed_addresses()[0], info.port or 80, MDNS_SERVICES[service_type]))
        await browser.async_cancel()
    finally:
        await azc.async_close()
    out = []
    for host, _port, kind in hosts:
        out += await probe_http(host, 8123 if kind == "homeassistant" else 80)
    return out


def serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in list_ports.comports():
        desc = f"{p.description or ''} {p.manufacturer or ''}".strip()
        out.append(_result(confidence=PROTOCOL, manufacturer=p.manufacturer, model=p.description or "Seriële poort",
                           serial=p.serial_number, protocol="serial", host=p.device, categories=["smart_meter"],
                           driver_id="dsmr.p1", connection={"port": p.device},
                           notes=[f"Seriële poort {p.device} ({desc}). Een P1-kabel (slimme meter) of een RS485-adapter "
                                  "(Modbus RTU): kies daarna het apparaat."]))
    return out


# ------------------------------------------------------------------- scan
def _key(r: dict) -> str:
    if r.get("serial"):
        return f"{(r.get('manufacturer') or '').lower()}|{str(r['serial']).lower()}"
    return f"{r.get('protocol')}|{r.get('host')}|{r.get('port')}|{r.get('unit_id')}"


def merge(results: list[dict], configured: list[dict]) -> list[dict]:
    """De-duplicate by identity, keep every route, mark devices that are already configured."""
    merged: dict[str, dict] = {}
    for r in results:
        route = {"node": r.get("node", "local"), "host": r.get("host"), "port": r.get("port"), "protocol": r.get("protocol")}
        k = _key(r)
        if k in merged:
            m = merged[k]
            if route not in m["routes"]:
                m["routes"].append(route)
            if ORDER.index(r["confidence"]) < ORDER.index(m["confidence"]):
                m.update({x: r[x] for x in ("confidence", "confidence_label", "profile_id", "model", "manufacturer")})
            continue
        r["routes"] = [route]
        merged[k] = r
    for r in merged.values():
        r["configured_device"] = None
        r["address_changed"] = False
        for d in configured:
            ident = (d.get("params") or {}).get("identity") or {}
            conn = d.get("connection") or {}
            same_serial = r.get("serial") and ident.get("serial") and str(ident["serial"]).lower() == str(r["serial"]).lower()
            same_addr = conn.get("host") in (r.get("host"), f"{r.get('host')}:{r.get('port')}") and \
                (conn.get("port") in (None, r.get("port")) or r.get("protocol") == "http") and \
                (conn.get("unit_id") in (None, r.get("unit_id")))
            if same_serial or (same_addr and not r.get("serial")):
                r["configured_device"] = d["id"]
                here = (r.get("host"), f"{r.get('host')}:{r.get('port')}")
                r["address_changed"] = bool(same_serial and conn.get("host") not in here)
                break
    return sorted(merged.values(), key=lambda r: (r["configured_device"] is not None, ORDER.index(r["confidence"])))


async def scan(*, lab=None, hosts: list[str] | None = None, use_mdns: bool = True, use_serial: bool = True,
               configured: list[dict] | None = None, timeout_s: float = 3.0) -> dict:
    tasks = []
    sources = []
    if lab is not None:
        sources.append("testomgeving")
        for key, port in lab.ports.items():
            if key in ("p1", "sgready", "boiler", "homeassistant"):
                tasks.append(probe_http("127.0.0.1", port))
            else:
                tasks.append(probe_modbus("127.0.0.1", port))
    for h in hosts or []:
        h = h.strip()
        if not h:
            continue
        host, _, port = h.partition(":")
        sources.append(f"adres {h}")
        if port:
            tasks.append(probe_modbus(host, int(port)) if int(port) not in (80, 8123) else probe_http(host, int(port)))
            continue
        tasks += [probe_http(host, 80), probe_modbus(host, 502)]
    if use_mdns:
        sources.append("mDNS")
        tasks.append(mdns(timeout_s))
    results = []
    for res in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(res, Exception):
            log.info("discovery probe failed", extra={"error": str(res)})
            continue
        results += res
    if lab is not None:
        for r in results:
            if r.get("host") == "127.0.0.1":
                r["virtual"] = True
                r["notes"] = ["Virtueel apparaat (Testomgeving)."] + r["notes"]
    if use_serial:
        sources.append("seriële poorten")
        results += serial_ports()
    return {"results": merge(results, configured or []), "sources": sources,
            "note": "Er wordt niet gescand op het hele netwerk: alleen mDNS, de Testomgeving en de adressen die u opgeeft."}
