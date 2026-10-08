"""Financial analysis from measured 15-minute history (audit P1-09/P1-10).

Every result is a *ladder of explicit baselines* on the same measured consumption and prices, so the
items add up exactly to one net, auditable total — nothing is counted twice:

  B0  without PV, battery and EMS : all consumption (house + heat pump + EV) imported
  B1  with PV, battery idle       : per slot net = consumption − PV; deficit imported, surplus exported
  B2  with PV, battery on its own : the battery's own self-consumption mode (no EMS): charge from
                                    PV surplus, discharge to cover the deficit, within capacity,
                                    power and SOC limits and with round-trip losses (simulated)
  B3  actual (with EMS)           : the measured import/export

  PV value               = B0 − B1
  battery (own control)  = B1 − B2
  EMS steering           = B2 − B3
  total                  = B0 − B3   (checked: the three items always sum to the total)

Assumptions (shown with the result): consumption of the heat pump and EV is taken *as measured*,
also in the baselines, so savings from shifting them in time are not attributed separately
(conservative). Fixed costs are identical in every baseline and never part of a saving.
Indicators such as "energy bought at negative prices" are shown separately and are **already
included** in the items above; they are not added to the total.
"""

from __future__ import annotations

from ems.core.config import EMSConfig
from ems.core.models import DeviceCategory

SLOT_H = 0.25


def _battery_specs(config: EMSConfig) -> dict | None:
    devs = [d for d in config.devices if d.enabled and d.category in (DeviceCategory.BATTERY,
                                                                       DeviceCategory.HYBRID_INVERTER)
            and d.params.get("capacity_kwh")]
    if not devs:
        return None

    def p(d, key, default):
        return float(d.params.get(key, (d.params.get("sim") or {}).get(key, default)))
    return {"capacity_kwh": sum(p(d, "capacity_kwh", 0) for d in devs),
            "max_charge_kw": sum(p(d, "max_charge_w", 0) for d in devs) / 1000,
            "max_discharge_kw": sum(p(d, "max_discharge_w", 0) for d in devs) / 1000,
            "charge_eff": min(p(d, "charge_efficiency", 0.95) for d in devs),
            "discharge_eff": min(p(d, "discharge_efficiency", 0.95) for d in devs)}


def _self_consumption_battery(rows: list[dict], spec: dict, min_soc: float, max_soc: float,
                              start_soc_pct: float) -> list[tuple[float, float]]:
    """Per slot (import_kwh, export_kwh) if the battery ran its own self-consumption mode."""
    cap = spec["capacity_kwh"]
    lo, hi = cap * min_soc / 100, cap * max_soc / 100
    e = min(hi, max(lo, cap * start_soc_pct / 100))
    out = []
    for r in rows:
        net = ((r.get("house_kwh") or 0.0) + (r.get("hp_kwh") or 0.0) + (r.get("ev_kwh") or 0.0)
               - (r.get("pv_kwh") or 0.0))
        if net < 0:                                     # surplus: charge
            room = (hi - e) / spec["charge_eff"]
            c = min(-net, spec["max_charge_kw"] * SLOT_H, max(0.0, room))
            e += c * spec["charge_eff"]
            out.append((0.0, -net - c))
        else:                                           # deficit: discharge
            avail = (e - lo) * spec["discharge_eff"]
            d = min(net, spec["max_discharge_kw"] * SLOT_H, max(0.0, avail))
            e -= d / spec["discharge_eff"]
            out.append((net - d, 0.0))
    return out


def finance_summary(rows: list[dict], config: EMSConfig, fixed_per_day: float, days: float) -> dict:
    usable = [r for r in rows if r.get("import_price") is not None and r.get("import_kwh") is not None]
    expected_slots = max(1, round(days * 96)) if days else max(1, len(rows))
    if not usable:
        return {"available": False, "reason": "Geen gemeten netdata met prijzen in deze periode", "slots": 0,
                "coverage_pct": 0.0}
    spec = _battery_specs(config)
    first_soc = next((r["soc_end"] for r in usable if r.get("soc_end") is not None), None)
    b2_flows = (_self_consumption_battery(usable, spec, config.battery.min_soc, config.battery.max_soc,
                                          first_soc if first_soc is not None else config.battery.min_soc)
                if spec else None)
    b0 = b1 = b2 = b3 = 0.0
    imp = exp = pv = house = hp = ev = ch = dis = 0.0
    neg_import_kwh = neg_import_value = 0.0
    hp_paid = hp_kwh_priced = 0.0
    gross = charge_cost = 0.0
    import_cost = export_revenue = 0.0
    for k, r in enumerate(usable):
        ip, ep = r["import_price"], r.get("export_price") or 0.0
        i, e = r.get("import_kwh") or 0.0, r.get("export_kwh") or 0.0
        p, h = r.get("pv_kwh") or 0.0, r.get("house_kwh") or 0.0
        w, v = r.get("hp_kwh") or 0.0, r.get("ev_kwh") or 0.0
        c, d = r.get("battery_charge_kwh") or 0.0, r.get("battery_discharge_kwh") or 0.0
        load = h + w + v
        net = load - p
        b0 += load * ip
        b1 += net * ip if net > 0 else net * ep
        if b2_flows is not None:
            bi, be = b2_flows[k]
            b2 += bi * ip - be * ep
        b3 += i * ip - e * ep
        import_cost += i * ip
        export_revenue += e * ep
        imp, exp, pv, house, hp, ev, ch, dis = imp + i, exp + e, pv + p, house + h, hp + w, ev + v, ch + c, dis + d
        if r.get("spot") is not None and r["spot"] < 0:
            neg_import_kwh += i
            neg_import_value += -i * ip
        hp_paid += w * ip
        hp_kwh_priced += w
        surplus = p - load
        gross += d * (ip if net > 0 else ep)
        charge_cost += c * (ep if surplus > 0 else ip)
    if b2_flows is None:
        b2 = b1                                         # no battery: nothing to attribute
    avg_import = sum(r["import_price"] for r in usable) / len(usable)
    fixed = fixed_per_day * days
    wear = (ch + dis) / 2 * config.battery.degradation_cost_per_kwh
    items = {"pv_eur": b0 - b1, "battery_own_control_eur": b1 - b2, "ems_steering_eur": b2 - b3}
    total_saving = b0 - b3
    reconciled = abs(sum(items.values()) - total_saving) < 1e-6
    coverage = 100 * len(usable) / expected_slots
    r2 = lambda x: round(x, 2)  # noqa: E731
    return {
        "available": True, "kind": "actual", "slots": len(usable), "expected_slots": expected_slots,
        "coverage_pct": r2(min(100.0, coverage)), "reliable": coverage >= 90,
        "energy": {"import_kwh": r2(imp), "export_kwh": r2(exp), "pv_kwh": r2(pv), "house_kwh": r2(house),
                   "heat_pump_kwh": r2(hp), "ev_kwh": r2(ev), "battery_charge_kwh": r2(ch),
                   "battery_discharge_kwh": r2(dis)},
        "costs": {"energy_cost_eur": r2(b3), "fixed_costs_eur": r2(fixed), "total_eur": r2(b3 + fixed),
                  "import_cost_eur": r2(import_cost), "export_revenue_eur": r2(export_revenue)},
        "baselines": [
            {"id": "B0", "label": "Zonder zonnepanelen, batterij en EMS", "energy_cost_eur": r2(b0),
             "definition": "Al het verbruik (huis, warmtepomp, auto) uit het net, tegen de prijs van dat kwartier."},
            {"id": "B1", "label": "Met zonnepanelen, batterij ongebruikt", "energy_cost_eur": r2(b1),
             "definition": "Per kwartier verbruik min opwek; tekort afgenomen, overschot teruggeleverd."},
            {"id": "B2", "label": "Met zonnepanelen, batterij op eigen regeling (zonder EMS)",
             "energy_cost_eur": r2(b2),
             "definition": ("Gesimuleerd: batterij laadt alleen uit zonne-overschot en ontlaadt bij tekort, binnen "
                            "capaciteit, vermogen, SOC-grenzen en rendement.") if spec else
                           "Geen batterij geconfigureerd: gelijk aan B1."},
            {"id": "B3", "label": "Werkelijk (met EMS)", "energy_cost_eur": r2(b3),
             "definition": "Gemeten afname en teruglevering."}],
        "savings": {**{k: r2(v) for k, v in items.items()}, "total_eur": r2(total_saving),
                    "reconciled": reconciled},
        "comparison": {"with_ems_eur": r2(b3 + fixed), "without_ems_eur": r2(b2 + fixed),
                       "saved_eur": r2(b2 - b3), "without_pv_and_battery_eur": r2(b0 + fixed),
                       "note": "Zonder EMS = zelfde verbruik en opwek, batterij op haar eigen zelfconsumptieregeling"},
        "assumptions": [
            "Verbruik van warmtepomp en auto zoals gemeten, ook in de vergelijkingen: besparing door verschuiven "
            "in de tijd wordt niet apart toegekend (voorzichtige schatting).",
            "Vaste kosten zijn in alle situaties gelijk en tellen nooit mee als besparing.",
            "Prijzen per kwartier uit de tariefinstellingen; geen officiële jaarafrekening."],
        "indicators": {
            "note": "Ter informatie; zit al in de posten hierboven en wordt niet opgeteld.",
            "negative_price_import_kwh": r2(neg_import_kwh), "negative_price_earned_eur": r2(neg_import_value),
            "heat_pump_avg_price": None if hp_kwh_priced < 0.01 else round(hp_paid / hp_kwh_priced, 4),
            "average_import_price": round(avg_import, 4)},
        "battery_trading": {"gross_value_eur": r2(gross), "charge_cost_eur": r2(charge_cost),
                            "energy_loss_kwh": r2(max(0.0, ch - dis)), "estimated_wear_eur": r2(wear),
                            "net_eur": r2(gross - charge_cost - wear),
                            "equivalent_full_cycles": None},
    }
