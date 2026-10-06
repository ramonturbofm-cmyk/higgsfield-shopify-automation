// View helpers shared across pages.
import { api, can, eur, guard, h, num, power, time } from "../lib.js";

export const kpi = (label, value, sub) => h("div", { class: "card kpi" }, h("div", { class: "l" }, label),
  h("div", { class: "v" }, value), sub ? h("div", { class: "s" }, sub) : null);

export function decisionList(rows, empty = "Nog geen beslissingen") {
  if (!rows?.length) return h("div", { class: "empty" }, empty);
  return h("div", { class: "grid" }, rows.map((d) => h("div", { class: "card flat" },
    h("div", { class: "row spread" }, h("b", {}, d.summary),
      h("span", { class: `pill ${d.outcome === "sent" ? "good" : d.outcome === "shadow" || d.outcome === "dry_run" ? "warn" : ""}` },
        { sent: "uitgevoerd", shadow: "schaduw: EMS zou", dry_run: "proef: EMS zou", released: "vrijgegeven",
          failed: "mislukt", blocked: "geblokkeerd" }[d.outcome] || d.outcome)),
    h("div", { class: "muted small" }, time(d.timestamp || d.ts), d.device ? ` · ${d.device}` : ""),
    d.reasons?.length ? h("ul", { class: "small" }, d.reasons.map((r) => h("li", {}, r))) : null,
    d.expected_profit !== null && d.expected_profit !== undefined ? h("div", { class: "small" }, `Verwacht voordeel planning: ${eur(d.expected_profit)}`) : null)));
}

const DURATIONS = [[30, "30 min"], [60, "1 uur"], [120, "2 uur"], [240, "4 uur"], [null, "tot ik stop"]];

/** Manual override panel for one device. actions: [[label, action, value?]] */
export function overridePanel(deviceId, actions, onDone) {
  if (!can("operator")) return h("div", { class: "muted small" }, "Handmatige bediening vereist operatorrechten.");
  const dur = h("select", {}, DURATIONS.map(([v, l]) => h("option", { value: v === null ? "" : v }, l)));
  dur.value = "60";
  const valueInputs = {};
  return h("div", { class: "grid" },
    h("div", { class: "row" }, h("span", { class: "small muted" }, "Duur:"), dur, h("span", { class: "small muted" },
      "Daarna automatisch terug naar AUTO.")),
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: () => guard(() => api(`/overrides/${deviceId}`, { method: "DELETE" }), "Terug naar AUTO").then(onDone) }, "AUTO"),
      actions.map(([label, action, value, unit]) => {
        let input = null;
        if (unit) { input = h("input", { type: "number", value, style: { width: "100px" } }); valueInputs[action] = input; }
        return h("span", { class: "row" }, input, unit ? h("span", { class: "small muted" }, unit) : null,
          h("button", { class: "btn", onclick: () => guard(() => api("/overrides", { method: "POST", body: {
            device: deviceId, action, value: input ? Number(input.value) : value,
            duration_min: dur.value === "" ? null : Number(dur.value) } }), `${label} ingesteld`).then(onDone) }, label));
      })));
}

export async function activeOverrides() {
  try { return await api("/overrides"); } catch { return []; }
}

export function overridesBox(list, deviceId) {
  const mine = list.filter((o) => !deviceId || o.device === deviceId);
  if (!mine.length) return h("div", { class: "pill good" }, "AUTO — het EMS regelt");
  return h("div", { class: "grid" }, mine.map((o) => h("div", { class: "pill warn" },
    `Handmatig: ${o.description} ${o.expires ? `tot ${time(o.expires)}` : "(tot handmatig beëindigd)"} — ${o.user}`)));
}

export function slotSummary(slot) {
  if (!slot) return [];
  const parts = [];
  if (Math.abs(slot.battery_w) > 100) parts.push(`Accu ${slot.battery_w > 0 ? "laden" : "ontladen"} ${power(Math.abs(slot.battery_w))}`);
  if (slot.curtail_w > 100) parts.push(`PV begrensd ${power(slot.curtail_w)}`);
  if (slot.hp_w !== null && slot.hp_reference_w !== null) {
    const m = slot.hp_w > slot.hp_reference_w * 1.3 + 200 ? "boost" : slot.hp_reference_w > 300 && slot.hp_w < slot.hp_reference_w * 0.5 ? "eco" : "normaal";
    parts.push(`Warmtepomp ${m}`);
  }
  for (const [id, w] of Object.entries(slot.ev_w || {})) if (w > 100) parts.push(`EV laden ${power(w)}`);
  parts.push(slot.grid_w < -50 ? `Net ${power(-slot.grid_w)} export` : slot.grid_w > 50 ? `Net ${power(slot.grid_w)} import` : "Net 0 W");
  return parts;
}

export const fmtPrice = (v) => v === null || v === undefined ? "—" : `€ ${num(v, 3)}/kWh`;
