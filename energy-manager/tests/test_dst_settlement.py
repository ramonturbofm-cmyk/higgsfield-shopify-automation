"""Daylight-saving days (92/96/100 quarter-hours), missing intervals, tax table and settlement rules."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ems.core.config import TariffConfig
from ems.prices.providers import PricePoint
from ems.prices.service import PriceService, local_day_slots
from ems.services.settlement import RULES, compare, rules_for, settle
from ems.tariffs.engine import TariffEngine
from ems.tariffs.taxes import energy_tax_rate

AMS = ZoneInfo("Europe/Amsterdam")


@pytest.mark.parametrize("day,n", [(date(2026, 3, 29), 92), (date(2026, 6, 1), 96), (date(2026, 10, 25), 100)])
def test_local_day_quarter_hours_around_dst(day, n):
    slots = local_day_slots(day, AMS, 15)
    assert len(slots) == n
    assert slots[0].astimezone(AMS).hour == 0 and slots[-1].astimezone(AMS).strftime("%H:%M") == "23:45"
    assert len(local_day_slots(day, AMS, 60)) == n // 4
    assert all(b - a == timedelta(minutes=15) for a, b in zip(slots, slots[1:], strict=False))


def test_price_coverage_detects_missing_quarter_and_25h_day():
    svc = PriceService(None, "NL", None, "Europe/Amsterdam")
    day = date(2026, 10, 25)
    slots = local_day_slots(day, AMS, 15)
    svc.add_points([PricePoint(t, 0.1, 15) for i, t in enumerate(slots) if i != 40], "test")
    cov = svc.coverage(day)
    assert cov["intervals"] == 100 and cov["hours"] == 25 and cov["known"] == 99
    assert not cov["complete"] and cov["missing_count"] == 1
    svc.add_points([PricePoint(slots[40], 0.1, 15)], "test")
    assert svc.coverage(day)["complete"]
    spring = svc.coverage(date(2026, 3, 29))
    assert spring["intervals"] == 92 and spring["hours"] == 23


def test_tax_table_by_year_and_history_uses_its_own_year():
    assert energy_tax_rate("NL", date(2024, 5, 1)).energy_tax_eur_kwh == 0.10880
    assert energy_tax_rate("NL", date(2025, 5, 1)).energy_tax_eur_kwh == 0.10154
    future = energy_tax_rate("NL", date(2031, 1, 1))
    assert future.verify and "2031" in future.source
    t = TariffConfig(contract_type="dynamic", import_markup_eur_kwh=0.02, energy_tax_mode="table", vat_pct=21)
    eng = TariffEngine(t, lambda ts: 0.10)
    p24 = eng.breakdown(datetime(2024, 6, 1, 12, tzinfo=UTC))
    p25 = eng.breakdown(datetime(2025, 6, 1, 12, tzinfo=UTC))
    assert p24.import_parts["energiebelasting"] == 0.1088 and p25.import_parts["energiebelasting"] == 0.10154
    assert p24.import_price == pytest.approx((0.10 + 0.02 + 0.1088) * 1.21, abs=1e-6)


def test_marginal_price_ignores_fixed_costs():
    t = TariffConfig(contract_type="dynamic", fixed_monthly_eur=30, grid_monthly_eur=40)
    eng = TariffEngine(t, lambda ts: 0.10)
    with_fixed = eng.breakdown(datetime(2026, 6, 1, 12, tzinfo=UTC)).import_price
    eng2 = TariffEngine(TariffConfig(contract_type="dynamic"), lambda ts: 0.10)
    assert with_fixed == eng2.breakdown(datetime(2026, 6, 1, 12, tzinfo=UTC)).import_price
    assert eng.fixed_costs_per_day() == pytest.approx(70 * 12 / 365)


def _slots():
    t0 = datetime(2026, 6, 1, tzinfo=UTC).timestamp()
    rows = []
    for i in range(96):                       # one day: import at night, export at noon
        noon = 40 <= i < 64
        rows.append({"slot_ts": t0 + i * 900, "spot": 0.08 if noon else 0.12,
                     "import_kwh": 0.0 if noon else 0.25, "export_kwh": 0.6 if noon else 0.0})
    return rows


def test_settlement_with_and_without_netting():
    tariff = TariffConfig(contract_type="dynamic", import_markup_eur_kwh=0.02, energy_tax_eur_kwh=0.10,
                          export_markup_eur_kwh=-0.02, vat_pct=21)
    rows = _slots()
    n26 = settle(rows, tariff, RULES["NL-2026"], "Europe/Amsterdam")
    n27 = settle(rows, tariff, RULES["NL-2027"], "Europe/Amsterdam")
    assert n26["import_kwh"] == 18.0 and n26["export_kwh"] == 14.4 and n26["netted_kwh"] == 14.4
    assert n27["netted_kwh"] == 0 and n27["energy_cost_eur"] > n26["energy_cost_eur"]
    # Without netting export only earns spot + export markup (0.06 / kWh here).
    assert n27["export_revenue_eur"] == pytest.approx(14.4 * 0.06, abs=0.01)
    cmp = compare(rows, tariff, "Europe/Amsterdam", fixed_per_day=1.0, days=1.0)
    s26, s27 = cmp["scenarios"]
    assert s27["difference_eur"] == pytest.approx(s27["total_eur"] - s26["total_eur"], abs=0.01)
    assert s26["fixed_costs_eur"] == 1.0 and s26["per_year_eur"] == round(s26["total_eur"] * 365)
    assert rules_for("NL", date(2026, 12, 31)).id == "NL-2026" and rules_for("NL", date(2027, 1, 1)).id == "NL-2027"
