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
  const file = h("input", { type: "file", accept: ".zip" });
  const keys = h("input", { type: "checkbox", checked: true });
  return h("div", { class: "card" }, h("div", { class: "row" },
    h("label", { class: "f check" }, keys, "Inclusief sleutels (nodig om gekoppelde apparaten op een nieuwe Pi te herstellen — bewaar privé)"),
    h("button", { class: "btn primary", onclick: () => download(`/backup?include_keys=${keys.checked}`, "energy-manager-backup.zip") }, "Volledige back-up downloaden"),
    h("button", { class: "btn", onclick: () => download("/config/export", "ems.yaml") }, "Configuratie exporteren")),
  h("div", { class: "row", style: { marginTop: "12px" } }, file, h("button", { class: "btn danger", onclick: () => {
    if (!file.files[0]) return;
    if (!confirm("Back-up herstellen? De huidige configuratie en historie worden vervangen (er wordt eerst een veiligheidskopie gemaakt).")) return;
    const fd = new FormData(); fd.append("file", file.files[0]);
    guard(() => api("/backup/restore", { method: "POST", form: fd }), "Back-up hersteld").then(() => location.reload());
  } }, "Back-up herstellen")),
  h("p", { class: "muted small" }, "Er wordt elke dag automatisch een back-up gemaakt (zonder sleutels; laatste 7 bewaard). Zo verhuist u naar een nieuwe Raspberry Pi: back-up downloaden → nieuwe Pi installeren → hier herstellen."));
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
