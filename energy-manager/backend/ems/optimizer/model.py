"""Mixed-integer linear optimization of the whole site over a 24-48 h horizon.

Per slot t (length dt hours), powers in W, energy in kWh:
  balance   pv_use + g_imp + b_dis = load + b_ch + sum(ev) + hp + g_exp
  battery   soc_t = soc_{t-1} + eta_c*b_ch*dt - b_dis*dt/eta_d,  reserve <= soc <= max
            b_ch <= Pc*y_b,  b_dis <= Pd*(1-y_b)                  (no simultaneous charge/discharge)
            b_grid >= min(b_ch, g_imp)  via binary w: b_grid >= b_ch - Pc*w, b_grid >= g_imp - Imax*(1-w)
            (PV serves the house first, so while importing all charging beyond PV surplus is grid energy)
  grid      g_imp <= Imax*y_g, g_exp <= Emax*(1-y_g), g_exp <= export_limit_t
  house     T_t = T_{t-1} + dt/C*(COP_t*hp - H*(T_{t-1}-Tout_t) + gains_t)
            T_t <= Tmax, T_t + s_min >= Tmin, T_t + s_comf >= Tcomfort
  EV        ev <= Pmax*avail*z, ev >= Pmin*z, sum(ev*dt*eff) + short >= need before deadline
Objective (EUR):
  sum dt*(p_imp*g_imp - p_exp*g_exp) + wear*(b_ch+b_dis)*dt/2 + spread*b_grid*dt
  + comfort/peak/EV-shortfall penalties - terminal value of stored energy
The optimizer never knows which brands are involved: it only sees these numbers.
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

log = logging.getLogger(__name__)

WEAR_MULTIPLIER = {"battery_saver": 2.0, "balanced": 1.0, "profit": 0.7, "aggressive": 0.4}


@dataclass
class BatteryModel:
    capacity_kwh: float
    soc_kwh: float
    min_kwh: float
    max_kwh: float
    max_charge_w: float
    max_discharge_w: float
    eta_charge: float = 0.95
    eta_discharge: float = 0.95
    degradation_eur_kwh: float = 0.04
    min_spread_eur_kwh: float = 0.0
    max_cycles_per_day: float = 2.0
    grid_charging: bool = True
    grid_export: bool = True


@dataclass
class HeatPumpModel:
    heat_loss_w_per_k: float
    capacity_kwh_per_k: float
    indoor_c: float
    min_c: float
    comfort_c: float
    max_c: float
    max_electric_w: float
    cop: list[float]
    outdoor_c: list[float]
    gains_w: list[float]
    comfort_weight_eur_kh: float = 0.05


@dataclass
class EVModel:
    device_id: str
    available: list[bool]
    max_w: float
    min_w: float
    need_kwh: float
    deadline_index: int        # slots before this index count towards the need
    efficiency: float = 0.9
    pv_only_cap_w: list[float] | None = None


@dataclass
class OptimizerInput:
    slots: list[datetime]
    dt_h: float
    import_price: list[float]
    export_price: list[float]
    pv_w: list[float]
    load_w: list[float]
    max_import_w: float
    max_export_w: float
    export_limit_w: list[float] | None = None
    curtailable: bool = True
    peak_limit_w: float | None = None
    battery: BatteryModel | None = None
    heat_pump: HeatPumpModel | None = None
    evs: list[EVModel] = field(default_factory=list)
    price_estimated: list[bool] | None = None
    wear_mode: str = "balanced"
    time_limit_s: float = 20.0


@dataclass
class Plan:
    status: str
    message: str
    slots: list[dict]
    expected_cost: float | None
    baseline_cost: float | None
    solve_time_s: float
    objective: float | None = None
    inputs_summary: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("optimal", "feasible")

    @property
    def expected_benefit(self) -> float | None:
        if self.expected_cost is None or self.baseline_cost is None:
            return None
        return self.baseline_cost - self.expected_cost

    def to_dict(self) -> dict:
        return {"status": self.status, "message": self.message, "expected_cost": self.expected_cost,
                "baseline_cost": self.baseline_cost, "expected_benefit": self.expected_benefit,
                "solve_time_s": round(self.solve_time_s, 3), "objective": self.objective,
                "inputs": self.inputs_summary, "slots": self.slots}


class _Model:
    """Tiny helper to build sparse MILPs without a modelling library."""

    def __init__(self) -> None:
        self.lb: list[float] = []
        self.ub: list[float] = []
        self.c: list[float] = []
        self.integer: list[int] = []
        self.rows: list[int] = []
        self.cols: list[int] = []
        self.vals: list[float] = []
        self.clb: list[float] = []
        self.cub: list[float] = []

    def var(self, n: int, lb=0.0, ub=np.inf, cost=0.0, integer=False) -> np.ndarray:
        start = len(self.lb)
        for i in range(n):
            self.lb.append(lb[i] if isinstance(lb, (list, np.ndarray)) else lb)
            self.ub.append(ub[i] if isinstance(ub, (list, np.ndarray)) else ub)
            self.c.append(cost[i] if isinstance(cost, (list, np.ndarray)) else cost)
            self.integer.append(1 if integer else 0)
        return np.arange(start, start + n)

    def add_cost(self, idx: int, value: float) -> None:
        self.c[idx] += value

    def con(self, terms: list[tuple[int, float]], lo: float = -np.inf, hi: float = np.inf) -> None:
        r = len(self.clb)
        for col, v in terms:
            if v != 0:
                self.rows.append(r)
                self.cols.append(int(col))
                self.vals.append(float(v))
        self.clb.append(lo)
        self.cub.append(hi)

    def solve(self, time_limit: float):
        n = len(self.lb)
        A = coo_matrix((self.vals, (self.rows, self.cols)), shape=(len(self.clb), n)).tocsr()
        return milp(np.array(self.c), constraints=LinearConstraint(A, self.clb, self.cub),
                    integrality=np.array(self.integer), bounds=Bounds(self.lb, self.ub),
                    options={"time_limit": time_limit, "mip_rel_gap": 1e-3, "disp": False})


def heat_pump_reference_w(hp: HeatPumpModel, t: int) -> float:
    """Electric power needed to just hold comfort temperature (the 'no EMS' behaviour)."""
    q = hp.heat_loss_w_per_k * (hp.comfort_c - hp.outdoor_c[t]) - hp.gains_w[t]
    return max(0.0, min(hp.max_electric_w, q / max(1.0, hp.cop[t])))


def _baseline_cost(inp: OptimizerInput) -> float:
    """Cost without EMS: battery in its own self-consumption mode, heat pump holding
    comfort, EVs charging at full power on arrival, no curtailment."""
    dt = inp.dt_h
    b = inp.battery
    soc = b.soc_kwh if b else 0.0
    ev_left = {e.device_id: e.need_kwh for e in inp.evs}
    cost = 0.0
    for t in range(len(inp.slots)):
        demand = inp.load_w[t]
        if inp.heat_pump:
            demand += heat_pump_reference_w(inp.heat_pump, t)
        for e in inp.evs:
            if e.available[t] and ev_left[e.device_id] > 0:
                p = min(e.max_w, ev_left[e.device_id] / e.efficiency / dt * 1000)
                ev_left[e.device_id] -= p * dt / 1000 * e.efficiency
                demand += p
        net = demand - inp.pv_w[t]
        if b:
            if net < 0:   # surplus -> charge
                p = min(-net, b.max_charge_w, (b.max_kwh - soc) / b.eta_charge / dt * 1000)
                soc += p * dt / 1000 * b.eta_charge
                net += p
            else:
                p = min(net, b.max_discharge_w, max(0.0, soc - b.min_kwh) * b.eta_discharge / dt * 1000)
                soc -= p * dt / 1000 / b.eta_discharge
                net -= p
        cost += (inp.import_price[t] * net if net > 0 else inp.export_price[t] * net) * dt / 1000
    return cost


def solve(inp: OptimizerInput) -> Plan:
    t0 = time.monotonic()
    T, dt = len(inp.slots), inp.dt_h
    if T == 0:
        return Plan("no_data", "geen tijdslots", [], None, None, 0.0)
    m = _Model()
    kw = dt / 1000.0                                  # W x kw = kWh in one slot
    exp_lim = inp.export_limit_w or [inp.max_export_w] * T
    b = inp.battery
    hp = inp.heat_pump

    gi = m.var(T, 0, inp.max_import_w, [p * kw for p in inp.import_price])
    ge = m.var(T, 0, [min(inp.max_export_w, max(0.0, e)) for e in exp_lim], [-p * kw for p in inp.export_price])
    pv_lb = [0.0 if inp.curtailable else p for p in inp.pv_w]
    pvu = m.var(T, pv_lb, inp.pv_w, -1e-5)            # tiny preference for using PV
    yg = m.var(T, 0, 1, 0, integer=True)

    wear = 0.0
    if b:
        wear = b.degradation_eur_kwh * WEAR_MULTIPLIER.get(inp.wear_mode, 1.0)
        bc = m.var(T, 0, b.max_charge_w, wear / 2 * kw)
        bd = m.var(T, 0, b.max_discharge_w, wear / 2 * kw)
        soc = m.var(T, b.min_kwh, b.max_kwh)
        bg = m.var(T, 0, b.max_charge_w if b.grid_charging else 0.0, b.min_spread_eur_kwh * kw)
        yb = m.var(T, 0, 1, 0, integer=True)
        wg = m.var(T, 0, 1, 0, integer=True)
    hp_el = tin = s_comf = s_min = None
    if hp:
        hp_el = m.var(T, 0, hp.max_electric_w)
        tin = m.var(T, -50, hp.max_c)
        s_comf = m.var(T, 0, np.inf, hp.comfort_weight_eur_kh * dt)
        s_min = m.var(T, 0, np.inf, 5.0 * dt)
    ev_vars = []
    for e in inp.evs:
        cap = [e.max_w if e.available[t] else 0.0 for t in range(T)]
        if e.pv_only_cap_w is not None:
            cap = [min(c, max(0.0, e.pv_only_cap_w[t])) for t, c in enumerate(cap)]
        p = m.var(T, 0, cap, 1e-6)
        z = m.var(T, 0, [1 if c >= e.min_w else 0 for c in cap], 0, integer=True)
        short = m.var(1, 0, np.inf, 2.0)[0]           # EUR/kWh not delivered by the deadline
        ev_vars.append((e, p, z, short))
    peak = None
    if inp.peak_limit_w is not None:
        peak = m.var(T, 0, np.inf, 1.0 * kw)          # 1 EUR per kWh above the peak limit

    for t in range(T):
        terms = [(pvu[t], 1), (gi[t], 1), (ge[t], -1)]
        if b:
            terms += [(bd[t], 1), (bc[t], -1)]
        if hp:
            terms.append((hp_el[t], -1))
        for _, p, _, _ in ev_vars:
            terms.append((p[t], -1))
        m.con(terms, inp.load_w[t], inp.load_w[t])
        m.con([(gi[t], 1), (yg[t], -inp.max_import_w)], hi=0)
        m.con([(ge[t], 1), (yg[t], inp.max_export_w)], hi=inp.max_export_w)
        if peak is not None:
            m.con([(gi[t], 1), (peak[t], -1)], hi=inp.peak_limit_w)
        if b:
            prev = [] if t == 0 else [(soc[t - 1], -1)]
            rhs = b.soc_kwh if t == 0 else 0.0
            m.con([(soc[t], 1), *prev, (bc[t], -b.eta_charge * kw), (bd[t], kw / b.eta_discharge)], rhs, rhs)
            m.con([(bc[t], 1), (yb[t], -b.max_charge_w)], hi=0)
            m.con([(bd[t], 1), (yb[t], b.max_discharge_w)], hi=b.max_discharge_w)
            m.con([(bg[t], 1), (bc[t], -1), (wg[t], b.max_charge_w)], lo=0)
            m.con([(bg[t], 1), (gi[t], -1), (wg[t], -inp.max_import_w)], lo=-inp.max_import_w)
            if not b.grid_export:
                m.con([(ge[t], 1), (pvu[t], -1)], hi=0)
        if hp:
            a = dt / (1000.0 * hp.capacity_kwh_per_k)
            k_prev = 1 - a * hp.heat_loss_w_per_k
            rhs = a * (hp.gains_w[t] + hp.heat_loss_w_per_k * hp.outdoor_c[t])
            if t == 0:
                rhs += k_prev * hp.indoor_c
                m.con([(tin[t], 1), (hp_el[t], -a * hp.cop[t])], rhs, rhs)
            else:
                m.con([(tin[t], 1), (tin[t - 1], -k_prev), (hp_el[t], -a * hp.cop[t])], rhs, rhs)
            m.con([(tin[t], 1), (s_min[t], 1)], lo=hp.min_c)
            m.con([(tin[t], 1), (s_comf[t], 1)], lo=hp.comfort_c)
        for e, p, z, _ in ev_vars:
            m.con([(p[t], 1), (z[t], -e.max_w)], hi=0)
            m.con([(p[t], 1), (z[t], -e.min_w)], lo=0)
    if b:
        days = max(T * dt / 24.0, 1.0)   # a short horizon still gets a full day of cycle budget
        m.con([(bd[t], kw) for t in range(T)], hi=b.max_cycles_per_day * b.capacity_kwh * days)
        # Value of energy left at the end: what it will likely save later.
        terminal = max(0.0, statistics.median(inp.import_price) * b.eta_discharge - wear)
        m.add_cost(soc[T - 1], -terminal)
    for e, p, _, short in ev_vars:
        dl = max(0, min(T, e.deadline_index))
        if e.need_kwh > 0 and dl > 0:
            m.con([(p[t], kw * e.efficiency) for t in range(dl)] + [(short, 1)], lo=e.need_kwh)

    res = m.solve(inp.time_limit_s)
    elapsed = time.monotonic() - t0
    summary = {"slots": T, "dt_h": dt, "pv_kwh": round(sum(inp.pv_w) * kw, 2),
               "load_kwh": round(sum(inp.load_w) * kw, 2), "battery": b is not None,
               "heat_pump": hp is not None, "evs": [e.device_id for e in inp.evs]}
    if res.x is None:
        log.warning("optimizer found no solution", extra={"status": res.status, "solver_message": res.message})
        return Plan("infeasible" if res.status == 2 else "failed", str(res.message), [], None,
                    _baseline_cost(inp), elapsed, None, summary)
    x = [float(v) + 0.0 for v in res.x]               # plain floats (JSON), no -0.0
    slots = []
    energy_cost = 0.0
    for t in range(T):
        g_i, g_e = x[gi[t]], x[ge[t]]
        energy_cost += (inp.import_price[t] * g_i - inp.export_price[t] * g_e) * kw
        row = {
            "start": inp.slots[t].isoformat(),
            "import_price": round(inp.import_price[t], 5), "export_price": round(inp.export_price[t], 5),
            "price_estimated": bool(inp.price_estimated[t]) if inp.price_estimated else False,
            "pv_forecast_w": round(inp.pv_w[t], 1), "pv_w": round(x[pvu[t]], 1),
            "curtail_w": round(max(0.0, inp.pv_w[t] - x[pvu[t]]), 1),
            "load_w": round(inp.load_w[t], 1), "grid_w": round(g_i - g_e, 1),
            "battery_w": round(x[bc[t]] - x[bd[t]], 1) if b else 0.0,
            "battery_grid_charge_w": round(x[bg[t]], 1) if b else 0.0,
            "soc_kwh": round(x[soc[t]], 3) if b else None,
            "soc_pct": round(100 * x[soc[t]] / b.capacity_kwh, 1) if b else None,
            "hp_w": round(x[hp_el[t]], 1) if hp else None,
            "hp_reference_w": round(heat_pump_reference_w(hp, t), 1) if hp else None,
            "indoor_c": round(x[tin[t]], 2) if hp else None,
            "ev_w": {e.device_id: round(x[p[t]], 1) + 0.0 for e, p, _, _ in ev_vars},
        }
        slots.append(row)
    wear_cost = wear * sum(x[bc[t]] + x[bd[t]] for t in range(T)) * kw / 2 if b else 0.0
    summary.update({"wear_cost_eur": round(wear_cost, 3),
                    "ev_shortfall_kwh": {e.device_id: round(x[s], 3) for e, _, _, s in ev_vars}})
    status = "optimal" if res.status == 0 else "feasible"
    return Plan(status, str(res.message), slots, round(energy_cost + wear_cost, 4), round(_baseline_cost(inp), 4),
                elapsed, float(res.fun), summary)
