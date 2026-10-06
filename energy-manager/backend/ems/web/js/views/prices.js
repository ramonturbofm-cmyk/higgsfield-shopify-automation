import { api, can, guard, h } from "../lib.js";
import { priceBars } from "../charts.js";
import { fmtPrice, kpi } from "./common.js";

export async function render(root) {
  const data = await api("/prices?hours=36&past_hours=12");
  const pts = data.points;
  const now = new Date(data.now);
  const idx = pts.findIndex((p) => new Date(p.start) > now) - 1;
  const cur = pts[Math.max(0, idx)];
  const future = pts.slice(Math.max(0, idx));
  const min = future.reduce((a, b) => (b.import < a.import ? b : a), future[0] || {});
  const max = future.reduce((a, b) => (b.import > a.import ? b : a), future[0] || {});
  const st = data.status;
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Prijzen"),
    can("operator") ? h("button", { class: "btn", onclick: () => guard(() => api("/prices/refresh", { method: "POST" }), "Prijzen ververst").then(() => location.reload()) }, "Verversen") : null),
    st.last_error ? h("div", { class: "notice warn inline" }, "Prijsbron: ", st.last_error, " — het EMS gebruikt de laatst bekende prijzen.") : null,
    !pts.length ? h("div", { class: "notice warn inline" }, "Geen prijsdata. Stel een prijsbron in bij Instellingen → Prijzen.") : h("div", {},
      h("div", { class: "grid cols-4" }, kpi("Nu (afname)", fmtPrice(cur?.import), `markt ${fmtPrice(cur?.spot)}`),
        kpi("Nu (teruglevering)", fmtPrice(cur?.export)), kpi("Goedkoopste komend", fmtPrice(min?.import), min?.start ? new Date(min.start).toLocaleString("nl-NL", { weekday: "short", hour: "2-digit", minute: "2-digit" }) : ""),
        kpi("Duurste komend", fmtPrice(max?.import), max?.start ? new Date(max.start).toLocaleString("nl-NL", { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "")),
      h("div", { class: "card" }, h("h3", {}, "Afnameprijs inclusief opslagen, belasting en btw (€/kWh)"),
        priceBars({ times: pts.map((p) => p.start), values: pts.map((p) => p.import), estimated: pts.map((p) => p.estimated), marker: Math.max(0, idx) })),
      h("div", { class: "card" }, h("h3", {}, "Terugleverprijs (€/kWh)"),
        priceBars({ times: pts.map((p) => p.start), values: pts.map((p) => p.export), estimated: pts.map((p) => p.estimated), marker: Math.max(0, idx) })),
      h("div", { class: "card" }, h("h3", {}, "Marktprijs (day-ahead, €/kWh)"),
        priceBars({ times: pts.map((p) => p.start), values: pts.map((p) => p.spot), estimated: pts.map((p) => p.estimated), marker: Math.max(0, idx) })),
      h("p", { class: "muted small" }, `Bron: ${st.provider || "geen"} · bekend tot ${st.known_until ? new Date(st.known_until).toLocaleString("nl-NL") : "—"} · lichte balken zijn schattingen (gemiddelde van de afgelopen week).`)));
}
