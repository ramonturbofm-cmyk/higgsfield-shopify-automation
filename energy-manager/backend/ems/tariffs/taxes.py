"""Versioned energy tax per country and year, with consumption brackets (electricity, EUR/kWh excl. VAT).

Historic calculations use the rate of *their* year. The tax is levied per connection on the
yearly taxable consumption, in brackets ("schijven"): the first kWh of a year fall in bracket 1,
consumption above its upper bound in bracket 2, and so on.

Sources and status per value (``verify=True`` = not checked against the final official table;
the UI shows this). NL 2026 first bracket: €0.09161/kWh excl. VAT (Belastingdienst tabel
milieubelastingen 2026: €0.1108481 incl. 21% VAT; audit P1-01). NL 2026 higher brackets: taken from
a published overview of the same table (incl.-VAT values / 1.21), marked ``verify``. The bracket above
10 million kWh is not filled in: such consumers must enter their rate manually. Users can always
override with a manual value (Energiecontract → Energiebelasting: handmatig).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# NL electricity bracket upper bounds (kWh per year per connection).
NL_BOUNDS = (10_000, 50_000, 10_000_000, None)


@dataclass(frozen=True)
class TaxBracket:
    upto_kwh: float | None          # upper bound of this bracket (None = no upper bound)
    rate_eur_kwh: float | None      # None = not in the table, enter manually
    verify: bool = False


@dataclass(frozen=True)
class TaxRate:
    country: str
    year: int
    energy_tax_eur_kwh: float       # first bracket (households stay below 10,000 kWh)
    source: str
    verify: bool = False
    brackets: tuple[TaxBracket, ...] = field(default=())

    def bracket_table(self) -> tuple[TaxBracket, ...]:
        return self.brackets or (TaxBracket(None, self.energy_tax_eur_kwh, self.verify),)


def _nl(year: int, first: float, source: str, higher: tuple[float | None, ...] = (), verify_higher: bool = True,
        verify: bool = False) -> TaxRate:
    rates = (first, *higher) + (None,) * (len(NL_BOUNDS) - 1 - len(higher))
    brackets = tuple(TaxBracket(b, r, verify if i == 0 else (verify_higher or r is None))
                     for i, (b, r) in enumerate(zip(NL_BOUNDS, rates, strict=True)))
    return TaxRate("NL", year, first, source, verify, brackets)


TAX_TABLE: dict[tuple[str, int], TaxRate] = {(r.country, r.year): r for r in (
    _nl(2023, 0.12599, "Belastingdienst, tarieven milieubelastingen 2023 (eerste schijf)"),
    _nl(2024, 0.10880, "Belastingdienst, tarieven milieubelastingen 2024 (eerste schijf)"),
    _nl(2025, 0.10154, "Belastingdienst, tarieven milieubelastingen 2025 (eerste schijf)"),
    _nl(2026, 0.09161, "Belastingdienst, tabel tarieven milieubelastingen 2026: € 0,1108481 incl. btw "
                       "= € 0,09161 excl. btw (eerste schijf, 0–10.000 kWh)",
        higher=(round(0.0807191 / 1.21, 5), round(0.0451935 / 1.21, 5))),
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
    return TaxRate(country, day.year, r.energy_tax_eur_kwh, f"{r.source} (geen tarief voor {day.year} bekend)", True,
                   tuple(TaxBracket(b.upto_kwh, b.rate_eur_kwh, True) for b in r.brackets))


def tax_on_consumption(rate: TaxRate, already_kwh: float, kwh: float) -> tuple[float, list[str]]:
    """Energy tax (EUR excl. VAT) on ``kwh`` taxable kWh when ``already_kwh`` of the year are already
    taxed. Splits over brackets. Returns (amount, warnings)."""
    amount, warnings = 0.0, []
    if kwh <= 0:
        return 0.0, warnings
    lower = 0.0
    pos, left = max(0.0, already_kwh), kwh
    for b in rate.bracket_table():
        upper = float("inf") if b.upto_kwh is None else float(b.upto_kwh)
        if pos < upper and left > 0:
            part = min(left, upper - pos)
            if b.rate_eur_kwh is None:
                warnings.append(f"verbruik boven {lower:,.0f} kWh/jaar: tarief voor deze schijf niet in de tabel "
                                "— stel de energiebelasting handmatig in".replace(",", "."))
                fallback = rate.energy_tax_eur_kwh
                amount += part * fallback
            else:
                if b.verify:
                    warnings.append(f"schijftarief vanaf {lower:,.0f} kWh nog niet gecontroleerd tegen de "
                                    "officiële tabel".replace(",", "."))
                amount += part * b.rate_eur_kwh
            pos += part
            left -= part
        lower = upper
    return amount, sorted(set(warnings))
