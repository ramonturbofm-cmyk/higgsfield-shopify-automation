// Shared helpers: DOM builder, API client, WebSocket, formatting, toasts.

// append()/replaceChildren() would render null/undefined/false as text; views build
// children conditionally, so drop empty entries globally.
for (const proto of [Element.prototype, DocumentFragment.prototype]) {
  for (const name of ["append", "replaceChildren", "prepend"]) {
    const orig = proto[name];
    proto[name] = function (...nodes) {
      return orig.apply(this, nodes.flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false));
    };
  }
}

export const state = {
  token: null, user: null, role: null, info: null, settings: null, tz: "Europe/Amsterdam",
  live: null, level: "simple", listeners: new Set(),
};
try {
  state.token = localStorage.getItem("ems.token");
  state.level = localStorage.getItem("ems.level") || "simple";
} catch { /* storage unavailable */ }

const ROLES = ["viewer", "operator", "admin", "installer"];
export const can = (role) => state.role && ROLES.indexOf(state.role) >= ROLES.indexOf(role);

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;            // only used with static, trusted markup
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false)
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}

export async function api(path, { method = "GET", body, raw = false, form } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(`/api/v1${path}`, { method, headers, body: payload });
  if (res.status === 401 && !path.startsWith("/auth/")) { logout(); throw new ApiError(401, "Sessie verlopen"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); }
    catch { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  if (raw) return res;
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

export function logout() {
  state.token = null; state.user = null; state.role = null;
  try { localStorage.removeItem("ems.token"); } catch { /* ignore */ }
  location.hash = "#/login";
}

export function saveToken(token) {
  state.token = token;
  try { localStorage.setItem("ems.token", token); } catch { /* ignore */ }
}

// ------------------------------------------------------------------ websocket
let ws = null, wsRetry = 1000, wsWanted = false;
export const wsStatus = { connected: false };
export function connectWs() {
  wsWanted = true;
  if (ws || !state.token) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/v1/ws?token=${encodeURIComponent(state.token)}`);
  ws.onopen = () => { wsRetry = 1000; wsStatus.connected = true; emit({ type: "ws", data: true }); };
  ws.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "live") state.live = msg.data;
    emit(msg);
  };
  ws.onclose = (ev) => {
    ws = null; wsStatus.connected = false; emit({ type: "ws", data: false });
    if (ev.code === 4401) { logout(); return; }
    if (wsWanted) setTimeout(connectWs, wsRetry);
    wsRetry = Math.min(30000, wsRetry * 2);
  };
}
export function disconnectWs() { wsWanted = false; if (ws) ws.close(); }
export function on(fn) { state.listeners.add(fn); return () => state.listeners.delete(fn); }
function emit(msg) { for (const fn of [...state.listeners]) { try { fn(msg); } catch (e) { console.error(e); } } }

// ------------------------------------------------------------------ formatting
const nf = (d) => new Intl.NumberFormat("nl-NL", { minimumFractionDigits: d, maximumFractionDigits: d });
export const num = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : nf(d).format(v);
export function power(w, signed = false) {
  if (w === null || w === undefined) return "—";
  const a = Math.abs(w), sign = signed && w > 0 ? "+" : (w < 0 ? "−" : "");
  return a >= 1000 ? `${sign}${num(a / 1000, a >= 10000 ? 1 : 2)} kW` : `${sign}${num(a, 0)} W`;
}
export const kwh = (v, d = 1) => v === null || v === undefined ? "—" : `${num(v, d)} kWh`;
export const eur = (v, d = 2) => v === null || v === undefined ? "—" : `€ ${num(v, d)}`;
export const pct = (v, d = 0) => v === null || v === undefined ? "—" : `${num(v, d)}%`;
export const temp = (v) => v === null || v === undefined ? "—" : `${num(v, 1)} °C`;
export function time(iso, opts = { hour: "2-digit", minute: "2-digit" }) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("nl-NL", { timeZone: state.tz, ...opts }).format(new Date(iso));
}
export const dateTime = (iso) => time(iso, { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
export function ago(iso) {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${num(Math.max(0, s), 1)} s geleden`;
  if (s < 3600) return `${Math.round(s / 60)} min geleden`;
  return dateTime(iso);
}

// ------------------------------------------------------------------ toasts
export function toast(text, err = false) {
  const box = document.getElementById("toast");
  const el = h("div", { class: err ? "err" : "" }, text);
  box.append(el);
  setTimeout(() => el.remove(), err ? 7000 : 3500);
}
export async function guard(fn, okText) {
  try { const r = await fn(); if (okText) toast(okText); return r; }
  catch (e) { toast(e.message || String(e), true); throw e; }
}

export const STATUS_LABEL = { online: "online", offline: "offline", stale: "verouderd", disabled: "uitgeschakeld",
  unknown: "onbekend" };
export function statusPill(status) {
  const cls = status === "online" ? "good" : status === "stale" ? "warn" : status === "offline" ? "bad" : "";
  return h("span", { class: `pill ${cls}` }, h("span", { class: "dot" }), STATUS_LABEL[status] || status);
}

export function field(label, input, help) {
  return h("label", { class: "f" }, h("span", { class: "lbl" }, label,
    help ? h("span", { class: "help", title: help }, "?") : null), input);
}

export function download(path, name) {
  return guard(async () => {
    const res = await api(path, { raw: true });
    const blob = await res.blob();
    const a = h("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  });
}
