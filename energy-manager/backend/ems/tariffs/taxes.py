"""Versioned energy tax per country and year (electricity, first consumption bracket, EUR/kWh excl. VAT).

Historic calculations use the rate of *their* year. Values come from the Dutch tax authority's
published tables (belastingdienst.nl, "Tabellen tarieven milieubelastingen"); the most recent
year is marked ``verify`` until checked against the final publication. Users can always
override with a manual value (Energiecontract → Energiebelasting: handmatig).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TaxRate:
    country: str
    year: int
    energy_tax_eur_kwh: float
    source: str
    verify: bool = False


TAX_TABLE: dict[tuple[str, int], TaxRate] = {(r.country, r.year): r for r in (
    TaxRate("NL", 2023, 0.12599, "Belastingdienst, tarieven milieubelastingen 2023"),
    TaxRate("NL", 2024, 0.10880, "Belastingdienst, tarieven milieubelastingen 2024"),
    TaxRate("NL", 2025, 0.10154, "Belastingdienst, tarieven milieubelastingen 2025"),
    TaxRate("NL", 2026, 0.09157, "Belastingplan 2026 — controleer op belastingdienst.nl", verify=True),
)}


def energy_tax_rate(country: str, day: date) -> TaxRate | None:
    """Rate for that year; for a year beyond the table the latest known year is used (marked verify)."""
    rate = TAX_TABLE.get((country, day.year))
    if rate is not None:
        return rate
    known = sorted(y for c, y in TAX_TABLE if c == country)
    if not known:
        return None
    year = max((y for y in known if y <= day.year), default=known[0])
    r = TAX_TABLE[(country, year)]
    return TaxRate(country, day.year, r.energy_tax_eur_kwh, f"{r.source} (geen tarief voor {day.year} bekend)", True)
