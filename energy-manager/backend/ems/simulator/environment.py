"""Deterministic synthetic environment: sun, weather and day-ahead prices.

All signals are random-access functions of time (no hidden sequential
state), so forecasts can look into the "future" of the same world and
results are reproducible for a given seed.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


@lru_cache(maxsize=65536)
def _uniform(seed: int, channel: str, key: int) -> float:
    return random.Random(f"{seed}:{channel}:{key}").random()


def smooth_noise(seed: int, channel: str, t_hours: float, period_h: float) -> float:
    """Smooth value noise in [0, 1) with features of roughly ``period_h``."""
    x = t_hours / period_h
    k = math.floor(x)
    f = x - k
    s = (1 - math.cos(math.pi * f)) / 2
    a, b = _uniform(seed, channel, k), _uniform(seed, channel, k + 1)
    return a + (b - a) * s


def hours_since_epoch(t: datetime) -> float:
    return (t - _EPOCH).total_seconds() / 3600.0


def sun_elevation_deg(t: datetime, latitude: float, longitude: float) -> float:
    """Approximate solar elevation (good to ~1 degree; fine for simulation)."""
    t = t.astimezone(UTC)
    doy = t.timetuple().tm_yday
    decl = math.radians(23.45) * math.sin(math.radians(360.0 / 365.0 * (284 + doy)))
    b = math.radians(360.0 / 365.0 * (doy - 81))
    eot_min = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    solar_time_h = t.hour + t.minute / 60 + t.second / 3600 + longitude / 15.0 + eot_min / 60.0
    hour_angle = math.radians(15.0 * (solar_time_h - 12.0))
    lat = math.radians(latitude)
    sin_el = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))


@dataclass(frozen=True)
class EnvironmentState:
    outdoor_temp_c: float
    cloud_cover: float          # 0 = clear, 1 = overcast
    sun_elevation_deg: float
    ghi_w_m2: float             # global horizontal irradiance (incl. clouds)
    spot_price_eur_kwh: float   # synthetic day-ahead price (excl. taxes)


class Environment:
    def __init__(self, latitude: float, longitude: float, timezone: str, seed: int = 1) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.tz = ZoneInfo(timezone)
        self.seed = seed

    def cloud_cover(self, t: datetime) -> float:
        h = hours_since_epoch(t)
        daily = smooth_noise(self.seed, "cloud_day", h, 24.0)
        fast = smooth_noise(self.seed, "cloud_fast", h, 2.0)
        return min(1.0, max(0.0, 1.15 * (0.7 * daily + 0.3 * fast) - 0.1))

    def clear_sky_ghi(self, t: datetime, hour_shift: float = 0.0) -> float:
        el = sun_elevation_deg(t - timedelta(hours=hour_shift), self.latitude, self.longitude)
        if el <= 0:
            return 0.0
        return 1050.0 * math.sin(math.radians(el)) ** 1.15

    def ghi(self, t: datetime, hour_shift: float = 0.0) -> float:
        cloud = self.cloud_cover(t)
        return self.clear_sky_ghi(t, hour_shift) * (1.0 - 0.78 * cloud ** 2.2)

    def outdoor_temp(self, t: datetime) -> float:
        local = t.astimezone(self.tz)
        doy = local.timetuple().tm_yday
        seasonal = 10.5 - 7.5 * math.cos(2 * math.pi * (doy - 20) / 365.0)
        hod = local.hour + local.minute / 60
        diurnal = 3.5 * math.cos(2 * math.pi * (hod - 15.0) / 24.0)
        weather = 6.0 * (smooth_noise(self.seed, "temp", hours_since_epoch(t), 36.0) - 0.5)
        clear_bonus = 2.0 * (1 - self.cloud_cover(t)) * math.cos(2 * math.pi * (hod - 14.0) / 24.0)
        return seasonal + diurnal + weather + clear_bonus

    def spot_price(self, t: datetime) -> float:
        """Synthetic 15-minute day-ahead price in EUR/kWh (duck curve)."""
        local = t.astimezone(self.tz)
        q = local.replace(minute=(local.minute // 15) * 15, second=0, microsecond=0)
        hod = q.hour + q.minute / 60
        price = 0.085
        price += 0.035 * math.exp(-((hod - 8.0) ** 2) / 2.0)
        price += 0.085 * math.exp(-((hod - 19.0) ** 2) / 3.0)
        price -= 0.02 * math.exp(-((hod - 3.5) ** 2) / 4.0)
        # Solar dip: deep (even negative) on sunny days around noon.
        solar = self.clear_sky_ghi(q.astimezone(UTC)) / 1000.0
        price -= 0.14 * solar * (1 - self.cloud_cover(q.astimezone(UTC)))
        price += 0.02 * (smooth_noise(self.seed, "price", hours_since_epoch(q), 6.0) - 0.5)
        if local.weekday() >= 5:
            price -= 0.012
        return round(price, 5)

    def state(self, t: datetime) -> EnvironmentState:
        return EnvironmentState(
            outdoor_temp_c=self.outdoor_temp(t),
            cloud_cover=self.cloud_cover(t),
            sun_elevation_deg=sun_elevation_deg(t, self.latitude, self.longitude),
            ghi_w_m2=self.ghi(t),
            spot_price_eur_kwh=self.spot_price(t),
        )
