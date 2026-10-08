import { api, can, dateTime, eur, guard, h, num, on, power, time } from "../lib.js";
import { lineChart, priceBars } from "../charts.js";
import { fmtPrice, kpi } from "./common.js";

const HP_LABEL = { boost: "Warmtepomp voorverwarmen", eco: "Warmtepomp zuinig", normal: null };

/** What the plan does in a slot or bucket — only from planned values and the planner's explicit fields. */
function actions(s) {
  const parts = [];
  if (Math.abs(s.battery_w || 0) > 100) parts.push(`Accu ${s.battery_w > 0 ? "laden" : "ontladen"} ${power(Math.abs(s.battery_w))}`);
  if ((s.curtail_w || 0) > 100) parts.push(`PV afgeregeld ${power(s.curtail_w)}`);
  if (HP_LABEL[s.hp_action]) parts.push(HP_LABEL[s.hp_action]);
  for (const w of Object.values(s.ev_w || {})) if (w > 100) parts.push(`EV laden ${power(w)}`);
  return parts.join(" · ") || "—";
}

export async function render(root, [res = "15"]) {
  const body = h("div", {});
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Resolutie" }, [["15", "Per kwartier"], ["60", "Per uur"]].map(([v, l]) =>
    h("button", { class: v === res ? "on" : "", "aria-pressed": v === res ? "true" : "false", onclick: () => { location.hash = `#/planning/${v}`; } }, l)));
  const title = h("h1", {}, "Planning");
  root.append(h("div", { class: "row spread" }, title, h("div", { class: "row" }, seg,
    can("operator") ? h("button", { class: "btn", onclick: () => guard(() => api("/optimizer/run", { method: "POST" }), "Planning herberekend").then(load) }, "Nu herberekenen") : null)), body);

  async function load() {
    // Horizon comes from the site setting (Instellingen → Optimizer), not from the browser.
    const [plan, table] = await Promise.all([api("/optimizer/plan"), api(`/optimizer/plan?resolution=${res}`)]);
    title.textContent = `Planning komende ${num(plan.horizon_hours, 0)} uur`;
    const slots = plan.slots || [];
    if (!slots.length) {
      body.replaceChildren(h("div", { class: "notice warn inline" }, "Geen planning beschikbaar: ", plan.message || plan.optimizer?.message || "nog niet berekend",
        ". Controleer de prijsbron in Instellingen."));
      return;
    }
    const t = slots.map((s) => s.start);
    const now = new Date();
    const nowIdx = slots.findIndex((s) => new Date(s.start) <= now && now < new Date(s.end));
    const shortHorizon = plan.planned_until && new Date(plan.planned_until) < new Date(now.getTime() + plan.horizon_hours * 3600e3 - 3600e3);
    body.replaceChildren(
      shortHorizon ? h("div", { class: "notice info inline", role: "status" }, `Planning loopt tot ${dateTime(plan.planned_until)}: daarna zijn nog geen prijzen bekend (bekend tot ${plan.prices_known_until ? dateTime(plan.prices_known_until) : "—"}).`) : null,
      h("div", { class: "grid cols-4" },
        kpi("Verwachte kosten", eur(plan.expected_cost), "rest van de planning, incl. slijtage"),
        kpi("Zonder EMS", eur(plan.baseline_cost), "zelfde verbruik, batterij op eigen regeling"),
        kpi("Verwacht voordeel", eur(plan.expected_benefit), "berekend door de optimizer"),
        kpi("Status", plan.status === "optimal" ? "Optimaal" : plan.status, `berekening ${plan.run_id || "—"}${plan.optimizer?.trigger ? ` · ${plan.optimizer.trigger}` : ""}`)),
      h("div", { class: "card" }, h("h3", {}, "Importprijs (€/kWh)"),
        priceBars({ times: t, values: slots.map((s) => s.import_price), estimated: slots.map((s) => s.price_estimated), marker: nowIdx < 0 ? null : nowIdx })),
      h("div", { class: "card" }, h("h3", {}, "Vermogens (kW)"),
        lineChart({ times: t, unit: "kW", decimals: 2, zero: true, series: [
          { name: "PV (gepland, na afregelen)", values: slots.map((s) => s.pv_w / 1000), cls: "c4" },
          { name: "Verbruik huis", values: slots.map((s) => s.load_w / 1000), cls: "c1" },
          { name: "Batterij (+laden)", values: slots.map((s) => s.battery_w / 1000), cls: "c3" },
          { name: "Net (+afname)", values: slots.map((s) => s.grid_w / 1000), cls: "c7" }] })),
      slots[0].soc_pct !== null ? h("div", { class: "card" }, h("h3", {}, "Verwachte batterij-laadtoestand (%)"),
        lineChart({ times: t, unit: "%", decimals: 0, series: [{ name: "SOC", values: slots.map((s) => s.soc_pct), cls: "c3", area: true }] })) : null,
      slots[0].indoor_c !== null ? h("div", { class: "card" }, h("h3", {}, "Binnentemperatuur volgens plan (°C)"),
        lineChart({ times: t, unit: "°C", decimals: 1, series: [{ name: "Binnen", values: slots.map((s) => s.indoor_c), cls: "c2" }] })) : null,
      h("h2", {}, res === "60" ? "Per uur (gemiddelden en sommen)" : "Per kwartier"),
      h("div", { class: "tbl-wrap" }, h("table", {},
        h("caption", { class: "small muted" }, res === "60" ? "Prijs en vermogen tijdgewogen gemiddeld, energie opgeteld, laadtoestand aan het eind van het uur." : ""),
        h("thead", {}, h("tr", {}, ["Tijd", "Afname", "Teruglevering", "PV", "Verbruik", "Batterij", "SOC", "Net", "Acties", ""].map((c) => h("th", { scope: "col" }, c)))),
        h("tbody", {}, table.slots.flatMap((s) => {
          const reasons = s.reasons || [];
          const why = h("tr", { hidden: true }, h("td", { colspan: 10 }, h("ul", { class: "small" },
            reasons.length ? reasons.map((r) => h("li", {}, r.text)) : h("li", {}, "Geen bijzondere actie"))));
          const range = s.import_price_min !== undefined && s.import_price_min !== s.import_price_max
            ? h("div", { class: "muted small" }, `${num(s.import_price_min, 3)}–${num(s.import_price_max, 3)}`) : null;
          return [h("tr", {}, h("td", {}, time(s.start)),
            h("td", {}, fmtPrice(s.import_price), range, s.price_estimated ? h("span", { class: "muted small" }, " (schatting)") : ""),
            h("td", {}, fmtPrice(s.export_price)),
            h("td", {}, res === "60" ? `${num(s.pv_kwh, 2)} kWh` : power(s.pv_w)),
            h("td", {}, res === "60" ? `${num(s.load_kwh, 2)} kWh` : power(s.load_w)),
            h("td", {}, res === "60" ? `${num(s.battery_kwh, 2)} kWh` : power(s.battery_w, true)),
            h("td", {}, s.soc_pct === null || s.soc_pct === undefined ? "—" : `${Math.round(s.soc_pct)}%`),
            h("td", {}, res === "60" ? `${num(s.grid_kwh, 2)} kWh` : power(s.grid_w, true)),
            h("td", { class: "small" }, actions(s)),
            h("td", {}, h("button", { class: "btn sm", "aria-expanded": "false", onclick: (ev) => { why.hidden = !why.hidden; ev.target.setAttribute("aria-expanded", String(!why.hidden)); } }, "Waarom?"))), why];
        })))),
      h("p", { class: "muted small" }, "Redenen komen rechtstreeks uit dezelfde optimizerberekening (", plan.run_id || "—", "). Bronnen: ",
        Object.entries(plan.inputs?.forecast_sources || {}).map(([k, v]) => `${k}: ${v}`).join(" · ")));
  }
  await load();
  return on((m) => { if (m.type === "plan") load(); });
}
