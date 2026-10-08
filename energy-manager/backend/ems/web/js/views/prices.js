import { api, can, dateTime, guard, h, num } from "../lib.js";
import { priceBars } from "../charts.js";
import { fmtPrice, kpi } from "./common.js";

const STATUS_LABEL = { confirmed: "bevestigd", estimated: "schatting", missing: "ontbreekt" };

export async function render(root) {
  const data = await api("/prices?hours=36&past_hours=12");
  const pts = data.points;
  const now = new Date(data.now);
  // "Nu" comes from the server: the published price whose interval contains now (start <= now < end).
  const cur = data.current;
  const marker = pts.findIndex((p) => new Date(p.start) <= now && now < new Date(p.end));
  const upcoming = pts.filter((p) => new Date(p.end) > now && p.status === "confirmed" && p.import !== null);
  const pick = (cmp) => upcoming.reduce((a, b) => (a === null || cmp(b.import, a.import) ? b : a), null);
  const min = pick((x, y) => x < y), max = pick((x, y) => x > y);
  const estimatedAhead = pts.some((p) => new Date(p.start) > now && p.status === "estimated");
  const st = data.status;
  const at = (p) => (p ? dateTime(p.start) : "");
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Prijzen"),
    can("operator") ? h("button", { class: "btn", onclick: () => guard(() => api("/prices/refresh", { method: "POST" }), "Prijzen ververst").then(() => location.reload()) }, "Verversen") : null),
  st.last_error ? h("div", { class: "notice warn inline", role: "status" }, "Prijsbron: ", st.last_error, " — het EMS gebruikt de laatst bekende prijzen.") : null,
  !pts.some((p) => p.status !== "missing") ? h("div", { class: "notice warn inline" }, "Geen prijsdata. Stel een prijsbron in bij Instellingen → Prijzen.") : h("div", {},
    h("div", { class: "grid cols-4" },
      kpi("Nu (afname)", cur ? fmtPrice(cur.import) : "niet beschikbaar", cur ? `markt ${fmtPrice(cur.spot)} · ${dateTime(cur.start)}–${dateTime(cur.end).slice(-5)}` : data.current_reason),
      kpi("Nu (teruglevering)", cur ? fmtPrice(cur.export) : "niet beschikbaar"),
      kpi("Goedkoopste komend", min ? fmtPrice(min.import) : "onvoldoende prijsinformatie", min ? `${at(min)} (bevestigd)` : ""),
      kpi("Duurste komend", max ? fmtPrice(max.import) : "onvoldoende prijsinformatie", max ? `${at(max)} (bevestigd)` : "")),
    estimatedAhead ? h("p", { class: "small muted" }, "Na het laatst gepubliceerde interval zijn de prijzen een schatting (gemiddelde van de afgelopen week); die worden nooit als bekende prijs getoond.") : null,
    h("div", { class: "card" }, h("h3", {}, "Afnameprijs inclusief opslagen, belasting en btw (€/kWh)"),
      priceBars({ times: pts.map((p) => p.start), values: pts.map((p) => p.import), estimated: pts.map((p) => p.status === "estimated"), marker: marker < 0 ? null : marker })),
    h("div", { class: "card" }, h("h3", {}, "Terugleverprijs (€/kWh)"),
      priceBars({ times: pts.map((p) => p.start), values: pts.map((p) => p.export), estimated: pts.map((p) => p.status === "estimated"), marker: marker < 0 ? null : marker })),
    h("div", { class: "card" }, h("h3", {}, "Marktprijs (day-ahead, €/kWh)"),
      priceBars({ times: pts.map((p) => p.start), values: pts.map((p) => p.spot), estimated: pts.map((p) => p.status === "estimated"), marker: marker < 0 ? null : marker })),
    h("details", { class: "card" }, h("summary", {}, "Alle intervallen als tabel"),
      h("div", { class: "tbl-wrap" }, h("table", {}, h("caption", { class: "small muted" }, `Tijden in ${data.timezone}`),
        h("thead", {}, h("tr", {}, ["Interval", "Markt", "Afname", "Teruglevering", "Status"].map((x) => h("th", { scope: "col" }, x)))),
        h("tbody", {}, pts.map((p) => h("tr", { class: p === pts[marker] ? "now" : "" },
          h("td", {}, dateTime(p.start)), h("td", { class: "num" }, num(p.spot, 4)), h("td", { class: "num" }, num(p.import, 4)),
          h("td", { class: "num" }, num(p.export, 4)), h("td", {}, STATUS_LABEL[p.status] || p.status))))))),
    h("p", { class: "muted small" }, `Bron: ${st.provider || "geen"} · bekend tot ${st.known_until ? dateTime(st.known_until) : "—"} · markt per ${data.market_resolution_min} min, uw contract per ${data.contract_resolution_min} min · lichte balken zijn schattingen.`)));
}
