import { api, eur, h, kwh, num, pct, power, temp } from "../lib.js";
import { devicePage } from "./devicepage.js";
import { kpi } from "./common.js";

export function render(root) {
  return devicePage(root, {
    title: "Batterij", categories: ["battery", "hybrid_inverter"], empty: "Geen batterij geconfigureerd.",
    values: (v) => [["Laadtoestand", pct(v.battery_soc_pct)], ["Vermogen", power(v.battery_power_w, true),
      v.battery_power_w > 20 ? "laden" : v.battery_power_w < -20 ? "ontladen" : "rust"],
      ["Temperatuur", temp(v.battery_temperature_c)], ["Modus", v.battery_mode || "—"]],
    actions: [["Laden", "battery_charge", 3000, "W"], ["Ontladen", "battery_discharge", 3000, "W"], ["Stand-by", "battery_standby", null]],
    history: { title: "Laatste 24 uur", unit: "%", decimals: 0, series: [{ name: "SOC", cls: "c3", get: (v) => v.battery_soc_pct ?? null }] },
    extra: async () => {
      const f = await api("/finance/summary?period=30d");
      const s = await api("/settings");
      if (!f.available) return h("div", { class: "card muted" }, "Nog onvoldoende historie voor batterijstatistieken.");
      const bt = f.battery_trading, e = f.energy;
      return h("div", {}, h("h2", {}, "Batterijgebruik en slijtage (30 dagen)"),
        h("div", { class: "grid cols-4" },
          kpi("Doorvoer", kwh(e.battery_charge_kwh + e.battery_discharge_kwh), `${kwh(e.battery_charge_kwh)} in / ${kwh(e.battery_discharge_kwh)} uit`),
          kpi("Equivalente volle cycli", num(bt.equivalent_full_cycles, 2)),
          kpi("Geschatte slijtage", eur(bt.estimated_wear_eur), `${eur(s.battery.degradation_cost_per_kwh, 3)} per kWh doorvoer`),
          kpi("Netto handel", eur(bt.net_eur), `bruto ${eur(bt.gross_value_eur)} − laden ${eur(bt.charge_cost_eur)} − slijtage`)),
        h("p", { class: "muted small" }, `Energieverlies: ${kwh(bt.energy_loss_kwh)}. Slijtagestand: ${s.battery.wear_mode} (wijzig bij Instellingen).`));
    },
  });
}
