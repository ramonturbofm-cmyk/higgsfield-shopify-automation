"""TariffEngine — ACTUAL_IMPORT_PRICE / ACTUAL_EXPORT_PRICE from the user's contract.

Dynamic contract (per kWh):
  import = (spot + import_markup + energy_tax + other + transaction_fee) x (1 + vat)
  export = (spot + export_markup - export_fee) x (1 + vat if export_vat)
Fixed contract:   import = fixed_import_price
Variable/ToU:     import = time-of-use price for that local time (incl. everything)
Netting (salderen): the marginal export price equals the import price while yearly
                    export stays below import — the user enables it explicitly.
Fixed monthly/daily costs are never marginal; they only appear in financial totals.
No supplier is hard-coded: every number comes from TariffConfig.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from ems.core.config import ContractType, TariffConfig
from ems.tariffs.taxes import energy_tax_rate

SpotLookup = Callable[[datetime], float | None]


@dataclass(frozen=True)
class PriceBreakdown:
    timestamp: str
    spot: float | None
    import_price: float | None
    export_price: float | None
    import_parts: dict
    export_parts: dict
    source: str

    def to_dict(self) -> dict:
        return asdict(self)


def _parse(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h) % 24, int(m))


class TariffEngine:
    def __init__(self, tariff: TariffConfig, spot: SpotLookup, timezone: str = "Europe/Amsterdam") -> None:
        self.tariff = tariff
        self.spot = spot
        self.tz = ZoneInfo(timezone)

    @property
    def needs_spot(self) -> bool:
        t = self.tariff
        if t.contract_type == ContractType.DYNAMIC:
            return True
        return t.fixed_export_price_eur_kwh is None and not t.netting

    def _tou(self, ts: datetime) -> float | None:
        local = ts.astimezone(self.tz)
        for p in self.tariff.time_of_use:
            if local.weekday() not in p.weekdays:
                continue
            start, end = _parse(p.start), _parse(p.end)
            now = local.time()
            inside = (start <= now < end) if start < end else (now >= start or now < end)
            if inside:
                return p.price_eur_kwh
        return None

    def contract_spot(self, ts: datetime, quarter_spot: float | None = None) -> float | None:
        """Market price as the contract bills it: the quarter-hour price, or for an hourly contract
        the average of the four quarter-hours of that (local) hour. Missing quarters are skipped."""
        if self.tariff.price_resolution_min != 60:
            return self.spot(ts) if quarter_spot is None else quarter_spot
        local = ts.astimezone(self.tz)
        hour = local.replace(minute=0, second=0, microsecond=0)
        vals = [v for k in range(4) if (v := self.spot(hour + timedelta(minutes=15 * k))) is not None]
        if not vals:
            return quarter_spot
        return sum(vals) / len(vals)

    def breakdown(self, ts: datetime) -> PriceBreakdown:
        return self.breakdown_with_spot(ts, self.contract_spot(ts))

    def breakdown_with_spot(self, ts: datetime, spot: float | None) -> PriceBreakdown:
        t = self.tariff
        vat = 1 + t.vat_pct / 100
        imp: float | None
        imp_parts: dict
        if t.contract_type == ContractType.FIXED and t.fixed_import_price_eur_kwh is not None:
            imp, imp_parts, source = t.fixed_import_price_eur_kwh, {"vast_tarief": t.fixed_import_price_eur_kwh}, "fixed"
        elif t.contract_type == ContractType.VARIABLE and (tou := self._tou(ts)) is not None:
            imp, imp_parts, source = tou, {"tijdsafhankelijk": tou}, "time_of_use"
        elif t.contract_type == ContractType.VARIABLE and t.fixed_import_price_eur_kwh is not None:
            imp, imp_parts, source = t.fixed_import_price_eur_kwh, {"vast_tarief": t.fixed_import_price_eur_kwh}, "fixed"
        elif spot is None:
            imp, imp_parts, source = None, {}, "missing"
        else:
            tax = self.energy_tax(ts)
            excl = spot + t.import_markup_eur_kwh + tax + t.import_other_eur_kwh + t.transaction_fee_eur_kwh
            imp = excl * vat
            imp_parts = {"marktprijs": spot, "inkoopopslag": t.import_markup_eur_kwh,
                         "energiebelasting": tax, "overig": t.import_other_eur_kwh,
                         "transactiekosten": t.transaction_fee_eur_kwh, "btw": excl * (vat - 1)}
            source = "dynamic"

        exp: float | None
        if t.netting and imp is not None:
            exp, exp_parts = imp, {"salderen": imp}
        elif t.fixed_export_price_eur_kwh is not None:
            exp, exp_parts = t.fixed_export_price_eur_kwh, {"vaste_vergoeding": t.fixed_export_price_eur_kwh}
        elif spot is None:
            exp, exp_parts = None, {}
        else:
            excl = spot + t.export_markup_eur_kwh - t.export_fee_eur_kwh
            factor = vat if t.export_vat else 1.0
            exp = excl * factor
            exp_parts = {"marktprijs": spot, "terugleveropslag": t.export_markup_eur_kwh,
                         "terugleverkosten": -t.export_fee_eur_kwh, "btw": excl * (factor - 1)}
        r = lambda v: None if v is None else round(v, 6)  # noqa: E731
        return PriceBreakdown(ts.isoformat(), r(spot), r(imp), r(exp),
                              {k: round(v, 6) for k, v in imp_parts.items()},
                              {k: round(v, 6) for k, v in exp_parts.items()}, source)

    def energy_tax(self, ts: datetime) -> float:
        """EUR/kWh excl. VAT valid at ``ts`` (versioned table per year, or the manual value)."""
        t = self.tariff
        if t.energy_tax_mode == "table":
            rate = energy_tax_rate(t.tax_country, ts.astimezone(self.tz).date())
            if rate is not None:
                return rate.energy_tax_eur_kwh
        return t.energy_tax_eur_kwh

    def get_import_price(self, ts: datetime) -> float | None:
        return self.breakdown(ts).import_price

    def get_export_price(self, ts: datetime) -> float | None:
        return self.breakdown(ts).export_price

    def fixed_costs_per_day(self) -> float:
        t = self.tariff
        monthly = t.fixed_monthly_eur + t.grid_monthly_eur + t.service_monthly_eur
        return t.fixed_daily_eur + monthly * 12 / 365 - t.energy_tax_credit_yearly_eur / 365
