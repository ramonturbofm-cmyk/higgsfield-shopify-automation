import { api, h, kwh, power } from "../lib.js";
import { devicePage } from "./devicepage.js";
import { kpi } from "./common.js";

export function render(root) {
  return devicePage(root, {
    title: "Zonnepanelen", categories: ["pv_inverter", "hybrid_inverter"], empty: "Geen PV-omvormer geconfigureerd.",
    values: (v) => [["Opwek", power(v.pv_power_w)], ["Begrenzing", v.pv_limit_w === null || v.pv_limit_w === undefined ? "geen" : power(v.pv_limit_w)],
      ["Totaal", kwh(v.pv_energy_kwh)]],
    history: { title: "Opwek laatste 24 uur", unit: "W", decimals: 0, series: [{ name: "PV", cls: "c4", get: (v) => v.pv_power_w ?? null }] },
    extra: async () => {
      const [fc, s] = await Promise.all([api("/forecast?hours=36"), api("/settings")]);
      return h("div", {}, h("h2", {}, "Teruglevering en prognose"),
        h("div", { class: "grid cols-3" },
          kpi("Terugleverstand", { unlimited: "Onbeperkt", smart: "Smart Export", zero: "Zero Export" }[s.strategy.export_mode],
            s.strategy.export_mode === "smart" ? `afregelen onder € ${s.strategy.export_price_threshold_eur}/kWh` : ""),
          kpi("PV-prognose komende 36 u", kwh(fc.pv_kwh_total), fc.sources.pv),
          kpi("Kalibratiefactor", String(fc.pv_calibration).replace(".", ","), "geleerd uit gemeten opwek")),
        h("p", { class: "muted small" }, "Volgorde PV-overschot: huis → ", s.strategy.surplus_priority.join(" → "), " → afregelen. Wijzig bij Instellingen → Strategie."));
    },
  });
}
