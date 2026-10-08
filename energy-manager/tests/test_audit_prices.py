"""Audit acceptance tests 9 and 10: DST interval counts, hourly contract on a 15-minute market,
manual 15/60-minute prices, and a "now" price that never comes from an expired interval."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from ems.api.app import create_app
from ems.core.config import TariffConfig
from ems.prices.providers import PricePoint, parse_manual_prices
from ems.prices.service import PriceService, local_day_slots
from ems.server.runtime import EMSRuntime
from ems.tariffs.engine import TariffEngine

TZ = ZoneInfo("Europe/Amsterdam")


# 9 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("day", "quarters"), [(date(2026, 3, 29), 92), (date(2026, 6, 1), 96),
                                               (date(2026, 10, 25), 100)])
def test_09_local_day_has_92_96_100_quarters(day, quarters):
    slots = local_day_slots(day, TZ)
    assert len(slots) == quarters
    assert all(b - a == timedelta(minutes=15) for a, b in zip(slots, slots[1:], strict=False))
    assert slots[0].astimezone(TZ).hour == 0


def test_09_hourly_contract_on_quarter_hour_market():
    svc = PriceService(None, "NL", None, "Europe/Amsterdam")
    hour = datetime(2026, 6, 1, 10, tzinfo=TZ).astimezone(UTC)
    svc.add_points([PricePoint(hour + timedelta(minutes=15 * k), v, 15) for k, v in enumerate((0.10, 0.20, 0.30, 0.40))],
                   "test")
    q = TariffEngine(TariffConfig(contract_type="dynamic", vat_pct=0, import_markup_eur_kwh=0), svc.spot)
    hr = TariffEngine(TariffConfig(contract_type="dynamic", vat_pct=0, import_markup_eur_kwh=0,
                                   price_resolution_min=60), svc.spot)
    t = hour + timedelta(minutes=30)
    assert q.breakdown(t).spot == pytest.approx(0.30)          # quarter price
    assert hr.breakdown(t).spot == pytest.approx(0.25)         # hourly average of the four quarters
    assert hr.breakdown(hour).spot == hr.breakdown(hour + timedelta(minutes=45)).spot


def test_09_manual_prices_15_and_60_minutes_with_gaps_and_dst():
    rows = [{"start": f"2026-10-25T{h:02d}:{m:02d}:00+02:00", "price_eur_kwh": "0,10", "resolution_min": 15}
            for h in (0, 1) for m in (0, 15, 30, 45)]
    rows += [{"start": "2026-10-25T02:00:00+01:00", "price_eur_kwh": 0.11, "resolution_min": 15}]   # after DST end
    pts, rep = parse_manual_prices(rows, "Europe/Amsterdam")
    assert rep["intervals"] == 9 and pts[0].price_eur_kwh == 0.10
    assert len(rep["gaps"]) == 1                               # 02:00+02:00 .. 02:00+01:00 missing (1 hour)
    with pytest.raises(ValueError, match="tijdzone"):
        parse_manual_prices([{"start": "2026-10-25T00:00:00", "price_eur_kwh": 0.1}])
    with pytest.raises(ValueError, match="grens"):
        parse_manual_prices([{"start": "2026-10-25T00:10:00+02:00", "price_eur_kwh": 0.1, "resolution_min": 15}])
    with pytest.raises(ValueError, match="dubbel"):
        parse_manual_prices([{"start": "2026-10-25T00:00:00+02:00", "price_eur_kwh": 0.1},
                             {"start": "2026-10-24T22:00:00Z", "price_eur_kwh": 0.1}])
    with pytest.raises(ValueError, match="MWh"):
        parse_manual_prices([{"start": "2026-10-25T00:00:00+02:00", "price_eur_kwh": 95.0}])
    hourly, _ = parse_manual_prices([{"start": "2026-10-25T00:00:00+02:00", "price_eur_kwh": 0.1,
                                      "resolution_min": 60}])
    assert hourly[0].resolution_min == 60


# 10 --------------------------------------------------------------------------------------------
def test_10_current_price_requires_interval_overlap():
    svc = PriceService(None, "NL", None, "Europe/Amsterdam")
    t0 = datetime(2026, 6, 1, 8, tzinfo=UTC)
    svc.add_points([PricePoint(t0 + timedelta(minutes=15 * k), 0.10 + k / 100, 15) for k in range(4)], "test")
    assert svc.current(t0 + timedelta(minutes=20))["spot"] == pytest.approx(0.11)
    assert svc.current(t0 + timedelta(minutes=60)) is None       # last interval expired: no stale "now"
    assert svc.current(t0 - timedelta(minutes=1)) is None
    # The 15-min series marks missing and estimated intervals instead of silently filling them.
    ser = svc.series(t0, t0 + timedelta(hours=2), include_missing=True)
    assert [p.status for p in ser[:4]] == ["confirmed"] * 4 and ser[-1].status == "missing"


async def test_10_api_now_price_not_available_when_expired(tmp_path):
    rt = EMSRuntime(tmp_path / "p", mode="production", env={})
    await rt.start(loops=False)
    app = create_app(rt, start_runtime=False)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        r = await c.post("/api/v1/auth/setup", json={"username": "beheer", "password": "geheim-wachtwoord"})
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        now = rt.now()
        old = (now - timedelta(hours=3)).astimezone(TZ).replace(minute=0, second=0, microsecond=0)
        body = {"points": [{"start": (old + timedelta(minutes=15 * k)).isoformat(), "price_eur_kwh": 0.2,
                            "resolution_min": 15} for k in range(4)]}
        assert (await c.post("/api/v1/prices/manual", json={**body, "dry_run": True})).json()["stored"] == 0
        assert (await c.post("/api/v1/prices/manual", json=body)).json()["stored"] == 4
        data = (await c.get("/api/v1/prices?hours=6&past_hours=4")).json()
        assert data["current"] is None and "geen gepubliceerde prijs" in data["current_reason"]
        assert {p["status"] for p in data["points"]} >= {"confirmed"}
        assert all(p["status"] != "confirmed" for p in data["points"] if p["start"] >= now.isoformat())
    finally:
        await c.aclose()
        await rt.stop()
