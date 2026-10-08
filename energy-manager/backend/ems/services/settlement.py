"""Settlement engine: what the measured energy costs under a country's settlement rules.

Separate from the tariff engine (prices per kWh). Rules per country and period:

* NL until 31-12-2026 — salderingsregeling: exported kWh are netted against imported kWh
  over the year; netted kWh are credited at the (average) import price. Export above the
  yearly import only earns the contract's feed-in value.
* NL from 1-1-2027 — no netting (Wet beëindiging salderingsregeling): every exported kWh
  earns the contract's export value, every imported kWh costs the import price.

The netting credit uses the period's average import price — an approximation of how
suppliers settle; suppliers can differ in details (shown in the result).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from ems.core.config import TariffConfig
from ems.tariffs.engine import TariffEngine


@dataclass(frozen=True)
class SettlementRules:
    id: str
    label: str
    country: str
    netting: bool
    valid_from: date
    valid_to: date | None
    source: str


RULES: dict[str, SettlementRules] = {r.id: r for r in (
    SettlementRules("NL-2026", "Nederland 2026 (met salderen)", "NL", True, date(2004, 1, 1), date(2026, 12, 31),
                    "Wet op de energiebelasting / salderingsregeling"),
    SettlementRules("NL-2027", "Nederland vanaf 2027 (zonder salderen)", "NL", False, date(2027, 1, 1), None,
                    "Wet beëindiging salderingsregeling (aangenomen 2024)"),
)}


def rules_for(country: str, day: date) -> SettlementRules | None:
    for r in RULES.values():
        if r.country == country and r.valid_from <= day and (r.valid_to is None or day <= r.valid_to):
            return r
    return None


def settle(slots: list[dict], tariff: TariffConfig, rules: SettlementRules, timezone: str,
           fixed_per_day: float = 0.0, days: float = 0.0) -> dict:
    """``slots``: 15-min history rows (slot_ts, spot, import_kwh, export_kwh). Prices are recomputed
    from the spot price with the contract (without the contract's own netting switch)."""
    plain = tariff.model_copy(update={"netting": False})
    engine = TariffEngine(plain, lambda _ts: None, timezone)
    imp_kwh = exp_kwh = imp_cost = exp_value = 0.0
    used = 0
    for r in slots:
        if r.get("import_kwh") is None:
            continue
        ts = datetime.fromtimestamp(r["slot_ts"], UTC)
        b = engine.breakdown_with_spot(ts, r.get("spot"))
        if b.import_price is None:
            continue
        used += 1
        i, e = r.get("import_kwh") or 0.0, r.get("export_kwh") or 0.0
        imp_kwh += i
        exp_kwh += e
        imp_cost += i * b.import_price
        exp_value += e * (b.export_price or 0.0)
    avg_import = imp_cost / imp_kwh if imp_kwh else 0.0
    avg_export = exp_value / exp_kwh if exp_kwh else 0.0
    if rules.netting:
        netted = min(imp_kwh, exp_kwh)
        netting_credit = netted * avg_import
        export_revenue = (exp_kwh - netted) * avg_export
    else:
        netted, netting_credit, export_revenue = 0.0, 0.0, exp_value
    energy = imp_cost - netting_credit - export_revenue
    fixed = fixed_per_day * days
    r2 = lambda x: round(x, 2)  # noqa: E731
    return {"rules": rules.id, "label": rules.label, "netting": rules.netting, "source": rules.source,
            "slots": used, "import_kwh": r2(imp_kwh), "export_kwh": r2(exp_kwh), "netted_kwh": r2(netted),
            "import_cost_eur": r2(imp_cost), "netting_credit_eur": r2(netting_credit),
            "export_revenue_eur": r2(export_revenue), "energy_cost_eur": r2(energy), "fixed_costs_eur": r2(fixed),
            "total_eur": r2(energy + fixed), "avg_import_price": round(avg_import, 4),
            "avg_export_value": round(avg_export, 4),
            "note": "Salderen benaderd met de gemiddelde afnameprijs van de periode." if rules.netting else ""}


def compare(slots: list[dict], tariff: TariffConfig, timezone: str, fixed_per_day: float, days: float,
            rule_ids: tuple[str, ...] = ("NL-2026", "NL-2027")) -> dict:
    results = [settle(slots, tariff, RULES[i], timezone, fixed_per_day, days) for i in rule_ids]
    scale = 365.0 / days if days > 0 else 0.0
    for r in results:
        r["per_year_eur"] = round(r["total_eur"] * scale, 0) if scale else None
    base = results[0]["total_eur"]
    for r in results[1:]:
        r["difference_eur"] = round(r["total_eur"] - base, 2)
        r["difference_per_year_eur"] = round(r["difference_eur"] * scale, 0) if scale else None
    return {"days": days, "scenarios": results,
            "explanation": "Zelfde gemeten verbruik en opwek, alleen de verrekenregels verschillen. Zonder salderen "
                           "is iedere zelf gebruikte kWh (batterij, warmtepomp, auto op zonne-energie) meer waard."}
