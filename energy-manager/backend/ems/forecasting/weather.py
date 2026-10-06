"""Weather providers. Open-Meteo forecast API (https://open-meteo.com/en/docs):
GET https://api.open-meteo.com/v1/forecast with hourly=temperature_2m,shortwave_radiation,cloud_cover.
``shortwave_radiation`` is the mean of the *preceding* hour (W/m2, GHI)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class WeatherPoint:
    start: datetime          # UTC, start of the interval the values describe
    minutes: int
    temperature_c: float | None
    ghi_w_m2: float | None
    cloud_cover_pct: float | None


class WeatherProvider(ABC):
    name = "weather"

    @abstractmethod
    async def fetch(self, latitude: float, longitude: float, start: datetime, end: datetime) -> list[WeatherPoint]: ...


def parse_open_meteo(data: dict) -> list[WeatherPoint]:
    h = data["hourly"]
    out = []
    for i, t in enumerate(h["time"]):
        stamp = datetime.fromisoformat(t).replace(tzinfo=UTC)
        get = lambda key, i=i: (h.get(key) or [None] * len(h["time"]))[i]  # noqa: E731
        # radiation describes the preceding hour -> interval [t-1h, t)
        out.append(WeatherPoint(stamp - timedelta(hours=1), 60, get("temperature_2m"), get("shortwave_radiation"),
                                get("cloud_cover")))
    return out


class OpenMeteoProvider(WeatherProvider):
    name = "open_meteo"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client

    async def fetch(self, latitude, longitude, start, end):
        days = max(1, min(16, (end - datetime.now(UTC)).days + 2))
        params = {"latitude": latitude, "longitude": longitude, "timezone": "UTC", "past_days": 1,
                  "forecast_days": days, "hourly": "temperature_2m,shortwave_radiation,cloud_cover"}
        client = self.client or httpx.AsyncClient(timeout=30)
        try:
            resp = await client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            return parse_open_meteo(resp.json())
        finally:
            if self.client is None:
                await client.aclose()


class DemoWeatherProvider(WeatherProvider):
    """Demo Mode only: the simulator's own weather (perfect foresight of the demo world)."""

    name = "demo"

    def __init__(self, environment) -> None:
        self.env = environment

    async def fetch(self, latitude, longitude, start, end):
        out, t = [], start.astimezone(UTC).replace(minute=(start.minute // 15) * 15, second=0, microsecond=0)
        while t < end:
            mid = t + timedelta(minutes=7.5)
            out.append(WeatherPoint(t, 15, self.env.outdoor_temp(mid), self.env.ghi(mid),
                                    100 * self.env.cloud_cover(mid)))
            t += timedelta(minutes=15)
        return out
