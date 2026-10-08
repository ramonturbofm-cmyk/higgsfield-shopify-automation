import { api, eur, h, kwh, num } from "../lib.js";
import { kpi } from "./common.js";

export async function render(root, [period = "today"]) {
  const seg = h("div", { class: "seg" }, [["today", "Vandaag"], ["month", "Deze maand"], ["year", "Dit jaar"], ["30d", "30 dagen"]].map(([v, l]) =>
    h("button", { class: v === period ? "on" : "", onclick: () => { location.hash = `#/finance/${v}`; } }, l)));
  const f = await api(`/finance/summary?period=${period}`);
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Financiën"), seg));
  if (!f.available) { root.append(h("div", { class: "card empty" }, f.reason), await settlementCard()); return; }
  const c = f.comparison, b = f.breakdown, t = f.battery_trading, e = f.energy;
  root.append(
    h("div", { class: "grid cols-3" }, kpi("Zonder EMS", eur(c.without_ems_eur), c.note), kpi("Met EMS", eur(c.with_ems_eur), "werkelijke kosten incl. vaste kosten"),
      kpi("Bespaard", eur(c.saved_eur), `t.o.v. zonder EMS · dekking ${num(f.coverage_pct, 0)}% van de kwartieren`)),
    h("h2", {}, "Kosten"),
    h("div", { class: "grid cols-4" }, kpi("Afname", eur(f.costs.import_cost_eur), kwh(e.import_kwh)), kpi("Teruglevering", eur(f.costs.export_revenue_eur), kwh(e.export_kwh)),
      kpi("Vaste kosten", eur(f.costs.fixed_costs_eur)), kpi("Totaal", eur(f.costs.total_eur))),
    h("h2", {}, "Waar komt de besparing vandaan?"),
    h("div", { class: "grid cols-4" }, kpi("Zonnepanelen", eur(b.pv_saving_eur), `${kwh(e.pv_kwh)} opgewekt`),
      kpi("Batterij en sturing", eur(b.battery_and_control_eur)), kpi("Negatieve prijzen", eur(b.negative_price_earned_eur), `${kwh(b.negative_price_import_kwh)} afgenomen bij negatieve prijs`),
      kpi("Warmtepomp verschuiven", eur(b.heat_pump_shift_saving_eur), b.heat_pump_avg_price ? `gem. € ${num(b.heat_pump_avg_price, 3)} vs € ${num(b.average_import_price, 3)}` : "")),
    h("h2", {}, "Batterijhandel"),
    h("div", { class: "tbl-wrap" }, h("table", {}, h("tbody", {},
      [["Bruto opbrengst", eur(t.gross_value_eur)], ["Laadkosten", eur(-t.charge_cost_eur)], ["Energieverlies", kwh(t.energy_loss_kwh)],
        ["Geschatte slijtage", eur(-t.estimated_wear_eur)], ["Netto opbrengst", eur(t.net_eur)], ["Equivalente volle cycli", num(t.equivalent_full_cycles, 2)]]
        .map(([l, v]) => h("tr", {}, h("td", {}, l), h("td", { class: "num" }, v)))))),
    h("p", { class: "muted small" }, "Transactiekosten zitten in de tariefberekening als u ze heeft ingevuld. Berekening op basis van gemeten kwartierdata."),
    await settlementCard());
}

async function settlementCard() {
  const box = h("div", {});
  const load = async (period) => {
    const s = await api(`/settlement/compare?period=${period}`);
    box.replaceChildren(h("div", { class: "tbl-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["", ...s.scenarios.map((x) => x.label + (x.rules === s.current_rules ? " (nu)" : ""))].map((t) => h("th", {}, t)))),
      h("tbody", {}, [["Afname", "import_kwh", kwh], ["Teruglevering", "export_kwh", kwh], ["Gesaldeerd", "netted_kwh", kwh],
        ["Kosten afname", "import_cost_eur", eur], ["Salderingskorting", "netting_credit_eur", eur], ["Terugleververgoeding", "export_revenue_eur", eur],
        ["Energiekosten", "energy_cost_eur", eur], ["Vaste kosten", "fixed_costs_eur", eur], ["Totaal", "total_eur", eur], ["Per jaar (geschat)", "per_year_eur", eur]]
        .map(([l, k, f]) => h("tr", {}, h("td", {}, l), s.scenarios.map((x) => h("td", { class: "num" }, f(x[k]))))),
      h("tr", {}, h("td", {}, h("b", {}, "Verschil per jaar")), s.scenarios.map((x, i) => h("td", { class: "num" },
        i === 0 ? "—" : h("b", {}, eur(x.difference_per_year_eur, 0)))))))),
      h("p", { class: "small muted" }, s.explanation, " ", s.scenarios.find((x) => x.note)?.note || ""));
  };
  const seg = h("div", { class: "seg" }, [["30d", "30 dagen"], ["90d", "90 dagen"], ["year", "Dit jaar"]].map(([v, l], i) =>
    h("button", { class: i === 0 ? "on" : "", onclick: (ev) => { [...seg.children].forEach((b) => b.classList.remove("on")); ev.target.classList.add("on"); load(v); } }, l)));
  await load("30d");
  return h("div", {}, h("div", { class: "row spread" }, h("h2", {}, "Salderen 2026 vs. 2027"), seg),
    h("div", { class: "card" }, box));
}
