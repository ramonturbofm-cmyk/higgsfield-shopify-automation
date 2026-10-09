"""PriceService: fetch, check, cache (database) and serve market prices — with an explicit status per interval.

Statuses (``PriceStatus``) — a forecast is never presented as a published price:

* ``OFFICIAL_DAY_AHEAD``: published by the source, passed validation (range, alignment, duplicates).
* ``ESTIMATED``: a single missing interval (gap of at most 2 h) *inside* published prices, linearly
  interpolated between its published neighbours.
* ``FORECAST``: beyond the last published price, from history (model ``same_slot_7d`` or
  ``weekday_profile_4w``), with a confidence (0..1) and a p10..p90 band; at most
  ``price_forecast_horizon_hours`` after the last published price.
* ``STALE``: a published price for a future interval while the source has been failing for more than
  ``STALE_AFTER_H`` hours — it was valid when fetched but could not be re-checked since.
* ``MISSING``: nothing known.

Day-ahead publication (around 13:00 local) is not guaranteed: after ``publication_expected`` the
service checks every ``publication_retry_minutes`` until tomorrow is complete and reports
``delayed`` after ``publication_alert_after``. Only a complete local day counts as available
(92/96/100 quarter-hours around daylight-saving changes). A reserve source is only asked when the
primary source fails or leaves intervals open, and only fills intervals the primary did not deliver.
An outage of the sources never stops the EMS: the database cache keeps being served.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from ems.core.config import ForecastConfig, PriceConfig
from ems.database import Database
from ems.prices.providers import PricePoint, PriceProvider, validate_points

log = logging.getLogger(__name__)

STALE_AFTER_H = 26.0
MAX_INTERPOLATION_GAP = timedelta(hours=2)
SYNC_KV = "prices.sync"


class PriceStatus(StrEnum):
    OFFICIAL_DAY_AHEAD = "OFFICIAL_DAY_AHEAD"
    FORECAST = "FORECAST"
    ESTIMATED = "ESTIMATED"
    STALE = "STALE"
    MISSING = "MISSING"


@dataclass
class PriceSeriesPoint:
    start: datetime
    spot: float | None
    resolution_min: int
    status: str = PriceStatus.MISSING
    confidence: float | None = None       # 1.0 for published prices
    low: float | None = None              # p10 (forecast) — the uncertainty band
    high: float | None = None             # p90
    source: str | None = None

    @property
    def estimated(self) -> bool:
        """True for anything that is not a published price (forecast or interpolated)."""
        return self.status in (PriceStatus.FORECAST, PriceStatus.ESTIMATED)

    @property
    def official(self) -> bool:
        return self.status in (PriceStatus.OFFICIAL_DAY_AHEAD, PriceStatus.STALE)

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "spot": self.spot, "status": str(self.status),
                "confidence": self.confidence, "low": self.low, "high": self.high, "source": self.source,
                "estimated": self.estimated}


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


def _hhmm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


class PriceService:
    def __init__(self, db: Database | None, area: str, provider: PriceProvider | None,
                 timezone: str = "Europe/Amsterdam", *, fallback: PriceProvider | None = None,
                 config: PriceConfig | None = None, forecast: ForecastConfig | None = None,
                 now_fn=None) -> None:
        self.tz = ZoneInfo(timezone)
        self.db = db
        self.area = area
        self.provider = provider
        self.fallback = fallback
        self.cfg = config or PriceConfig()
        self.fcfg = forecast or ForecastConfig()
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.last_error: str | None = None
        self.last_fetch: datetime | None = None          # last successful fetch (any source)
        self.last_attempt: datetime | None = None
        self.problems: list[str] = []                   # validation findings of the last fetch
        self.sync: dict[str, dict] = {}                  # per source: last_success, slots, meta, error
        self._cache: list[PricePoint] = []
        self._starts: list[datetime] = []
        self._source: dict[datetime, str] = {}
        self._load_sync()

    # ------------------------------------------------------------- persistence
    def _load_sync(self) -> None:
        if self.db is None:
            return
        try:
            data = self.db.kv_get(SYNC_KV) or {}
        except Exception:                               # database without kv table (very old): no history
            data = {}
        self.sync = {k: v for k, v in (data.get("sources") or {}).items() if isinstance(v, dict)}
        if data.get("last_success"):
            self.last_fetch = datetime.fromisoformat(data["last_success"])

    def _save_sync(self) -> None:
        if self.db is None:
            return
        self.db.kv_set(SYNC_KV, {"sources": self.sync,
                                 "last_success": None if self.last_fetch is None else self.last_fetch.isoformat()})

    def _load_cache(self, start: datetime, end: datetime) -> None:
        if self.db is None:
            return
        rows = self.db.prices_between(self.area, start.timestamp(), end.timestamp())
        pts = {p.start: p for p in self._cache}
        for r in rows:
            ts = datetime.fromtimestamp(r["ts"], UTC)
            if ts not in pts:
                pts[ts] = PricePoint(ts, r["spot_eur_kwh"], r["resolution_min"])
                self._source[ts] = r.get("source") or "cache"
        self._set(pts.values())

    def _set(self, points) -> None:
        self._cache = sorted(points, key=lambda p: p.start)
        self._starts = [p.start for p in self._cache]

    def add_points(self, points: list[PricePoint], source: str) -> int:
        merged = {p.start: p for p in self._cache}
        for p in points:
            merged[p.start] = p
            self._source[p.start] = source
        self._set(merged.values())
        if self.db is not None and points:
            self.db.upsert_prices(self.area, source, [(p.start.timestamp(), p.price_eur_kwh, p.resolution_min)
                                                      for p in points])
        return len(points)

    # ----------------------------------------------------------------- fetching
    def fetch_window(self, now: datetime) -> tuple[datetime, datetime]:
        day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=1), day + timedelta(days=2)

    async def _fetch_one(self, prov: PriceProvider, start: datetime, end: datetime, now: datetime,
                         only_missing: bool) -> tuple[int, str | None]:
        info = self.sync.setdefault(prov.name, {})
        info["last_attempt"] = now.isoformat()
        try:
            raw = await prov.fetch(start, end)
        except Exception as exc:
            msg = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}"
            info["error"] = msg
            log.warning("price fetch failed", extra={"provider": prov.name, "error": msg})
            return 0, msg
        points, problems = validate_points(raw)
        if only_missing:
            points = [p for p in points if self.spot(p.start) is None]
        n = await asyncio.to_thread(self.add_points, points, prov.name)
        self.problems = problems
        info.update({"error": None, "last_success": now.isoformat(), "slots": len(raw), "stored": n,
                     "rejected": len(problems), "meta": dict(getattr(prov, "last_meta", {}) or {})})
        return n, None

    def needs_fallback(self, now: datetime) -> bool:
        today = now.astimezone(self.tz).date()
        if not self.coverage(today)["complete"]:
            return True
        return self.publication(now)["state"] in ("waiting", "delayed")

    async def refresh(self, now: datetime) -> int:
        """Fetch yesterday..day-after-tomorrow; keep serving the cache on failure."""
        start, end = self.fetch_window(now)
        await asyncio.to_thread(self._load_cache, start - timedelta(days=35), end + timedelta(days=1))
        if self.provider is None:
            return 0
        self.last_attempt = now
        total, err = await self._fetch_one(self.provider, start, end, now, only_missing=False)
        self.last_error = err
        if err is None:
            self.last_fetch = now
        if self.fallback is not None and (err is not None or self.needs_fallback(now)):
            n, ferr = await self._fetch_one(self.fallback, start, end, now, only_missing=True)
            total += n
            if ferr is None:
                self.last_fetch = now
                if n:
                    log.info("reserve price source filled intervals", extra={"provider": self.fallback.name, "n": n})
        await asyncio.to_thread(self._save_sync)
        return total

    def next_refresh_s(self, now: datetime) -> float:
        """Seconds until the next fetch: the normal interval, right after the expected publication, and every
        ``publication_retry_minutes`` while today's or tomorrow's prices are incomplete or the source fails."""
        normal = self.cfg.refresh_minutes * 60
        retry = self.cfg.publication_retry_minutes * 60
        if self.provider is None or self.provider.name == "manual":
            return normal
        if self.last_error is not None or not self.coverage(now.astimezone(self.tz).date())["complete"]:
            return min(normal, retry)
        pub = self.publication(now)
        if pub["state"] in ("waiting", "delayed"):
            return min(normal, retry)
        if pub["state"] == "not_yet_expected":
            until = (datetime.fromisoformat(pub["expected_at"]) - now).total_seconds() + 60
            return max(60.0, min(normal, until))
        return normal

    # ------------------------------------------------------------------ lookups
    def spot(self, ts: datetime) -> float | None:
        """Published spot price for ts (None when not published / not cached)."""
        p = self._official(ts)
        return None if p is None else p.price_eur_kwh

    def _official(self, ts: datetime) -> PricePoint | None:
        i = bisect_right(self._starts, ts) - 1
        if i < 0:
            return None
        p = self._cache[i]
        return p if ts < p.start + timedelta(minutes=p.resolution_min) else None

    def _neighbours(self, ts: datetime) -> tuple[PricePoint | None, PricePoint | None]:
        i = bisect_right(self._starts, ts)
        before = self._cache[i - 1] if i > 0 else None
        after = self._cache[i] if i < len(self._cache) else None
        return before, after

    def stale(self, now: datetime | None = None) -> bool:
        """The source has failed for more than STALE_AFTER_H hours (cached prices cannot be re-checked)."""
        if self.last_error is None or self.provider is None:
            return False
        now = now or self.now_fn()
        return self.last_fetch is None or (now - self.last_fetch) > timedelta(hours=STALE_AFTER_H)

    def _history(self, ts: datetime) -> list[float]:
        """Published prices of the same local time of day: the previous 7 days, or the same weekday of
        the previous 4 weeks (local-time arithmetic, so daylight-saving changes keep the slot)."""
        local = ts.astimezone(self.tz).replace(tzinfo=None)
        steps = [timedelta(days=d) for d in range(1, 8)] if self.fcfg.price_forecast_model == "same_slot_7d" \
            else [timedelta(weeks=w) for w in range(1, 5)]
        out = []
        for s in steps:
            t = (local - s).replace(tzinfo=self.tz).astimezone(UTC)
            v = self.spot(t)
            if v is not None:
                out.append(v)
        return out

    def estimate(self, ts: datetime) -> float | None:
        """Model value (mean of the history) for ts, without horizon or confidence."""
        vals = self._history(ts)
        return statistics.fmean(vals) if vals else None

    def _forecast(self, ts: datetime, base: datetime) -> PriceSeriesPoint | None:
        vals = self._history(ts)
        need = 3 if self.fcfg.price_forecast_model == "same_slot_7d" else 2
        if len(vals) < need:
            return None
        mean = statistics.fmean(vals)
        sd = statistics.pstdev(vals)
        h = max(0.0, (ts - base).total_seconds() / 3600)
        widen = 1 + h / 48
        full = 7 if self.fcfg.price_forecast_model == "same_slot_7d" else 4
        conf = (1 / (1 + h / 48)) * (1 / (1 + sd / 0.05)) * min(1.0, len(vals) / full)
        return PriceSeriesPoint(ts, round(mean, 5), 15, PriceStatus.FORECAST, round(conf, 2),
                                round(mean - 1.2816 * sd * widen, 5), round(mean + 1.2816 * sd * widen, 5),
                                f"prognose:{self.fcfg.price_forecast_model}")

    def point(self, ts: datetime, now: datetime | None = None) -> PriceSeriesPoint:
        p = self._official(ts)
        if p is not None:
            end = p.start + timedelta(minutes=p.resolution_min)
            now = now or self.now_fn()
            status = PriceStatus.STALE if end > now and self.stale(now) else PriceStatus.OFFICIAL_DAY_AHEAD
            return PriceSeriesPoint(ts, p.price_eur_kwh, p.resolution_min, status, 1.0, source=self._source.get(p.start))
        before, after = self._neighbours(ts)
        if before is not None and after is not None:
            gap_start = before.start + timedelta(minutes=before.resolution_min)
            if after.start - gap_start <= MAX_INTERPOLATION_GAP:
                f = (ts - before.start) / (after.start - before.start)
                v = before.price_eur_kwh + (after.price_eur_kwh - before.price_eur_kwh) * f
                return PriceSeriesPoint(ts, round(v, 5), 15, PriceStatus.ESTIMATED, 0.8,
                                        min(before.price_eur_kwh, after.price_eur_kwh),
                                        max(before.price_eur_kwh, after.price_eur_kwh), "interpolatie")
        last = self.last_known()
        if (after is None and last is not None and ts >= last and self.fcfg.price_forecast_enabled
                and ts < last + timedelta(hours=self.fcfg.price_forecast_horizon_hours)):
            fc = self._forecast(ts, last)
            if fc is not None:
                return fc
        return PriceSeriesPoint(ts, None, 15, PriceStatus.MISSING)

    def spot_or_estimate(self, ts: datetime) -> tuple[float | None, bool]:
        """(value, is_not_published). Prefer ``point()``, which also gives status and confidence."""
        pt = self.point(ts)
        return pt.spot, not pt.official

    def last_known(self) -> datetime | None:
        if not self._cache:
            return None
        p = self._cache[-1]
        return p.start + timedelta(minutes=p.resolution_min)

    def series(self, start: datetime, end: datetime, step_min: int = 15, estimate: bool = True,
               include_missing: bool = False) -> list[PriceSeriesPoint]:
        """Interval series. ``estimate=False``: published prices only."""
        out, t, now = [], start, self.now_fn()
        while t < end:
            if estimate:
                pt = self.point(t, now)
                pt.resolution_min = step_min
            else:
                v = self.spot(t)
                pt = PriceSeriesPoint(t, v, step_min, PriceStatus.OFFICIAL_DAY_AHEAD if v is not None
                                      else PriceStatus.MISSING, 1.0 if v is not None else None,
                                      source=self._source.get(self._official(t).start) if v is not None else None)
            if pt.spot is not None or include_missing:
                out.append(pt)
            t += timedelta(minutes=step_min)
        return out

    def current(self, now: datetime) -> dict | None:
        """The *published* price whose interval contains ``now`` (start <= now < end), or None.
        Never an older price and never an estimate (audit P1-06)."""
        p = self._official(now)
        if p is None:
            return None
        end = p.start + timedelta(minutes=p.resolution_min)
        return {"start": p.start.isoformat(), "end": end.isoformat(), "spot": p.price_eur_kwh,
                "resolution_min": p.resolution_min, "source": self._source.get(p.start),
                "status": str(PriceStatus.STALE if self.stale(now) else PriceStatus.OFFICIAL_DAY_AHEAD)}

    def coverage(self, day: date, minutes: int = 15) -> dict:
        """Which intervals of a local day have a published price (detects missing quarter-hours)."""
        slots = local_day_slots(day, self.tz, minutes)
        missing = [t for t in slots if self.spot(t) is None]
        return {"date": day.isoformat(), "intervals": len(slots), "hours": len(slots) * minutes / 60,
                "known": len(slots) - len(missing), "complete": not missing,
                "missing": [t.astimezone(self.tz).strftime("%H:%M") for t in missing[:20]],
                "missing_count": len(missing)}

    def publication(self, now: datetime) -> dict:
        """State of tomorrow's day-ahead prices: available | not_yet_expected | waiting | delayed."""
        local = now.astimezone(self.tz)
        tomorrow = local.date() + timedelta(days=1)
        cov = self.coverage(tomorrow)
        eh, em = _hhmm(self.cfg.publication_expected)
        ah, am = _hhmm(self.cfg.publication_alert_after)
        expected = local.replace(hour=eh, minute=em, second=0, microsecond=0)
        alert = local.replace(hour=ah, minute=am, second=0, microsecond=0)
        if cov["complete"]:
            state = "available"
        elif local < expected:
            state = "not_yet_expected"
        elif local < alert:
            state = "waiting"
        else:
            state = "delayed"
        return {"date": tomorrow.isoformat(), "state": state, "available": cov["complete"],
                "known": cov["known"], "intervals": cov["intervals"], "missing_count": cov["missing_count"],
                "expected_at": expected.isoformat(), "alert_after": alert.isoformat()}

    def status(self, now: datetime | None = None) -> dict:
        now = now or self.now_fn()
        today = now.astimezone(self.tz).date()
        cov = {"today": self.coverage(today), "tomorrow": self.coverage(today + timedelta(days=1))} \
            if self._cache else {}
        last = self.last_known()
        return {
            "coverage": cov, "provider": None if self.provider is None else self.provider.name,
            "fallback_provider": None if self.fallback is None else self.fallback.name, "area": self.area,
            "last_fetch": None if self.last_fetch is None else self.last_fetch.isoformat(),
            "last_success": None if self.last_fetch is None else self.last_fetch.isoformat(),
            "last_attempt": None if self.last_attempt is None else self.last_attempt.isoformat(),
            "last_error": self.last_error, "known_until": None if last is None else last.isoformat(),
            "cached_points": len(self._cache), "stale": self.stale(now), "problems": self.problems[:10],
            "publication": self.publication(now), "sources": self.sync,
            "next_check_s": round(self.next_refresh_s(now)),
            "forecast": {"enabled": self.fcfg.price_forecast_enabled, "model": self.fcfg.price_forecast_model,
                         "horizon_hours": self.fcfg.price_forecast_horizon_hours,
                         "confidence_threshold": self.fcfg.price_forecast_confidence_threshold},
        }
