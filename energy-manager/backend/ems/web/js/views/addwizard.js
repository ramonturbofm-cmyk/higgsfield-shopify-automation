import { api, can, guard, h, state, toast } from "../lib.js";
import { capabilityTable, checksList, deviceForm } from "./deviceform.js";

/* Apparaat toevoegen — de gebruiker kiest zijn apparaat, niet een protocol.
 *   1. Soort (15 categorieën)
 *   2. Methode: automatisch zoeken · merk en model · via een gateway · handmatig (expert)
 *   3. Gegevens (alleen de velden die bij het niveau Eenvoudig / Geavanceerd / Expert horen)
 *   4. Testen: verbinding, identiteit en een eerlijk functierapport — daarna pas opslaan. */

const SUPPORT = {
  supported: ["good", "Ondersteund"],
  read_only: ["st", "Alleen uitlezen"],
  community_read_only: ["warn", "Alleen uitlezen (community, niet geverifieerd)"],
  via_sg_ready: ["st", "Via SG Ready"],
  via_home_assistant: ["st", "Via Home Assistant"],
  simulator: ["warn", "Testomgeving (virtueel)"],
  not_supported: ["bad", "Nog niet ondersteund"],
};
const TEST_STATUS = {
  HARDWARE_TESTED: "op hardware getest", SIMULATOR_TESTED: "getest in de simulator", EXPERIMENTAL: "experimenteel, niet op hardware getest",
  COMMUNITY: "community-profiel, niet geverifieerd", UNVERIFIED: "volgens documentatie, niet op hardware getest",
};
const STEPS = ["Soort", "Methode", "Gegevens", "Testen"];

/** Home Assistant: list the entities of the user's own HA (official REST API) to fill in the mapping. */
function haHelper(form) {
  const out = h("div", { "aria-live": "polite" });
  const fetchList = async () => {
    let c;
    try { c = form.collect().connection; } catch (e) { out.replaceChildren(h("div", { class: "notice bad inline" }, e.message)); return; }
    if (!c.token || c.token === "********") { out.replaceChildren(h("div", { class: "notice warn inline" }, "Vul eerst het access token in.")); return; }
    out.replaceChildren(h("div", { class: "muted" }, "Entiteiten ophalen…"));
    try {
      const r = await api("/integrations/homeassistant/entities", { method: "POST", body: { url: c.url, token: c.token } });
      out.replaceChildren(h("p", { class: "small muted" }, `${r.entities.length} bruikbare entiteiten. Neem de entity_id over in ‘Meetwaarden’ (en, na bevestiging, ‘Bediening’).`),
        h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Naam", "entity_id", "Soort", "Waarde"].map((t) => h("th", {}, t)))),
          h("tbody", {}, r.entities.slice(0, 300).map((e) => h("tr", {}, h("td", {}, e.name), h("td", { class: "mono small" }, e.entity_id),
            h("td", { class: "small" }, e.kind), h("td", { class: "small" }, `${e.state ?? ""} ${e.unit || ""}`)))))));
    } catch (e) { out.replaceChildren(h("div", { class: "notice bad inline" }, e.message)); }
  };
  return h("div", { class: "card flat" }, h("b", {}, "Entiteiten uit Home Assistant"),
    h("p", { class: "small muted" }, "Maak in Home Assistant een ‘long-lived access token’ (Profiel → Beveiliging). Het token wordt versleuteld opgeslagen."),
    h("button", { class: "btn", onclick: fetchList }, "Entiteiten ophalen"), out);
}

export async function addWizard(root, preset, homewizardFlow) {
  if (!can("installer")) { root.append(h("div", { class: "notice warn inline" }, "Apparaten toevoegen vereist installateursrechten.")); return; }
  const [cats, drivers, schemas] = await Promise.all([api("/library/categories"), api("/drivers"), api("/device-schemas")]);
  const schemaOf = Object.fromEntries(schemas.map((x) => [x.category, x]));
  const driverOf = Object.fromEntries(drivers.map((d) => [d.driver_id, d]));
  const lab = state.info?.profile === "lab";
  const steps = h("div", { class: "steps", role: "list" });
  const body = h("div", { class: "card wizard" });
  root.append(h("h1", {}, "Apparaat toevoegen"),
    lab ? h("div", { class: "notice warn inline" }, h("div", {}, h("b", {}, "Testomgeving. "),
      "De virtuele apparaten van de testwoning draaien op deze computer en worden via echte protocollen (Modbus TCP, SunSpec, HTTP) aangesproken. Niets hiervan is een echt apparaat.")) : null,
    steps, body);
  const wiz = { category: null };
  const setSteps = (n) => steps.replaceChildren(...STEPS.map((x, i) =>
    h("span", { role: "listitem", class: i === n ? "on" : i < n ? "done" : "" }, `${i < n ? "✓ " : ""}${x}`)));
  const catLabel = () => cats.find((c) => c.id === wiz.category)?.label || wiz.category;
  const back = (fn, text = "← Terug") => h("button", { class: "btn", onclick: fn }, text);

  // ------------------------------------------------------------ 1. soort
  const stepCategory = () => {
    setSteps(0);
    body.replaceChildren(h("h3", {}, "Welk soort apparaat wilt u toevoegen?"),
      h("div", { class: "grid cols-4 choices" }, cats.map((c) => h("button", { class: "btn choice", "data-category": c.id,
        onclick: () => { wiz.category = c.id; stepMethod(); } },
      h("span", { class: "ico", "aria-hidden": "true" }, c.icon), h("b", {}, c.label),
      h("span", { class: "muted small" }, c.supported_models ? `${c.supported_models} ${c.supported_models === 1 ? "koppeling" : "koppelingen"}` : "via gateway / handmatig")))));
  };

  // ----------------------------------------------------------- 2. methode
  const stepMethod = () => {
    setSteps(1);
    const methods = [
      ["search", "🔎", "Automatisch zoeken", "Zoekt op uw netwerk naar apparaten die het EMS kent (mDNS, HomeWizard, Shelly, SunSpec/Modbus op poort 502). Er wordt geen poortscan gedaan."],
      ["brand", "🏷", "Kies merk en model", "Kies uw apparaat uit de lijst. U ziet meteen wat wel en niet kan."],
      ["gateway", "🔌", "Via een gateway", "Home Assistant, een SG Ready-relais of een Modbus-gateway (RS485 → netwerk)."],
      ["manual", "🛠", "Handmatig (expert)", "Zelf Modbus-registers, REST-velden of MQTT-topics opgeven. Alleen uitlezen."],
    ];
    body.replaceChildren(h("h3", {}, `${catLabel()}: hoe wilt u het apparaat toevoegen?`),
      h("div", { class: "grid cols-4 choices" }, methods.map(([k, ico, title, text]) => h("button", {
        class: `btn choice ${k === "manual" ? "advanced-only" : ""}`, "data-method": k,
        onclick: () => ({ search: stepSearch, brand: stepBrand, gateway: stepGateway, manual: stepManual })[k]() },
      h("span", { class: "ico", "aria-hidden": "true" }, ico), h("b", {}, title), h("span", { class: "muted small" }, text)))),
      state.level === "simple" ? h("p", { class: "small muted" }, "Handmatige koppelingen zijn zichtbaar op het niveau Geavanceerd of Expert (rechtsboven).") : null,
      h("div", { class: "row" }, back(stepCategory)));
  };

  // ---------------------------------------------------- automatisch zoeken
  const stepSearch = () => {
    setSteps(1);
    const out = h("div", { "aria-live": "polite" });
    const ip = h("input", { placeholder: "bijv. 192.168.1.50", "aria-label": "IP-adres" });
    const run = async (hosts) => {
      out.replaceChildren(h("div", { class: "muted" }, hosts.length ? `Zoeken op ${hosts.join(", ")}…` : "Zoeken op het netwerk… (max. ± 10 seconden)"));
      try {
        const r = await api("/discovery/scan", { method: "POST", body: { hosts, mdns: !hosts.length }, timeout: 60000 });
        renderResults(r.results || [], out);
      } catch (e) { out.replaceChildren(h("div", { class: "notice bad inline" }, e.message)); }
    };
    body.replaceChildren(h("h3", {}, `${catLabel()}: automatisch zoeken`),
      h("p", { class: "small muted" }, "Het EMS zoekt alleen naar bekende aankondigingen (mDNS) en vraagt op een opgegeven adres naar de identiteit. Herkenning: ",
        h("b", {}, "exact model"), " · ", h("b", {}, "fabrikant"), " · ", h("b", {}, "alleen protocol"), ". Een apparaat dat niet zeker herkend is, wordt nooit als een bepaald merk behandeld."),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => run([]) }, "Zoeken op het netwerk")),
      h("div", { class: "form", style: { marginTop: "12px" } }, h("label", { class: "f" }, "Of zoek op één IP-adres", ip)),
      h("div", { class: "row" }, h("button", { class: "btn", onclick: () => ip.value.trim() ? run([ip.value.trim()]) : toast("Vul een IP-adres in", true) }, "Zoeken op dit adres")),
      out, h("div", { class: "row", style: { marginTop: "12px" } }, back(stepMethod)));
    run([]);
  };
  const renderResults = (results, out) => {
    const mine = results.filter((r) => r.categories?.includes(wiz.category));
    const other = results.filter((r) => !r.categories?.includes(wiz.category));
    const row = (r) => {
      const matches = r.categories?.includes(wiz.category) && r.driver_id;
      const action = r.configured_device && r.address_changed
        ? h("button", { class: "btn primary", onclick: () => guard(() => api(`/devices/${r.configured_device}/address`, { method: "PUT",
          body: { host: r.connection?.host || r.host, port: r.connection?.port ?? null } }), "Adres bijgewerkt").then(() => { location.hash = `#/devices/${r.configured_device}`; }) }, "Adres bijwerken")
        : r.configured_device ? h("span", { class: "pill good" }, "Al toegevoegd")
          : matches ? h("button", { class: "btn primary", onclick: () => fromDiscovery(r) }, "Kiezen") : null;
      return h("div", { class: "card flat row spread result", "data-driver": r.driver_id || "" }, h("div", {},
        h("b", {}, [r.manufacturer, r.model].filter(Boolean).join(" ") || "Onbekend apparaat"), " ",
        h("span", { class: `pill ${r.confidence === "EXACT_MODEL" ? "good" : r.confidence === "MANUFACTURER" ? "st" : "warn"}` }, r.confidence_label),
        r.virtual ? h("span", { class: "pill warn" }, "virtueel") : null,
        h("div", { class: "muted small" }, `${r.host}${r.port ? `:${r.port}` : ""}`, r.serial ? ` · serienummer ${r.serial}` : "", r.firmware ? ` · firmware ${r.firmware}` : ""),
        r.address_changed ? h("div", { class: "small" }, "Dit apparaat is al toegevoegd maar heeft een ander adres gekregen.") : null,
        ...(r.notes || []).map((n) => h("div", { class: "small muted" }, n))), action);
    };
    out.replaceChildren(
      mine.length ? h("div", {}, h("h4", {}, `Gevonden: ${catLabel()}`), ...mine.map(row))
        : h("div", { class: "notice warn inline" }, "Geen passend apparaat gevonden. Kies uw merk en model, of zoek op het IP-adres van het apparaat."),
      other.length ? h("details", {}, h("summary", { class: "small" }, `Andere gevonden apparaten (${other.length})`), ...other.map(row)) : null);
  };
  const fromDiscovery = async (r) => {
    const paramPreset = r.profile_id ? (await api(`/library/profiles/${r.profile_id}`).catch(() => null))?.default_params || {} : {};
    stepDetails({ driver: driverOf[r.driver_id], preset: r.connection || {}, paramPreset,
      defaultName: r.model || driverOf[r.driver_id]?.name, origin: stepSearch, identity: r });
  };

  // ----------------------------------------------------------- merk/model
  const stepBrand = async () => {
    setSteps(1);
    body.replaceChildren(h("div", { class: "muted" }, "Laden…"));
    const brands = await api(`/library/brands?category=${wiz.category}`);
    const search = h("input", { type: "search", placeholder: "Zoek merk of model", "aria-label": "zoek merk of model" });
    const list = h("div", {});
    const modelRow = (b, m) => {
      const [cls, text] = SUPPORT[m.support] || ["", m.support];
      const usable = m.driver_id && driverOf[m.driver_id] && m.support !== "not_supported" && (m.support !== "simulator" || m.lab_connection || lab);
      return h("div", { class: "card flat row spread model", "data-profile": m.profile_id || "", "data-driver": m.driver_id || "" }, h("div", {},
        h("b", {}, m.model), " ", h("span", { class: `pill ${cls}` }, text),
        m.test_status ? h("span", { class: "muted small" }, ` · ${TEST_STATUS[m.test_status] || m.test_status}`) : null,
        m.note ? h("div", { class: "small muted" }, m.note) : null,
        m.requirements?.length ? h("div", { class: "small" }, "Nodig: ", m.requirements.join("; ")) : null),
      usable ? h("button", { class: "btn primary", onclick: () => fromBrand(b, m) }, "Kiezen") : null);
    };
    const paint = () => {
      const q = search.value.trim().toLowerCase();
      const shown = brands.map((b) => ({ ...b, models: b.models.filter((m) => !q || `${b.manufacturer} ${m.model}`.toLowerCase().includes(q)) }))
        .filter((b) => b.models.length);
      list.replaceChildren(...(shown.length ? shown.map((b) => h("div", { class: "mfr" }, h("h4", {}, b.manufacturer),
        b.note ? h("p", { class: "small muted" }, b.note) : null, ...b.models.map((m) => modelRow(b, m))))
        : [h("p", { class: "muted" }, "Geen merk of model gevonden. Probeer ‘Via een gateway’ of (Expert) ‘Handmatig’.")]));
    };
    search.addEventListener("input", paint);
    body.replaceChildren(h("h3", {}, `${catLabel()}: merk en model`),
      h("p", { class: "small muted" }, "Alleen koppelingen die op officiële documentatie of een open standaard zijn gebouwd kunnen regelen. Staat uw apparaat er niet bij of is het ‘nog niet ondersteund’, dan regelt het EMS het niet — er worden geen registers of commando's verzonnen."),
      search, list, h("div", { class: "row", style: { marginTop: "12px" } }, back(stepMethod)));
    paint();
  };
  const fromBrand = async (b, m) => {
    const drv = driverOf[m.driver_id];
    if (m.driver_id === "homewizard.p1" && !m.lab_connection) { homewizardFlow(body, (n) => setSteps(Math.min(n, 3))); return; }
    const prof = m.profile_id ? await api(`/library/profiles/${m.profile_id}`).catch(() => null) : null;
    const preset = { ...(prof?.connection_parameters || {}), ...(m.profile_id ? { profile_id: m.profile_id } : {}),
      ...(m.lab_connection ? (m.driver_id === "homewizard.p1" ? { host: m.lab_connection.host, port: m.lab_connection.port, api: "v1" } : m.lab_connection.host.includes(":") ? { host: m.lab_connection.host } : m.lab_connection) : {}) };
    stepDetails({ driver: drv, preset, paramPreset: prof?.default_params || {}, defaultName: m.model, origin: stepBrand, profile: prof });
  };

  // -------------------------------------------------------------- gateway
  const stepGateway = () => {
    setSteps(1);
    const opts = [];
    if (["heat_pump", "heat_pump_boiler"].includes(wiz.category) && driverOf["sgready.relay"]) {
      opts.push(["SG Ready via netwerkrelais", "Voor warmtepompen met een SG Ready-ingang. Het EMS geeft bedrijfsverzoeken (normaal, aanbevolen aan, …) — de warmtepomp blijft zelf zijn compressor regelen. Laat de bedrading door een installateur aanleggen volgens de handleiding van uw warmtepomp.", "sgready.relay"]);
    }
    if (driverOf["homeassistant.entities"]) {
      opts.push(["Home Assistant", "Gebruik entiteiten uit uw eigen Home Assistant. Bediening alleen na bevestiging dat geen andere automatisering hetzelfde apparaat regelt.", "homeassistant.entities"]);
    }
    if (["smart_relay", "smart_plug", "boiler", "flexible_load"].includes(wiz.category) && driverOf["shelly.switch"]) {
      opts.push(["Netwerkrelais (Shelly Gen2+)", "Aan/uit-schakelen van een verbruiker via een relais of slimme stekker, met minimale aan- en uittijden.", "shelly.switch"]);
    }
    body.replaceChildren(h("h3", {}, `${catLabel()}: via een gateway`),
      ...opts.map(([title, text, id]) => h("div", { class: "card flat row spread" }, h("div", {}, h("b", {}, title), h("div", { class: "small muted" }, text)),
        h("button", { class: "btn primary", onclick: () => stepDetails({ driver: driverOf[id], preset: {}, defaultName: title, origin: stepGateway }) }, "Kiezen"))),
      h("div", { class: "card flat" }, h("b", {}, "Modbus-gateway (RS485 naar netwerk)"),
        h("p", { class: "small muted" }, "Kies uw apparaat bij ‘Merk en model’ en vul bij de verbinding het IP-adres van de gateway en het Modbus-adres (unit-ID) van het apparaat in (niveau Geavanceerd). Direct via een USB-RS485-adapter kan ook: kies dan bij ‘Verbinding’ RTU.")),
      h("div", { class: "row" }, back(stepMethod)));
  };

  // --------------------------------------------------------- handmatig
  const stepManual = () => {
    setSteps(1);
    const generic = drivers.filter((d) => d.driver_id.startsWith("generic.") && d.categories.includes(wiz.category) && d.available_in_mode);
    body.replaceChildren(h("h3", {}, `${catLabel()}: handmatig`),
      h("div", { class: "notice warn inline" }, "Handmatige koppelingen lezen alleen uit. Het EMS regelt zo'n apparaat niet: daarvoor is een apparaatprofiel nodig dat op de officiële documentatie is gebouwd. Neem adressen, paden en topics uitsluitend over uit de handleiding van uw apparaat."),
      generic.length ? h("div", {}, ...generic.map((d) => h("div", { class: "card flat row spread" }, h("div", {}, h("b", {}, d.name),
        h("div", { class: "small muted" }, d.connection_types.join(", "))),
      h("button", { class: "btn", onclick: () => stepDetails({ driver: d, preset: {}, origin: stepManual }) }, "Kiezen"))))
        : h("p", { class: "muted" }, "Geen handmatige koppeling voor dit soort apparaat."),
      h("div", { class: "row" }, back(stepMethod)));
  };

  // ------------------------------------------------------------- gegevens
  const stepDetails = ({ driver, preset, paramPreset = {}, defaultName, origin, profile = null, identity = null }) => {
    if (!driver) { toast("Deze koppeling is niet beschikbaar", true); return; }
    setSteps(2);
    const form = deviceForm({ schema: schemaOf[wiz.category], driver, caps: driver.capabilities.map((c) => c.id), preset, paramPreset, defaultName });
    const isMeter = wiz.category === "smart_meter";
    const primary = h("input", { type: "checkbox" });
    const result = h("div", { "aria-live": "polite" });
    let seen = null;   // identity the device reported in the test (kept so a new IP address is recognised later)
    const collect = () => ({ ...form.collect(), category: wiz.category, driver: driver.driver_id });
    const save = async () => {
      const gm = isMeter ? await api("/gridmeter") : null;
      const req = collect();
      if (seen?.serial_number) req.params = { ...req.params, identity: { manufacturer: seen.manufacturer, model: seen.model, serial: seen.serial_number } };
      const dev = await api("/devices", { method: "POST", body: { ...req, primary_grid_meter: isMeter && primary.checked,
        replace_primary: Boolean(isMeter && primary.checked && gm?.device_id) } });
      // The wizard's test counts as the connection test of the commissioning procedure.
      await api(`/devices/${dev.id}/test`, { method: "POST", timeout: 60000 }).catch(() => null);
      location.hash = `#/devices/${dev.id}`;
    };
    const test = h("button", { class: "btn primary", onclick: async () => {
      let req;
      try { req = collect(); } catch (e) { result.replaceChildren(h("div", { class: "notice bad inline" }, e.message)); return; }
      setSteps(3); test.disabled = true; result.replaceChildren(h("div", { class: "muted" }, "Verbinding testen en functies uitlezen…"));
      try {
        const r = await api("/devices/probe", { method: "POST", body: req, timeout: 60000 });
        const id = r.identity || {};
        seen = r.reachable ? id : null;
        result.replaceChildren(h("h3", {}, r.reachable ? "✓ Verbonden" : "✗ Niet bereikbaar"),
          Object.keys(id).length ? h("p", { class: "small" }, "Apparaat meldt: ", h("b", {}, [id.manufacturer, id.model].filter(Boolean).join(" ")),
            id.serial_number ? ` · serienummer ${id.serial_number}` : "", id.firmware_version ? ` · firmware ${id.firmware_version}` : "") : null,
          r.error ? h("div", { class: "notice bad inline" }, r.error) : null,
          h("h4", {}, "Wat kan het EMS met dit apparaat?"), capabilityTable(r.capability_report),
          h("p", { class: "small muted" }, "Na toevoegen staat het apparaat op ‘alleen lezen’. Bedienen kan pas na de inbedrijfstelling (schaduwmodus → beperkt → volledig)."),
          h("details", {}, h("summary", { class: "small" }, "Testdetails"), checksList(r)),
          !r.reachable ? h("p", { class: "small muted" }, "U kunt het apparaat opslaan als niet-verbonden; het EMS gebruikt of regelt het pas na een geslaagde test.") : null,
          h("div", { class: "row" }, h("button", { class: "btn primary", "data-action": "save", onclick: () => guard(save, "Apparaat toegevoegd (alleen lezen)") },
            r.reachable ? "Opslaan" : "Opslaan als niet-verbonden")));
      } catch (e) { result.replaceChildren(h("div", { class: "notice bad inline" }, e.message)); setSteps(2); }
      test.disabled = false;
    } }, "Verbinding testen");
    body.replaceChildren(h("h3", {}, `${defaultName || driver.name}: gegevens`),
      profile ? h("p", { class: "small muted" }, `Profiel: ${profile.manufacturer} ${profile.model} · ${TEST_STATUS[profile.test_status] || profile.test_status}`,
        profile.known_limitations?.length ? ` · beperkingen: ${profile.known_limitations.join("; ")}` : "") : null,
      identity?.virtual ? h("p", { class: "small" }, h("span", { class: "pill warn" }, "virtueel apparaat (Testomgeving)")) : null,
      form.el,
      driver.driver_id === "homeassistant.entities" ? haHelper(form) : null,
      isMeter ? h("label", { class: "f check" }, primary, "Gebruiken als primaire netmeter (vervangt een bestaande primaire netmeter)") : null,
      h("div", { class: "row", style: { marginTop: "14px" } }, back(origin), test), result);
    if (isMeter) api("/gridmeter").then((gm) => { primary.checked = !gm.device_id || gm.needs_confirmation; }).catch(() => {});
  };

  if (preset === "homewizard") { wiz.category = "smart_meter"; homewizardFlow(body, setSteps); }
  else if (preset && cats.some((c) => c.id === preset)) { wiz.category = preset; stepMethod(); }
  else stepCategory();
}
