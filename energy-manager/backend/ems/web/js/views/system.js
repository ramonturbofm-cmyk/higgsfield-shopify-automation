import { api, can, dateTime, download, guard, h, num, statusPill } from "../lib.js";
import { decisionList, kpi } from "./common.js";

export async function render(root) {
  const s = await api("/system/status");
  const upd = await api("/system/update");
  const up = s.uptime_s;
  root.append(h("h1", {}, "Systeem"),
    h("div", { class: "grid cols-4" }, kpi("Versie", s.version, `modus: ${s.mode}`), kpi("Uptime", up > 86400 ? `${num(up / 86400, 1)} dagen` : `${num(up / 3600, 1)} uur`, `regelmodus: ${s.gate_mode}`),
      kpi("Database", s.database.ok ? "OK" : "FOUT", `${s.database.backend}, schema v${s.database.schema_version}`),
      kpi("Fallback", s.failsafe.active ? "ACTIEF" : "uit", s.failsafe.active ? s.failsafe.reason : `${s.failsafe.events} keer sinds start`)),
    h("div", { class: "grid cols-2", style: { marginTop: "14px" } },
      h("div", { class: "card" }, h("h3", {}, "Netmeter en functies"), h("p", { class: "small" }, s.grid_meter.reason),
        h("table", {}, h("tbody", {}, Object.values(s.features).map((f) => h("tr", {}, h("td", {}, f.label),
          h("td", {}, f.available ? h("span", { class: "pill good" }, "beschikbaar") : h("span", { class: "pill warn", title: f.reason }, `beperkt: ${f.reason}`))))))),
      h("div", { class: "card" }, h("h3", {}, "Diensten"), h("table", {}, h("tbody", {},
        h("tr", {}, h("td", {}, "Prijsbron"), h("td", {}, `${s.prices.provider || "geen"}`, s.prices.last_error ? h("div", { class: "nok small" }, s.prices.last_error) : "")),
        h("tr", {}, h("td", {}, "Weerbron"), h("td", {}, `${s.forecast.weather_provider || "geen"}`, s.forecast.last_error ? h("div", { class: "nok small" }, s.forecast.last_error) : "")),
        h("tr", {}, h("td", {}, "Optimizer"), h("td", {}, `${s.optimizer.status || "—"} · ${s.optimizer.runs} runs · ${s.optimizer.trigger || ""}`)),
        h("tr", {}, h("td", {}, "Watchdog"), h("td", {}, s.watchdog_tripped ? h("span", { class: "pill bad" }, "geactiveerd") : h("span", { class: "pill good" }, "OK"))))))),
    h("h2", {}, "Apparaatgezondheid"),
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Apparaat", "Verbonden", "Laatste data", "Fouten", "Melding"].map((t) => h("th", {}, t)))),
      h("tbody", {}, Object.entries(s.devices).map(([id, d]) => h("tr", {}, h("td", {}, h("a", { href: `#/devices/${id}` }, d.name)),
        h("td", {}, statusPill(d.connected ? "online" : "offline")), h("td", {}, d.age_s === null ? "—" : `${num(d.age_s, 1)} s`), h("td", {}, d.failures), h("td", { class: "small" }, d.error || "")))))),
    h("h2", {}, "Back-up en herstel"),
    can("admin") ? backupCard() : h("div", { class: "muted" }, "Vereist beheerdersrechten."),
    h("h2", {}, "Updates"),
    h("div", { class: "card" }, h("div", {}, `Huidige versie: ${upd.current_version}`), h("p", { class: "small muted" }, upd.how_to_update),
      h("details", {}, h("summary", {}, "Wijzigingen"), h("pre", { class: "small", style: { whiteSpace: "pre-wrap" } }, upd.changelog))),
    h("h2", {}, "Beslissingenlogboek"), await decisionsBox(),
    can("admin") ? h("div", {}, h("h2", {}, "Systeemlogboek"), await logsBox()) : null);
}

function backupCard() {
  const file = h("input", { type: "file", accept: ".zip,.emsbackup", "aria-label": "back-upbestand" });
  const restorePw = h("input", { type: "password", autocomplete: "off", placeholder: "alleen bij een versleutelde back-up", "aria-label": "back-upwachtwoord voor herstellen" });
  const keys = h("input", { type: "checkbox" });                      // audit P0-05: off by default
  const pw1 = h("input", { type: "password", autocomplete: "new-password", minlength: 10, "aria-label": "back-upwachtwoord" });
  const pw2 = h("input", { type: "password", autocomplete: "new-password", minlength: 10, "aria-label": "herhaal back-upwachtwoord" });
  const pwBox = h("div", { class: "form", style: { display: "none" } },
    h("label", { class: "f" }, "Back-upwachtwoord (min. 10 tekens)", pw1), h("label", { class: "f" }, "Herhaal wachtwoord", pw2),
    h("p", { class: "small muted" }, "Het hele bestand wordt hiermee versleuteld. Zonder dit wachtwoord is de back-up niet te herstellen — bewaar het veilig."));
  const encrypt = h("input", { type: "checkbox" });
  const sync = () => { pwBox.style.display = keys.checked || encrypt.checked ? "" : "none"; if (keys.checked) encrypt.checked = true; encrypt.disabled = keys.checked; };
  keys.addEventListener("change", sync); encrypt.addEventListener("change", sync);
  const make = () => guard(async () => {
    const withPw = keys.checked || encrypt.checked;
    if (withPw && pw1.value.length < 10) throw new Error("Wachtwoord moet minimaal 10 tekens hebben");
    if (withPw && pw1.value !== pw2.value) throw new Error("Wachtwoorden komen niet overeen");
    const res = await api("/backup", { method: "POST", raw: true, body: { include_keys: keys.checked, password: withPw ? pw1.value : null } });
    const blob = await res.blob();
    const name = (res.headers.get("content-disposition") || "").match(/filename="([^"]+)"/)?.[1] || "energy-manager-backup.zip";
    const a = h("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    pw1.value = ""; pw2.value = "";
  }, "Back-up gemaakt");
  return h("div", { class: "card" },
    h("label", { class: "f check" }, keys, "Inclusief sleutels (nodig om opgeslagen wachtwoorden en gekoppelde apparaten op een nieuwe computer te herstellen) — vereist een wachtwoord"),
    h("label", { class: "f check" }, encrypt, "Back-up versleutelen met een wachtwoord"),
    pwBox,
    h("div", { class: "row" }, h("button", { class: "btn primary", onclick: make }, "Volledige back-up downloaden"),
      h("button", { class: "btn", onclick: () => download("/config/export", "ems.yaml") }, "Configuratie exporteren")),
    h("div", { class: "row", style: { marginTop: "12px" } }, file, restorePw, h("button", { class: "btn danger", onclick: () => {
      if (!file.files[0]) return;
      if (!confirm("Back-up herstellen? De huidige configuratie en historie worden vervangen (er wordt eerst een veiligheidskopie gemaakt).")) return;
      const fd = new FormData(); fd.append("file", file.files[0]);
      if (restorePw.value) fd.append("password", restorePw.value);
      guard(() => api("/backup/restore", { method: "POST", form: fd, timeout: 120000 }), "Back-up hersteld").then(() => location.reload());
    } }, "Back-up herstellen")),
    h("p", { class: "muted small" }, "Er wordt elke dag automatisch een back-up gemaakt (zonder sleutels; laatste 7 bewaard). Verhuizen naar een nieuwe computer: back-up mét sleutels en wachtwoord downloaden → Energy Manager installeren → hier herstellen."));
}

async function decisionsBox() {
  const rows = await api("/decisions?limit=30");
  return decisionList(rows);
}

async function logsBox() {
  const rows = await api("/system/logs?limit=200");
  return h("div", { class: "tbl-wrap", style: { maxHeight: "420px" } }, h("table", {}, h("thead", {}, h("tr", {}, ["Tijd", "Niveau", "Bron", "Bericht"].map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { class: "small" }, dateTime(r.ts)), h("td", {}, r.level), h("td", { class: "small muted" }, r.logger),
      h("td", { class: "small" }, r.message, Object.keys(r.extra || {}).length ? h("span", { class: "muted" }, ` ${JSON.stringify(r.extra)}`) : ""))))));
}
