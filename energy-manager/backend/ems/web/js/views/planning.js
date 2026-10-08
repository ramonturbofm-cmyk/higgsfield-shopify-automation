import { api, can, eur, guard, h, on, power, time } from "../lib.js";
import { lineChart, priceBars } from "../charts.js";
import { fmtPrice, kpi, slotSummary } from "./common.js";

export async function render(root) {
  const body = h("div", {});
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Planning komende 36 uur"),
    can("operator") ? h("button", { class: "btn", onclick: () => guard(() => api("/optimizer/run", { method: "POST" }), "Planning herberekend").then(load) }, "Nu herberekenen") : null), body);
  async function load() {
    const plan = await api("/optimizer/plan?hours=36");
    const slots = plan.slots || [];
    if (!slots.length) {
      body.replaceChildren(h("div", { class: "notice warn inline" }, "Geen planning beschikbaar: ", plan.message || plan.optimizer?.message || "nog niet berekend",
        ". Controleer de prijsbron in Instellingen."));
      return;
    }
    const t = slots.map((s) => s.start);
    const nowIdx = slots.findIndex((s) => new Date(s.start) > new Date()) - 1;
    const hours = slots.filter((_, i) => i % 4 === 0);
    body.replaceChildren(
      h("div", { class: "grid cols-4" },
        kpi("Verwachte kosten", eur(plan.expected_cost), "rest van de planning"),
        kpi("Zonder EMS", eur(plan.baseline_cost), "zelfde verbruik, batterij op eigen regeling"),
        kpi("Verwacht voordeel", eur(plan.expected_benefit), plan.optimizer?.trigger ? `laatste berekening: ${plan.optimizer.trigger}` : ""),
        kpi("Status", plan.status === "optimal" ? "Optimaal" : plan.status, plan.inputs?.heat_pump_note || "")),
      h("div", { class: "card" }, h("h3", {}, "Importprijs (€/kWh)"),
        priceBars({ times: t, values: slots.map((s) => s.import_price), estimated: slots.map((s) => s.price_estimated), marker: Math.max(0, nowIdx) })),
      h("div", { class: "card" }, h("h3", {}, "Vermogens (kW)"),
        lineChart({ times: t, unit: "kW", decimals: 2, zero: true, series: [
          { name: "PV (verwacht)", values: slots.map((s) => s.pv_w / 1000), cls: "c4" },
          { name: "Verbruik huis", values: slots.map((s) => s.load_w / 1000), cls: "c1" },
          { name: "Batterij (+laden)", values: slots.map((s) => s.battery_w / 1000), cls: "c3" },
          { name: "Net (+afname)", values: slots.map((s) => s.grid_w / 1000), cls: "c7" }] })),
      slots[0].soc_pct !== null ? h("div", { class: "card" }, h("h3", {}, "Verwachte batterij-laadtoestand (%)"),
        lineChart({ times: t, unit: "%", decimals: 0, series: [{ name: "SOC", values: slots.map((s) => s.soc_pct), cls: "c3", area: true }] })) : null,
      slots[0].indoor_c !== null ? h("div", { class: "card" }, h("h3", {}, "Binnentemperatuur volgens plan (°C)"),
        lineChart({ times: t, unit: "°C", decimals: 1, series: [{ name: "Binnen", values: slots.map((s) => s.indoor_c), cls: "c2" }] })) : null,
      h("h2", {}, "Per uur"),
      h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Tijd", "Afname", "Teruglevering", "PV", "Verbruik", "Batterij", "SOC", "Net", "Acties", ""].map((c) => h("th", {}, c)))),
        h("tbody", {}, hours.flatMap((s) => {
          const why = h("tr", { hidden: true }, h("td", { colspan: 10 }, h("ul", { class: "small" }, explain(s, slots).map((r) => h("li", {}, r)))));
          return [h("tr", {}, h("td", {}, time(s.start)),
            h("td", {}, fmtPrice(s.import_price), s.price_estimated ? h("span", { class: "muted small" }, " (schatting)") : ""),
            h("td", {}, fmtPrice(s.export_price)), h("td", {}, power(s.pv_forecast_w)), h("td", {}, power(s.load_w)),
            h("td", {}, power(s.battery_w, true)), h("td", {}, s.soc_pct === null ? "—" : `${Math.round(s.soc_pct)}%`),
            h("td", {}, power(s.grid_w, true)), h("td", { class: "small" }, slotSummary(s).join(" · ")),
            h("td", {}, h("button", { class: "btn sm", onclick: () => { why.hidden = !why.hidden; } }, "Waarom?"))), why];
        })))),
      h("p", { class: "muted small" }, "Bronnen: ", Object.entries(plan.inputs?.forecast_sources || {}).map(([k, v]) => `${k}: ${v}`).join(" · ")));
  }
  await load();
  return on((m) => { if (m.type === "plan") load(); });
}

const eurk = (v) => (v === null || v === undefined) ? "?" : `€${v.toFixed(3).replace(".", ",")}`;

/** Plain-language explanation of one plan slot, derived from the plan itself. */
function explain(s, slots) {
  const i = slots.indexOf(s);
  const ahead = slots.slice(i + 1, i + 49);   // next 12 hours
  const maxP = Math.max(...ahead.map((x) => x.import_price ?? -Infinity));
  const minP = Math.min(...ahead.map((x) => x.import_price ?? Infinity));
  const out = [];
  const surplus = (s.pv_forecast_w || 0) - (s.load_w || 0);
  if (s.battery_w > 200) {
    if ((s.battery_grid_charge_w || 0) > 100) out.push(`Batterij laden uit het net: afnameprijs ${eurk(s.import_price)} is laag; hoogste prijs komende 12 uur ${eurk(maxP)}.`);
    else out.push(`Batterij laden met zonne-overschot (${Math.round(surplus)} W): opslaan is meer waard dan terugleveren voor ${eurk(s.export_price)}.`);
  } else if (s.battery_w < -200) {
    out.push(`Batterij ontladen: afnameprijs ${eurk(s.import_price)} is hoog; laagste prijs komende 12 uur ${eurk(minP)}.`);
    if (s.grid_w < -200) out.push(`Overschot wordt teruggeleverd voor ${eurk(s.export_price)}.`);
  } else if (s.soc_pct !== null) {
    out.push("Geen batterijactie: het prijsverschil weegt niet op tegen omzettingsverlies en slijtage, of de batterij bewaart capaciteit voor later.");
  }
  if ((s.curtail_w || 0) > 100) out.push(`PV ${Math.round(s.curtail_w)} W afgeregeld: terugleveren levert ${eurk(s.export_price)} op (negatief of onder de drempel).`);
  if (s.hp_w !== null && s.hp_reference_w !== null && s.hp_w !== undefined) {
    if (s.hp_w > (s.hp_reference_w || 0) + 300) out.push("Warmtepomp voorverwarmen: nu goedkoper verwarmen, de woning slaat de warmte op.");
    else if (s.hp_w < (s.hp_reference_w || 0) - 300) out.push("Warmtepomp zuiniger: dure periode, binnen de comfortgrenzen uitstellen.");
  }
  if (s.price_estimated) out.push("Let op: prijs is een schatting (nog niet gepubliceerd).");
  return out.length ? out : ["Geen bijzondere actie in dit uur."];
}
