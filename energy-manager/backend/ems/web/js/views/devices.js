import { api, ago, can, guard, h, num, on, power, state, statusPill, toast } from "../lib.js";

const CAT_LABEL = {};

export async function render(root, [id, sub]) {
  const cats = await api("/device-categories");
  cats.forEach((c) => { CAT_LABEL[c.id] = c.label; });
  if (id === "add") return addWizard(root, cats, sub);
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
    h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Naam", "Soort", "Driver", "Status", "Rol / inbedrijfstelling", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, devices.map((d) => h("tr", {},
        h("td", {}, h("a", { href: `#/devices/${d.id}` }, d.name)), h("td", {}, CAT_LABEL[d.category] || d.category),
        h("td", { class: "small" }, d.driver_info?.name || d.driver, d.driver_info?.simulated ? h("span", { class: "pill warn" }, "simulatie") : null),
        h("td", {}, statusPill(d.status)),
        h("td", { class: "small" }, d.is_primary_grid_meter ? h("span", { class: "pill good" }, "PRIMARY GRID METER") : null, " ", d.control_level),
        h("td", {}, h("a", { class: "btn sm", href: `#/devices/${d.id}` }, "Open"))))))));
}

// ---------------------------------------------------------------- wizard
function checksList(result) {
  return h("ul", { class: "checks" }, result.checks.map((c) => h("li", { class: c.ok ? "ok" : "nok" },
    `${c.ok ? "✓" : "✗"} ${c.label}`, c.detail ? h("span", { class: "muted small" }, ` — ${c.detail}`) : null)));
}

async function addWizard(root, cats, preset) {
  if (!can("installer")) { root.append(h("div", { class: "notice warn inline" }, "Apparaten toevoegen vereist installateursrechten.")); return; }
  const drivers = await api("/drivers");
  const steps = h("div", { class: "steps" });
  const body = h("div", { class: "card" });
  root.append(h("h1", {}, "Apparaat toevoegen"), steps, body);
  const wiz = { category: null, driver: null };
  const setSteps = (n) => steps.replaceChildren(...["Soort", "Fabrikant / driver", "Verbinding", "Testen"].map((s, i) =>
    h("span", { class: i === n ? "on" : i < n ? "done" : "" }, `${i < n ? "✓ " : ""}${s}`)));

  const stepCategory = () => {
    setSteps(0);
    body.replaceChildren(h("h3", {}, "Welk soort apparaat?"), h("div", { class: "grid cols-4" }, cats.map((c) => {
      const n = drivers.filter((d) => d.categories.includes(c.id) && d.available_in_mode).length;
      return h("button", { class: "btn", disabled: n === 0, title: n ? "" : "nog geen driver beschikbaar",
        onclick: () => { wiz.category = c.id; stepDriver(); } }, c.label, n ? "" : " (nog geen driver)");
    })), h("p", { class: "muted small" }, "Ontbreekt uw merk? Drivers worden alleen gebouwd op basis van officiële documentatie; tot dan kunt u in Demo Mode een gesimuleerd apparaat gebruiken."));
  };
  const stepDriver = () => {
    setSteps(1);
    const options = drivers.filter((d) => d.categories.includes(wiz.category) && d.available_in_mode);
    body.replaceChildren(h("h3", {}, `${CAT_LABEL[wiz.category]}: fabrikant / koppeling`), h("div", { class: "grid" }, options.map((d) =>
      h("div", { class: "card flat row spread" }, h("div", {}, h("b", {}, d.name), h("div", { class: "muted small" },
        `${d.vendor} · ${d.connection_types.join(", ")}`, d.verified ? " · getest op hardware" : " · nog niet op hardware getest"),
        h("div", { class: "small" }, d.capabilities.map((c) => c.label).join(", "))),
      h("button", { class: "btn primary", onclick: () => { wiz.driver = d; d.driver_id === "homewizard.p1" ? homewizardFlow(body, setSteps) : stepConnection(); } }, "Kiezen")))),
    h("button", { class: "btn", onclick: stepCategory }, "← Terug"));
  };
  const stepConnection = () => {
    setSteps(2);
    const d = wiz.driver;
    const name = h("input", { value: `${d.name}` });
    const phase = h("select", {}, ["3P", "L1", "L2", "L3"].map((p) => h("option", { value: p }, p === "3P" ? "3-fase" : p)));
    const inputs = {};
    const fields = Object.entries(d.connection_schema || {}).filter(([, s]) => !s.readonly).map(([k, s]) => {
      let input;
      if (s.enum) input = h("select", {}, s.enum.map((v) => h("option", { value: v }, v)));
      else if (s.type === "boolean") input = h("input", { type: "checkbox", checked: s.default ? true : null });
      else if (s.type === "array") input = h("textarea", { rows: 7, class: "mono", spellcheck: "false",
        placeholder: s.example ? JSON.stringify(s.example, null, 1) : "[]" });
      else input = h("input", { type: s.type === "integer" ? "number" : "text", value: s.default ?? "" });
      if (s.default !== undefined && s.enum) input.value = s.default;
      inputs[k] = [input, s];
      return h("label", { class: s.type === "boolean" ? "f check" : "f" }, s.label_nl || k, input,
        s.help_nl ? h("span", { class: "muted small" }, s.help_nl) : null);
    });
    const result = h("div", {});
    const collect = () => {
      const conn = {};
      for (const [k, [input, s]] of Object.entries(inputs)) {
        if (s.type === "boolean") conn[k] = input.checked;
        else if (s.type === "array") {
          if (!input.value.trim()) continue;
          try { conn[k] = JSON.parse(input.value); } catch (e) { throw new Error(`${s.label_nl || k}: ongeldige JSON (${e.message})`); }
        }
        else if (input.value !== "") conn[k] = s.type === "integer" ? Number(input.value) : input.value;
      }
      return { name: name.value, category: wiz.category, driver: d.driver_id, phase: phase.value, connection: conn };
    };
    const test = h("button", { class: "btn primary", onclick: async () => {
      setSteps(3); test.disabled = true; result.replaceChildren(h("div", { class: "muted" }, "Verbinding testen…"));
      try {
        const r = await api("/devices/test", { method: "POST", body: collect() });
        result.replaceChildren(h("h3", {}, r.reachable ? "Testresultaat" : "Niet bereikbaar"), checksList(r),
          h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => guard(async () => {
            const dev = await api("/devices", { method: "POST", body: collect() });
            location.hash = `#/devices/${dev.id}`;
          }, "Apparaat toegevoegd (alleen-lezen; regeling via inbedrijfstelling)") }, r.reachable ? "Toevoegen" : "Toch toevoegen")));
      } catch (e) { result.replaceChildren(h("div", { class: "notice bad inline" }, e.message)); }
      test.disabled = false;
    } }, "Verbinding testen");
    body.replaceChildren(h("h3", {}, `${d.name}: verbinding`), h("div", { class: "form" }, h("label", { class: "f" }, "Naam", name),
      h("label", { class: "f" }, "Fase", phase), ...fields), h("div", { class: "row", style: { marginTop: "14px" } },
      h("button", { class: "btn", onclick: stepDriver }, "← Terug"), test), result);
  };
  if (preset === "homewizard") { wiz.category = "smart_meter"; wiz.driver = drivers.find((d) => d.driver_id === "homewizard.p1"); homewizardFlow(body, setSteps); }
  else stepCategory();
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
    const primary = h("input", { type: "checkbox", checked: true });
    const name = h("input", { value: "HomeWizard P1 Meter" });
    out.replaceChildren(h("div", { class: "notice good inline" }, `✓ Gekoppeld (${info.api}${info.firmware_version ? `, firmware ${info.firmware_version}` : ""})`),
      h("div", { class: "form" }, h("label", { class: "f" }, "Naam", name),
        h("label", { class: "f check" }, primary, "Gebruiken als primaire netmeter (aanbevolen)")),
      h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn primary", onclick: () => guard(async () => {
        const dev = await api("/integrations/homewizard/add", { method: "POST",
          body: { host, serial: info.serial || serial, api: apiVer, name: name.value, primary_grid_meter: primary.checked } });
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
  root.append(box);
  let dev, com;
  const load = async () => {
    [dev, com] = await Promise.all([api(`/devices/${id}`), api(`/devices/${id}/commissioning`)]);
    paint(state.live);
  };
  const paint = (live) => {
    if (!dev) return;
    const v = live?.devices?.[id]?.values || dev.values || {};
    const status = live?.devices?.[id]?.status || dev.status;
    const isMeter = dev.driver_info?.grid_meter_kind;
    const gm = live?.grid_meter;
    const phases = [1, 2, 3].map((n) => [n, v[`grid_power_l${n}_w`], v[`grid_current_l${n}_a`], v[`grid_voltage_l${n}_v`]]).filter((p) => p[1] !== undefined || p[2] !== undefined);
    box.replaceChildren(
      h("div", { class: "row spread" }, h("h1", {}, dev.name), h("a", { class: "btn", href: "#/devices" }, "← Apparaten")),
      h("div", { class: "grid cols-3" },
        h("div", { class: "card kpi" }, h("div", { class: "l" }, "Status"), h("div", { class: "v" }, statusPill(status)),
          h("div", { class: "s" }, dev.error || (dev.health?.last_update ? `Laatste update ${ago(dev.health.last_update)}` : ""))),
        h("div", { class: "card kpi" }, h("div", { class: "l" }, "Rol"), h("div", { class: "v small" },
          dev.is_primary_grid_meter ? "PRIMARY GRID METER" : isMeter ? "meter" : CAT_LABEL[dev.category]),
          h("div", { class: "s" }, dev.is_primary_grid_meter && gm?.needs_confirmation ? "automatisch gekozen" : "")),
        h("div", { class: "card kpi" }, h("div", { class: "l" }, "Driver"), h("div", { class: "v small" }, dev.driver_info?.name || dev.driver),
          h("div", { class: "s" }, dev.driver_info?.verified ? "getest op hardware" : dev.driver_info?.simulated ? "simulatie" : "nog niet op hardware getest"))),
      isMeter && v.grid_power_w !== undefined ? h("div", { class: "card" }, h("h3", {}, "Netmeting"),
        h("div", { class: "kpi" }, h("div", { class: "v" }, `${power(Math.abs(v.grid_power_w))} ${v.grid_power_w > 0 ? "import" : v.grid_power_w < 0 ? "export" : ""}`)),
        phases.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Fase", "Vermogen", "Stroom", "Spanning"].map((t) => h("th", {}, t)))),
          h("tbody", {}, phases.map(([n, p, a, u]) => h("tr", {}, h("td", {}, `L${n}`), h("td", {}, power(p, true)),
            h("td", {}, a === undefined ? "—" : `${num(a, 1)} A`), h("td", {}, u === undefined ? "—" : `${num(u, 1)} V`))))) : null,
        h("div", { class: "muted small" }, `Meterstanden: afname ${num(v.grid_import_energy_kwh, 3)} kWh · teruglevering ${num(v.grid_export_energy_kwh, 3)} kWh`,
          dev.is_primary_grid_meter && gm?.age_s !== null && gm?.age_s !== undefined ? ` · laatste update ${num(gm.age_s, 1)} seconden geleden` : "")) :
        h("div", { class: "card" }, h("h3", {}, "Actuele waarden"), Object.keys(v).length ?
          h("table", {}, h("tbody", {}, Object.entries(v).map(([k, val]) => h("tr", {}, h("td", { class: "muted" }, k), h("td", {}, typeof val === "number" ? num(val, 2) : String(val))))))
          : h("div", { class: "muted" }, "Geen data")),
      commissioningCard(),
      h("div", { class: "card" }, h("h3", {}, "Gezondheid en diagnose"),
        h("table", {}, h("tbody", {}, Object.entries({ ...(dev.health || {}), ...(dev.diagnostics || {}) })
          .filter(([, val]) => val !== null && typeof val !== "object").map(([k, val]) => h("tr", {}, h("td", { class: "muted" }, k), h("td", {}, String(val))))))),
      can("installer") ? h("div", { class: "card" }, h("h3", {}, "Beheer"), h("div", { class: "row" },
        h("button", { class: "btn", onclick: async () => {
          const r = await guard(() => api(`/devices/${id}/test`, { method: "POST" }));
          toast(r.reachable ? "Test geslaagd" : "Niet bereikbaar", !r.reachable); load();
        } }, "Verbinding testen"),
        dev.driver === "homewizard.p1" ? h("button", { class: "btn", onclick: () => guard(() => api(`/devices/${id}/identify`, { method: "POST" }), "Lampje knippert") }, "Identificeren (lampje)") : null,
        isMeter && !dev.is_primary_grid_meter ? h("button", { class: "btn", onclick: () => guard(() => api(`/devices/${id}/primary-grid-meter`, { method: "POST" }), "Ingesteld als primaire netmeter").then(load) }, "Als primaire netmeter gebruiken") : null,
        isMeter && dev.is_primary_grid_meter && gm?.needs_confirmation ? h("button", { class: "btn primary", onclick: () => guard(() => api(`/devices/${id}/primary-grid-meter`, { method: "POST" }), "Bevestigd").then(load) }, "Bevestigen als primaire netmeter") : null,
        h("button", { class: "btn", onclick: () => guard(() => api(`/devices/${id}`, { method: "PUT", body: { enabled: !dev.enabled } }), dev.enabled ? "Uitgeschakeld" : "Ingeschakeld").then(load) }, dev.enabled ? "Uitschakelen" : "Inschakelen"),
        h("button", { class: "btn danger", onclick: () => { if (confirm(`${dev.name} verwijderen?`)) guard(() => api(`/devices/${id}`, { method: "DELETE" }), "Verwijderd").then(() => { location.hash = "#/devices"; }); } }, "Verwijderen"))) : null,
    );
  };
  const commissioningCard = () => {
    if (!com) return null;
    const wd = com.ems_would_do;
    const actual = com.actual || {};
    return h("div", { class: "card" }, h("h3", {}, "Inbedrijfstelling"),
      h("div", { class: "steps" }, Object.entries(com.levels).map(([k, l]) => h("span", { class: k === com.level ? "on" : "", title: l.reason || LEVEL_HELP[k] }, l.label))),
      h("p", { class: "small muted" }, LEVEL_HELP[com.level]),
      com.control_capabilities.length ? h("div", { class: "grid cols-2" },
        h("div", { class: "card flat" }, h("b", {}, "WERKELIJK"), h("div", { class: "small" },
          ["battery_power_w", "battery_soc_pct", "pv_power_w", "pv_limit_w", "ev_power_w", "hp_mode", "hp_power_w"].filter((k) => actual[k] !== undefined)
            .map((k) => h("div", {}, `${k}: ${typeof actual[k] === "number" ? num(actual[k], 1) : actual[k]}`)))),
        h("div", { class: "card flat" }, h("b", {}, com.level === "shadow" ? "EMS WOULD DO" : "LAATSTE EMS-BESLISSING"),
          wd ? h("div", { class: "small" }, h("div", {}, wd.summary), wd.expected_benefit !== null && wd.expected_benefit !== undefined ? h("div", {}, `Verwacht voordeel: € ${num(wd.expected_benefit, 2)}`) : null,
            h("div", { class: "muted" }, "Reden: ", (wd.reasons || []).join("; ")), h("div", { class: "muted" }, `status: ${wd.outcome} · ${ago(wd.ts)}`))
            : h("div", { class: "muted small" }, "Nog geen beslissing"))) : h("div", { class: "muted small" }, "Alleen-meten apparaat."),
      com.last_test ? h("details", {}, h("summary", { class: "small" }, `Laatste verbindingstest: ${com.last_test.reachable ? "geslaagd" : "mislukt"}`), checksList(com.last_test)) : null,
      can("installer") ? h("div", { class: "row", style: { marginTop: "10px" } }, Object.entries(com.levels).filter(([k]) => k !== com.level).map(([k, l]) =>
        h("button", { class: "btn sm", disabled: !l.allowed, title: l.reason, onclick: () => {
          const confirmFull = k === "full" ? confirm("Volledige regeling inschakelen? Het EMS mag dit apparaat dan volledig aansturen.") : false;
          if (k === "full" && !confirmFull) return;
          guard(() => api(`/devices/${id}/commissioning`, { method: "PUT", body: { level: k, confirm: confirmFull } }), `Naar ${l.label}`).then(load);
        } }, `→ ${l.label}`))) : null);
  };
  await load();
  const off = on((m) => { if (m.type === "live") paint(m.data); });
  return off;
}
