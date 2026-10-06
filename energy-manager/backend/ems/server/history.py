"""History: raw site samples (every control tick, >= 10 s apart), per-device samples
(every minute) and 15-minute aggregates with energy, prices and costs.
Only measured data is stored — never generated values."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ems.core.snapshot import SiteSnapshot
from ems.database import Database
from ems.tariffs import TariffEngine

SLOT_S = 900


class HistoryRecorder:
    def __init__(self, db: Database, site_id: str, tariff: TariffEngine, min_interval_s: float = 10.0) -> None:
        self.db = db
        self.site_id = site_id
        self.tariff = tariff
        self.min_interval_s = min_interval_s
        self._last_sample: datetime | None = None
        self._last_device: datetime | None = None

    def sample_row(self, snap: SiteSnapshot) -> dict:
        pc = snap.phase_currents_a or (None, None, None)
        br = self.tariff.breakdown(snap.timestamp)
        return {
            "ts": snap.timestamp.timestamp(), "site_id": self.site_id, "grid_valid": snap.grid_valid,
            "grid_w": snap.grid_power_w if snap.grid_valid else None,
            "pv_w": snap.pv_power_w, "battery_w": snap.battery_power_w, "soc": snap.battery_soc_pct,
            "hp_w": snap.hp_power_w, "ev_w": snap.ev_power_w, "house_w": snap.house_load_w,
            "l1_a": pc[0], "l2_a": pc[1], "l3_a": pc[2],
            "indoor_c": snap.indoor_temp_c, "outdoor_c": snap.outdoor_temp_c,
            "spot": br.spot, "import_price": br.import_price, "export_price": br.export_price,
        }

    async def record(self, snap: SiteSnapshot) -> None:
        now = snap.timestamp
        if self._last_sample is None or (now - self._last_sample).total_seconds() >= self.min_interval_s - 0.01:
            self._last_sample = now
            await asyncio.to_thread(self.db.insert_sample, self.sample_row(snap))
        if self._last_device is None or (now - self._last_device).total_seconds() >= 60:
            self._last_device = now
            rows = [{"ts": now.timestamp(), "device_id": d.device_id, "status": d.status.value,
                     "values": {str(k): v for k, v in d.values.items()}} for d in snap.devices.values()]
            await asyncio.to_thread(self.db.insert_device_samples, rows)

    # ------------------------------------------------------------ aggregation
    def aggregate(self, now: datetime) -> int:
        last = self.db.last_slot_ts(self.site_id)
        first = self.db.first_sample_ts(self.site_id)
        if first is None:
            return 0
        start = (last + SLOT_S) if last is not None else (first // SLOT_S) * SLOT_S
        end_limit = (now.timestamp() // SLOT_S) * SLOT_S       # only completed slots
        rows, t = [], start
        while t < end_limit:
            samples = self.db.samples_between(self.site_id, t, t + SLOT_S)
            if samples:
                rows.append(self._slot(t, samples))
            t += SLOT_S
            if len(rows) >= 96 * 7:
                break
        self.db.upsert_slots(rows)
        return len(rows)

    def _slot(self, slot_ts: float, samples: list[dict]) -> dict:
        # Each sample represents the power until the next sample (max 60 s gap counted).
        def energy(key: str, positive: bool | None = None) -> float | None:
            total, seen = 0.0, False
            for i, s in enumerate(samples):
                v = s.get(key)
                if v is None:
                    continue
                nxt = samples[i + 1]["ts"] if i + 1 < len(samples) else min(slot_ts + SLOT_S, s["ts"] + 10)
                dur = max(0.0, min(60.0, nxt - s["ts"]))
                if positive is True:
                    v = max(0.0, v)
                elif positive is False:
                    v = max(0.0, -v)
                total += v * dur / 3.6e6
                seen = True
            return round(total, 5) if seen else None

        def mean(key: str) -> float | None:
            vals = [s[key] for s in samples if s.get(key) is not None]
            return round(sum(vals) / len(vals), 4) if vals else None

        imp, exp = energy("grid_w", True), energy("grid_w", False)
        ip, ep = mean("import_price"), mean("export_price")
        covered = sum(1 for s in samples if s.get("grid_w") is not None)
        coverage = min(1.0, covered * self.min_interval_s / SLOT_S)
        soc_vals = [s["soc"] for s in samples if s.get("soc") is not None]
        return {
            "site_id": self.site_id, "slot_ts": slot_ts,
            "import_kwh": imp, "export_kwh": exp, "pv_kwh": energy("pv_w"),
            "battery_charge_kwh": energy("battery_w", True), "battery_discharge_kwh": energy("battery_w", False),
            "hp_kwh": energy("hp_w"), "ev_kwh": energy("ev_w"), "house_kwh": energy("house_w"),
            "soc_end": soc_vals[-1] if soc_vals else None,
            "indoor_c": mean("indoor_c"), "outdoor_c": mean("outdoor_c"), "spot": mean("spot"),
            "import_price": ip, "export_price": ep,
            "cost_eur": None if imp is None or ip is None else round(imp * ip, 5),
            "revenue_eur": None if exp is None or ep is None else round(exp * ep, 5),
            "coverage": round(coverage, 3),
        }


def day_bounds(now: datetime, tz, period: str) -> tuple[datetime, datetime]:
    local = now.astimezone(tz)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start = day
    elif period == "month":
        start = day.replace(day=1)
    elif period == "year":
        start = day.replace(month=1, day=1)
    elif period.endswith("d") and period[:-1].isdigit():
        start = day - timedelta(days=int(period[:-1]) - 1)
    else:
        raise ValueError(f"onbekende periode {period!r}")
    return start.astimezone(UTC), now.astimezone(UTC)
