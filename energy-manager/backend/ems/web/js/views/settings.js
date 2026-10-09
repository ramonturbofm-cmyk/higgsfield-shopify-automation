import { api, can, dateTime, eur, field, guard, h, num, state } from "../lib.js";

const SECTION_LABEL = { site: "Woning", grid: "Netaansluiting", battery: "Batterij", heatpump: "Warmtepomp", strategy: "Strategie",
  optimizer: "Optimizer", control: "Regeling", forecast: "Prognoses", notifications: "Meldingen", runtime: "Systeem", tariff: "Energiecontract",
  prices: "Marktgegevens (prijsbron)", cloud: "Energy Manager Cloud", node: "Deze computer (node)" };
const LEVELS = ["simple", "advanced", "expert"];
const ENUM_LABEL = { lowest_cost: "Laagste kosten", maximum_profit: "Maximale opbrengst", maximum_self_consumption: "Maximale zelfconsumptie",
  zero_export: "Geen teruglevering", battery_saver: "Batterij sparen", peak_shaving: "Piekbegrenzing", comfort: "Comfort", eco: "Eco",
  backup_priority: "Noodstroom eerst", custom: "Aangepast", balanced: "Gebalanceerd", profit: "Winst", aggressive: "Agressief",
  unlimited: "Onbeperkt", smart: "Smart Export", zero: "Zero Export", dynamic: "Dynamisch", fixed: "Vast", variable: "Variabel",
  production: "Productie", demo: "Demo", none: "Geen", energyzero: "EnergyZero (Nederland, geen token nodig)", quarter: "Kwartier (15 min)", hour: "Uur", entsoe: "ENTSO-E (token nodig)", manual: "Handmatig", open_meteo: "Open-Meteo",
  custom_api: "Eigen API (expert)", eur_kwh: "€/kWh", eur_mwh: "€/MWh", same_slot_7d: "Zelfde kwartier, afgelopen 7 dagen",
  weekday_profile_4w: "Zelfde weekdag, afgelopen 4 weken", stop_plan: "Planning stoppen", use_forecast: "Schatting gebruiken", NL: "Nederland" };
const EXPERT_ONLY_OPTIONS = { provider: ["custom_api"], fallback_provider: ["custom_api"] };

function resolve(schema, prop) {
  if (prop.$ref) return schema.$defs[prop.$ref.split("/").pop()];
  if (prop.allOf?.[0]?.$ref) return { ...schema.$defs[prop.allOf[0].$ref.split("/").pop()], ...prop };
  return prop;
}

function sectionForm(schema, section, values, { only = null, onChange = null } = {}) {
  const def = resolve(schema, schema.properties[section]);
  const level = LEVELS.indexOf(state.level);
  const inputs = {};
  const els = [];
  for (const [key, raw] of Object.entries(def.properties)) {
    const p = resolve(schema, raw);
    const lvl = LEVELS.indexOf(raw.level || p.level || "expert");
    if (lvl > level) continue;
    if (only && !only.includes(key)) continue;    // e.g. no ENTSO-E token when EnergyZero is chosen
    const label = (raw.label_nl || key) + (raw.unit ? ` (${raw.unit})` : "");
    const nullable = Array.isArray(raw.anyOf) && raw.anyOf.some((a) => a.type === "null");
    const base = nullable ? raw.anyOf.find((a) => a.type !== "null") : p;
    const type = base.type || (base.enum ? "string" : p.type);
    let input;
    const v = values[key];
    if (base.enum || p.enum) {
      const hidden = state.level === "expert" || section !== "prices" ? [] : (EXPERT_ONLY_OPTIONS[key] || []).filter((e) => e !== v);
      input = h("select", {}, (base.enum || p.enum).filter((e) => !hidden.includes(e)).map((e) => h("option", { value: e }, ENUM_LABEL[e] || e)));
      input.value = v;
    } else if (type === "boolean") {
      input = h("input", { type: "checkbox", checked: v ? true : null });
    } else if (type === "number" || type === "integer") {
      // Range from the backend schema (same bounds the server validates).
      const lo = base.minimum ?? base.exclusiveMinimum ?? raw.minimum, hi = base.maximum ?? base.exclusiveMaximum ?? raw.maximum;
      input = h("input", { type: "number", step: type === "integer" ? "1" : "any", value: v ?? "", min: lo ?? null, max: hi ?? null,
        placeholder: nullable ? "leeg = automatisch" : "", "aria-label": raw.label_nl || key });
      if (lo !== undefined || hi !== undefined) input.title = `toegestaan: ${lo ?? "…"} – ${hi ?? "…"}${raw.unit ? ` ${raw.unit}` : ""}`;
    } else if (type === "array" || type === "object") {
      input = h("textarea", { rows: 2 }, JSON.stringify(v));
    } else {
      input = h("input", { value: v ?? "", type: key.includes("token") || key.includes("auth") ? "password" : "text", autocomplete: "off" });
    }
    if (onChange) input.addEventListener("change", () => onChange(key));
    inputs[key] = { input, type, nullable };
    const rangeTxt = (type === "number" || type === "integer") && input.title ? `${input.title}.` : "";
    els.push(type === "boolean" ? h("label", { class: "f check", title: raw.help_nl || "" }, input, label)
      : field(label, input, [raw.help_nl, rangeTxt.trim()].filter(Boolean).join(" ")));
  }
  const read = () => {
    const out = {};
    for (const [k, { input, type, nullable }] of Object.entries(inputs)) {
      if (type === "boolean") out[k] = input.checked;
      else if (type === "number" || type === "integer") out[k] = input.value === "" ? (nullable ? null : undefined) : Number(input.value);
      else if (type === "array" || type === "object") out[k] = JSON.parse(input.value || "null");
      else out[k] = input.value;
      if (out[k] === undefined) delete out[k];
    }
    return out;
  };
  return { el: h("div", { class: "form" }, els.length ? els : h("div", { class: "muted small" }, "Geen instellingen op dit niveau.")), read };
}

/** Market data: only the settings of the chosen source(s) — the backend decides which (POST /prices/fields). */
async function marketCard(schema, values, ro, howto) {
  let current = { ...values.prices };
  const body = h("div", {});
  const result = h("div", { class: "small", role: "status" });
  let form = null;
  const build = async () => {
    if (form) current = { ...current, ...form.read() };
    const { visible } = await api("/prices/fields", { method: "POST", body: { values: current } });
    form = sectionForm(schema, "prices", current, { only: visible, onChange: (k) => { if (["provider", "fallback_enabled", "fallback_provider"].includes(k)) build(); } });
    body.replaceChildren(form.el, current.provider === "entsoe" || current.fallback_provider === "entsoe" ? howto : null);
  };
  await build();
  const test = (which) => guard(async () => {
    result.replaceChildren(h("div", { class: "muted" }, "Verbinding testen…"));
    const r = await api("/prices/test", { method: "POST", body: { values: { ...current, ...form.read() }, which } });
    const days = Object.entries(r.per_day || {}).map(([d, n]) => `${d}: ${n}`).join(", ");
    result.replaceChildren(r.ok
      ? h("div", { class: "ok" }, `Verbonden met ${r.provider}: ${r.slots} intervallen van ${r.interval_min} min (${days}). Morgen ${r.tomorrow_available ? "beschikbaar" : "nog niet (volledig) gepubliceerd"}. ${r.duration_ms} ms.${r.rejected ? ` ${r.rejected} waarden verworpen.` : ""}`)
      : h("div", { class: "nok" }, `Test mislukt (${r.provider}): ${r.error}`));
  });
  return h("div", { class: "card" }, h("h3", {}, "Marktgegevens (prijsbron)"),
    h("p", { class: "muted small" }, "Waar de beursprijzen vandaan komen. Wat u zelf betaalt (opslagen, belasting, btw, terugleververgoeding) stelt u in bij Energiecontract."),
    body, result,
    h("div", { class: "row", style: { marginTop: "12px" } },
      h("button", { class: "btn", disabled: ro || null, onclick: () => test("provider") }, "Verbinding testen"),
      current.fallback_enabled && state.level !== "simple" ? h("button", { class: "btn", disabled: ro || null, onclick: () => test("fallback_provider") }, "Reservebron testen") : null,
      h("button", { class: "btn primary", disabled: ro || null, onclick: () => guard(async () => {
        await api("/settings", { method: "PUT", body: { prices: form.read() } });
        state.settings = await api("/settings");
      }, "Marktgegevens opgeslagen") }, "Opslaan")));
}

export async function render(root, [tab = "general"]) {
  const [schema, values] = await Promise.all([api("/settings/schema"), api("/settings")]);
  const tabs = h("div", { class: "seg" }, [["general", "Algemeen"], ["tariff", "Energiecontract"], ["prices", "Marktgegevens & prognoses"], ["profile", "EMS-strategie"],
    ["users", "Gebruikers"], ...(state.level === "simple" ? [] : [["cloud", "Cloud"]])].map(([k, l]) => h("button", { class: k === tab ? "on" : "", onclick: () => { location.hash = `#/settings/${k}`; } }, l)));
  const lv = h("div", { class: "seg", title: "Hoeveel instellingen wilt u zien?" }, LEVELS.map((l) => h("button", { class: l === state.level ? "on" : "",
    onclick: () => { state.level = l; document.body.dataset.level = l; try { localStorage.setItem("ems.level", l); } catch { /* ignore */ } root.replaceChildren(); render(root, [tab]); } },
  { simple: "SIMPLE", advanced: "ADVANCED", expert: "EXPERT" }[l])));
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Instellingen"), lv), tabs, h("div", { style: { height: "14px" } }));
  const ro = !can("admin");
  if (ro) root.append(h("div", { class: "notice info inline" }, "Alleen-lezen: wijzigen vereist beheerdersrechten."));
  const card = (section, extra, only = null) => {
    const f = sectionForm(schema, section, values[section], { only });
    return h("div", { class: "card" }, h("h3", {}, SECTION_LABEL[section]), f.el, extra || null,
      h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn primary", disabled: ro || null, onclick: () => guard(async () => {
        await api("/settings", { method: "PUT", body: { [section]: f.read() } });
        state.settings = await api("/settings");
      }, `${SECTION_LABEL[section]} opgeslagen`) }, "Opslaan")));
  };
  if (tab === "general") {
    root.append(h("div", { class: "grid cols-2" }, ...["site", "grid", "battery", "heatpump", "strategy", "optimizer", "notifications", "control", "node", "runtime"]
      .filter((s) => state.level !== "simple" || ["site", "grid", "battery", "heatpump", "strategy"].includes(s)).map((s) => card(s))));
  } else if (tab === "tariff") {
    const prev = h("div", {});
    const spot = h("input", { type: "number", step: "0.001", value: "0.10" });
    const show = async () => {
      const b = await api(`/tariff/preview?spot=${encodeURIComponent(spot.value)}`);
      prev.replaceChildren(h("table", {}, h("tbody", {},
        h("tr", {}, h("th", {}, "Afname"), h("td", { class: "num" }, b.import_price === null ? "—" : `€ ${num(b.import_price, 4)}/kWh`)),
        ...Object.entries(b.import_parts).map(([k, v]) => h("tr", { class: "small muted" }, h("td", {}, `  ${k}`), h("td", { class: "num" }, num(v, 4)))),
        h("tr", {}, h("th", {}, "Teruglevering"), h("td", { class: "num" }, b.export_price === null ? "—" : `€ ${num(b.export_price, 4)}/kWh`)),
        ...Object.entries(b.export_parts).map(([k, v]) => h("tr", { class: "small muted" }, h("td", {}, `  ${k}`), h("td", { class: "num" }, num(v, 4)))),
        h("tr", {}, h("th", {}, "Vaste kosten per dag"), h("td", { class: "num" }, `€ ${num(b.fixed_costs_per_day, 2)}`)))));
    };
    spot.addEventListener("input", show);
    root.append(h("div", { class: "grid cols-2" }, card("tariff"), h("div", { class: "card" }, h("h3", {}, "Rekenvoorbeeld"),
      field("Marktprijs (€/kWh, excl. btw)", spot), prev,
      h("p", { class: "muted small" }, "ACTUAL_IMPORT_PRICE en ACTUAL_EXPORT_PRICE worden zo per kwartier berekend en gebruikt door de optimizer en het financiële overzicht. Er is geen leverancier ingebouwd: vul de waarden van uw eigen contract in."))));
    show();
  } else if (tab === "prices") {
    const csv = h("textarea", { rows: 6, "aria-label": "prijzen (CSV)", placeholder: "2026-10-07T00:00:00+02:00;0.105\n2026-10-07T00:15:00+02:00;0.098" });
    const res = h("select", { "aria-label": "resolutie" }, h("option", { value: "15" }, "per kwartier (15 min)"), h("option", { value: "60" }, "per uur (60 min)"));
    const file = h("input", { type: "file", accept: ".csv,.txt", "aria-label": "CSV-bestand" });
    file.addEventListener("change", async () => { if (file.files[0]) csv.value = await file.files[0].text(); });
    const report = h("div", { class: "small", role: "status" });
    const parse = () => csv.value.trim().split(/\n+/).filter((l) => l.trim() && !/^\s*(start|tijd)/i.test(l)).map((l) => {
      const [start, price] = l.split(/[;\t]|,(?=\s*-?\d)/);
      return { start: (start || "").trim(), price_eur_kwh: (price || "").trim(), resolution_min: Number(res.value) };
    });
    const send = (dryRun) => guard(async () => {
      const r = await api("/prices/manual", { method: "POST", body: { points: parse(), dry_run: dryRun } });
      report.replaceChildren(h("div", {}, `${r.intervals} intervallen (${r.first ? dateTime(r.first) : "—"} t/m ${r.last_end ? dateTime(r.last_end) : "—"})${dryRun ? "" : " opgeslagen"}.`),
        r.gaps.length ? h("div", { class: "nok" }, `Ontbrekende intervallen: ${r.gaps.map((g) => `${dateTime(g.from)} – ${dateTime(g.to)}`).join("; ")}. Daar gebruikt het EMS een schatting.`) : h("div", { class: "ok" }, "Geen gaten."));
    }, dryRun ? null : "Prijzen opgeslagen");
    const howto = h("details", { class: "small", style: { marginTop: "10px" } }, h("summary", {}, "Hoe krijg ik een ENTSO-E-token? (gratis)"),
      h("ol", {},
        h("li", {}, "Maak een gratis account aan op ", h("a", { href: "https://transparency.entsoe.eu/", target: "_blank", rel: "noopener" }, "transparency.entsoe.eu"), " (Login → Register)."),
        h("li", {}, "Stuur een e-mail naar transparency@entsoe.eu met als onderwerp “Restful API access” en in de tekst het e-mailadres van uw account."),
        h("li", {}, "Na goedkeuring (meestal binnen enkele werkdagen): log in → My Account Settings → Generate a new token."),
        h("li", {}, "Kopieer het token hierboven, kies prijsbron ENTSO-E en klik op Opslaan.")),
      h("p", { class: "muted" }, "Makkelijker: kies prijsbron „EnergyZero” — dezelfde Nederlandse marktprijzen, zonder account of token."));
    root.append(h("div", { class: "grid cols-2" }, await marketCard(schema, values, ro, howto), card("forecast"), h("div", { class: "card" }, h("h3", {}, "Prijzen handmatig invoeren"),
      h("p", { class: "muted small" }, "Eén regel per interval: tijdstip mét tijdzone (bijv. +02:00);marktprijs in €/kWh excl. btw. Rond de zomer-/wintertijd heeft een dag 92 of 100 kwartieren."),
      h("div", { class: "row" }, h("label", { class: "f" }, "Resolutie", res), h("label", { class: "f" }, "Of CSV-bestand", file)), csv,
      h("div", { class: "row", style: { marginTop: "10px" } },
        h("button", { class: "btn", onclick: () => send(true) }, "Controleren"),
        h("button", { class: "btn primary", disabled: ro || null, onclick: () => send(false) }, "Opslaan")), report)));
  } else if (tab === "profile") {
    const p = await api("/profiles");
    const sim = h("div", {});
    const simulate = (x) => guard(async () => {
      sim.replaceChildren(h("div", { class: "muted" }, `Simuleren: ${x.label} over de afgelopen 30 dagen…`));
      const { job_id } = await api("/backtest", { method: "POST", body: { days: 30, overrides: { strategy: { profile: x.id } } } });
      let j;
      for (;;) { j = await api(`/jobs/${job_id}`); if (j.status !== "running") break; await new Promise((r) => setTimeout(r, 1000)); }
      if (j.status !== "done") { sim.replaceChildren(h("div", { class: "notice bad inline" }, j.error)); return; }
      const r = j.result, cur = r.current, neu = r.proposed;
      sim.replaceChildren(h("div", { class: "card" }, h("h3", {}, `Simulatie: ${x.label}`),
        h("table", {}, h("tbody", {},
          h("tr", {}, h("td", {}, "Huidig"), h("td", { class: "num" }, eur(cur.net_cost_incl_wear_eur))),
          h("tr", {}, h("td", {}, "Nieuw"), h("td", { class: "num" }, eur(neu.net_cost_incl_wear_eur))),
          h("tr", {}, h("td", {}, h("b", {}, r.difference_eur <= 0 ? "Extra voordeel" : "Extra kosten")), h("td", { class: "num" }, h("b", {}, eur(Math.abs(r.difference_eur))))),
          h("tr", {}, h("td", {}, "Extra batterijcycli"), h("td", { class: "num" }, num(neu.battery_cycles - cur.battery_cycles, 1))))),
        h("p", { class: "small muted" }, `${r.days} dagen, ${r.source}. ${r.method}.`),
        h("div", { class: "row" }, h("button", { class: "btn", onclick: () => sim.replaceChildren() }, "Annuleren"),
          h("button", { class: "btn primary", disabled: !can("operator") || null, onclick: () => guard(() => api("/profiles/active", { method: "PUT", body: { profile: x.id } }), `Profiel: ${x.label}`)
            .then(() => { root.replaceChildren(); render(root, [tab]); }) }, "Toepassen"))));
    });
    root.append(h("div", { class: "card" }, h("h3", {}, "EMS-strategie"), h("div", { class: "grid cols-3" }, p.profiles.map((x) =>
      h("div", { class: `card flat profile ${x.id === p.active ? "active" : ""}` },
        h("div", { class: "row spread" }, h("b", {}, x.label), x.id === p.active ? h("span", { class: "pill good" }, "actief") : null),
        h("div", { class: "small muted", style: { margin: "6px 0 10px" } }, x.help),
        x.id === p.active ? null : h("div", { class: "row" },
          h("button", { class: "btn sm", disabled: !can("operator") || null, onclick: () => simulate(x) }, "Simuleer wijziging"),
          h("button", { class: "btn sm", disabled: !can("operator") || null, onclick: () => guard(() => api("/profiles/active", { method: "PUT", body: { profile: x.id } }), `Profiel: ${x.label}`)
            .then(() => { root.replaceChildren(); render(root, [tab]); }) }, "Kiezen"))))),
      h("p", { class: "muted small" }, "De strategie bepaalt hoe de optimizer kosten, opbrengst, batterijslijtage, comfort en teruglevering afweegt.")), sim, card("strategy"));
  } else if (tab === "cloud") {
    const st = await api("/cloud");
    const url = h("input", { type: "url", value: st.url || "", placeholder: "https://cloud.voorbeeld.nl", "aria-label": "cloud-adres" });
    const code = h("input", { value: "", placeholder: "ABCD-EFGH", autocomplete: "off", "aria-label": "koppelcode", style: { textTransform: "uppercase" } });
    const openCloud = () => { if (/^https:\/\//.test(url.value)) window.open(url.value, "_blank", "noopener"); };
    const statusRows = st.paired ? h("table", {}, h("tbody", {},
      h("tr", {}, h("td", {}, "Gekoppeld aan"), h("td", {}, `${st.organization || "—"} · ${st.site_name || "—"}`)),
      h("tr", {}, h("td", {}, "Verbinding"), h("td", {}, st.connected ? h("span", { class: "pill good" }, "verbonden") : h("span", { class: "pill warn" }, st.last_error || "niet verbonden"))),
      h("tr", {}, h("td", {}, "Laatste contact"), h("td", {}, st.last_ok ? dateTime(st.last_ok) : "—")),
      h("tr", {}, h("td", {}, "Licentie"), h("td", {}, st.license ? `${st.license.plan || "—"} · ${st.license.valid ? "geldig" : "verlopen (alleen cloudfuncties uit)"}` : "—")),
      h("tr", {}, h("td", {}, "Bediening op afstand"), h("td", {}, `cloud: ${st.remote_control_enabled_in_cloud ? "aan" : "uit"} · deze installatie: ${st.remote_control_allowed_locally ? "toegestaan" : "niet toegestaan"}`)))) : null;
    root.append(h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("h3", {}, "Energy Manager Cloud"),
        h("p", { class: "muted small" }, "Optioneel. Uw installatie maakt alleen een uitgaande, versleutelde verbinding; er hoeven geen poorten open op uw router. ", st.local_note),
        st.paired ? statusRows : h("div", { class: "form" },
          field("Cloud-adres", url, "Het adres van de Energy Manager Cloud-dienst."),
          h("div", { class: "row" }, h("button", { class: "btn", onclick: openCloud }, "Inloggen bij Energy Manager Cloud")),
          h("p", { class: "small muted" }, "Log in (of maak een account) en kies bij uw locatie ‘Installatie koppelen’. U krijgt een koppelcode die 15 minuten geldig is."),
          field("Koppelcode", code)),
        h("div", { class: "row", style: { marginTop: "12px" } }, st.paired
          ? [h("button", { class: "btn", disabled: ro || null, onclick: () => guard(() => api("/cloud/check", { method: "POST" }), "Verbinding gecontroleerd").then(() => { root.replaceChildren(); render(root, [tab]); }) }, "Verbinding controleren"),
            h("button", { class: "btn danger", disabled: ro || null, onclick: () => confirm("Deze installatie ontkoppelen van Energy Manager Cloud?") && guard(() => api("/cloud/unpair", { method: "POST" }), "Ontkoppeld").then(() => { root.replaceChildren(); render(root, [tab]); }) }, "Ontkoppelen")]
          : h("button", { class: "btn primary", disabled: ro || null, onclick: () => guard(() => api("/cloud/pair", { method: "POST", body: { url: url.value, code: code.value } }), "Installatie gekoppeld").then(() => { root.replaceChildren(); render(root, [tab]); }) }, "Deze installatie aan mijn account koppelen"))),
      card("cloud", h("p", { class: "small muted" }, "Opdrachten op afstand worden alleen uitgevoerd als bediening op afstand in de cloud voor deze locatie aan staat én hier is toegestaan. Ze gaan door dezelfde veiligheidscontrole als handmatige bediening."), ["enabled", "remote_control_allowed", "share_summary", "heartbeat_s"])));
  } else if (tab === "users") {
    if (!can("admin")) { root.append(h("div", { class: "card muted" }, "Vereist beheerdersrechten.")); return; }
    const [users, tokens] = await Promise.all([api("/users"), api("/auth/tokens")]);
    const un = h("input"), pw = h("input", { type: "password" }), role = h("select", {}, ["viewer", "operator", "admin", "installer"].map((r) => h("option", { value: r }, r)));
    const tn = h("input", { placeholder: "bijv. Home Assistant" }), tr = h("select", {}, ["viewer", "operator", "admin"].map((r) => h("option", { value: r }, r)));
    const tokOut = h("div", {});
    root.append(h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("h3", {}, "Gebruikers"), h("table", {}, h("tbody", {}, users.map((u) => h("tr", {}, h("td", {}, u.username), h("td", {}, u.role),
        h("td", {}, u.username !== state.user ? h("button", { class: "btn sm danger", onclick: () => confirm(`${u.username} verwijderen?`) && guard(() => api(`/users/${u.username}`, { method: "DELETE" }), "Verwijderd").then(() => location.reload()) }, "×") : "(u)"))))),
        h("div", { class: "form" }, field("Gebruikersnaam", un), field("Wachtwoord (min. 8)", pw), field("Rol", role)),
        h("button", { class: "btn primary", style: { marginTop: "10px" }, onclick: () => guard(() => api("/users", { method: "POST", body: { username: un.value, password: pw.value, role: role.value } }), "Gebruiker aangemaakt").then(() => location.reload()) }, "Toevoegen"),
        h("p", { class: "muted small" }, "Rollen: viewer (bekijken), operator (+ handmatige bediening), admin (+ instellingen), installer (+ apparaten en inbedrijfstelling).")),
      h("div", { class: "card" }, h("h3", {}, "API-tokens (integraties)"), h("table", {}, h("tbody", {}, tokens.map((t) => h("tr", {}, h("td", {}, t.name), h("td", {}, t.role),
        h("td", {}, h("button", { class: "btn sm danger", onclick: () => guard(() => api(`/auth/tokens/${t.id}`, { method: "DELETE" }), "Ingetrokken").then(() => location.reload()) }, "Intrekken")))))),
        h("div", { class: "form" }, field("Naam", tn), field("Rol", tr)),
        h("button", { class: "btn", style: { marginTop: "10px" }, onclick: () => guard(async () => {
          const r = await api("/auth/tokens", { method: "POST", body: { name: tn.value, role: tr.value } });
          tokOut.replaceChildren(h("div", { class: "notice warn inline" }, h("div", {}, h("b", {}, "Token (eenmalig zichtbaar): "), h("code", {}, r.token))));
        }) }, "Token aanmaken"), tokOut)));
  }
}
