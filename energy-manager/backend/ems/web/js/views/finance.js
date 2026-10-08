import { api, eur, h, kwh, num } from "../lib.js";
import { kpi } from "./common.js";

export async function render(root, [period = "today"]) {
  const seg = h("div", { class: "seg" }, [["today", "Vandaag"], ["month", "Deze maand"], ["year", "Dit jaar"], ["30d", "30 dagen"]].map(([v, l]) =>
    h("button", { class: v === period ? "on" : "", onclick: () => { location.hash = `#/finance/${v}`; } }, l)));
  const f = await api(`/finance/summary?period=${period}`);
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Financiën"), seg));
  if (!f.available) { root.append(h("div", { class: "card empty" }, f.reason), await settlementCard()); return; }
  const sv = f.savings, ind = f.indicators, t = f.battery_trading, e = f.energy;
  root.append(
    !f.reliable ? h("div", { class: "notice warn inline", role: "status" }, h("b", {}, "Beperkte datadekking: "),
      `${num(f.coverage_pct, 0)}% van de kwartieren in deze periode heeft meting en prijs. Bedragen zijn onvolledig.`) : null,
    h("div", { class: "grid cols-3" },
      kpi("Werkelijk (met EMS)", eur(f.costs.total_eur), "energiekosten + vaste kosten"),
      kpi("Bespaard door EMS-sturing", eur(sv.ems_steering_eur), "t.o.v. batterij op eigen regeling (B2 − B3)"),
      kpi("Datadekking", `${num(f.coverage_pct, 0)}%`, `${f.slots} van ${f.expected_slots} kwartieren`)),
    h("h2", {}, "Vergelijkingen (zelfde verbruik en prijzen)"),
    h("div", { class: "tbl-wrap" }, h("table", {}, h("caption", { class: "small muted" }, "Energiekosten per uitgangssituatie, zonder vaste kosten"),
      h("thead", {}, h("tr", {}, ["", "Situatie", "Energiekosten", "Wat is aangenomen"].map((x) => h("th", { scope: "col" }, x)))),
      h("tbody", {}, f.baselines.map((x) => h("tr", {}, h("td", {}, x.id), h("td", {}, x.label), h("td", { class: "num" }, eur(x.energy_cost_eur)),
        h("td", { class: "small muted" }, x.definition)))))),
    h("h2", {}, "Waar komt de besparing vandaan?"),
    h("div", { class: "tbl-wrap" }, h("table", {}, h("tbody", {},
      [["Zonnepanelen (B0 − B1)", sv.pv_eur], ["Batterij op eigen regeling (B1 − B2)", sv.battery_own_control_eur],
        ["EMS-sturing (B2 − B3)", sv.ems_steering_eur]].map(([l, v]) => h("tr", {}, h("td", {}, l), h("td", { class: "num" }, eur(v)))),
      h("tr", {}, h("td", {}, h("b", {}, "Totaal (B0 − B3)")), h("td", { class: "num" }, h("b", {}, eur(sv.total_eur))))))),
    h("p", { class: "small muted" }, sv.reconciled ? "De posten tellen exact op tot het totaal; niets wordt dubbel geteld." : "Let op: posten sluiten niet aan op het totaal."),
    h("ul", { class: "small muted" }, f.assumptions.map((a) => h("li", {}, a))),
    h("h2", {}, "Kosten"),
    h("div", { class: "grid cols-4" }, kpi("Afname", eur(f.costs.import_cost_eur), kwh(e.import_kwh)), kpi("Teruglevering", eur(f.costs.export_revenue_eur), kwh(e.export_kwh)),
      kpi("Vaste kosten", eur(f.costs.fixed_costs_eur)), kpi("Totaal", eur(f.costs.total_eur))),
    h("h2", {}, "Ter informatie"),
    h("p", { class: "small muted" }, ind.note),
    h("div", { class: "grid cols-3" },
      kpi("Afgenomen bij negatieve prijs", kwh(ind.negative_price_import_kwh), `${eur(ind.negative_price_earned_eur)} ontvangen`),
      kpi("Gem. prijs warmtepomp", ind.heat_pump_avg_price ? `€ ${num(ind.heat_pump_avg_price, 3)}/kWh` : "—", `gem. afnameprijs € ${num(ind.average_import_price, 3)}/kWh`),
      kpi("Batterijhandel netto", eur(t.net_eur), `bruto ${eur(t.gross_value_eur)} − laden ${eur(t.charge_cost_eur)} − slijtage ${eur(t.estimated_wear_eur)}`)),
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
      h("p", { class: "small muted" }, s.explanation, " ", s.scenarios.find((x) => x.note)?.note || ""),
      h("p", { class: "small" }, h("b", {}, "Beperking: "), s.limitation),
      (s.scenarios.flatMap((x) => x.warnings || [])).length ? h("ul", { class: "small muted" },
        [...new Set(s.scenarios.flatMap((x) => x.warnings || []))].map((w) => h("li", {}, w))) : null);
  };
  const seg = h("div", { class: "seg" }, [["30d", "30 dagen"], ["90d", "90 dagen"], ["year", "Dit jaar"]].map(([v, l], i) =>
    h("button", { class: i === 0 ? "on" : "", onclick: (ev) => { [...seg.children].forEach((b) => b.classList.remove("on")); ev.target.classList.add("on"); load(v); } }, l)));
  await load("30d");
  return h("div", {}, h("div", { class: "row spread" }, h("h2", {}, "Scenario: salderen 2026 vs. 2027"), seg),
    h("p", { class: "small muted" }, "Een indicatieve vergelijking, geen jaarafrekening van uw leverancier."),
    h("div", { class: "card" }, box));
}
