"""Day-ahead price providers.

* ENTSO-E Transparency Platform RESTful API (documentType A44, day-ahead prices).
  Source: "Transparency Platform RESTful API - user guide" (ENTSO-E). Requires a
  free security token. Prices are published in EUR/MWh; we store EUR/kWh.
* Manual: user-supplied prices (CSV/JSON via the API).
* Demo: the simulator's synthetic prices — only allowed in Demo Mode.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
