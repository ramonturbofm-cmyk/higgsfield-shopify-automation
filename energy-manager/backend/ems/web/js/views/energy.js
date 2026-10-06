import { api, download, eur, h, kwh, state } from "../lib.js";
import { lineChart } from "../charts.js";
import { kpi } from "./common.js";

export async function render(root, [range = "24"]) {
  const hours = Number(range) || 24;
  const res = hours <= 48 ? "raw" : "15m";
  const data = await api(`/history?hours=${hours}&resolution=${res}`);
  const rows = data.rows;
  const step = Math.max(1, Math.ceil(rows.length / 700));
  const r = rows.filter((_, i) => i % step === 0);
  const ts = r.map((x) => new Date((x.ts ?? x.slot_ts) * 1000).toISOString());
  const kw = (k) => r.map((x) => (x[k] === null || x[k] === undefined ? null : res === "raw" ? x[k] / 1000 : x[k] * 4));
  const seg = h("div", { class: "seg" }, [["24", "24 uur"], ["48", "2 dagen"], ["168", "7 dagen"], ["720", "30 dagen"]].map(([v, l]) =>
    h("button", { class: v === String(hours) ? "on" : "", onclick: () => { location.hash = `#/energy/${v}`; } }, l)));
  const slots = res === "15m" ? rows : (await api(`/history?hours=${hours}&resolution=15m`)).rows;
  const sum = (k) => slots.reduce((a, x) => a + (x[k] || 0), 0);
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Energie"), seg),
    h("div", { class: "grid cols-4" }, kpi("Afname", kwh(sum("import_kwh"))), kpi("Teruglevering", kwh(sum("export_kwh"))),
      kpi("PV-opwek", kwh(sum("pv_kwh"))), kpi("Netto energiekosten", eur(sum("cost_eur") - sum("revenue_eur")), "excl. vaste kosten")),
    !rows.length ? h("div", { class: "empty" }, "Nog geen historie opgeslagen.") : h("div", {},
      h("div", { class: "card" }, h("h3", {}, `Vermogen (kW) — ${res === "raw" ? "per meting" : "kwartiergemiddelde"}`),
        lineChart({ times: ts, unit: "kW", decimals: 2, zero: true, series: [
          { name: "Net (+afname)", values: kw(res === "raw" ? "grid_w" : "import_kwh").map((v, i) => res === "raw" ? v : v - (r[i].export_kwh || 0) * 4), cls: "c7" },
          { name: "PV", values: kw(res === "raw" ? "pv_w" : "pv_kwh"), cls: "c4" },
          { name: "Huis", values: kw(res === "raw" ? "house_w" : "house_kwh"), cls: "c1" },
          { name: "Batterij (+laden)", values: res === "raw" ? kw("battery_w") : r.map((x) => ((x.battery_charge_kwh || 0) - (x.battery_discharge_kwh || 0)) * 4), cls: "c3" }] })),
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("h3", {}, "Batterij-SOC (%)"), lineChart({ times: ts, unit: "%", decimals: 0, height: 180,
          series: [{ name: "SOC", values: r.map((x) => x.soc ?? x.soc_end ?? null), cls: "c3" }] })),
        h("div", { class: "card" }, h("h3", {}, "Temperatuur (°C)"), lineChart({ times: ts, unit: "°C", height: 180,
          series: [{ name: "Binnen", values: r.map((x) => x.indoor_c ?? null), cls: "c2" }, { name: "Buiten", values: r.map((x) => x.outdoor_c ?? null), cls: "c7" }] })))),
    h("h2", {}, "Exporteren"),
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: () => download(`/history/export?hours=${hours}&fmt=csv&resolution=15m`, "energie.csv") }, "CSV"),
      h("button", { class: "btn", onclick: () => download(`/history/export?hours=${hours}&fmt=excel&resolution=15m`, "energie-excel.csv") }, "Excel-CSV"),
      h("button", { class: "btn", onclick: () => download(`/history/export?hours=${hours}&fmt=json&resolution=15m`, "energie.json") }, "JSON"),
      h("span", { class: "muted small" }, `Tijdzone ${state.tz}`)));
}
