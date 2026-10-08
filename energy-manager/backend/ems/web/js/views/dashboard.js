import { api, eur, h, on, pct, power, state, svg, temp, time } from "../lib.js";
import { decisionList, fmtPrice, kpi } from "./common.js";

const QUALITY = { STALE: "verouderd", ESTIMATED: "geschat", INVALID: "ongeldig", MISSING: "ontbreekt", FORECAST: "prognose" };

const NODES = {
  pv: { x: 380, y: 62, label: "Zonnepanelen", cls: "c4" },
  grid: { x: 110, y: 205, label: "Net", cls: "c7" },
  house: { x: 380, y: 205, label: "Huis", cls: "c1" },
  battery: { x: 650, y: 205, label: "Batterij", cls: "c3" },
  hp: { x: 230, y: 350, label: "Warmtepomp", cls: "c2" },
  ev: { x: 530, y: 350, label: "Laadpaal", cls: "c5" },
};

function flowDiagram(f, present) {
  const s = svg("svg", { class: "flow", viewBox: "15 10 730 410", role: "img", "aria-label": "Energiestromen" });
  const links = [["pv", "house"], ["grid", "house"], ["battery", "house"], ["hp", "house"], ["ev", "house"]];
  for (const [a, b] of links) {
    if (!present[a]) continue;
    const A = NODES[a], B = NODES[b];
    s.append(svg("line", { class: "link", x1: A.x, y1: A.y, x2: B.x, y2: B.y }));
  }
  const stream = (from, to, w, cls) => {
    if (!w || Math.abs(w) < 20) return;
    const A = NODES[from], B = NODES[to];
    const speed = Math.max(0.35, 2.2 - Math.abs(w) / 4000);
    s.append(svg("line", { class: `stream ${cls}`, x1: A.x, y1: A.y, x2: B.x, y2: B.y, style: `animation-duration:${speed}s` }));
  };
  if (present.pv) stream("pv", "house", f.pv_w, NODES.pv.cls);
  if (present.grid && f.grid_w !== null) f.grid_w >= 0 ? stream("grid", "house", f.grid_w, NODES.grid.cls) : stream("house", "grid", -f.grid_w, NODES.grid.cls);
  if (present.battery) f.battery_w < 0 ? stream("battery", "house", -f.battery_w, NODES.battery.cls) : stream("house", "battery", f.battery_w, NODES.battery.cls);
  if (present.hp) stream("house", "hp", f.hp_w, NODES.hp.cls);
  if (present.ev) stream("house", "ev", f.ev_w, NODES.ev.cls);
  const node = (key, value, extra) => {
    if (!present[key]) return;
    const n = NODES[key];
    s.append(svg("g", { class: "node", transform: `translate(${n.x - 85},${n.y - 40})` },
      svg("rect", { width: 170, height: extra ? 92 : 76, rx: 14 }),
      svg("rect", { width: 5, height: extra ? 92 : 76, rx: 2.5, class: n.cls }),
      svg("text", { class: "t", x: 16, y: 24 }, n.label),
      svg("text", { class: "v", x: 16, y: 56 }, value),
      extra ? svg("text", { class: "x", x: 16, y: 80 }, extra) : null));
  };
  node("pv", power(f.pv_w));
  node("grid", f.grid_w === null ? "Geen data" : power(Math.abs(f.grid_w)),
    f.grid_w === null ? "geen netmeter" : f.grid_w > 20 ? "afname" : f.grid_w < -20 ? "teruglevering" : "in balans");
  node("house", power(f.house_w), f.house_w === null ? "niet te bepalen" : null);
  node("battery", power(f.battery_w, true), `SOC ${pct(f.soc_pct)}${f.battery_w > 20 ? " · laden" : f.battery_w < -20 ? " · ontladen" : ""}`);
  node("hp", power(f.hp_w), f.indoor_c !== null ? `binnen ${temp(f.indoor_c)}` : null);
  node("ev", power(f.ev_w));
  return s;
}

export async function render(root) {
  const flowBox = h("div", { class: "card" });
  const tiles = h("div", { class: "grid cols-6" });
  const nowBox = h("div", { class: "card" });
  const devBox = h("div", { class: "card" });
  const decBox = h("div", {});
  root.append(h("h1", {}, state.settings?.site?.name || "Dashboard"), tiles, h("div", { style: { height: "14px" } }),
    h("div", { class: "grid cols-2" }, flowBox, h("div", { class: "grid" }, nowBox, devBox)),
    h("h2", {}, "Laatste EMS-beslissingen"), decBox);

  const cats = (live) => {
    const c = new Set(Object.values(live.devices || {}).map((d) => d.category));
    return { pv: c.has("pv_inverter") || c.has("hybrid_inverter"), grid: true, house: true,
      battery: c.has("battery") || c.has("hybrid_inverter"), hp: c.has("heat_pump"), ev: c.has("ev_charger") };
  };
  const paint = (live) => {
    if (!live) return;
    const f = live.flows || {};
    flowBox.replaceChildren(h("h3", {}, "Live energiestromen"), live.flows ? flowDiagram(f, cats(live)) : h("div", { class: "empty" }, "Nog geen meetgegevens"),
      h("div", { class: "muted small" }, `Bijgewerkt ${time(live.timestamp, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`,
        live.grid_meter?.age_s !== null && live.grid_meter?.age_s !== undefined ? ` · netmeting ${live.grid_meter.age_s.toFixed(1).replace(".", ",")} s oud` : ""));
    const q = live.quality || {};
    const na = (key, v) => (q[key] && ["MISSING", "INVALID", "UNKNOWN"].includes(q[key])) ? "N/A" : v;
    const qnote = (key) => q[key] && !["GOOD", "CALCULATED", "UNKNOWN"].includes(q[key]) ? ` · ${QUALITY[q[key]] || q[key]}` : "";
    const st = live.ems_status || {};
    tiles.replaceChildren(
      kpi("Zonnepanelen", na("pv", power(f.pv_w)), `opwek nu${qnote("pv")}`),
      kpi("Huis", na("house", power(f.house_w)), q.house === "CALCULATED" ? "berekend uit netmeting" : (q.house === "MISSING" ? "niet te bepalen" : "")),
      kpi("Batterij", na("soc", pct(f.soc_pct)), `${power(f.battery_w, true)}${f.battery_w > 20 ? " laden" : f.battery_w < -20 ? " ontladen" : ""}${qnote("battery")}`),
      kpi("Net", f.grid_w === null || f.grid_w === undefined ? "N/A" : power(f.grid_w, true),
        f.grid_w === null || f.grid_w === undefined ? "geen actuele netmeting" : f.grid_w > 20 ? "afname" : f.grid_w < -20 ? "teruglevering" : "in balans"),
      h("div", { class: "card kpi" }, h("div", { class: "l" }, "Stroomprijs nu"),
        h("div", { class: "v" }, fmtPrice(live.price?.import)),
        h("div", { class: "s" }, "afname · ", h("b", {}, "markt "), fmtPrice(live.price?.spot), " · ", h("b", {}, "teruglevering "), fmtPrice(live.price?.export))),
      h("a", { class: `card kpi status-${(st.state || "").toLowerCase()}`, href: "#/installation", style: { textDecoration: "none", color: "inherit" } },
        h("div", { class: "l" }, "EMS-status"), h("div", { class: "v" }, st.label || "—"), h("div", { class: "s" }, st.reason || "")));
    const n = live.now;
    nowBox.replaceChildren(h("h3", {}, "Wat doet het EMS nu?"),
      n ? h("div", { class: "now" },
        h("div", { class: "now-what" }, n.what, n.outcome && n.outcome !== "sent" ? h("span", { class: "pill warn", style: { marginLeft: "8px" } },
          { shadow: "schaduw: EMS zou", rejected: "geweigerd", not_commissioned: "niet in bedrijf", dry_run: "proef" }[n.outcome] || n.outcome) : null),
        h("dl", { class: "now-dl" },
          h("dt", {}, "Waarom?"), h("dd", {}, n.why?.length ? h("ul", {}, n.why.map((r) => h("li", {}, r))) : "—"),
          h("dt", {}, "Tot wanneer?"), h("dd", {}, n.until ? `ongeveer ${time(n.until)} (volgens planning)` : "tot de volgende herberekening (elke 5 min)"),
          h("dt", {}, "Verwacht voordeel"), h("dd", {}, n.expected_benefit_eur === null || n.expected_benefit_eur === undefined ? "—" : `${eur(n.expected_benefit_eur)} t.o.v. zonder EMS (planning)`),
          h("dt", {}, "Grenzen"), h("dd", { class: "small" }, (n.limits || []).join(" · "))))
        : h("div", { class: "muted" }, "Nog geen beslissing — het EMS start op of er zijn geen bestuurbare apparaten."),
      live.balance && live.balance.ok === false ? h("div", { class: "notice warn inline" }, "Energiebalans klopt niet: ",
        `${power(live.balance.residual_w)} onverklaard. Mogelijke oorzaken: `, live.balance.hints.join("; ")) : null,
      live.failsafe?.active ? h("div", { class: "notice bad inline" }, "Veilige modus: ", live.failsafe.reason) : null);
    devBox.replaceChildren(h("h3", {}, "Apparaten"), h("table", {}, h("tbody", {},
      Object.entries(live.devices || {}).map(([id, d]) => h("tr", {},
        h("td", {}, h("a", { href: `#/devices/${id}` }, d.name)),
        h("td", {}, h("span", { class: `pill ${d.status === "online" ? "good" : d.status === "offline" ? "bad" : "warn"}` }, d.status)),
        h("td", { class: "small muted" }, d.control_level === "full" ? "" : `inbedrijfstelling: ${d.control_level}`))))));
  };
  paint(state.live);
  const loadDecisions = async () => decBox.replaceChildren(decisionList(await api("/decisions?limit=6")));
  loadDecisions();
  const off = on((msg) => {
    if (msg.type === "live") paint(msg.data);
    if (msg.type === "decision") loadDecisions();
  });
  return off;
}
