"""Day-ahead price providers.

* ENTSO-E Transparency Platform RESTful API (documentType A44, day-ahead prices).
  Source: "Transparency Platform RESTful API - user guide" (ENTSO-E). Requires a
  free security token. Prices are published in EUR/MWh; we store EUR/kWh.
* EnergyZero Public API (no token): ``GET https://public.api.energyzero.nl/v1/prices`` with
  ``date`` (DD-MM-YYYY), ``interval`` (``INTERVAL_QUARTER`` | ``INTERVAL_HOUR``) and ``energy_type``
  (``ENERGY_TYPE_ELECTRICITY``) — the parameters of EnergyZero's public "Get prices" documentation.
  The response lists ``base`` / ``base_with_vat`` / ``all_in`` / ``all_in_with_vat`` with items
  ``{"start": "...Z", "end": "...Z", "price": {"value": <EUR/kWh, number or string>}}``; verified
  against the live service in CI (the documentation host is not reachable from our build sandbox).
  We use ``base`` (market price excl. VAT); the tariff engine adds the supplier's markup, energy
  tax and VAT from the user's own contract.
* Custom API (expert): an HTTPS JSON source configured by the user (see prices/settings.py for the
  SSRF protection); never follows redirects.
* Manual: user-supplied prices (CSV/JSON via the API).
* Demo: the simulator's synthetic prices — only allowed in Demo Mode.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

ENTSOE_URL = "https://web-api.tp.entsoe.eu/api"


@dataclass(frozen=True)
class PricePoint:
    start: datetime            # UTC
    price_eur_kwh: float
    resolution_min: int


class PriceProvider(ABC):
    name: str = "provider"
    #: Information about the last request for the status screen (endpoint, version, timing).
    last_meta: dict = {}

    @abstractmethod
    async def fetch(self, start: datetime, end: datetime) -> list[PricePoint]: ...


RETRY_DELAYS_S = (1.0, 3.0, 9.0)
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


async def get_with_retry(client: httpx.AsyncClient, url: str, *, params=None, headers=None,
                         delays=RETRY_DELAYS_S, sleep=asyncio.sleep) -> httpx.Response:
    """GET with retries on network errors, timeouts, HTTP 429 and 5xx (not on other 4xx).
    Never follows redirects (a redirect is treated as an error)."""
    last_exc: Exception | None = None
    for attempt in range(len(delays) + 1):
        try:
            resp = await client.get(url, params=params, headers=headers, follow_redirects=False)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < len(delays):
                await sleep(delays[attempt])
                continue
            if 300 <= resp.status_code < 400:
                raise ValueError(f"onverwachte doorverwijzing (HTTP {resp.status_code}) — niet gevolgd")
            if len(resp.content) > MAX_RESPONSE_BYTES:
                raise ValueError("antwoord te groot")
            return resp
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt < len(delays):
                await sleep(delays[attempt])
                continue
            raise ValueError(f"geen verbinding ({type(exc).__name__})") from exc
    raise ValueError(f"geen verbinding ({last_exc})")


def validate_points(points: list[PricePoint], lo: float = -2.0, hi: float = 10.0) -> tuple[list[PricePoint], list[str]]:
    """Reject implausible values (EUR/kWh), misaligned intervals and conflicting duplicates."""
    ok: dict[datetime, PricePoint] = {}
    problems: list[str] = []
    for p in points:
        if not math.isfinite(p.price_eur_kwh) or not lo <= p.price_eur_kwh <= hi:
            problems.append(f"onwaarschijnlijke prijs {p.price_eur_kwh} om {p.start:%Y-%m-%d %H:%M} UTC verworpen")
            continue
        if p.resolution_min not in (15, 30, 60) or (p.start.minute % p.resolution_min) or p.start.second:
            problems.append(f"interval {p.start:%H:%M}/{p.resolution_min} min verworpen (niet uitgelijnd)")
            continue
        prev = ok.get(p.start)
        if prev is not None and prev.price_eur_kwh != p.price_eur_kwh:
            problems.append(f"tegenstrijdige prijzen voor {p.start:%Y-%m-%d %H:%M} UTC verworpen")
            del ok[p.start]
            continue
        ok[p.start] = p
    return [ok[k] for k in sorted(ok)], problems


def _iso_duration_minutes(value: str) -> int:
    value = value.strip()
    if value == "PT15M":
        return 15
    if value == "PT30M":
        return 30
    if value in ("PT60M", "PT1H"):
        return 60
    if value.startswith("PT") and value.endswith("M"):
        return int(value[2:-1])
    raise ValueError(f"unsupported resolution {value}")


def parse_entsoe_a44(xml_text: str) -> list[PricePoint]:
    """Parse a Publication_MarketDocument. Curve type A03 may omit points whose price
    equals the previous one; missing positions are carried forward."""
    root = ET.fromstring(xml_text)
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    if root.tag.endswith("Acknowledgement_MarketDocument"):
        reason = root.find(f".//{ns}Reason/{ns}text")
        raise ValueError(f"ENTSO-E: {reason.text if reason is not None else 'geen data'}")
    points: dict[datetime, PricePoint] = {}
    rev = root.find(f"{ns}revisionNumber")
    parse_entsoe_a44.revision = None if rev is None else rev.text
    for period in root.iter(f"{ns}Period"):
        interval = period.find(f"{ns}timeInterval")
        start = datetime.strptime(interval.find(f"{ns}start").text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)
        end = datetime.strptime(interval.find(f"{ns}end").text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)
        res = _iso_duration_minutes(period.find(f"{ns}resolution").text)
        n_slots = int((end - start).total_seconds() // (res * 60))
        given = {}
        for pt in period.findall(f"{ns}Point"):
            pos = int(pt.find(f"{ns}position").text)
            given[pos] = float(pt.find(f"{ns}price.amount").text) / 1000.0
        last = None
        for pos in range(1, n_slots + 1):
            if pos in given:
                last = given[pos]
            if last is None:
                continue
            ts = start + timedelta(minutes=res * (pos - 1))
            points[ts] = PricePoint(ts, round(last, 6), res)
    return [points[k] for k in sorted(points)]


class EntsoeProvider(PriceProvider):
    name = "entsoe"

    def __init__(self, token: str, bidding_zone: str, client: httpx.AsyncClient | None = None) -> None:
        if not token:
            raise ValueError("ENTSO-E-token ontbreekt: vul het in bij Instellingen → Prijzen & prognoses")
        self.token = token
        self.zone = bidding_zone
        self.client = client

    async def fetch(self, start: datetime, end: datetime) -> list[PricePoint]:
        params = {
            "securityToken": self.token, "documentType": "A44",
            "in_Domain": self.zone, "out_Domain": self.zone,
            "periodStart": start.astimezone(UTC).strftime("%Y%m%d%H00"),
            "periodEnd": end.astimezone(UTC).strftime("%Y%m%d%H00"),
        }
        client = self.client or httpx.AsyncClient(timeout=30)
        t0 = time.monotonic()
        try:
            resp = await get_with_retry(client, ENTSOE_URL, params=params)
        finally:
            if self.client is None:
                await client.aclose()
        if resp.status_code == 401:
            raise ValueError("ENTSO-E: het API-token wordt niet geaccepteerd — "
                             "controleer het token in Instellingen → Prijzen")
        if resp.status_code >= 400 and "Acknowledgement" not in resp.text:
            raise ValueError(f"ENTSO-E: HTTP {resp.status_code}")
        pts = parse_entsoe_a44(resp.text)
        # Never log or return the token: only the host is reported.
        self.last_meta = {"endpoint": ENTSOE_URL, "version": getattr(parse_entsoe_a44, "revision", None),
                          "duration_ms": round((time.monotonic() - t0) * 1000)}
        return pts


ENERGYZERO_INTERVALS = {"quarter": ("INTERVAL_QUARTER", 15), "hour": ("INTERVAL_HOUR", 60)}
ENERGYZERO_URL = "https://public.api.energyzero.nl/v1/prices"
NL_TZ = ZoneInfo("Europe/Amsterdam")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_energyzero(payload: dict, stream: str = "base") -> list[PricePoint]:
    """EnergyZero ``/v1/prices`` response -> price points (EUR/kWh, excl. VAT for ``base``)."""
    if not isinstance(payload, dict):
        raise ValueError("EnergyZero: onverwacht antwoord")
    out = []
    for item in payload.get(stream) or []:
        try:
            start, end = _parse_utc(item["start"]), _parse_utc(item["end"])
            value = item["price"]["value"]
            price = float(value)
        except (KeyError, TypeError, ValueError):
            continue
        minutes = int((end - start).total_seconds() // 60)
        if minutes <= 0:
            continue
        out.append(PricePoint(start, round(price, 6), minutes))
    return sorted(out, key=lambda p: p.start)


class EnergyZeroProvider(PriceProvider):
    """Dutch day-ahead prices from the EnergyZero Public API — no account or token needed."""

    name = "energyzero"

    def __init__(self, interval: str = "quarter", client: httpx.AsyncClient | None = None,
                 url: str = ENERGYZERO_URL, sleep=asyncio.sleep) -> None:
        if interval not in ENERGYZERO_INTERVALS:
            raise ValueError("EnergyZero: interval moet 'quarter' of 'hour' zijn")
        self.interval, self.resolution = ENERGYZERO_INTERVALS[interval]
        self.client = client
        self.url = url
        self.sleep = sleep
        self.last_meta = {}

    @property
    def _endpoint(self) -> tuple[str, str]:          # kept for the CI live check output
        return self.url, "energy_type"

    async def _day(self, client: httpx.AsyncClient, day: date) -> list[PricePoint]:
        params = {"date": day.strftime("%d-%m-%Y"), "interval": self.interval, "energy_type": "ENERGY_TYPE_ELECTRICITY"}
        resp = await get_with_retry(client, self.url, params=params, headers={"Accept": "application/json"},
                                    sleep=self.sleep)
        if resp.status_code == 404:
            return []                         # no prices (yet) for this day
        if resp.status_code >= 400:
            raise ValueError(f"EnergyZero: HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ValueError("EnergyZero: antwoord is geen JSON") from exc
        if not isinstance(payload, dict) or "base" not in payload:
            raise ValueError("EnergyZero: onverwacht antwoord (veld 'base' ontbreekt)")
        return parse_energyzero(payload)

    async def fetch(self, start: datetime, end: datetime) -> list[PricePoint]:
        client = self.client or httpx.AsyncClient(timeout=20)
        t0 = time.monotonic()
        try:
            day, last = start.astimezone(NL_TZ).date(), end.astimezone(NL_TZ).date()
            points: dict[datetime, PricePoint] = {}
            while day <= last:
                for p in await self._day(client, day):
                    if start - timedelta(minutes=p.resolution_min) < p.start < end:
                        points[p.start] = p
                day += timedelta(days=1)
        finally:
            if self.client is None:
                await client.aclose()
        # EnergyZero publishes no data version; report the endpoint and interval instead.
        self.last_meta = {"endpoint": self.url, "interval": self.interval, "version": None,
                          "duration_ms": round((time.monotonic() - t0) * 1000)}
        return [points[k] for k in sorted(points)]


def _json_path(doc, path: str):
    cur = doc
    for part in [x for x in (path or "").split(".") if x]:
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


class CustomApiProvider(PriceProvider):
    """User-configured HTTPS JSON price source (Expert). The URL must pass ``validate_endpoint``; the
    optional Authorization header is only ever sent to this URL; redirects are never followed."""

    name = "custom_api"

    def __init__(self, url: str, *, items_path: str = "", start_field: str = "start", price_field: str = "price",
                 unit: str = "eur_kwh", auth_header: str = "", client: httpx.AsyncClient | None = None,
                 sleep=asyncio.sleep) -> None:
        if not url:
            raise ValueError("Eigen API: adres ontbreekt")
        self.url, self.items_path, self.start_field, self.price_field = url, items_path, start_field, price_field
        self.factor = 0.001 if unit == "eur_mwh" else 1.0
        self.auth_header = auth_header
        self.client = client
        self.sleep = sleep
        self.last_meta = {}

    async def fetch(self, start: datetime, end: datetime) -> list[PricePoint]:
        client = self.client or httpx.AsyncClient(timeout=20)
        headers = {"Accept": "application/json"}
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        out: dict[datetime, float] = {}
        try:
            day, last = start.astimezone(NL_TZ).date(), end.astimezone(NL_TZ).date()
            while day <= last:
                url = self.url.replace("{date}", day.isoformat())
                resp = await get_with_retry(client, url, headers=headers, sleep=self.sleep)
                if resp.status_code == 404:
                    day += timedelta(days=1)
                    continue
                if resp.status_code >= 400:
                    raise ValueError(f"Eigen API: HTTP {resp.status_code}")
                items = _json_path(resp.json(), self.items_path) if self.items_path else resp.json()
                if not isinstance(items, list):
                    raise ValueError("Eigen API: geen lijst gevonden op het opgegeven pad")
                for it in items:
                    try:
                        ts = _parse_utc(str(_json_path(it, self.start_field)))
                        out[ts] = float(_json_path(it, self.price_field)) * self.factor
                    except (TypeError, ValueError):
                        continue
                day += timedelta(days=1)
        finally:
            if self.client is None:
                await client.aclose()
        starts = sorted(out)
        res = int(min((b - a).total_seconds() for a, b in zip(starts, starts[1:], strict=False)) // 60) if len(starts) > 1 else 60
        self.last_meta = {"endpoint": urlsplit_host(self.url), "version": None}
        return [PricePoint(t, round(out[t], 6), res) for t in starts if start - timedelta(minutes=res) < t < end]


def urlsplit_host(url: str) -> str:
    from urllib.parse import urlsplit
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}"


class StaticProvider(PriceProvider):
    """Prices kept in the database (manual upload). fetch() returns nothing new."""

    name = "manual"

    async def fetch(self, start: datetime, end: datetime) -> list[PricePoint]:
        return []


class DemoProvider(PriceProvider):
    """Synthetic prices from the simulator environment (Demo Mode only)."""

    name = "demo"

    def __init__(self, environment) -> None:
        self.env = environment

    async def fetch(self, start: datetime, end: datetime) -> list[PricePoint]:
        out, t = [], start.astimezone(UTC).replace(minute=(start.minute // 15) * 15, second=0, microsecond=0)
        while t < end:
            out.append(PricePoint(t, self.env.spot_price(t), 15))
            t += timedelta(minutes=15)
        return out


def parse_manual_prices(rows: list[dict], timezone: str = "Europe/Amsterdam") -> tuple[list[PricePoint], dict]:
    """Manual prices: [{"start": ISO-8601 *with* offset, "price_eur_kwh": 0.12, "resolution_min": 15|60}, ...].

    Validates time zone, resolution, alignment and duplicates, and reports gaps (missing intervals,
    also around daylight-saving changes) so the user sees what the EMS will have to estimate."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(timezone)
    out: dict[datetime, PricePoint] = {}
    for n, r in enumerate(rows, start=1):
        raw = str(r.get("start", "")).strip()
        try:
            start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"regel {n}: ongeldig tijdstip {raw!r}") from None
        if start.tzinfo is None:
            raise ValueError(f"regel {n}: tijdstip zonder tijdzone (bijv. 2026-10-07T00:00:00+02:00)")
        res = int(r.get("resolution_min", 60))
        if res not in (15, 60):
            raise ValueError(f"regel {n}: resolutie moet 15 of 60 minuten zijn")
        if start.minute % res or start.second:
            raise ValueError(f"regel {n}: {raw} valt niet op een {res}-minutengrens")
        try:
            price = float(str(r["price_eur_kwh"]).replace(",", "."))
        except (KeyError, ValueError):
            raise ValueError(f"regel {n}: ongeldige prijs") from None
        if not -5 <= price <= 5:
            raise ValueError(f"regel {n}: prijs {price} €/kWh onwaarschijnlijk (verwacht €/kWh, niet €/MWh)")
        key = start.astimezone(UTC)
        if key in out:
            raise ValueError(f"regel {n}: dubbel interval {raw}")
        out[key] = PricePoint(key, price, res)
    points = sorted(out.values(), key=lambda p: p.start)
    gaps = []
    for a, b in zip(points, points[1:], strict=False):
        end = a.start + timedelta(minutes=a.resolution_min)
        if b.start > end:
            gaps.append({"from": end.astimezone(tz).isoformat(), "to": b.start.astimezone(tz).isoformat()})
    report = {"intervals": len(points), "gaps": gaps,
              "first": points[0].start.astimezone(tz).isoformat() if points else None,
              "last_end": (points[-1].start + timedelta(minutes=points[-1].resolution_min)).astimezone(tz).isoformat()
              if points else None}
    return points, report
