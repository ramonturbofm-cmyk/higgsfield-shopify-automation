import { api, can, connectWs, guard, h, logout, on, setSession, state, wsStatus } from "./lib.js";

const NAV = [
  ["dashboard", "Dashboard", "⚡"], ["installation", "Mijn installatie", "🏠"], ["energy", "Energie", "📈"], ["planning", "Planning", "🗓"],
  ["prices", "Prijzen", "€"], ["battery", "Batterij", "🔋"], ["pv", "Zonnepanelen", "☀"],
  ["heatpump", "Warmtepomp", "🌡"], ["ev", "Laadpaal / EV", "🚗"], "-",
  ["devices", "Apparaten", "🔌"], ["automations", "Automatiseringen", "⚙"], ["finance", "Financiën", "💶"],
  ["backtest", "Backtest & Auto-Tune", "🧪"], ["notifications", "Meldingen", "🔔"], "-",
  ["settings", "Instellingen", "🛠"], ["nodes", "Nodes", "🖧"], ["system", "Systeem", "🖥"],
];
const VIEWS = {
  dashboard: () => import("./views/dashboard.js"), energy: () => import("./views/energy.js"),
  planning: () => import("./views/planning.js"), prices: () => import("./views/prices.js"),
  battery: () => import("./views/battery.js"), pv: () => import("./views/pv.js"),
  heatpump: () => import("./views/heatpump.js"), ev: () => import("./views/ev.js"),
  devices: () => import("./views/devices.js"), automations: () => import("./views/automations.js"),
  finance: () => import("./views/finance.js"), backtest: () => import("./views/backtest.js"),
  notifications: () => import("./views/notifications.js"), settings: () => import("./views/settings.js"),
  system: () => import("./views/system.js"), installation: () => import("./views/installation.js"),
  nodes: () => import("./views/nodes.js"),
};
const TITLES = Object.fromEntries(NAV.filter((n) => n !== "-").map(([k, t]) => [k, t]));
let cleanup = null;

// ------------------------------------------------------------------ theme
function applyTheme() {
  let t = "auto";
  try { t = localStorage.getItem("ems.theme") || "auto"; } catch { /* ignore */ }
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  return t;
}
function cycleTheme() {
  const order = ["auto", "light", "dark"];
  const next = order[(order.indexOf(applyTheme()) + 1) % 3];
  try { localStorage.setItem("ems.theme", next); } catch { /* ignore */ }
  applyTheme(); renderTopbar();
}

// ------------------------------------------------------------------ layout
function renderSidebar(active) {
  const side = document.getElementById("sidebar");
  side.replaceChildren(
    h("div", { class: "brand" }, h("img", { src: "/static/icon.svg", alt: "" }), "Energy Manager"),
    h("nav", { class: "nav" }, NAV.map((n) => n === "-" ? h("div", { class: "sep" }) :
      h("a", { href: `#/${n[0]}`, class: n[0] === active ? "active" : "", onclick: () => side.classList.remove("open") },
        h("span", { "aria-hidden": "true" }, n[2]), n[1]))));
}

// Inside the Windows app the connect screen passes its own URL so we can link back.
const APP_HOME = (() => {
  const q = new URLSearchParams(location.search).get("app");
  if (q) { try { sessionStorage.setItem("ems.app", q); } catch { /* ignore */ } return q; }
  try { return sessionStorage.getItem("ems.app"); } catch { return null; }
})();

function renderTopbar(title) {
  const bar = document.getElementById("topbar");
  if (title !== undefined) bar.dataset.title = title;
  const live = state.live;
  const theme = (() => { try { return localStorage.getItem("ems.theme") || "auto"; } catch { return "auto"; } })();
  const gm = live?.grid_meter;
  const pills = [];
  if (state.info?.mode === "demo") pills.push(h("span", { class: "pill warn mode-tag", title: "Gesimuleerde woning" }, "Demo Mode"));
  if (gm) {
    pills.push(gm.status === "no_primary_grid_meter"
      ? h("span", { class: "pill warn", title: gm.reason }, "Geen netmeter")
      : h("span", { class: `pill ${gm.available ? "good" : "bad"}`, title: gm.reason },
        h("span", { class: "dot" }), `Netmeter ${gm.available ? "online" : "offline"}`));
  }
  const st = live?.ems_status;
  if (st) {
    const cls = { AUTOMATIC: "good", SHADOW_MODE: "warn", MANUAL_OVERRIDE: "warn", DEGRADED: "warn", SAFE_MODE: "bad", ERROR: "bad" }[st.state] || "";
    pills.push(h("a", { class: `pill ${cls}`, href: "#/installation", title: st.reason || "" }, h("span", { class: "dot" }), `EMS: ${st.label}`));
  } else if (live?.failsafe?.active) pills.push(h("span", { class: "pill bad", title: live.failsafe.reason }, "Fallback actief"));
  if (!state.online) pills.push(h("span", { class: "pill bad", role: "status", title: "De EMS-server antwoordt niet" },
    h("span", { class: "dot" }), "Server offline"));
  else pills.push(h("span", { class: `pill ${wsStatus.connected ? "good" : ""}`, title: "Live-verbinding (WebSocket)" },
    h("span", { class: "dot" }), wsStatus.connected ? "live" : "verbinden…"));
  bar.replaceChildren(
    h("button", { class: "btn sm hamb", "aria-label": "Menu", onclick: () => document.getElementById("sidebar").classList.toggle("open") }, "☰"),
    h("span", { class: "title" }, bar.dataset.title || ""),
    ...pills,
    h("button", { class: "btn sm", title: "Thema: automatisch / licht / donker", onclick: cycleTheme },
      { auto: "◐ Auto", light: "☀ Licht", dark: "☾ Donker" }[theme]),
    state.user ? h("span", { class: "pill", title: `rol: ${state.role}` }, state.user) : null,
    state.user ? h("button", { class: "btn sm", onclick: () => logout() }, "Uitloggen") : null,
    APP_HOME && /^(tauri:|https?:\/\/tauri\.localhost)/.test(APP_HOME) ? h("a", { class: "btn sm", href: `${APP_HOME}?choose=1` }, "Andere server") : null,
  );
}

function renderBanner() {
  const el = document.getElementById("banner");
  const live = state.live;
  const items = [];
  if (live?.failsafe?.active) items.push(h("div", { class: "notice bad" }, "⚠",
    h("div", {}, h("b", {}, "EMS-fallback actief. "), "Alle apparaten draaien op hun eigen regeling. Oorzaak: ", live.failsafe.reason)));
  if (live?.grid_meter?.status === "no_primary_grid_meter") items.push(h("div", { class: "notice warn" }, "ⓘ",
    h("div", {}, h("b", {}, "Geen primaire netmeter ingesteld. Sommige EMS-functies zijn beperkt. "),
      "Zero-export, piekbegrenzing en fasebewaking zijn uitgeschakeld. ",
      can("installer") ? h("a", { href: "#/devices/add" }, "Netmeter toevoegen") : null)));
  else if (live?.grid_meter?.needs_confirmation) items.push(h("div", { class: "notice info" }, "ⓘ",
    h("div", {}, live.grid_meter.reason, " ", h("a", { href: `#/devices/${live.grid_meter.device_id}` }, "Bekijken"))));
  el.replaceChildren(...items);
}

// ------------------------------------------------------------------ auth views
function loginView(root, setup) {
  const user = h("input", { autocomplete: "username", required: true, value: setup ? "" : (state.info?.demo_login?.username || "") });
  const pass = h("input", { type: "password", autocomplete: setup ? "new-password" : "current-password", required: true,
    value: setup ? "" : (state.info?.demo_login?.password || "") });
  const msg = h("div", { class: "nok small" });
  const form = h("form", { class: "card login", onsubmit: async (e) => {
    e.preventDefault(); msg.textContent = "";
    try {
      const r = await api(setup ? "/auth/setup" : "/auth/login", { method: "POST", body: { username: user.value, password: pass.value } });
      setSession(r); location.hash = "#/dashboard"; boot();
    } catch (err) { msg.textContent = err.message; }
  } },
  h("div", { class: "brand" }, h("img", { src: "/static/icon.svg", alt: "" }), "Energy Manager"),
  h("h1", {}, setup ? "Eerste gebruiker aanmaken" : "Inloggen"),
  setup ? h("p", { class: "muted small" }, "Deze gebruiker wordt installateur (alle rechten). Kies een sterk wachtwoord (min. 8 tekens).") : null,
  state.info?.demo_login && !setup ? h("div", { class: "notice info inline" }, "Demo Mode: gebruiker ", h("b", {}, "demo"), " / wachtwoord ", h("b", {}, "demo")) : null,
  h("div", { class: "grid" }, h("label", { class: "f" }, "Gebruikersnaam", user), h("label", { class: "f" }, "Wachtwoord", pass),
    msg, h("button", { class: "btn primary", type: "submit" }, setup ? "Aanmaken en inloggen" : "Inloggen")));
  document.getElementById("sidebar").replaceChildren();
  document.getElementById("topbar").replaceChildren();
  document.getElementById("banner").replaceChildren();
  root.replaceChildren(form);
}

// ------------------------------------------------------------------ router
async function route() {
  const root = document.getElementById("view");
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  const [, page = "dashboard", ...rest] = (location.hash || "#/dashboard").split("/");
  if (!state.info) state.info = await api("/system/info");
  if (page === "setup" || (state.info.setup_required && !state.user)) return loginView(root, true);
  if (page === "login" || !state.user) return loginView(root, false);
  const view = VIEWS[page] ? page : "dashboard";
  renderSidebar(view);
  renderTopbar(TITLES[view]);
  renderBanner();
  root.replaceChildren(h("div", { class: "empty" }, "Laden…"));
  try {
    const mod = await VIEWS[view]();
    root.replaceChildren();
    cleanup = await mod.render(root, rest) || null;
    root.focus({ preventScroll: true });
  } catch (e) {
    root.replaceChildren(h("div", { class: "notice bad inline" }, `Fout bij laden: ${e.message}`));
  }
}

async function boot() {
  applyTheme();
  try { state.info = await api("/system/info"); } catch (e) {
    document.getElementById("view").replaceChildren(h("div", { class: "notice bad inline" }, `Server niet bereikbaar: ${e.message}`));
    return;
  }
  try {
    const sess = await api("/auth/session");   // valid session cookie? (never 401)
    if (!sess.authenticated) throw new Error("niet ingelogd");
    setSession(sess);
    state.settings = await api("/settings");
    state.tz = state.settings.site.timezone;
    state.live = await api("/energy/live");
    connectWs();
  } catch { state.user = null; state.role = null; }
  route();
}

on((msg) => {
  if (msg.type === "live" || msg.type === "ws" || msg.type === "online") { renderTopbar(); renderBanner(); }
  if (msg.type === "notification" && ["warning", "critical"].includes(msg.data.level)) {
    import("./lib.js").then(({ toast }) => toast(msg.data.message, msg.data.level === "critical"));
  }
});
window.addEventListener("hashchange", route);
boot();
