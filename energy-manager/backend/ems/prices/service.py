"""PriceService: fetch, cache (database) and serve spot prices.

An outage of the external source never stops the EMS: cached prices keep
being served, and beyond the last known price a clearly-labelled estimate
(average of the same quarter-hour over the last 7 days) is used for planning.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ems.database import Database
from ems.prices.providers import PricePoint, PriceProvider

log = logging.getLogger(__name__)


@dataclass
class PriceSeriesPoint:
    start: datetime
    spot: float
    resolution_min: int
    estimated: bool


class PriceService:
    def __init__(self, db: Database | None, area: str, provider: PriceProvider | None) -> None:
        self.db = db
        self.area = area
        self.provider = provider
        self.last_error: str | None = None
        self.last_fetch: datetime | None = None
        self._cache: list[PricePoint] = []
        self._starts: list[datetime] = []

    def _load_cache(self, start: datetime, end: datetime) -> None:
        if self.db is None:
            return
        rows = self.db.prices_between(self.area, start.timestamp(), end.timestamp())
        pts = {p.start: p for p in self._cache}
        for r in rows:
            ts = datetime.fromtimestamp(r["ts"], UTC)
            pts[ts] = PricePoint(ts, r["spot_eur_kwh"], r["resolution_min"])
        self._set(pts.values())

    def _set(self, points) -> None:
        self._cache = sorted(points, key=lambda p: p.start)
        self._starts = [p.start for p in self._cache]

    def add_points(self, points: list[PricePoint], source: str) -> int:
        merged = {p.start: p for p in self._cache}
        for p in points:
            merged[p.start] = p
        self._set(merged.values())
        if self.db is not None and points:
            self.db.upsert_prices(self.area, source, [(p.start.timestamp(), p.price_eur_kwh, p.resolution_min)
                                                      for p in points])
        return len(points)

    async def refresh(self, now: datetime) -> int:
        """Fetch yesterday..day-after-tomorrow; keep serving the cache on failure."""
        day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        start, end = day - timedelta(days=1), day + timedelta(days=2)
        await asyncio.to_thread(self._load_cache, start - timedelta(days=7), end + timedelta(days=1))
        if self.provider is None:
            return 0
        try:
            points = await self.provider.fetch(start, end)
        except Exception as exc:
            self.last_error = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}"
            log.warning("price fetch failed", extra={"provider": self.provider.name, "error": self.last_error})
            return 0
        self.last_error = None
        self.last_fetch = now
        return await asyncio.to_thread(self.add_points, points, self.provider.name)

    def spot(self, ts: datetime) -> float | None:
        """Known spot price for ts (None when not published / not cached)."""
        i = bisect_right(self._starts, ts) - 1
        if i < 0:
            return None
        p = self._cache[i]
        if ts < p.start + timedelta(minutes=p.resolution_min):
            return p.price_eur_kwh
        return None

    def estimate(self, ts: datetime) -> float | None:
        """Average spot of the same time of day over the previous 7 days (cache)."""
        vals = []
        for d in range(1, 8):
            v = self.spot(ts - timedelta(days=d))
            if v is not None:
                vals.append(v)
        return statistics.fmean(vals) if vals else None

    def spot_or_estimate(self, ts: datetime) -> tuple[float | None, bool]:
        v = self.spot(ts)
        if v is not None:
            return v, False
        return self.estimate(ts), True

    def last_known(self) -> datetime | None:
        if not self._cache:
            return None
        p = self._cache[-1]
        return p.start + timedelta(minutes=p.resolution_min)

    def series(self, start: datetime, end: datetime, step_min: int = 15, estimate: bool = True) -> list[PriceSeriesPoint]:
        out, t = [], start
        while t < end:
            v, est = self.spot_or_estimate(t) if estimate else (self.spot(t), False)
            if v is not None:
                out.append(PriceSeriesPoint(t, v, step_min, est))
            t += timedelta(minutes=step_min)
        return out

    def status(self) -> dict:
        return {"provider": None if self.provider is None else self.provider.name, "area": self.area,
                "last_fetch": None if self.last_fetch is None else self.last_fetch.isoformat(),
                "last_error": self.last_error, "known_until": None if not self._cache else self.last_known().isoformat(),
                "cached_points": len(self._cache)}
