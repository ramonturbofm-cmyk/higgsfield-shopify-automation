"""Structured reasons, heat-pump action and time aggregation for optimizer plans (audit P1-14..P1-20).

Reasons come from the *solved* plan of one optimizer run (same run id): the decision values, the
input prices and the constraints that are active (SOC at a limit, grid or export limit reached).
Each reason has a stable ``code`` (for UI/tests/translation), Dutch ``text`` and the numbers used.
Nothing is inferred in the browser any more.

Look-ahead windows are defined by timestamps (e.g. "next 12 hours" = slots whose start lies within
12 h), never by slot counts, so 5-, 15- and 60-minute plans are explained the same way.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOOKAHEAD = timedelta(hours=12)
EPS_W = 100.0


def _eur(v: float | None) -> str:
    return "?" if v is None else f"€ {v:.3f}".replace(".", ",")


def hp_action(hp_w: float | None, ref_w: float | None) -> tuple[str | None, str | None]:
    """Heat-pump mode for a planned electric power versus the power it would draw on its own.
    The single rule used by the plan output *and* the controller."""
    if hp_w is None or ref_w is None:
        return None, None
    if hp_w > ref_w * 1.3 + 200:
        return "boost", "hp_preheat_cheap"
    if ref_w > 300 and hp_w < ref_w * 0.5:
        return "eco", "hp_reduce_expensive"
    return "normal", "hp_normal"


def slot_reasons(rows: list[dict], i: int, *, soc_min_kwh: float | None, soc_max_kwh: float | None,
                 break_even=None, max_import_w: float | None = None, export_limit_w: float | None = None,
                 peak_limit_w: float | None = None) -> list[dict]:
    """Reasons for slot ``i`` of a solved plan. ``break_even(price) -> sell price`` (battery)."""
    s = rows[i]
    start = datetime.fromisoformat(s["start"])
    ahead = [r for r in rows[i + 1:] if datetime.fromisoformat(r["start"]) - start <= LOOKAHEAD
             and r.get("import_price") is not None]
    known_ahead = [r for r in ahead if not r.get("price_estimated")]
    out: list[dict] = []

    def add(code: str, text: str, **data) -> None:
        out.append({"code": code, "text": text, "data": data})

    hi = max(ahead, key=lambda r: r["import_price"]) if ahead else None
    lo = min(ahead, key=lambda r: r["import_price"]) if ahead else None
    bw, grid_charge = s.get("battery_w") or 0.0, s.get("battery_grid_charge_w") or 0.0
    if s.get("soc_pct") is not None:
        if bw > EPS_W and grid_charge > EPS_W:
            be = break_even(s["import_price"]) if break_even else None
            add("battery_charge_grid",
                f"Batterij laadt uit het net bij {_eur(s['import_price'])}/kWh"
                + (f"; later tot {_eur(hi['import_price'])}/kWh (om {hi['start'][11:16]})" if hi else "")
                + (f"; break-even na verlies en slijtage {_eur(be)}/kWh" if be is not None else ""),
                import_price=s["import_price"], later_max_price=hi["import_price"] if hi else None,
                break_even_price=be)
        elif bw > EPS_W:
            add("battery_charge_pv", f"Batterij laadt met zonne-overschot: opslaan levert meer op dan terugleveren "
                                     f"voor {_eur(s['export_price'])}/kWh", export_price=s["export_price"])
        elif bw < -EPS_W:
            add("battery_discharge",
                f"Batterij ontlaadt bij {_eur(s['import_price'])}/kWh"
                + (f"; goedkoopste komende 12 uur {_eur(lo['import_price'])}/kWh" if lo else ""),
                import_price=s["import_price"], later_min_price=lo["import_price"] if lo else None)
            if (s.get("grid_w") or 0) < -EPS_W:
                add("battery_export", f"Overschot gaat naar het net voor {_eur(s['export_price'])}/kWh",
                    export_price=s["export_price"])
        else:
            soc = s.get("soc_kwh")
            if soc is not None and soc_min_kwh is not None and soc <= soc_min_kwh + 0.05:
                add("soc_at_min", "Batterij staat op de minimale laadtoestand (grens actief)", soc_kwh=soc)
            elif soc is not None and soc_max_kwh is not None and soc >= soc_max_kwh - 0.05:
                add("soc_at_max", "Batterij is vol tot de ingestelde maximale laadtoestand (grens actief)", soc_kwh=soc)
            elif break_even and hi is not None and hi["import_price"] < break_even(s["import_price"]):
                add("spread_below_break_even",
                    f"Geen batterijactie: hoogste prijs komende 12 uur {_eur(hi['import_price'])}/kWh ligt onder de "
                    f"break-even {_eur(break_even(s['import_price']))}/kWh (verlies + slijtage)",
                    later_max_price=hi["import_price"], break_even_price=break_even(s["import_price"]))
            else:
                add("battery_hold", "Batterij bewaart haar lading voor een later, duurder moment")
    if (s.get("curtail_w") or 0) > EPS_W:
        if (s.get("export_price") or 0) < 0:
            add("curtail_negative_export", f"PV {s['curtail_w']:.0f} W afgeregeld: terugleveren kost geld "
                                           f"({_eur(s['export_price'])}/kWh)", export_price=s["export_price"])
        elif export_limit_w is not None and -(s.get("grid_w") or 0) >= export_limit_w - 1:
            add("curtail_export_limit", f"PV afgeregeld: terugleverlimiet {export_limit_w:.0f} W bereikt",
                export_limit_w=export_limit_w)
        else:
            add("curtail", f"PV {s['curtail_w']:.0f} W afgeregeld", curtail_w=s["curtail_w"])
    if max_import_w and (s.get("grid_w") or 0) >= max_import_w - 1:
        add("grid_import_limit", "Maximale netafname (aansluiting) bereikt", max_import_w=max_import_w)
    if peak_limit_w and (s.get("grid_w") or 0) >= peak_limit_w - 1:
        add("peak_limit", f"Piekgrens {peak_limit_w:.0f} W bereikt", peak_limit_w=peak_limit_w)
    if s.get("hp_action") in ("boost", "eco"):
        add(s["hp_action_code"], "Warmtepomp voorverwarmen: nu goedkoper verwarmen, de woning houdt de warmte vast"
            if s["hp_action"] == "boost" else "Warmtepomp zuiniger: dure periode, binnen de comfortgrenzen",
            hp_w=s.get("hp_w"), hp_reference_w=s.get("hp_reference_w"))
    if s.get("price_estimated"):
        add("price_estimated", "Prijs is een schatting (nog niet gepubliceerd)")
    if not known_ahead and not ahead:
        add("insufficient_price_info", "Onvoldoende prijsinformatie voor de komende uren")
    return out


def aggregate(rows: list[dict], minutes: int, tz: str) -> list[dict]:
    """Aggregate plan slots into ``minutes`` buckets (local time): energy summed (kWh), power and
    prices time-weighted, price min/max kept, SOC/temperature at the end of the bucket."""
    zone = ZoneInfo(tz)
    buckets: OrderedDict[datetime, list[dict]] = OrderedDict()
    for r in rows:
        local = datetime.fromisoformat(r["start"]).astimezone(zone)
        key = local.replace(minute=(local.minute // minutes) * minutes if minutes < 60 else 0, second=0, microsecond=0)
        buckets.setdefault(key, []).append(r)
    out = []
    for key, items in buckets.items():
        durs = [float(r.get("duration_min") or 15) for r in items]
        total = sum(durs)

        def wavg(k, items=items, durs=durs, total=total):
            vals = [(r.get(k), d) for r, d in zip(items, durs, strict=True) if r.get(k) is not None]
            return None if not vals else sum(v * d for v, d in vals) / sum(d for _, d in vals)

        def kwh(k, items=items, durs=durs):
            vals = [(r.get(k), d) for r, d in zip(items, durs, strict=True) if r.get(k) is not None]
            return None if not vals else sum(v * d / 60 / 1000 for v, d in vals)
        prices = [r["import_price"] for r in items if r.get("import_price") is not None]
        codes, reasons = set(), []
        for r in items:
            for x in r.get("reasons") or []:
                if x["code"] not in codes:
                    codes.add(x["code"])
                    reasons.append(x)
        last = items[-1]
        out.append({
            "start": key.isoformat(), "end": (key + timedelta(minutes=total)).isoformat(), "duration_min": total,
            "slots": len(items),
            "import_price": wavg("import_price"), "export_price": wavg("export_price"),
            "import_price_min": min(prices) if prices else None, "import_price_max": max(prices) if prices else None,
            "price_estimated": any(r.get("price_estimated") for r in items),
            **{k: wavg(k) for k in ("pv_w", "pv_forecast_w", "curtail_w", "load_w", "grid_w", "battery_w",
                                    "battery_grid_charge_w", "hp_w", "hp_reference_w")},
            **{f"{k[:-2]}_kwh": kwh(k) for k in ("pv_w", "load_w", "grid_w", "battery_w", "hp_w")},
            "soc_pct": last.get("soc_pct"), "indoor_c": last.get("indoor_c"),
            "hp_action": next((r["hp_action"] for r in items if r.get("hp_action") in ("boost", "eco")),
                              last.get("hp_action")),
            "ev_w": {d: sum(r.get("ev_w", {}).get(d, 0.0) * dd for r, dd in zip(items, durs, strict=True)) / total
                     for d in (last.get("ev_w") or {})},
            "reasons": reasons})
    return out
