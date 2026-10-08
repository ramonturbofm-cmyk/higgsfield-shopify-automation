// Connect screen of the Windows app. Probes EMS servers via GET /api/v1/system/info
// (public endpoint; the server allows the app's origin via CORS) and then loads the
// server's own web interface in this window.
const DEFAULTS = ["http://energy-manager.local:8080", "http://raspberrypi.local:8080", "http://127.0.0.1:8080"];
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

function open(base) {
  const servers = [base, ...load("servers", []).filter((s) => s !== base)].slice(0, 8);
  save("servers", servers);
  save("last", base);
  // Tell the web UI it runs inside the app so it can offer "Andere server".
  location.href = `${base}/?app=${encodeURIComponent(location.origin + location.pathname)}#/dashboard`;
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
    : [Object.assign(document.createElement("p"), { className: "muted", textContent: "Geen server gevonden. Voer het adres van de Raspberry Pi handmatig in. Nog geen Raspberry Pi? Start via het Startmenu \"Energy Manager Server (Demo Mode)\" en klik daarna opnieuw op Zoeken." })]));
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
    $("msg").replaceChildren(`Geen Energy Manager gevonden op ${base}. Staat de server aan (zwart venster "Energy Manager server")? `,
      "U kunt het adres ook direct openen: ", direct);
    return;
  }
  open(base);
};

(async () => {
  const choose = new URLSearchParams(location.search).has("choose");
  const last = load("last", null);
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
