import { api, can, dateTime, guard, h, num } from "../lib.js";
import { priceLine } from "../charts.js";
import { fmtPrice, kpi } from "./common.js";

const STATUS_LABEL = { OFFICIAL_DAY_AHEAD: "beursprijs", FORECAST: "prognose", ESTIMATED: "geschat (gat)", STALE: "verouderd", MISSING: "ontbreekt" };
const PUB_LABEL = { available: "gepubliceerd", not_yet_expected: "nog niet verwacht", waiting: "wordt verwacht — controleert regelmatig", delayed: "VERTRAAGD" };

export async function render(root) {
  const data = await api("/prices?hours=48&past_hours=12");
  const pts = data.points;
  const now = new Date(data.now);
  // "Nu" comes from the server: the published price whose interval contains now (start <= now < end).
  const cur = data.current;
  const marker = pts.findIndex((p) => new Date(p.start) <= now && now < new Date(p.end));
  const upcoming = pts.filter((p) => new Date(p.end) > now && p.status === "OFFICIAL_DAY_AHEAD" && p.import !== null);
  const pick = (cmp) => upcoming.reduce((a, b) => (a === null || cmp(b.import, a.import) ? b : a), null);
  const min = pick((x, y) => x < y), max = pick((x, y) => x > y);
  const forecastAhead = pts.some((p) => new Date(p.start) > now && p.status === "FORECAST");
  const st = data.status;
  const pub = st.publication || {};
  const at = (p) => (p ? dateTime(p.start) : "");
  const series = (key) => ({ times: pts.map((p) => p.start), values: pts.map((p) => p[key]), status: pts.map((p) => p.status), marker: marker < 0 ? null : marker });
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Prijzen"),
    can("operator") ? h("button", { class: "btn", onclick: () => guard(() => api("/prices/refresh", { method: "POST" }), "Prijzen ververst").then(() => location.reload()) }, "Verversen") : null),
  st.last_error ? h("div", { class: "notice warn inline", role: "status" }, "Prijsbron: ", st.last_error,
    st.stale ? " — al langer dan een dag geen verbinding: prijzen zijn als 'verouderd' gemarkeerd." : " — het EMS gebruikt de laatst bekende prijzen.") : null,
  pub.state === "delayed" ? h("div", { class: "notice warn inline", role: "status" },
    `Prijzen voor ${pub.date} zijn nog niet volledig gepubliceerd (${pub.known}/${pub.intervals} kwartieren). Het EMS controleert automatisch opnieuw en plant niet op onbevestigde prijzen.`) : null,
  !pts.some((p) => p.status !== "MISSING") ? h("div", { class: "notice warn inline" }, "Geen prijsdata. Stel een prijsbron in bij Instellingen → Marktgegevens.") : h("div", {},
    h("div", { class: "grid cols-4" },
      kpi("Nu (afname)", cur ? fmtPrice(cur.import) : "niet beschikbaar", cur ? `markt ${fmtPrice(cur.spot)} · ${dateTime(cur.start)}–${dateTime(cur.end).slice(-5)}` : data.current_reason),
      kpi("Nu (teruglevering)", cur ? fmtPrice(cur.export) : "niet beschikbaar"),
      kpi("Goedkoopste komend", min ? fmtPrice(min.import) : "onvoldoende prijsinformatie", min ? `${at(min)} (beursprijs)` : ""),
      kpi("Duurste komend", max ? fmtPrice(max.import) : "onvoldoende prijsinformatie", max ? `${at(max)} (beursprijs)` : "")),
    forecastAhead ? h("p", { class: "small muted" }, "Na de laatst gepubliceerde beursprijs volgt een prognose (gestippeld, met bandbreedte). Een prognose is nooit een bevestigde prijs.") : null,
    h("div", { class: "card" }, h("h3", {}, "Afnameprijs inclusief opslagen, belasting en btw (€/kWh)"),
      priceLine({ ...series("import"), low: pts.map((p) => p.import_low), high: pts.map((p) => p.import_high), title: "afnameprijs" })),
    h("div", { class: "card" }, h("h3", {}, "Terugleverprijs (€/kWh)"), priceLine({ ...series("export"), title: "terugleverprijs" })),
    h("div", { class: "card" }, h("h3", {}, "Marktprijs (day-ahead, €/kWh excl. btw)"),
      priceLine({ ...series("spot"), low: pts.map((p) => p.spot_low), high: pts.map((p) => p.spot_high), title: "marktprijs" })),
    h("div", { class: "card" }, h("h3", {}, "Prijsbron"), h("table", {}, h("tbody", {},
      h("tr", {}, h("td", {}, "Bron"), h("td", {}, `${st.provider || "geen"}${st.fallback_provider ? ` (reserve: ${st.fallback_provider})` : ""}`)),
      h("tr", {}, h("td", {}, "Laatste geslaagde synchronisatie"), h("td", {}, st.last_success ? dateTime(st.last_success) : "—")),
      h("tr", {}, h("td", {}, "Bekend tot"), h("td", {}, st.known_until ? dateTime(st.known_until) : "—")),
      h("tr", {}, h("td", {}, `Morgen (${pub.date || "—"})`), h("td", {}, `${PUB_LABEL[pub.state] || "—"} · ${pub.known ?? 0}/${pub.intervals ?? 0} kwartieren`)),
      h("tr", {}, h("td", {}, "Volgende controle"), h("td", {}, st.next_check_s ? `over ${Math.round(st.next_check_s / 60)} min` : "—")),
      st.problems?.length ? h("tr", {}, h("td", {}, "Verworpen waarden"), h("td", { class: "small" }, st.problems.join("; "))) : null,
      h("tr", {}, h("td", {}, "Prognose"), h("td", {}, st.forecast?.enabled ? `${st.forecast.horizon_hours} uur na de laatste beursprijs (${st.forecast.model})` : "uit"))))),
    h("details", { class: "card" }, h("summary", {}, "Alle intervallen als tabel"),
      h("div", { class: "tbl-wrap" }, h("table", {}, h("caption", { class: "small muted" }, `Tijden in ${data.timezone}`),
        h("thead", {}, h("tr", {}, ["Interval", "Markt", "Afname", "Teruglevering", "Status", "Betrouwbaarheid"].map((x) => h("th", { scope: "col" }, x)))),
        h("tbody", {}, pts.map((p) => h("tr", { class: p === pts[marker] ? "now" : "" },
          h("td", {}, dateTime(p.start)), h("td", { class: "num" }, num(p.spot, 4)), h("td", { class: "num" }, num(p.import, 4)),
          h("td", { class: "num" }, num(p.export, 4)), h("td", {}, h("span", { class: `pill st-${p.status}` }, STATUS_LABEL[p.status] || p.status)),
          h("td", { class: "num" }, p.confidence === null || p.confidence === undefined ? "—" : `${Math.round(p.confidence * 100)}%`))))))),
    h("p", { class: "muted small" }, `Markt per ${data.market_resolution_min} min, uw contract rekent per ${data.contract_resolution_min} min. Doorgetrokken = gepubliceerde beursprijs, gestippeld = prognose, grijs = verouderd of ontbrekend.`)));
}
