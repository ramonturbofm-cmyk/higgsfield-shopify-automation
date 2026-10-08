"""Settlement engine: what the measured energy costs under a contract and the country's rules.

Separate from the tariff engine (marginal prices per kWh for the optimizer). Results here are
**estimates of the bill** (``kind: "estimate"``), never an official invoice; fixed costs only appear
here, never in marginal decisions.

Rules per country and period:
* NL until 31-12-2026 — salderingsregeling: energy tax (and VAT on it) is only due on the yearly
  *net* consumption (import − export). How the supply part of netted kWh is settled depends on
  the contract (``tariff.netting_method``):
    - ``tax_only`` (typical for dynamic contracts): every kWh is bought and sold at its own
      interval price; netting only removes energy tax + VAT on the netted kWh;
    - ``import_price`` (typical for fixed/variable contracts): netted kWh are credited at the
      period's average all-in import price.
  ``auto`` picks ``tax_only`` for dynamic and ``import_price`` for other contracts.
* NL from 1-1-2027 — no netting (Wet beëindiging salderingsregeling): tax on every imported kWh,
  every exported kWh earns the contract's export value (which may be negative).

Energy tax uses the bracket table of the year of each interval (``tariffs.taxes``), counted from the
start of the period (consumption before the period is unknown; noted in the result). A period that
crosses a year boundary is split per year, each with that year's rules and rates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from ems.core.config import ContractType, TariffConfig
from ems.tariffs.engine import TariffEngine
from ems.tariffs.taxes import TaxRate, energy_tax_rate, tax_on_consumption


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


def _netting_method(tariff: TariffConfig) -> str:
    m = getattr(tariff, "netting_method", "auto")
    if m != "auto":
        return m
    return "tax_only" if tariff.contract_type == ContractType.DYNAMIC else "import_price"


def _tax_rate(tariff: TariffConfig, day: date) -> TaxRate:
    if tariff.energy_tax_mode == "table":
        r = energy_tax_rate(tariff.tax_country, day)
        if r is not None:
            return r
    return TaxRate(tariff.tax_country, day.year, tariff.energy_tax_eur_kwh, "handmatig ingesteld tarief")


def _settle_year(rows: list[dict], tariff: TariffConfig, rules: SettlementRules, tz: ZoneInfo, year: int) -> dict:
    plain = tariff.model_copy(update={"netting": False})
    engine = TariffEngine(plain, lambda _ts: None, str(tz))
    dynamic = tariff.contract_type == ContractType.DYNAMIC
    vat = 1 + tariff.vat_pct / 100
    imp_kwh = exp_kwh = supply = allin_cost = exp_value = neg_kwh = neg_cost = 0.0
    used = missing = 0
    hourly: dict[datetime, float] = {}
    if tariff.price_resolution_min == 60:            # contract bills the hourly average of the quarters
        acc: dict[datetime, list[float]] = {}
        for r in rows:
            if r.get("spot") is not None:
                h = datetime.fromtimestamp(r["slot_ts"], UTC).astimezone(tz).replace(minute=0, second=0)
                acc.setdefault(h, []).append(r["spot"])
        hourly = {h: sum(v) / len(v) for h, v in acc.items()}
    for r in rows:
        if r.get("import_kwh") is None:
            missing += 1
            continue
        ts = datetime.fromtimestamp(r["slot_ts"], UTC)
        spot = hourly.get(ts.astimezone(tz).replace(minute=0, second=0), r.get("spot")) if hourly else r.get("spot")
        b = engine.breakdown_with_spot(ts, spot)
        if b.import_price is None:
            missing += 1
            continue
        used += 1
        i, e = r.get("import_kwh") or 0.0, r.get("export_kwh") or 0.0
        imp_kwh += i
        exp_kwh += e
        allin_cost += i * b.import_price
        if dynamic:
            parts = b.import_parts
            supply += i * (parts.get("marktprijs", 0.0) + parts.get("inkoopopslag", 0.0) + parts.get("overig", 0.0)
                           + parts.get("transactiekosten", 0.0))
        ep = b.export_price or 0.0
        exp_value += e * ep
        if ep < 0 and e > 0:
            neg_kwh += e
            neg_cost += -e * ep
    method = _netting_method(tariff) if rules.netting else "none"
    netted = min(imp_kwh, exp_kwh) if rules.netting else 0.0
    rate = _tax_rate(tariff, date(year, 7, 1))
    warnings: list[str] = []
    if dynamic:
        taxable = imp_kwh - netted if rules.netting else imp_kwh
        tax, warnings = tax_on_consumption(rate, 0.0, taxable)
        tax_full, _ = tax_on_consumption(rate, 0.0, imp_kwh)
        import_cost = (supply + tax_full) * vat                              # gross, before netting
        if method == "import_price":
            avg_imp = allin_cost / imp_kwh if imp_kwh else 0.0
            avg_exp = exp_value / exp_kwh if exp_kwh else 0.0
            netting_credit = netted * avg_imp
            export_revenue = (exp_kwh - netted) * avg_exp
        else:                                                                # tax_only / no netting
            netting_credit = (tax_full - tax) * vat                          # avoided energy tax + VAT
            export_revenue = exp_value
        energy = import_cost - netting_credit - export_revenue
        tax_eur = tax
    else:
        import_cost = allin_cost
        avg_imp = allin_cost / imp_kwh if imp_kwh else 0.0
        avg_exp = exp_value / exp_kwh if exp_kwh else 0.0
        if rules.netting:
            netting_credit = netted * avg_imp
            export_revenue = (exp_kwh - netted) * avg_exp
        else:
            netting_credit, export_revenue = 0.0, exp_value
        energy = import_cost - netting_credit - export_revenue
        tax_eur = None
        taxable = imp_kwh - netted
    return {"year": year, "rules": rules.id, "netting_rules": rules.netting, "netting_method": method,
            "slots": used, "missing_slots": missing,
            "import_kwh": imp_kwh, "export_kwh": exp_kwh, "netted_kwh": netted, "taxable_kwh": taxable,
            "import_cost_eur": import_cost, "netting_credit_eur": netting_credit, "export_revenue_eur": export_revenue,
            "energy_tax_eur": tax_eur, "energy_cost_eur": energy, "negative_export_kwh": neg_kwh,
            "negative_export_cost_eur": neg_cost, "allin_cost": allin_cost, "exp_value": exp_value,
            "tax_source": rate.source, "warnings": warnings}


def settle(slots: list[dict], tariff: TariffConfig, rules: SettlementRules | None, timezone: str,
           fixed_per_day: float = 0.0, days: float = 0.0) -> dict:
    """Estimated bill for ``slots`` (15-min rows: slot_ts, spot, import_kwh, export_kwh).

    ``rules=None`` applies the rules valid on each interval's date (split per year); passing rules
    forces them on the whole period (scenario comparison)."""
    tz = ZoneInfo(timezone)
    by_year: dict[int, list[dict]] = {}
    for r in slots:
        by_year.setdefault(datetime.fromtimestamp(r["slot_ts"], UTC).astimezone(tz).year, []).append(r)
    parts = []
    for year, rows in sorted(by_year.items()):
        rr = rules or rules_for(tariff.tax_country, date(year, 7, 1)) or RULES["NL-2027"]
        if rr.netting and not tariff.netting and rules is None:
            rr = SettlementRules(f"{rr.id}-geen-saldering", f"{rr.label} — contract zonder salderen", rr.country,
                                 False, rr.valid_from, rr.valid_to, rr.source)
        parts.append(_settle_year(rows, tariff, rr, tz, year))
    total = {k: sum(p[k] for p in parts) for k in ("import_kwh", "export_kwh", "netted_kwh", "import_cost_eur",
                                                   "netting_credit_eur", "export_revenue_eur", "energy_cost_eur",
                                                   "negative_export_kwh", "negative_export_cost_eur", "allin_cost",
                                                   "exp_value", "slots", "missing_slots")}
    first = rules or (rules_for(tariff.tax_country, date(min(by_year), 7, 1)) if by_year else None) or RULES["NL-2027"]
    fixed = fixed_per_day * days
    r2 = lambda x: round(x, 2)  # noqa: E731
    warnings = sorted({w for p in parts for w in p["warnings"]})
    if total["missing_slots"]:
        warnings.append(f"{total['missing_slots']} kwartieren zonder meting of prijs niet meegeteld")
    tax_parts = [p["energy_tax_eur"] for p in parts if p["energy_tax_eur"] is not None]
    method = parts[0]["netting_method"] if parts else "none"
    note = []
    if any(p["netted_kwh"] for p in parts):
        note.append("Salderen: " + ("alleen energiebelasting + btw over gesaldeerde kWh vervalt; iedere kWh wordt "
                                    "tegen de eigen kwartierprijs gekocht en verkocht" if method == "tax_only" else
                                    "gesaldeerde kWh verrekend tegen de gemiddelde afnameprijs van de periode"))
    if tax_parts:
        note.append("Energiebelasting per schijf gerekend vanaf het begin van de periode (verbruik eerder in het "
                    "jaar is niet bekend)")
    return {"kind": "estimate", "rules": first.id if len(parts) <= 1 else "+".join(p["rules"] for p in parts),
            "label": first.label if len(parts) <= 1 else " / ".join(f"{p['year']}: {p['rules']}" for p in parts),
            "netting": any(p["netting_rules"] for p in parts),
            "netting_method": method, "source": first.source, "years": [p["year"] for p in parts],
            "slots": total["slots"], "missing_slots": total["missing_slots"],
            "import_kwh": r2(total["import_kwh"]), "export_kwh": r2(total["export_kwh"]),
            "netted_kwh": r2(total["netted_kwh"]), "import_cost_eur": r2(total["import_cost_eur"]),
            "netting_credit_eur": r2(total["netting_credit_eur"]), "export_revenue_eur": r2(total["export_revenue_eur"]),
            "energy_tax_eur": r2(sum(tax_parts)) if tax_parts else None,
            "negative_export_kwh": r2(total["negative_export_kwh"]),
            "negative_export_cost_eur": r2(total["negative_export_cost_eur"]),
            "energy_cost_eur": r2(total["energy_cost_eur"]), "fixed_costs_eur": r2(fixed),
            "total_eur": r2(total["energy_cost_eur"] + fixed),
            "avg_import_price": round(total["allin_cost"] / total["import_kwh"], 4) if total["import_kwh"] else 0.0,
            "avg_export_value": round(total["exp_value"] / total["export_kwh"], 4) if total["export_kwh"] else 0.0,
            "tax_sources": sorted({p["tax_source"] for p in parts}), "warnings": warnings, "note": "; ".join(note)}


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
    return {"days": days, "scenarios": results, "kind": "scenario",
            "explanation": "Zelfde gemeten verbruik en opwek, alleen de verrekenregels verschillen. Zonder salderen "
                           "is iedere zelf gebruikte kWh (batterij, warmtepomp, auto op zonne-energie) meer waard.",
            "limitation": "Ceteris paribus: het gedrag (batterijstrategie, verbruik) is in beide scenario's gelijk "
                          "gehouden. In werkelijkheid zou de optimizer zonder salderen anders plannen; dit is dus "
                          "geen simulatie van het toekomstige gedrag."}
