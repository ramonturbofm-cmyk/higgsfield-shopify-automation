import { api, ago, can, guard, h, num, on, power, state, statusPill, toast } from "../lib.js";
import { addWizard } from "./addwizard.js";
import { controlStatePill, withPrimaryConfirm, wouldVsDoes } from "./common.js";
import { capabilityTable, checksList, deviceForm } from "./deviceform.js";

const CAT_LABEL = {};

export async function render(root, [id, sub]) {
  const cats = await api("/device-categories");
  cats.forEach((c) => { CAT_LABEL[c.id] = c.label; });
  if (id === "add") return addWizard(root, sub, homewizardFlow);
  if (id) return detail(root, id);
  return list(root);
}

// ------------------------------------------------------------------ list
async function list(root) {
  const [devices, gm] = await Promise.all([api("/devices"), api("/gridmeter")]);
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Apparaten"),
    can("installer") ? h("a", { class: "btn primary", href: "#/devices/add" }, "+ Apparaat toevoegen") : null),
  h("div", { class: `notice ${gm.device_id ? "info" : "warn"} inline` },
    h("div", {}, h("b", {}, "Primaire netmeter: "), gm.device_id ? `${devices.find((d) => d.id === gm.device_id)?.name} — ${gm.reason}` : gm.message)),
  !devices.length ? h("div", { class: "card empty" }, "Nog geen apparaten. Begin met de slimme meter (bijvoorbeeld een HomeWizard P1 Meter).") :
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Naam", "Soort", "Driver", "Status", "Rol / regeling", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, devices.map((d) => h("tr", {},
        h("td", {}, h("a", { href: `#/devices/${d.id}` }, d.name)), h("td", {}, CAT_LABEL[d.category] || d.category),
        h("td", { class: "small" }, d.driver_info?.name || d.driver, d.driver_info?.simulated ? h("span", { class: "pill warn" }, "simulatie") : null),
        h("td", {}, statusPill(d.status)),
        h("td", { class: "small" }, d.is_primary_grid_meter ? h("span", { class: "pill good" }, "Primaire netmeter") : null, " ",
          controlStatePill(d.control_state, d.control_state_label)),
        h("td", {}, h("a", { class: "btn sm", href: `#/devices/${d.id}` }, "Open"))))))));
}

/** Edit an existing device: same form; the device id (and its history) stays the same.
 *  ``slot`` lives outside the live-repainted part of the page, so typing is never lost. */
async function editDevice(slot, dev, onDone) {
  const [drivers, schema] = await Promise.all([api("/drivers"), api(`/device-schemas/${dev.category}`)]);
  const driver = drivers.find((d) => d.driver_id === dev.driver) || { name: dev.driver, connection_schema: {} };
  const form = deviceForm({ schema, driver, caps: (dev.capabilities || []).map((c) => c.id), existing: dev });
  const box = h("div", { class: "card" }, h("h3", {}, "Apparaat bewerken"),
    h("p", { class: "small muted" }, "Naam, adres, fase en apparaatgegevens wijzigen. De historie blijft bewaard (zelfde apparaat-ID)."),
    form.el, h("div", { class: "row", style: { marginTop: "12px" } },
      h("button", { class: "btn primary", onclick: () => guard(async () => {
        const v = form.collect();
        await api(`/devices/${dev.id}`, { method: "PUT", body: { name: v.name, phase: v.phase, connection: v.connection, params: v.params } });
        slot.replaceChildren(); onDone();
      }, "Opgeslagen") }, "Opslaan"),
      h("button", { class: "btn", onclick: () => slot.replaceChildren() }, "Annuleren")));
  slot.replaceChildren(box);
  box.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

// ------------------------------------------------------------ HomeWizard
function homewizardFlow(body, setSteps) {
  setSteps(2);
  const out = h("div", {});
  const ip = h("input", { placeholder: "bijv. 192.168.1.50" });
  const api_v = h("select", {}, h("option", { value: "v2" }, "API v2 (aanbevolen, HTTPS + koppelen)"), h("option", { value: "v1" }, "API v1 (HTTP, 'Local API' aanzetten in de app)"));
  const pick = (host, serial, apiVer) => { ip.value = host; api_v.value = apiVer; pair(host, serial, apiVer); };
  const search = h("button", { class: "btn primary", onclick: async () => {
    search.disabled = true; out.replaceChildren(h("div", { class: "muted" }, "Zoeken op het netwerk (mDNS)…"));
    try {
      const r = await api("/integrations/homewizard/discover", { method: "POST" });
      if (!r.devices.length) out.replaceChildren(h("div", { class: "notice warn inline" }, r.error || "Geen HomeWizard-apparaten gevonden. Voer het IP-adres handmatig in."));
      else out.replaceChildren(h("h3", {}, "Gevonden"), ...r.devices.map((d) => h("div", { class: "card flat row spread" },
        h("div", {}, h("b", {}, d.name), h("div", { class: "muted small" }, `${d.host} · ${d.product_type || "?"} · serienr ${d.serial} · API ${d.apis.join("+")}`,
          d.is_p1 ? "" : " · geen P1 Meter")),
        h("button", { class: "btn primary", disabled: !d.is_p1, onclick: () => pick(d.host, d.serial, d.recommended_api) }, "Koppelen"))));
    } catch (e) { out.replaceChildren(h("div", { class: "notice bad inline" }, e.message)); }
    search.disabled = false;
  } }, "Automatisch zoeken");
  const manual = h("button", { class: "btn", onclick: () => ip.value ? pair(ip.value, null, api_v.value) : toast("Vul een IP-adres in", true) }, "Koppelen");

  async function pair(host, serial, apiVer) {
    setSteps(3);
    out.replaceChildren(h("div", { class: "muted" }, "Verbinding maken…"));
    let info;
    try {
      if (apiVer === "v2" && !serial) {
        const ident = await api("/integrations/homewizard/identity", { method: "POST", body: { host, api: "v2" } });
        serial = ident.serial;
      }
      const deadline = Date.now() + 60000;
      for (;;) {
        info = await api("/integrations/homewizard/pair", { method: "POST", body: { host, serial, api: apiVer } });
        if (info.status === "paired") break;
        out.replaceChildren(h("div", { class: "notice info inline" }, h("div", {}, h("b", {}, "Druk nu op de knop van de HomeWizard P1 Meter. "),
          "Na het indrukken heeft u 30 seconden; het koppelen gaat dan vanzelf verder.")));
        if (Date.now() > deadline) throw new Error("Geen knopdruk ontvangen. Probeer het opnieuw.");
        await new Promise((r) => setTimeout(r, 2000));
      }
    } catch (e) { out.replaceChildren(h("div", { class: "notice bad inline" }, e.message)); return; }
    const gmNow = await api("/gridmeter");
    const current = gmNow.device_id ? (await api(`/devices/${gmNow.device_id}`)).name : null;
    // Never replace an existing primary meter by default: the user must tick it explicitly.
    const primary = h("input", { type: "checkbox", checked: current ? null : true });
    const name = h("input", { value: "HomeWizard P1 Meter" });
    out.replaceChildren(h("div", { class: "notice good inline" }, `✓ Gekoppeld (${info.api}${info.firmware_version ? `, firmware ${info.firmware_version}` : ""})`),
      h("div", { class: "form" }, h("label", { class: "f" }, "Naam", name),
        h("label", { class: "f check" }, primary, current ? `Primaire netmeter „${current}” vervangen door deze meter` : "Gebruiken als primaire netmeter (aanbevolen)")),
      h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn primary", onclick: () => guard(async () => {
        const dev = await api("/integrations/homewizard/add", { method: "POST",
          body: { host, serial: info.serial || serial, api: apiVer, name: name.value, primary_grid_meter: primary.checked,
            replace_primary: Boolean(current && primary.checked) } });
        location.hash = `#/devices/${dev.id}`;
      }, "HomeWizard P1 Meter toegevoegd") }, "Toevoegen")));
  }
  body.replaceChildren(h("h3", {}, "HomeWizard P1 Meter koppelen"),
    h("p", { class: "muted small" }, "Zorg dat de P1 Meter met hetzelfde netwerk is verbonden als de EMS-server."),
    h("div", { class: "row" }, search), h("div", { class: "form", style: { marginTop: "14px" } },
      h("label", { class: "f" }, "IP-adres handmatig", ip), h("label", { class: "f" }, "API", api_v)),
    h("div", { class: "row", style: { marginTop: "10px" } }, manual), out);
}

// ---------------------------------------------------------------- detail
const LEVEL_HELP = {
  connection_test: "Alleen bereikbaarheid testen.", read_only: "Meetwaarden worden gebruikt; het EMS schrijft niets.",
  shadow: "De optimizer draait volledig mee en toont wat hij zou doen, zonder iets uit te voeren.",
  limited: "Commando's worden uitgevoerd met een beperkt vermogen.", full: "Volledige regeling door het EMS.",
};

async function detail(root, id) {
  const box = h("div", {});
  const editSlot = h("div", { class: "edit-slot" });   // not repainted by live updates
  root.append(editSlot, box);
  let dev, com, report = null;
  const ML = await api("/metric-labels");
  const label = (k) => (state.level === "expert" ? k : (ML[k]?.label || k));
  const fmtVal = (k, v) => typeof v === "number" ? `${num(v, Math.abs(v) >= 100 ? 0 : 2)}${ML[k]?.unit ? ` ${ML[k].unit}` : ""}` : v === true ? "ja" : v === false ? "nee" : String(v);
  // Created once: live updates repaint the page and must not wipe what the installer typed.
  const typed = h("input", { "aria-label": "typ de apparaatnaam", autocomplete: "off" });
  const load = async () => {
    [dev, com, report] = await Promise.all([api(`/devices/${id}`), api(`/devices/${id}/commissioning`),
      api(`/devices/${id}/capability-report`).catch(() => null)]);
    paint(state.live);
  };
  const paint = (live) => {
    if (!dev) return;
    const v = live?.devices?.[id]?.values || dev.values || {};
    const status = live?.devices?.[id]?.status || dev.status;
    const isMeter = dev.is_meter;
    const gm = live?.grid_meter;
    const phases = [1, 2, 3].map((n) => [n, v[`grid_power_l${n}_w`], v[`grid_current_l${n}_a`], v[`grid_voltage_l${n}_v`]]).filter((p) => p[1] !== undefined || p[2] !== undefined);
    box.replaceChildren(
      h("div", { class: "row spread" }, h("h1", {}, dev.name), h("a", { class: "btn", href: "#/devices" }, "← Apparaten")),
      h("div", { class: "grid cols-4" },
        h("div", { class: "card kpi" }, h("div", { class: "l" }, "Regeling"), h("div", { class: "v" }, controlStatePill(dev.control_state, dev.control_state_label)),
          h("div", { class: "s" }, dev.control_state_label)),
        h("div", { class: "card kpi" }, h("div", { class: "l" }, "Status"), h("div", { class: "v" }, statusPill(status)),
          h("div", { class: "s" }, dev.error || (dev.health?.last_update ? `Laatste update ${ago(dev.health.last_update)}` : ""))),
        h("div", { class: "card kpi" }, h("div", { class: "l" }, "Rol"), h("div", { class: "v small" },
          dev.is_primary_grid_meter ? "Primaire netmeter" : isMeter ? "meter" : CAT_LABEL[dev.category]),
          h("div", { class: "s" }, dev.is_primary_grid_meter && gm?.needs_confirmation ? "automatisch gekozen" : "")),
        h("div", { class: "card kpi" }, h("div", { class: "l" }, dev.profile ? "Merk en model" : "Koppeling"),
          h("div", { class: "v small" }, dev.profile && !dev.profile.missing ? `${dev.profile.manufacturer} ${dev.profile.model}` : dev.driver_info?.name || dev.driver),
          h("div", { class: "s" }, dev.virtual ? "virtueel apparaat (Testomgeving)" : dev.driver_info?.simulated ? "simulatie (Demo)"
            : dev.profile?.missing ? "profiel ontbreekt" : (dev.driver_info?.verified || dev.profile?.test_status === "HARDWARE_TESTED") ? "getest op hardware" : "nog niet op hardware getest"))),
      isMeter && v.grid_power_w !== undefined ? h("div", { class: "card" }, h("h3", {}, "Netmeting"),
        h("div", { class: "kpi" }, h("div", { class: "v" }, `${power(Math.abs(v.grid_power_w))} ${v.grid_power_w > 0 ? "import" : v.grid_power_w < 0 ? "export" : ""}`)),
        phases.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Fase", "Vermogen", "Stroom", "Spanning"].map((t) => h("th", {}, t)))),
          h("tbody", {}, phases.map(([n, p, a, u]) => h("tr", {}, h("td", {}, `L${n}`), h("td", {}, power(p, true)),
            h("td", {}, a === undefined ? "—" : `${num(a, 1)} A`), h("td", {}, u === undefined ? "—" : `${num(u, 1)} V`))))) : null,
        h("div", { class: "muted small" }, `Meterstanden: afname ${num(v.grid_import_energy_kwh, 3)} kWh · teruglevering ${num(v.grid_export_energy_kwh, 3)} kWh`,
          dev.is_primary_grid_meter && gm?.age_s !== null && gm?.age_s !== undefined ? ` · laatste update ${num(gm.age_s, 1)} seconden geleden` : "")) :
        h("div", { class: "card" }, h("h3", {}, "Actuele waarden"), Object.keys(v).length ?
          h("table", {}, h("tbody", {}, Object.entries(v).map(([k, val]) => h("tr", {}, h("th", { scope: "row", class: "muted" }, label(k)), h("td", {}, fmtVal(k, val))))))
          : h("div", { class: "muted" }, "Geen data")),
      report?.rows ? h("div", { class: "card" }, h("h3", {}, "Functies van dit apparaat"),
        report.identity && Object.keys(report.identity).length ? h("p", { class: "small muted" }, "Apparaat meldt: ",
          [report.identity.manufacturer, report.identity.model, report.identity.serial_number && `serienummer ${report.identity.serial_number}`,
            report.identity.firmware_version && `firmware ${report.identity.firmware_version}`].filter(Boolean).join(" · ")) : null,
        capabilityTable(report.rows)) : null,
      commissioningCard(),
      h("div", { class: "card advanced-only" }, h("h3", {}, "Gezondheid en diagnose"),
        h("table", {}, h("tbody", {}, Object.entries({ ...(dev.health || {}), ...(dev.diagnostics || {}) })
          .filter(([, val]) => val !== null && typeof val !== "object").map(([k, val]) => h("tr", {}, h("td", { class: "muted" }, k), h("td", {}, String(val))))))),
      can("installer") ? h("div", { class: "card" }, h("h3", {}, "Beheer"), h("div", { class: "row" },
        h("button", { class: "btn", onclick: () => editDevice(editSlot, dev, load) }, "Bewerken"),
        h("button", { class: "btn", onclick: async () => {
          const r = await guard(() => api(`/devices/${id}/test`, { method: "POST" }));
          toast(r.reachable ? "Test geslaagd" : "Niet bereikbaar", !r.reachable); load();
        } }, "Verbinding testen"),
        dev.driver === "homewizard.p1" ? h("button", { class: "btn", onclick: () => guard(() => api(`/devices/${id}/identify`, { method: "POST" }), "Lampje knippert") }, "Identificeren (lampje)") : null,
        isMeter && !dev.is_primary_grid_meter ? h("button", { class: "btn", onclick: (ev) => withPrimaryConfirm(
          (replace) => api(`/devices/${id}/primary-grid-meter?replace=${replace}`, { method: "POST" }), ev.target.closest(".card"), load) }, "Als primaire netmeter gebruiken") : null,
        isMeter && dev.is_primary_grid_meter && gm?.needs_confirmation ? h("button", { class: "btn primary", onclick: () => guard(() => api(`/devices/${id}/primary-grid-meter`, { method: "POST" }), "Bevestigd").then(load) }, "Bevestigen als primaire netmeter") : null,
        h("button", { class: "btn", onclick: () => guard(() => api(`/devices/${id}`, { method: "PUT", body: { enabled: !dev.enabled } }), dev.enabled ? "Uitgeschakeld" : "Ingeschakeld").then(load) }, dev.enabled ? "Uitschakelen" : "Inschakelen"),
        h("button", { class: "btn danger", onclick: () => { if (confirm(`${dev.name} verwijderen?`)) guard(() => api(`/devices/${id}`, { method: "DELETE" }), "Verwijderd").then(() => { location.hash = "#/devices"; }); } }, "Verwijderen"))) : null,
    );
  };
  const commissioningCard = () => {
    if (!com) return null;
    const actual = com.actual || {};
    typed.placeholder = com.name;
    const levelButtons = Object.entries(com.levels).filter(([k]) => k !== com.level).map(([k, l]) => {
      const go = () => guard(() => api(`/devices/${id}/commissioning`, { method: "PUT",
        body: { level: k, confirm_text: k === "full" ? typed.value : null } }), `Naar ${l.label}`).then(load);
      return h("button", { class: `btn sm ${k === "full" ? "danger" : ""}`, disabled: !l.allowed, title: l.reason, onclick: go }, `→ ${l.label}`);
    });
    return h("div", { class: "card" }, h("h3", {}, "Inbedrijfstelling"),
      h("div", { class: "steps" }, Object.entries(com.levels).map(([k, l]) => h("span", { class: k === com.level ? "on" : "", title: l.reason || LEVEL_HELP[k] }, l.label))),
      h("p", { class: "small muted" }, LEVEL_HELP[com.level]),
      h("h4", {}, "Procedure voor dit apparaat"),
      h("ol", { class: "small procedure" }, com.procedure.map((st) => h("li", { class: st.done ? "ok" : "" },
        h("span", { "aria-hidden": "true" }, st.done ? "✓ " : "○ "), st.label, st.detail ? h("span", { class: "muted" }, ` — ${st.detail}`) : null))),
      dev ? wouldVsDoes(dev) : null,
      com.control_capabilities.length ? h("details", {}, h("summary", { class: "small" }, "Actuele meetwaarden"), h("div", { class: "small" },
        Object.entries(actual).map(([k, v]) => h("div", {}, `${label(k)}: ${fmtVal(k, v)}`)))) : null,
      com.last_test ? h("details", {}, h("summary", { class: "small" }, `Laatste verbindingstest: ${com.last_test.reachable ? "geslaagd" : "mislukt"}`), checksList(com.last_test)) : null,
      can("installer") ? h("div", { class: "grid", style: { marginTop: "10px" } },
        ["shadow", "limited", "full"].includes(com.level) && com.control_capabilities.length
          ? h("div", { class: "row" }, h("button", { class: "btn sm", onclick: () => guard(async () => {
            const r = await api(`/devices/${id}/commissioning/write-test`, { method: "POST" });
            if (!r.ok) throw new Error(`Schrijftest mislukt: ${r.detail}`);
          }, "Schrijftest geslaagd").then(load) }, "Schrijftest uitvoeren"),
          h("span", { class: "small muted" }, "Zet het apparaat terug op zijn eigen regeling en leest het daarna opnieuw uit.")) : null,
        com.levels.full && com.level !== "full" ? h("label", { class: "f" },
          `Voor volledige regeling: typ de apparaatnaam „${com.name}” ter bevestiging`, typed) : null,
        h("div", { class: "row" }, levelButtons)) : null);
  };
  await load();
  const off = on((m) => { if (m.type === "live") paint(m.data); });
  return off;
}
