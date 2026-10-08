"""Day-ahead price providers.

* ENTSO-E Transparency Platform RESTful API (documentType A44, day-ahead prices).
  Source: "Transparency Platform RESTful API - user guide" (ENTSO-E). Requires a
  free security token. Prices are published in EUR/MWh; we store EUR/kWh.
* EnergyZero public prices API (no token): day-ahead market prices for the Netherlands
  per quarter or hour. Endpoint supplied by the user and cross-checked against the
  open-source client python-energyzero 5.1.0 (MIT, used by Home Assistant): response
  lists ``base`` / ``base_with_vat`` / ``all_in`` / ``all_in_with_vat`` with items
  ``{"start": "...Z", "end": "...Z", "price": {"value": <EUR/kWh>}}``. We use ``base``
  (market price excl. VAT); the tariff engine adds supplier markup, energy tax and VAT.
* Manual: user-supplied prices (CSV/JSON via the API).
* Demo: the simulator's synthetic prices — only allowed in Demo Mode.
"""

from __future__ import annotations

import logging
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

    @abstractmethod
    async def fetch(self, start: datetime, end: datetime) -> list[PricePoint]: ...


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
        try:
            resp = await client.get(ENTSOE_URL, params=params)
        finally:
            if self.client is None:
                await client.aclose()
        if resp.status_code == 401:
            raise ValueError("ENTSO-E: het API-token wordt niet geaccepteerd — "
                             "controleer het token in Instellingen → Prijzen & prognoses")
        if resp.status_code >= 400 and "Acknowledgement" not in resp.text:
            raise ValueError(f"ENTSO-E: HTTP {resp.status_code}")
        return parse_entsoe_a44(resp.text)


ENERGYZERO_INTERVALS = {"quarter": ("INTERVAL_QUARTER", 15), "hour": ("INTERVAL_HOUR", 60)}
# Two published URL forms of the same endpoint: the one the user supplied and the one used by
# python-energyzero 5.1.0. The first that answers is remembered.
ENERGYZERO_ENDPOINTS = (
    ("https://public.api.energyzero.nl/v1/prices", "energy_type"),
    ("https://public.api.energyzero.nl/public/v1/prices", "energyType"),
)
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
    """Dutch day-ahead prices from EnergyZero's public API — no account or token needed."""

    name = "energyzero"

    def __init__(self, interval: str = "quarter", client: httpx.AsyncClient | None = None) -> None:
        if interval not in ENERGYZERO_INTERVALS:
            raise ValueError("EnergyZero: interval moet 'quarter' of 'hour' zijn")
        self.interval, self.resolution = ENERGYZERO_INTERVALS[interval]
        self.client = client
        self._endpoint: tuple[str, str] | None = None

    async def _day(self, client: httpx.AsyncClient, day: date) -> list[PricePoint]:
        endpoints = [self._endpoint] if self._endpoint else list(ENERGYZERO_ENDPOINTS)
        for url, type_param in endpoints:
            params = {"date": day.strftime("%d-%m-%Y"), "interval": self.interval,
                      type_param: "ENERGY_TYPE_ELECTRICITY"}
            resp = await client.get(url, params=params, headers={"Accept": "application/json"})
            if resp.status_code == 404:
                continue                     # no prices (yet) for this day, or the other URL form
            if resp.status_code >= 400:
                raise ValueError(f"EnergyZero: HTTP {resp.status_code}")
            try:
                payload = resp.json()
            except ValueError as exc:
                raise ValueError("EnergyZero: antwoord is geen JSON") from exc
            self._endpoint = (url, type_param)
            return parse_energyzero(payload)
        return []

    async def fetch(self, start: datetime, end: datetime) -> list[PricePoint]:
        client = self.client or httpx.AsyncClient(timeout=20)
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
        return [points[k] for k in sorted(points)]


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


def parse_manual_prices(rows: list[dict]) -> list[PricePoint]:
    """[{"start": ISO-8601 with offset, "price_eur_kwh": 0.12, "resolution_min": 60}, ...]"""
    out = []
    for r in rows:
        start = datetime.fromisoformat(str(r["start"]).replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise ValueError("tijdstip zonder tijdzone")
        out.append(PricePoint(start.astimezone(UTC), float(r["price_eur_kwh"]), int(r.get("resolution_min", 60))))
    return out
