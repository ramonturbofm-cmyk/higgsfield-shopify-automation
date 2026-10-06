"""Financial analysis from measured 15-minute history.

Actual    = sum(import x import price) - sum(export x export price) + fixed costs
Without EMS (counterfactual, same consumption and PV, battery not used):
            net = house + heat pump + EV - PV per slot, priced at that slot's prices
Without PV and battery: everything imported.
Battery trading = what the battery changed versus "without EMS", split into gross
value, charge cost, conversion losses, estimated wear and net result.
Prices already contain transaction fees when configured in the tariff.
"""

from __future__ import annotations

from ems.core.config import EMSConfig


def finance_summary(rows: list[dict], config: EMSConfig, fixed_per_day: float, days: float) -> dict:
    usable = [r for r in rows if r.get("import_price") is not None and r.get("import_kwh") is not None]
    if not usable:
        return {"available": False, "reason": "Geen gemeten netdata met prijzen in deze periode", "slots": 0}
    actual = without_ems = without_all = 0.0
    imp = exp = pv = house = hp = ev = ch = dis = 0.0
    neg_import_kwh = neg_import_value = 0.0
    hp_paid = hp_kwh_priced = 0.0
    gross = charge_cost = 0.0
    for r in usable:
        ip, ep = r["import_price"], r.get("export_price") or 0.0
        i, e = r.get("import_kwh") or 0.0, r.get("export_kwh") or 0.0
        p, h = r.get("pv_kwh") or 0.0, r.get("house_kwh") or 0.0
        w, v = r.get("hp_kwh") or 0.0, r.get("ev_kwh") or 0.0
        c, d = r.get("battery_charge_kwh") or 0.0, r.get("battery_discharge_kwh") or 0.0
        actual += i * ip - e * ep
        net = h + w + v - p
        without_ems += net * ip if net > 0 else net * ep
        without_all += (h + w + v) * ip
        imp, exp, pv, house, hp, ev, ch, dis = imp + i, exp + e, pv + p, house + h, hp + w, ev + v, ch + c, dis + d
        if r.get("spot") is not None and r["spot"] < 0:
            neg_import_kwh += i
            neg_import_value += -i * ip
        hp_paid += w * ip
        hp_kwh_priced += w
        # Battery: discharge avoids import (or is exported); charge costs import or forgone export.
        surplus = p - h - w - v
        gross += d * (ip if net > 0 else ep)
        charge_cost += c * (ep if surplus > 0 else ip)
    avg_import = sum(r["import_price"] for r in usable) / len(usable)
    fixed = fixed_per_day * days
    wear = (ch + dis) / 2 * config.battery.degradation_cost_per_kwh
    without_pv_battery = without_all
    pv_saving = without_all - without_ems
    battery_result = without_ems - actual
    r2 = lambda x: round(x, 2)  # noqa: E731
    return {
        "available": True, "slots": len(usable), "coverage_pct": r2(100 * len(usable) / max(1, len(rows))),
        "energy": {"import_kwh": r2(imp), "export_kwh": r2(exp), "pv_kwh": r2(pv), "house_kwh": r2(house),
                   "heat_pump_kwh": r2(hp), "ev_kwh": r2(ev), "battery_charge_kwh": r2(ch),
                   "battery_discharge_kwh": r2(dis)},
        "costs": {"energy_cost_eur": r2(actual), "fixed_costs_eur": r2(fixed), "total_eur": r2(actual + fixed),
                  "import_cost_eur": r2(sum((r.get("import_kwh") or 0) * r["import_price"] for r in usable)),
                  "export_revenue_eur": r2(sum((r.get("export_kwh") or 0) * (r.get("export_price") or 0)
                                               for r in usable))},
        "comparison": {"with_ems_eur": r2(actual + fixed), "without_ems_eur": r2(without_ems + fixed),
                       "saved_eur": r2(without_ems - actual),
                       "without_pv_and_battery_eur": r2(without_pv_battery + fixed),
                       "note": "Zonder EMS = zelfde verbruik en PV, batterij niet gebruikt"},
        "breakdown": {
            "pv_saving_eur": r2(pv_saving),
            "battery_and_control_eur": r2(battery_result),
            "negative_price_import_kwh": r2(neg_import_kwh), "negative_price_earned_eur": r2(neg_import_value),
            "heat_pump_avg_price": None if hp_kwh_priced < 0.01 else round(hp_paid / hp_kwh_priced, 4),
            "average_import_price": round(avg_import, 4),
            "heat_pump_shift_saving_eur": r2(hp_kwh_priced * avg_import - hp_paid),
        },
        "battery_trading": {"gross_value_eur": r2(gross), "charge_cost_eur": r2(charge_cost),
                            "energy_loss_kwh": r2(max(0.0, ch - dis)), "estimated_wear_eur": r2(wear),
                            "net_eur": r2(gross - charge_cost - wear),
                            "equivalent_full_cycles": None},
    }
