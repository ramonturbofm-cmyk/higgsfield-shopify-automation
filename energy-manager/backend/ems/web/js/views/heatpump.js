import { api, h, num, power, temp } from "../lib.js";
import { devicePage } from "./devicepage.js";
import { kpi } from "./common.js";

export function render(root) {
  return devicePage(root, {
    title: "Warmtepomp", categories: ["heat_pump", "heat_pump_boiler"], empty: "Geen warmtepomp geconfigureerd.",
    values: (v) => [["Binnen", temp(v.indoor_temp_c), `setpoint ${temp(v.hp_setpoint_c)}`], ["Buiten", temp(v.outdoor_temp_c)],
      ["Elektrisch", power(v.hp_power_w), v.hp_compressor_on ? "compressor aan" : "compressor uit"],
      ["COP", v.hp_cop ? num(v.hp_cop, 2) : "—", `aanvoer ${temp(v.flow_temp_c)} · modus ${v.hp_mode || "—"}`]],
    history: { title: "Binnentemperatuur laatste 24 uur", unit: "°C", series: [{ name: "Binnen", cls: "c2", get: (v) => v.indoor_temp_c ?? null }] },
    extra: async () => {
      const s = await api("/settings");
      const hp = s.heatpump;
      return h("div", {}, h("h2", {}, "Comfortgrenzen"), h("div", { class: "grid cols-3" },
        kpi("Normaal", temp(hp.comfort_temperature)), kpi("Goedkope uren maximaal", temp(hp.max_preheat_temperature)),
        kpi("Dure uren minimaal", temp(hp.min_temperature))),
        h("p", { class: "muted small" }, "De warmtepomp houdt altijd zijn eigen regeling, vorstbeveiliging, ontdooi- en legionellaprogramma's. ",
          "Het EMS verschuift alleen het setpoint (normaal/boost/eco) en wisselt hooguit eens per 20 minuten."));
    },
  });
}
