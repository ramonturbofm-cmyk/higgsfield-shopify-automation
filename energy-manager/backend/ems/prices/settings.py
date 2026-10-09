"""Which market-data settings are relevant, and safe outbound URLs for price sources.

* ``visible_fields``: the settings screen is built from this. A credential or endpoint of a provider
  is only shown (and only used) when that provider is the active source or the enabled reserve
  source — so choosing EnergyZero never shows an ENTSO-E token.
* ``validate_endpoint``: protection against SSRF and credential leakage for configurable endpoints.
  Only ``https``; no user/password in the URL; the host must resolve exclusively to public
  addresses (no loopback, private, link-local, multicast, reserved, CGNAT or IPv6 ULA); the official
  EnergyZero URL is always allowed. Requests made with these URLs never follow redirects.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from ems.core.config import ENERGYZERO_OFFICIAL_URL, PriceConfig

ALWAYS = ("provider", "fallback_enabled", "refresh_minutes")
ADVANCED_COMMON = ("market_area", "market_interval", "publication_expected", "publication_retry_minutes",
                   "publication_alert_after", "allow_custom_endpoints")
PROVIDER_LABELS = {"none": "Geen", "energyzero": "EnergyZero", "entsoe": "ENTSO-E", "custom_api": "Eigen API",
                   "manual": "Handmatig", "demo": "Demo"}


def active_providers(values: dict[str, Any]) -> set[str]:
    out = {values.get("provider") or "none"}
    if values.get("fallback_enabled") and values.get("fallback_provider") not in (None, "none"):
        out.add(values["fallback_provider"])
    return out


def visible_fields(values: dict[str, Any]) -> list[str]:
    """Field names of the ``prices`` section that apply to the given (possibly unsaved) values."""
    active = active_providers(values)
    out = []
    for name, field in PriceConfig.model_fields.items():
        extra = field.json_schema_extra or {}
        providers = extra.get("providers")
        if providers is not None and not active & set(providers):
            continue
        if name == "fallback_provider" and not values.get("fallback_enabled"):
            continue
        out.append(name)
    return out


class EndpointError(ValueError):
    pass


_BLOCKED_NETS = [ipaddress.ip_network(n) for n in ("100.64.0.0/10", "fc00::/7", "64:ff9b::/96")]


def _public(ip: ipaddress._BaseAddress) -> bool:
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified or any(ip in n for n in _BLOCKED_NETS))


def validate_endpoint(url: str, *, allow_custom: bool, resolver=socket.getaddrinfo) -> str:
    """Return the normalised URL or raise ``EndpointError`` (Dutch message)."""
    url = (url or "").strip()
    if url.rstrip("/") == ENERGYZERO_OFFICIAL_URL:
        return ENERGYZERO_OFFICIAL_URL
    if not allow_custom:
        raise EndpointError("aangepaste API-adressen zijn uitgeschakeld (Expert: 'Aangepaste API-adressen toestaan')")
    parts = urlsplit(url.replace("{date}", "2026-01-01"))
    if parts.scheme != "https":
        raise EndpointError("alleen https-adressen zijn toegestaan")
    if parts.username or parts.password or "@" in parts.netloc:
        raise EndpointError("geen gebruikersnaam/wachtwoord in het adres; gebruik de Authorization-header")
    host = parts.hostname
    if not host:
        raise EndpointError("adres zonder host")
    try:
        literal = ipaddress.ip_address(host)
        addrs = [literal]
    except ValueError:
        if host.endswith((".local", ".internal", ".lan", ".home", ".localhost")) or host == "localhost":
            raise EndpointError("interne netwerknamen zijn niet toegestaan") from None
        try:
            infos = resolver(host, parts.port or 443, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise EndpointError(f"host {host} niet gevonden") from exc
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    if not addrs or not all(_public(a) for a in addrs):
        raise EndpointError("dit adres verwijst naar een intern of gereserveerd netwerk en is niet toegestaan")
    return url


SECRET_FIELDS = {"entsoe_token": "prices.entsoe_token", "custom_auth_header": "prices.custom_auth_header"}
MASK = "********"


def build_provider(kind: str, cfg: PriceConfig, secret, *, demo_env=None, client=None):
    """Construct the provider ``kind`` from the market-data settings. ``secret(name)`` resolves
    ``secret:<name>`` references. Raises ValueError (Dutch message) for invalid settings."""
    from ems.prices.providers import CustomApiProvider, DemoProvider, EnergyZeroProvider, EntsoeProvider, StaticProvider

    def resolve(v: str) -> str:
        return (secret(v.removeprefix("secret:")) or "") if v.startswith("secret:") else v

    match kind:
        case "none":
            return None
        case "energyzero":
            url = validate_endpoint(cfg.energyzero_url, allow_custom=cfg.allow_custom_endpoints)
            return EnergyZeroProvider(cfg.market_interval, client=client, url=url)
        case "entsoe":
            return EntsoeProvider(resolve(cfg.entsoe_token), cfg.bidding_zone, client=client)
        case "custom_api":
            url = validate_endpoint(cfg.custom_url, allow_custom=cfg.allow_custom_endpoints)
            return CustomApiProvider(url, items_path=cfg.custom_items_path, start_field=cfg.custom_start_field,
                                     price_field=cfg.custom_price_field, unit=cfg.custom_unit,
                                     auth_header=resolve(cfg.custom_auth_header), client=client)
        case "manual":
            return StaticProvider()
        case "demo":
            if demo_env is None:
                raise ValueError("demo-prijzen zijn alleen beschikbaar in Demo Mode")
            return DemoProvider(demo_env)
    raise ValueError(f"onbekende prijsbron {kind!r}")
