"""ForecastService: per-slot PV, base load, heat-pump load and outdoor temperature.

Practical, explainable models first:
* PV     = sum over arrays of kWp x GHI/1000 x performance ratio x orientation
           factor, x a calibration factor learnt from actual/forecast history.
* Load   = median of the same quarter-hour on comparable days (weekday/weekend)
           over the last N days of measured house load; until enough history
           exists a standard household profile scaled to the configured yearly
           consumption is used and labelled "standaardprofiel".
* Temp   = weather provider; otherwise the last measurement held constant ("persistentie").
Every series reports its source so the UI can show how reliable it is.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.core.config import EMSConfig
from ems.core.models import DeviceCategory
from ems.database import Database
from ems.forecasting.weather import WeatherPoint, WeatherProvider
from ems.simulator.components import SimBaseLoad

log = logging.getLogger(__name__)
SLOT_MIN = 15


@dataclass
class Forecast:
    created: datetime
    slots: list[datetime]
    pv_w: list[float]
    load_w: list[float]
    hp_w: list[float]
    outdoor_c: list[float | None]
    sources: dict[str, str] = field(default_factory=dict)
    pv_calibration: float = 1.0

    def to_dict(self) -> dict:
        return {"created": self.created.isoformat(), "slot_minutes": SLOT_MIN,
                "slots": [s.isoformat() for s in self.slots], "pv_w": [round(v, 1) for v in self.pv_w],
                "load_w": [round(v, 1) for v in self.load_w], "hp_w": [round(v, 1) for v in self.hp_w],
                "outdoor_c": [None if v is None else round(v, 2) for v in self.outdoor_c],
                "sources": self.sources, "pv_calibration": round(self.pv_calibration, 3),
                "pv_kwh_total": round(sum(self.pv_w) * SLOT_MIN / 60 / 1000, 2),
                "load_kwh_total": round(sum(self.load_w) * SLOT_MIN / 60 / 1000, 2)}


def pv_arrays(config: EMSConfig) -> list[dict]:
    arrays = []
    for d in config.devices_of(DeviceCategory.PV_INVERTER, DeviceCategory.HYBRID_INVERTER):
        p = dict(d.params.get("sim", {}))
        p.update({k: v for k, v in d.params.items() if k != "sim"})
        kwp = p.get("peak_power_kw")
        if kwp:
            arrays.append({"device_id": d.id, "kwp": float(kwp), "azimuth": float(p.get("azimuth_deg", 180)),
                           "pr": float(p.get("performance_ratio", 0.85)),
                           "inverter_kw": float(p.get("inverter_max_kw") or kwp)})
    return arrays


def orientation_factor(azimuth: float) -> float:
    return 1.0 - 0.12 * min(1.0, abs(azimuth - 180.0) / 90.0)


class ForecastService:
    def __init__(self, config: EMSConfig, db: Database | None, weather: WeatherProvider | None) -> None:
        self.config = config
        self.db = db
        self.weather = weather
        self.tz = ZoneInfo(config.site.timezone)
        self._weather: list[WeatherPoint] = []
        self._wstarts: list[datetime] = []
        self.last_error: str | None = None
        self.last_refresh: datetime | None = None
        self.latest: Forecast | None = None

    async def refresh_weather(self, now: datetime) -> None:
        if self.weather is None:
            return
        try:
            pts = await self.weather.fetch(self.config.site.latitude, self.config.site.longitude,
                                           now - timedelta(hours=2), now + timedelta(hours=48))
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("weather fetch failed", extra={"error": self.last_error})
            return
        merged = {p.start: p for p in self._weather if p.start >= now - timedelta(days=2)}
        merged.update({p.start: p for p in pts})
        self._weather = [merged[k] for k in sorted(merged)]
        self._wstarts = [p.start for p in self._weather]
        self.last_error, self.last_refresh = None, now

    def _weather_at(self, ts: datetime) -> WeatherPoint | None:
        i = bisect_right(self._wstarts, ts) - 1
        if i < 0:
            return None
        p = self._weather[i]
        return p if ts < p.start + timedelta(minutes=p.minutes) else None

    # ------------------------------------------------------------------ PV
    def pv_calibration(self) -> float:
        if self.db is None:
            return 1.0
        ratios = self.db.kv_get("forecast.pv_ratios", []) or []
        vals = [r for r in ratios[-14:] if r and 0.2 < r < 3]
        return max(0.5, min(1.5, statistics.median(vals))) if len(vals) >= 3 else 1.0

    def _pv(self, slots: list[datetime], calibration: float) -> tuple[list[float], str]:
        arrays = pv_arrays(self.config)
        if not arrays:
            return [0.0] * len(slots), "geen PV geconfigureerd"
        out, have = [], 0
        for t in slots:
            w = self._weather_at(t + timedelta(minutes=SLOT_MIN / 2))
            if w is None or w.ghi_w_m2 is None:
                out.append(0.0)
                continue
            have += 1
            total = 0.0
            for a in arrays:
                p = a["kwp"] * w.ghi_w_m2 * a["pr"] * orientation_factor(a["azimuth"]) * calibration
                total += min(p, a["inverter_kw"] * 1000)
            out.append(max(0.0, total))
        if have == 0:
            return out, "geen weersverwachting beschikbaar (PV = 0)"
        return out, f"{self.weather.name if self.weather else 'weer'} x kalibratie {calibration:.2f}"

    # ---------------------------------------------------------------- load
    def _history_profile(self, now: datetime, column: str) -> dict[tuple[bool, int], float] | None:
        if self.db is None:
            return None
        days = self.config.forecast.history_days
        rows = self.db.slots_between(self.config.site.id, (now - timedelta(days=days)).timestamp(), now.timestamp())
        buckets: dict[tuple[bool, int], list[float]] = {}
        for r in rows:
            if r.get(column) is None or (r.get("coverage") or 0) < 0.5:
                continue
            local = datetime.fromtimestamp(r["slot_ts"], UTC).astimezone(self.tz)
            key = (local.weekday() >= 5, local.hour * 4 + local.minute // 15)
            buckets.setdefault(key, []).append(r[column] * 4000.0)  # kWh per 15 min -> W
        covered_days = len({k for k in buckets}) / 96
        if covered_days < 1.5 or len(rows) < 3 * 96:
            return None
        return {k: statistics.median(v) for k, v in buckets.items()}

    def _load(self, slots, now) -> tuple[list[float], str]:
        profile = self._history_profile(now, "house_kwh")
        mean_w = self.config.site.annual_consumption_kwh * 1000 / 8760
        out = []
        for t in slots:
            local = t.astimezone(self.tz)
            weekend = local.weekday() >= 5
            key = (weekend, local.hour * 4 + local.minute // 15)
            if profile is not None and key in profile:
                out.append(profile[key])
            else:
                hod = local.hour + local.minute / 60 + 7.5 / 60
                out.append(mean_w * SimBaseLoad.shape(hod, weekend) / 1.08)
        return out, ("historie (mediaan per kwartier)" if profile else "standaardprofiel o.b.v. jaarverbruik")

    def _hp_history(self, slots, now) -> tuple[list[float], str]:
        profile = self._history_profile(now, "hp_kwh")
        if profile is None:
            return [0.0] * len(slots), "geen historie"
        out = []
        for t in slots:
            local = t.astimezone(self.tz)
            out.append(profile.get((local.weekday() >= 5, local.hour * 4 + local.minute // 15), 0.0))
        return out, "historie"

    # ---------------------------------------------------------------- main
    def build(self, now: datetime, horizon_h: float, last_outdoor_c: float | None = None) -> Forecast:
        start = now.astimezone(UTC).replace(minute=(now.minute // SLOT_MIN) * SLOT_MIN, second=0, microsecond=0)
        n = int(horizon_h * 60 / SLOT_MIN)
        slots = [start + timedelta(minutes=SLOT_MIN * i) for i in range(n)]
        cal = self.pv_calibration()
        pv, pv_src = self._pv(slots, cal)
        load, load_src = self._load(slots, now)
        hp, hp_src = self._hp_history(slots, now)
        temps, temp_src = [], "weersverwachting"
        for t in slots:
            w = self._weather_at(t + timedelta(minutes=SLOT_MIN / 2))
            temps.append(w.temperature_c if w and w.temperature_c is not None else last_outdoor_c)
        if not self._weather:
            temp_src = "persistentie laatste meting" if last_outdoor_c is not None else "onbekend"
        fc = Forecast(now, slots, pv, load, hp, temps,
                      {"pv": pv_src, "load": load_src, "heat_pump": hp_src, "outdoor_temp": temp_src}, cal)
        self.latest = fc
        return fc

    async def update_pv_calibration(self, day: datetime) -> float | None:
        """Compare yesterday's actual PV energy with what the weather model predicts."""
        if self.db is None or not self._weather:
            return None
        start = day.astimezone(self.tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        end = start + timedelta(days=1)
        rows = await asyncio.to_thread(self.db.slots_between, self.config.site.id, start.timestamp(), end.timestamp())
        actual = sum(r.get("pv_kwh") or 0 for r in rows)
        slots = [start + timedelta(minutes=SLOT_MIN * i) for i in range(96)]
        predicted = sum(self._pv(slots, 1.0)[0]) * SLOT_MIN / 60 / 1000
        if predicted < 1.0 or len(rows) < 80:
            return None
        ratio = actual / predicted
        ratios = (self.db.kv_get("forecast.pv_ratios", []) or [])[-30:] + [round(ratio, 4)]
        await asyncio.to_thread(self.db.kv_set, "forecast.pv_ratios", ratios)
        return ratio

    def status(self) -> dict:
        return {"weather_provider": None if self.weather is None else self.weather.name,
                "last_refresh": None if self.last_refresh is None else self.last_refresh.isoformat(),
                "last_error": self.last_error, "weather_points": len(self._weather),
                "pv_calibration": self.pv_calibration(),
                "sources": None if self.latest is None else self.latest.sources}
