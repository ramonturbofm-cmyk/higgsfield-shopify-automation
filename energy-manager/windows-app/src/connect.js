// Start screen of the Windows app. Either starts the EMS server built into this
// installation (background process, see src-tauri/src/main.rs) or finds a server on
// the network (Raspberry Pi). Servers are probed via GET /api/v1/system/info (public;
// the server allows the app's origin via CORS); then the server's own web interface
// is loaded in this window.
const LOCAL = "http://127.0.0.1:8080";
const DEFAULTS = ["http://energy-manager.local:8080", "http://raspberrypi.local:8080", LOCAL];
const tauri = window.__TAURI__?.core;
const $ = (id) => document.getElementById(id);
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } };

function normalize(input) {
  let s = input.trim();
  if (!s) return null;
  if (!/^https?:\/\//i.test(s)) s = `http://${s}`;
  const u = new URL(s);
  if (!u.port && u.protocol === "http:") u.port = "8080";
  return `${u.protocol}//${u.host}`;
}

async function probe(base, ms = 3000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(`${base}/api/v1/system/info`, { signal: ctl.signal });
    if (!r.ok) return null;
    const info = await r.json();
    return info.product === "Energy Manager" ? info : null;
  } catch { return null; } finally { clearTimeout(t); }
}

function open(base, hash = "#/dashboard") {
  const servers = [base, ...load("servers", []).filter((s) => s !== base)].slice(0, 8);
  save("servers", servers);
  save("last", base);
  // Tell the web UI it runs inside the app so it can offer "Andere server".
  location.href = `${base}/?app=${encodeURIComponent(location.origin + location.pathname)}${hash}`;
}

function item(base, info) {
  const el = document.createElement("div");
  el.className = "item";
  const left = document.createElement("div");
  const title = document.createElement("b");
  title.textContent = info ? info.site : base;
  const sub = document.createElement("div");
  sub.className = "small muted";
  sub.textContent = info ? `${base} · versie ${info.version} · ${info.mode === "demo" ? "Demo Mode" : "productie"}` : `${base} · niet bereikbaar`;
  left.append(title, sub);
  const btn = document.createElement("button");
  btn.className = "btn primary";
  btn.textContent = "Verbinden";
  btn.disabled = !info;
  btn.onclick = () => open(base);
  el.append(left, btn);
  return el;
}

async function scan() {
  const list = $("list");
  list.replaceChildren(Object.assign(document.createElement("p"), { className: "muted", textContent: "Zoeken…" }));
  const candidates = [...new Set([...load("servers", []), ...DEFAULTS])];
  const results = await Promise.all(candidates.map(async (b) => [b, await probe(b)]));
  const found = results.filter(([, i]) => i);
  list.replaceChildren(...(found.length ? found.map(([b, i]) => item(b, i))
    : [Object.assign(document.createElement("p"), { className: "muted", textContent: "Geen server gevonden. Voer het adres van de Raspberry Pi handmatig in. Geen Raspberry Pi? Gebruik „Op deze computer” hierboven." })]));
}

$("scan").onclick = scan;
$("manual").onsubmit = async (e) => {
  e.preventDefault();
  $("msg").textContent = "";
  let base;
  try { base = normalize($("addr").value); } catch { base = null; }
  if (!base) { $("msg").textContent = $("addr").value.trim() ? "Ongeldig adres." : "Vul eerst het IP-adres of de naam van de Raspberry Pi in (bijv. 192.168.1.20), of 127.0.0.1 voor de server op deze pc."; return; }
  $("msg").textContent = "Verbinden…";
  const info = await probe(base, 8000);
  if (!info) {
    // The check may be blocked while the server itself is fine: offer to open it directly.
    const direct = Object.assign(document.createElement("button"), { type: "button", className: "btn", textContent: "Toch openen" });
    direct.onclick = () => open(base);
    $("msg").replaceChildren(`Geen Energy Manager gevonden op ${base}. Staat de server aan en zit deze pc op hetzelfde netwerk? `,
      "U kunt het adres ook direct openen: ", direct);
    return;
  }
  open(base);
};

// ------------------------------------------------------------ built-in server
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const localMsg = (text, err = false) => { $("localMsg").textContent = text; $("localMsg").className = `small${err ? " err" : ""}`; };
const selectedMode = () => document.querySelector("input[name=mode]:checked")?.value || "production";

async function startLocal(mode, hash) {
  save("localMode", mode);
  $("startLocal").disabled = true;
  try {
    let info = await probe(LOCAL, 1500);
    if (info && info.mode !== mode) {           // running in the other mode: restart
      localMsg("EMS wordt herstart in de gekozen modus…");
      await tauri.invoke("stop_local_server");
      info = null;
    }
    if (!info) {
      localMsg("EMS wordt gestart… (de eerste keer kan dit tot een minuut duren)");
      await tauri.invoke("start_local_server", { mode });
      for (let i = 0; i < 120 && !info; i++) { await sleep(1000); info = await probe(LOCAL, 1500); }
    }
    if (!info) {
      localMsg("Het EMS start niet. Kijk in %LOCALAPPDATA%\\EnergyManager\\logs\\server.log of start "
        + "'Energy Manager Server (met venster)' uit het Startmenu om de foutmelding te zien.", true);
      return;
    }
    open(LOCAL, hash);
  } catch (e) {
    localMsg(String(e), true);
  } finally {
    $("startLocal").disabled = false;
  }
}

async function stopLocal() {
  $("stopLocal").disabled = true;
  localMsg("EMS wordt gestopt… apparaten krijgen hun eigen regeling terug.");
  try { await tauri.invoke("stop_local_server"); localMsg("Het EMS op deze computer is gestopt."); $("stopLocal").hidden = true; }
  catch (e) { localMsg(String(e), true); }
  $("stopLocal").disabled = false;
}

async function initLocal() {
  const available = tauri ? await tauri.invoke("local_server_available").catch(() => false) : false;
  if (!available) return false;
  $("local").hidden = false;
  const mode = load("localMode", "production");
  for (const r of document.querySelectorAll("input[name=mode]")) r.checked = r.value === mode;
  $("startLocal").onclick = () => startLocal(selectedMode());
  $("stopLocal").onclick = stopLocal;
  $("openNodes").hidden = false;
  $("openNodes").onclick = () => startLocal(load("localMode", selectedMode()), "#/nodes");
  const info = await probe(LOCAL, 1500);
  if (info) {
    $("stopLocal").hidden = false;
    localMsg(`Het EMS draait al op deze computer (${info.mode === "demo" ? "Demo" : "eigen installatie"}).`);
  }
  return true;
}

(async () => {
  const choose = new URLSearchParams(location.search).has("choose");
  const last = load("last", null);
  const local = await initLocal();
  if (local && last === LOCAL && !choose) {
    const box = $("auto");
    box.hidden = false;
    box.textContent = "Energy Manager op deze computer wordt geopend…";
    await startLocal(load("localMode", "production"));
    box.hidden = true;
    return;
  }
  if (local && !last) return;                   // first start: let the user choose
  if (last && !choose) {
    const box = $("auto");
    box.hidden = false;
    box.textContent = `Verbinden met ${last}…`;
    const info = await probe(last);
    if (info) {
      box.textContent = `Verbinden met ${info.site} (${last})…`;
      const cancel = document.createElement("button");
      cancel.className = "btn";
      cancel.textContent = "Andere server kiezen";
      let go = true;
      cancel.onclick = () => { go = false; box.hidden = true; scan(); };
      box.append(" ", cancel);
      setTimeout(() => { if (go) open(last); }, 1200);
      return;
    }
    box.textContent = `${last} is niet bereikbaar.`;
  }
  scan();
})();
