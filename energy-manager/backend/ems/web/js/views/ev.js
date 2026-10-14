import { api, guard, h, kwh, num, pct, power, state } from "../lib.js";
import { devicePage } from "./devicepage.js";

export function render(root) {
  return devicePage(root, {
    title: "Laadpaal / EV", categories: ["ev_charger"], empty: "Geen laadpaal geconfigureerd.",
    // Vehicle SOC only when a source actually reports it (most chargers do not know the car's SOC).
    values: (v) => [["Laadvermogen", power(v.ev_power_w)], ["Auto", v.ev_connected ? "aangesloten" : "niet aangesloten"],
      v.ev_soc_pct !== undefined && v.ev_soc_pct !== null ? ["Auto-SOC", pct(v.ev_soc_pct)] : ["Geladen deze sessie", kwh(v.ev_session_energy_kwh)], ["Laadstroom", v.ev_current_limit_a !== undefined ? `${num(v.ev_current_limit_a, 0)} A` : "—", `sessie ${kwh(v.ev_session_energy_kwh)}`]],
    history: { title: "Laadvermogen laatste 24 uur", unit: "W", decimals: 0, series: [{ name: "EV", cls: "c5", get: (v) => v.ev_power_w ?? null }] },
    extra: async () => {
      const devs = (await api("/devices")).filter((d) => d.category === "ev_charger");
      return h("div", {}, h("h2", {}, "Laadvoorkeuren"), ...devs.map((d) => {
        const mode = h("select", {}, [["smart", "Slim (goedkoopste uren, vóór vertrek)"], ["pv_only", "Alleen zonne-overschot"],
          ["min_pv", "Minimaal + zonne-overschot"], ["max", "Altijd maximaal"], ["off", "Uit"]].map(([v, l]) => h("option", { value: v }, l)));
        mode.value = d.params.charge_mode || "smart";
        const dep = h("input", { type: "time", value: d.params.departure || "07:30" });
        const hasSoc = (d.capabilities || []).some((c) => c.id === "read_ev_soc") && d.values?.ev_soc_pct !== undefined;
        const target = hasSoc ? h("input", { type: "number", min: 10, max: 100, value: d.params.target_soc_pct ?? 80 })
          : h("input", { type: "number", min: 0, max: 150, step: 1, value: d.params.session_target_kwh ?? 20 });
        const phase = state.live?.features?.phase_balancing;
        return h("div", { class: "card" }, h("h3", {}, d.name), h("div", { class: "form" },
          h("label", { class: "f" }, "Laadmodus", mode), h("label", { class: "f" }, "Vertrektijd", dep),
          h("label", { class: "f" }, hasSoc ? "Gewenste SOC bij vertrek (%)" : "Gewenste energie bij vertrek (kWh)", target,
            hasSoc ? null : h("span", { class: "muted small" }, "De laadtoestand van de auto is niet bekend (de laadpaal meldt die niet); daarom wordt een hoeveelheid energie gepland."))),
          h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn primary", onclick: () => guard(() => api(`/devices/${d.id}`, {
            method: "PUT", body: { params: { charge_mode: mode.value, departure: dep.value, ...(hasSoc ? { target_soc_pct: Number(target.value) } : { session_target_kwh: Number(target.value) }) } } }), "Opgeslagen") }, "Opslaan")),
          h("p", { class: "muted small" }, hasSoc ? "Slim laden plant de goedkoopste momenten zodat de auto bij vertrek de gewenste SOC heeft. "
            : "Slim laden plant de goedkoopste momenten voor de gewenste hoeveelheid energie vóór vertrek. ",
          phase?.available ? "Fasebewaking is actief: de laadstroom wordt begrensd als een fase van de aansluiting vol dreigt te raken."
            : `Fasebewaking is niet actief${phase?.reason ? ` (${phase.reason})` : ""}; de laadstroom blijft binnen de ingestelde maximale stroom.`));
      }));
    },
  });
}
