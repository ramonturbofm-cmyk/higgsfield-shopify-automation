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
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.database import Database
from ems.prices.providers import PricePoint, PriceProvider

log = logging.getLogger(__name__)


@dataclass
class PriceSeriesPoint:
    start: datetime
    spot: float | None
    resolution_min: int
    estimated: bool

    @property
    def status(self) -> str:
        """confirmed (published price) | estimated (forecast, never presented as known) | missing."""
        return "missing" if self.spot is None else "estimated" if self.estimated else "confirmed"


def local_day_slots(day: date, tz: ZoneInfo, minutes: int = 15) -> list[datetime]:
    """UTC start times of every interval of a *local* day: 92 / 96 / 100 quarter-hours on
    the 23 / 24 / 25-hour days around daylight-saving changes."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(UTC)
    out, t = [], start
    while t < end:
        out.append(t)
        t += timedelta(minutes=minutes)
    return out


class PriceService:
    def __init__(self, db: Database | None, area: str, provider: PriceProvider | None,
                 timezone: str = "Europe/Amsterdam") -> None:
        self.tz = ZoneInfo(timezone)
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

    def series(self, start: datetime, end: datetime, step_min: int = 15, estimate: bool = True,
               include_missing: bool = False) -> list[PriceSeriesPoint]:
        out, t = [], start
        while t < end:
            v, est = self.spot_or_estimate(t) if estimate else (self.spot(t), False)
            if v is not None or include_missing:
                out.append(PriceSeriesPoint(t, v, step_min, est and v is not None))
            t += timedelta(minutes=step_min)
        return out

    def current(self, now: datetime) -> dict | None:
        """The *published* price whose interval contains ``now`` (start <= now < end), or None.
        Never an older price and never an estimate (audit P1-06)."""
        i = bisect_right(self._starts, now) - 1
        if i < 0:
            return None
        p = self._cache[i]
        end = p.start + timedelta(minutes=p.resolution_min)
        if not p.start <= now < end:
            return None
        return {"start": p.start.isoformat(), "end": end.isoformat(), "spot": p.price_eur_kwh,
                "resolution_min": p.resolution_min}

    def coverage(self, day: date, minutes: int = 15) -> dict:
        """Which intervals of a local day have a known price (detects missing quarter-hours)."""
        slots = local_day_slots(day, self.tz, minutes)
        missing = [t for t in slots if self.spot(t) is None]
        return {"date": day.isoformat(), "intervals": len(slots), "hours": len(slots) * minutes / 60,
                "known": len(slots) - len(missing), "complete": not missing,
                "missing": [t.astimezone(self.tz).strftime("%H:%M") for t in missing[:20]],
                "missing_count": len(missing)}

    def status(self, now: datetime | None = None) -> dict:
        cov = {}
        if now is not None and self._cache:
            today = now.astimezone(self.tz).date()
            cov = {"today": self.coverage(today), "tomorrow": self.coverage(today + timedelta(days=1))}
        return {"coverage": cov, "provider": None if self.provider is None else self.provider.name, "area": self.area,
                "last_fetch": None if self.last_fetch is None else self.last_fetch.isoformat(),
                "last_error": self.last_error, "known_until": None if not self._cache else self.last_known().isoformat(),
                "cached_points": len(self._cache)}
