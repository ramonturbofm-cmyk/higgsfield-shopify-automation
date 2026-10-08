import { api, guard, h, kwh, num, pct, power } from "../lib.js";
import { devicePage } from "./devicepage.js";

export function render(root) {
  return devicePage(root, {
    title: "Laadpaal / EV", categories: ["ev_charger"], empty: "Geen laadpaal geconfigureerd.",
    values: (v) => [["Laadvermogen", power(v.ev_power_w)], ["Auto", v.ev_connected ? "aangesloten" : "niet aangesloten"],
      ["Auto-SOC", pct(v.ev_soc_pct)], ["Laadstroom", v.ev_current_limit_a !== undefined ? `${num(v.ev_current_limit_a, 0)} A` : "—", `sessie ${kwh(v.ev_session_energy_kwh)}`]],
    history: { title: "Laadvermogen laatste 24 uur", unit: "W", decimals: 0, series: [{ name: "EV", cls: "c5", get: (v) => v.ev_power_w ?? null }] },
    extra: async () => {
      const devs = (await api("/devices")).filter((d) => d.category === "ev_charger");
      return h("div", {}, h("h2", {}, "Laadvoorkeuren"), ...devs.map((d) => {
        const mode = h("select", {}, [["smart", "Slim (goedkoopste uren, vóór vertrek)"], ["pv_only", "Alleen zonne-overschot"],
          ["min_pv", "Minimaal + zonne-overschot"], ["max", "Altijd maximaal"], ["off", "Uit"]].map(([v, l]) => h("option", { value: v }, l)));
        mode.value = d.params.charge_mode || "smart";
        const dep = h("input", { type: "time", value: d.params.departure || "07:30" });
        const target = h("input", { type: "number", min: 10, max: 100, value: d.params.target_soc_pct ?? 80 });
        return h("div", { class: "card" }, h("h3", {}, d.name), h("div", { class: "form" },
          h("label", { class: "f" }, "Laadmodus", mode), h("label", { class: "f" }, "Vertrektijd", dep),
          h("label", { class: "f" }, "Gewenste SOC bij vertrek (%)", target)),
          h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn primary", onclick: () => guard(() => api(`/devices/${d.id}`, {
            method: "PUT", body: { params: { charge_mode: mode.value, departure: dep.value, target_soc_pct: Number(target.value) } } }), "Opgeslagen") }, "Opslaan")),
          h("p", { class: "muted small" }, "Slim laden plant de goedkoopste momenten zodat de auto bij vertrek de gewenste SOC heeft. ",
            "Fasebewaking begrenst de laadstroom altijd als de aansluiting vol dreigt te raken."));
      }));
    },
  });
}
