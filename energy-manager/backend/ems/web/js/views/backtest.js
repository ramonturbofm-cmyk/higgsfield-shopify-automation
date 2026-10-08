import { api, can, eur, guard, h, kwh, num } from "../lib.js";

async function waitJob(id, out) {
  for (;;) {
    const j = await api(`/jobs/${id}`);
    if (j.status !== "running") return j;
    out.replaceChildren(h("div", { class: "muted" }, `Bezig… ${Math.round((j.progress || 0) * 100)}%`));
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function resultTable(res) {
  const rows = [["Kosten (incl. slijtage)", "net_cost_incl_wear_eur", eur], ["Energiekosten", "cost_eur", eur], ["Afname", "import_kwh", kwh],
    ["Teruglevering", "export_kwh", kwh], ["Batterijdoorvoer", "throughput_kwh", kwh], ["Batterijcycli", "battery_cycles", (v) => num(v, 2)],
    ["Laden uit net", "grid_charge_kwh", kwh], ["PV afgeregeld", "curtailed_kwh", kwh], ["Geschatte slijtage", "estimated_wear_eur", eur]];
  return h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["", "Huidige instelling", "Nieuwe instelling", "Verschil"].map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map(([l, k, f]) => h("tr", {}, h("td", {}, l), h("td", { class: "num" }, f(res.current[k])), h("td", { class: "num" }, f(res.proposed[k])),
      h("td", { class: "num" }, f(res.proposed[k] - res.current[k])))))));
}

export async function render(root) {
  const s = await api("/settings");
  const days = h("select", { "aria-label": "periode" }, [...[1, 7, 14, 30, 90, 365].map((d) => h("option", { value: d }, `laatste ${d} dag${d > 1 ? "en" : ""}`)),
    h("option", { value: "custom" }, "eigen periode…")]);
  days.value = "7";
  const from = h("input", { type: "date", "aria-label": "begindatum" });
  const to = h("input", { type: "date", "aria-label": "einddatum" });
  const custom = h("span", { class: "row", hidden: true }, h("label", { class: "f" }, "Van", from), h("label", { class: "f" }, "Tot en met", to));
  days.addEventListener("change", () => { custom.hidden = days.value !== "custom"; });
  const fld = (label, sec, key, val, step = "0.01") => { const i = h("input", { type: "number", step, value: val }); i.dataset.sec = sec; i.dataset.key = key; i.dataset.orig = val; return h("label", { class: "f" }, label, i); };
  const fields = h("div", { class: "form" },
    fld("Minimaal prijsverschil (€/kWh)", "battery", "min_arbitrage_spread_eur", s.battery.min_arbitrage_spread_eur),
    fld("Batterijslijtage (€/kWh)", "battery", "degradation_cost_per_kwh", s.battery.degradation_cost_per_kwh),
    fld("Noodstroomreserve (%)", "battery", "reserve_soc", s.battery.reserve_soc, "1"),
    fld("Maximale SOC (%)", "battery", "max_soc", s.battery.max_soc, "1"),
    fld("Max. cycli per dag", "battery", "max_cycles_per_day", s.battery.max_cycles_per_day, "0.1"));
  const out = h("div", {});
  const sugBox = h("div", {});
  const run = h("button", { class: "btn primary", disabled: !can("operator"), onclick: () => guard(async () => {
    const overrides = {};
    fields.querySelectorAll("input").forEach((i) => { if (i.value !== i.dataset.orig) (overrides[i.dataset.sec] ||= {})[i.dataset.key] = Number(i.value); });
    const period = days.value === "custom" ? { start: from.value, end: to.value } : { days: Number(days.value) };
    if (days.value === "custom" && (!from.value || !to.value)) throw new Error("Kies een begin- en einddatum");
    const { job_id } = await api("/backtest", { method: "POST", body: { ...period, overrides } });
    const j = await waitJob(job_id, out);
    if (j.status !== "done") { out.replaceChildren(h("div", { class: "notice bad inline" }, j.error)); return; }
    const r = j.result;
    out.replaceChildren(h("div", { class: `notice ${r.difference_eur < 0 ? "good" : "info"} inline` },
      `Verschil: ${eur(r.difference_eur)} over ${r.days} dagen (${r.start} t/m ${r.end}; ${r.difference_eur < 0 ? "goedkoper" : "duurder of gelijk"}). Zonder EMS: ${eur(r.without_ems_eur)}.`),
    r.coverage.coverage_pct < 90 ? h("div", { class: "notice warn inline" }, `Datadekking ${num(r.coverage.coverage_pct, 0)}%: ${r.coverage.days_used} van ${r.coverage.days_requested} dagen bruikbaar.`) : null,
    resultTable(r),
    h("p", { class: "muted small" }, `Bron: ${r.source}. Methode: ${r.method}. ${r.baseline}. Datadekking ${num(r.coverage.coverage_pct, 1)}% (${r.coverage.rule}). `,
      `Vingerafdruk invoer: ${r.fingerprint} — dezelfde periode en instellingen geven exact dezelfde uitkomst.`));
  }) }, "Simuleren");
  root.append(h("h1", {}, "Backtest & Auto-Tune"),
    h("div", { class: "card" }, h("h3", {}, "Instelling testen tegen historische gegevens"),
      h("p", { class: "muted small" }, "Wijzig een of meer waarden en vergelijk met de huidige instelling. Er wordt niets aangepast."),
      h("div", { class: "row" }, h("label", { class: "f" }, "Periode", days), custom), fields, h("div", { class: "row", style: { marginTop: "12px" } }, run), out),
    h("div", { class: "card" }, h("div", { class: "row spread" }, h("h3", {}, "Auto-Tune"),
      h("button", { class: "btn", disabled: !can("operator"), onclick: () => guard(async () => {
        const { job_id } = await api("/autotune?days=14", { method: "POST" });
        const j = await waitJob(job_id, sugBox);
        if (j.status !== "done") sugBox.replaceChildren(h("div", { class: "notice bad inline" }, j.error));
        loadSug();
      }) }, "Analyseren")),
    h("p", { class: "muted small" }, "Auto-Tune stelt alleen voor; u kiest altijd zelf: NEGEREN, TESTEN of TOEPASSEN."), sugBox));
  async function loadSug() {
    const list = await api("/autotune/suggestions");
    sugBox.replaceChildren(...(list.length ? list.map((sg) => h("div", { class: "card flat" }, h("div", {}, sg.text),
      h("div", { class: "row", style: { marginTop: "8px" } }, h("span", { class: "pill" }, sg.status),
        ...["ignore", "test", "apply"].map((a) => h("button", { class: `btn sm ${a === "apply" ? "primary" : ""}`, disabled: !can("admin") || sg.status === "applied",
          onclick: () => guard(() => api(`/autotune/suggestions/${encodeURIComponent(sg.id)}/${a}`, { method: "POST" }),
            { ignore: "Genegeerd", test: "Backtest gestart (zie Taken)", apply: "Toegepast" }[a]).then(loadSug) },
          { ignore: "NEGEREN", test: "TESTEN", apply: "TOEPASSEN" }[a]))))) : [h("div", { class: "muted small" }, "Nog geen adviezen.")]));
  }
  loadSug();
}
