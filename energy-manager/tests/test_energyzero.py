"""EnergyZero public prices API (no token). Response format as used by python-energyzero 5.1.0."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ems.prices.providers import EnergyZeroProvider, parse_energyzero


def _payload(day_start_utc: datetime, minutes: int, n: int, base: float = 0.10) -> dict:
    def items(offset: float):
        out = []
        for i in range(n):
            s = day_start_utc + timedelta(minutes=minutes * i)
            out.append({"start": s.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "end": (s + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "price": {"value": str(round(base + offset + i / 1000, 5))}})
        return out
    return {"base": items(0), "base_with_vat": items(0.02), "all_in": items(0.15), "all_in_with_vat": items(0.2)}


def test_parse_uses_market_price_excl_vat():
    start = datetime(2026, 10, 7, 22, tzinfo=UTC)
    pts = parse_energyzero(_payload(start, 15, 96))
    assert len(pts) == 96 and pts[0].start == start and pts[0].resolution_min == 15
    assert pts[0].price_eur_kwh == pytest.approx(0.10) and pts[5].price_eur_kwh == pytest.approx(0.105)
    with pytest.raises(ValueError):
        parse_energyzero([])  # type: ignore[arg-type]


async def test_provider_quarter_with_url_fallback_and_missing_day():
    calls = []
    day1 = datetime(2026, 10, 7, 22, tzinfo=UTC)   # 08-10-2026 00:00 Amsterdam

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        q = request.url.params
        if request.url.path == "/v1/prices":          # first URL form: pretend it does not exist
            return httpx.Response(404, json={"error": "not found"})
        assert q["energyType"] == "ENERGY_TYPE_ELECTRICITY" and q["interval"] == "INTERVAL_QUARTER"
        if q["date"] == "08-10-2026":
            return httpx.Response(200, json=_payload(day1, 15, 96))
        return httpx.Response(404, json={"error": "no data"})   # tomorrow not published yet

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    prov = EnergyZeroProvider("quarter", client=client)
    pts = await prov.fetch(datetime(2026, 10, 8, 10, tzinfo=UTC), datetime(2026, 10, 9, 10, tzinfo=UTC))
    assert pts and all(p.resolution_min == 15 for p in pts)
    assert pts[0].start == datetime(2026, 10, 8, 10, tzinfo=UTC)          # filtered to the requested window
    assert pts[-1].start == datetime(2026, 10, 8, 21, 45, tzinfo=UTC)     # end of the published day
    assert prov._endpoint[1] == "energyType"                              # working URL form remembered
    assert any(u.params.get("date") == "09-10-2026" for u in calls)
    await client.aclose()


async def test_provider_hour_with_user_url_and_errors():
    day1 = datetime(2026, 10, 7, 22, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/prices" and request.url.params["energy_type"] == "ENERGY_TYPE_ELECTRICITY"
        assert request.url.params["interval"] == "INTERVAL_HOUR"
        return httpx.Response(200, json=_payload(day1, 60, 24))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pts = await EnergyZeroProvider("hour", client=client).fetch(day1, day1 + timedelta(hours=24))
    assert len(pts) == 24 and pts[1].start - pts[0].start == timedelta(hours=1) and pts[0].resolution_min == 60
    await client.aclose()

    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ValueError, match="HTTP 500"):
        await EnergyZeroProvider("quarter", client=bad).fetch(day1, day1 + timedelta(hours=2))
    await bad.aclose()
    with pytest.raises(ValueError):
        EnergyZeroProvider("week")
