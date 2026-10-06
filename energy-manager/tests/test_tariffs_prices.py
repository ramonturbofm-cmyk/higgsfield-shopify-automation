from datetime import UTC, datetime, timedelta

import pytest

from ems.core.config import TariffConfig
from ems.database import Database
from ems.prices.providers import DemoProvider, PricePoint, PriceProvider, parse_entsoe_a44, parse_manual_prices
from ems.prices.service import PriceService
from ems.simulator.environment import Environment
from ems.tariffs import TariffEngine

T = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)


def engine(spot=0.10, **kw):
    return TariffEngine(TariffConfig(**kw), lambda ts: spot)


def test_dynamic_import_and_export_formula():
    e = engine(0.10, import_markup_eur_kwh=0.02, energy_tax_eur_kwh=0.10, import_other_eur_kwh=0.01,
               transaction_fee_eur_kwh=0.005, vat_pct=21, export_markup_eur_kwh=-0.02, export_fee_eur_kwh=0.01)
    b = e.breakdown(T)
    assert b.import_price == pytest.approx((0.10 + 0.02 + 0.10 + 0.01 + 0.005) * 1.21)
    assert b.export_price == pytest.approx(0.10 - 0.02 - 0.01)
    assert b.import_parts["btw"] == pytest.approx(0.235 * 0.21)
    assert e.get_import_price(T) == b.import_price and e.get_export_price(T) == b.export_price


def test_negative_spot_and_export_vat():
    e = engine(-0.05, import_markup_eur_kwh=0.02, energy_tax_eur_kwh=0.1, vat_pct=21,
               export_markup_eur_kwh=0.0, export_vat=True)
    assert e.get_export_price(T) == pytest.approx(-0.05 * 1.21)
    assert e.get_import_price(T) == pytest.approx(0.07 * 1.21)


def test_netting_fixed_and_time_of_use():
    assert engine(0.1, netting=True).get_export_price(T) == engine(0.1).get_import_price(T)
    fixed = engine(None, contract_type="fixed", fixed_import_price_eur_kwh=0.30, fixed_export_price_eur_kwh=0.08)
    assert fixed.get_import_price(T) == 0.30 and fixed.get_export_price(T) == 0.08
    tou = engine(None, contract_type="variable", fixed_import_price_eur_kwh=0.30, fixed_export_price_eur_kwh=0.05,
                 time_of_use=[{"start": "23:00", "end": "07:00", "price_eur_kwh": 0.20}])
    night = datetime(2026, 7, 1, 23, 30, tzinfo=UTC)  # 01:30 local
    assert tou.get_import_price(night) == 0.20
    assert tou.get_import_price(T) == 0.30


def test_missing_spot_gives_unavailable_not_invented():
    e = engine(None)
    b = e.breakdown(T)
    assert b.import_price is None and b.export_price is None and b.source == "missing"


def test_fixed_costs_per_day():
    e = engine(0.1, fixed_monthly_eur=6.0, grid_monthly_eur=40.0, fixed_daily_eur=0.1,
               energy_tax_credit_yearly_eur=365.0)
    assert e.fixed_costs_per_day() == pytest.approx(0.1 + 46 * 12 / 365 - 1.0)


ENTSOE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3">
 <TimeSeries><Period>
  <timeInterval><start>2026-06-30T22:00Z</start><end>2026-06-30T23:00Z</end></timeInterval>
  <resolution>PT15M</resolution>
  <Point><position>1</position><price.amount>85.10</price.amount></Point>
  <Point><position>3</position><price.amount>-12.00</price.amount></Point>
 </Period></TimeSeries>
</Publication_MarketDocument>"""


def test_parse_entsoe_with_omitted_points():
    pts = parse_entsoe_a44(ENTSOE_XML)
    assert [p.price_eur_kwh for p in pts] == [0.0851, 0.0851, -0.012, -0.012]
    assert pts[0].start == datetime(2026, 6, 30, 22, 0, tzinfo=UTC) and pts[0].resolution_min == 15


def test_parse_entsoe_acknowledgement_is_error():
    xml = ('<Acknowledgement_MarketDocument xmlns="urn:x"><Reason><code>999</code>'
           '<text>No matching data found</text></Reason></Acknowledgement_MarketDocument>')
    with pytest.raises(ValueError, match="No matching data"):
        parse_entsoe_a44(xml)


def test_manual_prices_require_timezone():
    pts = parse_manual_prices([{"start": "2026-07-01T00:00:00+02:00", "price_eur_kwh": 0.1}])
    assert pts[0].start == datetime(2026, 6, 30, 22, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_manual_prices([{"start": "2026-07-01T00:00:00", "price_eur_kwh": 0.1}])


class Failing(PriceProvider):
    name = "failing"

    async def fetch(self, start, end):
        raise ConnectionError("offline")


async def test_price_service_cache_survives_outage(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/t.db")
    db.migrate()
    svc = PriceService(db, "NL", DemoProvider(Environment(52, 5, "Europe/Amsterdam")))
    n = await svc.refresh(T)
    assert n > 200
    assert svc.spot(T) is not None
    # New process, provider down: cached prices still served from the database.
    svc2 = PriceService(db, "NL", Failing())
    assert await svc2.refresh(T) == 0
    assert svc2.last_error and "offline" in svc2.last_error
    assert svc2.spot(T) == svc.spot(T)
    # Beyond the published horizon: estimate from previous days, flagged as such.
    far = T + timedelta(days=3)
    assert svc2.spot(far) is None
    value, estimated = svc2.spot_or_estimate(far)
    assert estimated and value is not None


def test_series_without_estimates_has_gaps():
    svc = PriceService(None, "NL", None)
    svc.add_points([PricePoint(T, 0.1, 60)], "manual")
    s = svc.series(T - timedelta(hours=1), T + timedelta(hours=2), estimate=False)
    assert [p.spot for p in s] == [0.1, 0.1, 0.1, 0.1]
