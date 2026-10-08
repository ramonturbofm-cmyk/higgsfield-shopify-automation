"""Audit acceptance tests 7 and 8: tax brackets, contract-dependent settlement, year boundary, negative export."""

from datetime import UTC, date, datetime

import pytest

from ems.core.config import TariffConfig
from ems.services.settlement import RULES, settle
from ems.tariffs.engine import TariffEngine
from ems.tariffs.taxes import energy_tax_rate, tax_on_consumption

TZ = "Europe/Amsterdam"


# 7 ---------------------------------------------------------------------------------------------
def test_07_nl_2026_first_bracket_and_brackets():
    r = energy_tax_rate("NL", date(2026, 3, 1))
    assert r.energy_tax_eur_kwh == 0.09161 and not r.verify
    assert round(0.09161 * 1.21, 5) == pytest.approx(0.1108481, abs=1e-5)       # official incl. VAT value
    b = r.bracket_table()
    assert b[0].upto_kwh == 10_000 and b[0].rate_eur_kwh == 0.09161 and not b[0].verify
    assert b[1].upto_kwh == 50_000 and b[1].verify                               # not yet checked officially
    assert b[-1].rate_eur_kwh is None                                            # > 10 million kWh: manual
    # 12,000 kWh: 10,000 in bracket 1, 2,000 in bracket 2.
    amount, warnings = tax_on_consumption(r, 0, 12_000)
    assert amount == pytest.approx(10_000 * 0.09161 + 2_000 * b[1].rate_eur_kwh)
    assert any("niet gecontroleerd" in w for w in warnings)
    # A household below 10,000 kWh only uses the first bracket, without warnings.
    assert tax_on_consumption(r, 0, 3_000) == (pytest.approx(3_000 * 0.09161), [])
    # Already 9,500 kWh taxed this year: the next 1,000 kWh straddle the boundary.
    amount, _ = tax_on_consumption(r, 9_500, 1_000)
    assert amount == pytest.approx(500 * 0.09161 + 500 * b[1].rate_eur_kwh)


def test_07_tariff_engine_uses_2026_table_rate():
    t = TariffConfig(contract_type="dynamic", import_markup_eur_kwh=0.02, energy_tax_mode="table", vat_pct=21)
    p = TariffEngine(t, lambda ts: 0.10).breakdown(datetime(2026, 6, 1, 12, tzinfo=UTC))
    assert p.import_parts["energiebelasting"] == 0.09161
    assert p.import_price == pytest.approx((0.10 + 0.02 + 0.09161) * 1.21, abs=1e-6)


def _day(year, month, day, spot_noon=0.08, spot_night=0.12):
    t0 = datetime(year, month, day, tzinfo=UTC).timestamp()
    return [{"slot_ts": t0 + i * 900, "spot": spot_noon if 40 <= i < 64 else spot_night,
             "import_kwh": 0.0 if 40 <= i < 64 else 0.25, "export_kwh": 0.6 if 40 <= i < 64 else 0.0}
            for i in range(96)]


def test_07_settlement_is_contract_dependent():
    rows = _day(2026, 6, 1)
    dyn = TariffConfig(contract_type="dynamic", import_markup_eur_kwh=0.02, energy_tax_mode="table",
                       export_markup_eur_kwh=-0.02, vat_pct=21, netting=True)
    fixed = TariffConfig(contract_type="fixed", fixed_import_price_eur_kwh=0.30, fixed_export_price_eur_kwh=0.05,
                         netting=True)
    d = settle(rows, dyn, None, TZ)
    f = settle(rows, fixed, None, TZ)
    assert d["netting_method"] == "tax_only" and f["netting_method"] == "import_price"
    # Dynamic: netting removes energy tax + VAT on the netted 14.4 kWh; exports earn their own price.
    assert d["netting_credit_eur"] == pytest.approx(14.4 * 0.09161 * 1.21, abs=0.01)
    assert d["export_revenue_eur"] == pytest.approx(14.4 * 0.06, abs=0.01)
    assert d["energy_tax_eur"] == pytest.approx((18.0 - 14.4) * 0.09161, abs=0.01)
    # Fixed: netted kWh credited at the all-in import price.
    assert f["netting_credit_eur"] == pytest.approx(14.4 * 0.30, abs=0.01)
    assert f["energy_tax_eur"] is None                       # included in the fixed price, not separable
    # The user can override the method to what the contract says.
    alt = settle(rows, dyn.model_copy(update={"netting_method": "import_price"}), None, TZ)
    assert alt["netting_method"] == "import_price" and alt["netting_credit_eur"] != d["netting_credit_eur"]
    # Fixed costs never enter the energy part.
    withfix = settle(rows, dyn, None, TZ, fixed_per_day=1.5, days=1)
    assert withfix["energy_cost_eur"] == d["energy_cost_eur"] and withfix["total_eur"] == d["total_eur"] + 1.5


# 8 ---------------------------------------------------------------------------------------------
def test_08_year_boundary_switches_rules_and_rates():
    rows = _day(2026, 12, 31) + _day(2027, 1, 1)
    t = TariffConfig(contract_type="dynamic", import_markup_eur_kwh=0.02, energy_tax_mode="table",
                     export_markup_eur_kwh=-0.02, vat_pct=21, netting=True)
    s = settle(rows, t, None, TZ)
    assert s["years"] == [2026, 2027]
    assert "NL-2026" in s["rules"] and "NL-2027" in s["rules"]
    one26 = settle(_day(2026, 12, 31), t, None, TZ)
    one27 = settle(_day(2027, 1, 1), t, None, TZ)
    assert one26["netted_kwh"] > 0 and one27["netted_kwh"] == 0              # netting ends 1-1-2027
    assert s["energy_cost_eur"] == pytest.approx(one26["energy_cost_eur"] + one27["energy_cost_eur"], abs=0.02)
    assert any("2027" in src for src in one27["tax_sources"])                # 2027 marked: no table value yet


def test_08_negative_export_prices_cost_money():
    rows = _day(2027, 5, 1, spot_noon=-0.10)
    t = TariffConfig(contract_type="dynamic", import_markup_eur_kwh=0.02, energy_tax_mode="table",
                     export_markup_eur_kwh=-0.02, vat_pct=21)
    s = settle(rows, t, None, TZ)
    assert s["negative_export_kwh"] == pytest.approx(14.4, abs=0.01)
    assert s["negative_export_cost_eur"] == pytest.approx(14.4 * 0.12, abs=0.01)
    assert s["export_revenue_eur"] == pytest.approx(-14.4 * 0.12, abs=0.01)
    # A contract with a fixed feed-in price is not affected by the negative market price.
    fixed = t.model_copy(update={"fixed_export_price_eur_kwh": 0.01})
    assert settle(rows, fixed, None, TZ)["negative_export_kwh"] == 0


def test_08_scenario_comparison_states_its_limitation():
    from ems.services.settlement import compare
    t = TariffConfig(contract_type="dynamic", energy_tax_mode="table", netting=True)
    out = compare(_day(2026, 6, 1), t, TZ, fixed_per_day=0, days=1)
    assert out["kind"] == "scenario" and "Ceteris paribus" in out["limitation"]
    assert [s["rules"] for s in out["scenarios"]] == ["NL-2026", "NL-2027"]
    assert RULES["NL-2027"].netting is False


# 11 --------------------------------------------------------------------------------------------
def _arbitrage(p2: float):
    from datetime import timedelta

    from ems.optimizer import BatteryModel, OptimizerInput, solve
    t0 = datetime(2026, 7, 1, tzinfo=UTC)
    n = 16
    price = [0.10] * 8 + [p2] * 8
    bat = BatteryModel(10, 1.0, 1.0, 9.5, 5000, 5000, eta_charge=0.95, eta_discharge=0.95, degradation_eur_kwh=0.02)
    inp = OptimizerInput([t0 + timedelta(minutes=15 * i) for i in range(n)], 0.25, price, list(price),
                         [0.0] * n, [0.0] * n, 17250, 17250, battery=bat)
    return bat, solve(inp)


def test_11_break_even_spread_includes_losses_and_wear():
    from ems.optimizer.model import break_even_sell_price
    bat, _ = _arbitrage(0.2)
    be = break_even_sell_price(0.10, bat)
    assert be == pytest.approx((0.10 + 0.01) / (0.95 * 0.95) + 0.01)
    # Just below break-even the optimizer does not cycle; clearly above it does, and its expected
    # benefit is the optimizer's own number (energy + wear), not an invented price difference.
    _, below = _arbitrage(be - 0.01)
    assert sum(r["battery_grid_charge_w"] for r in below.slots) < 1
    _, above = _arbitrage(be + 0.05)
    assert sum(r["battery_grid_charge_w"] for r in above.slots) > 1000
    assert above.inputs_summary["wear_cost_eur"] > 0 and above.expected_benefit > 0


def test_11_vat_and_fixed_costs_are_not_marginal():
    t = TariffConfig(contract_type="dynamic", import_markup_eur_kwh=0.02, energy_tax_mode="table", vat_pct=21,
                     export_markup_eur_kwh=-0.01, fixed_monthly_eur=10, grid_monthly_eur=30)
    eng = TariffEngine(t, lambda ts: 0.10)
    b = eng.breakdown(datetime(2026, 6, 1, 12, tzinfo=UTC))
    assert b.import_parts["btw"] == pytest.approx((0.10 + 0.02 + 0.09161) * 0.21, abs=1e-6)
    assert b.export_price == pytest.approx(0.09)                    # no VAT on export unless configured
    assert "vast" not in " ".join(b.import_parts)                    # fixed costs never per kWh
    assert eng.fixed_costs_per_day() == pytest.approx(40 * 12 / 365)


# 12 (finance part) -----------------------------------------------------------------------------
def test_12_finance_baselines_reconcile_without_double_counting():
    from conftest import make_config
    from ems.services.finance import finance_summary
    cfg = make_config()
    t0 = datetime(2026, 6, 1, tzinfo=UTC).timestamp()
    rows = []
    for i in range(96):
        sun = 40 <= i < 64
        price = 0.30 if 68 <= i < 84 else 0.12
        rows.append({"slot_ts": t0 + i * 900, "spot": price - 0.05, "import_price": price, "export_price": price - 0.05,
                     "pv_kwh": 1.0 if sun else 0.0, "house_kwh": 0.15, "hp_kwh": 0.05, "ev_kwh": 0.0,
                     "battery_charge_kwh": 0.5 if sun else 0.0, "battery_discharge_kwh": 0.6 if 68 <= i < 84 else 0.0,
                     "import_kwh": 0.0 if sun or 68 <= i < 84 else 0.2, "export_kwh": 0.3 if sun else 0.0,
                     "soc_end": 50.0})
    f = finance_summary(rows[:80], cfg, fixed_per_day=1.0, days=1.0)
    sv = f["savings"]
    assert sv["reconciled"]
    assert sv["pv_eur"] + sv["battery_own_control_eur"] + sv["ems_steering_eur"] == pytest.approx(sv["total_eur"], abs=0.02)
    b = {x["id"]: x["energy_cost_eur"] for x in f["baselines"]}
    assert sv["total_eur"] == pytest.approx(b["B0"] - b["B3"], abs=0.01)
    assert f["coverage_pct"] == pytest.approx(80 / 96 * 100, abs=0.1) and not f["reliable"]
    assert f["costs"]["fixed_costs_eur"] == 1.0 and b["B3"] == f["costs"]["energy_cost_eur"]   # fixed costs apart
    assert "niet opgeteld" in f["indicators"]["note"]
    assert f == finance_summary(rows[:80], cfg, fixed_per_day=1.0, days=1.0)                    # reproducible
