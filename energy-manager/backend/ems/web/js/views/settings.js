import { api, can, field, guard, h, num, state } from "../lib.js";

const SECTION_LABEL = { site: "Woning", grid: "Netaansluiting", battery: "Batterij", heatpump: "Warmtepomp", strategy: "Strategie",
  optimizer: "Optimizer", control: "Regeling", forecast: "Prognoses", notifications: "Meldingen", runtime: "Systeem", tariff: "Energiecontract",
  prices: "Prijzen" };
const LEVELS = ["simple", "advanced", "expert"];
const ENUM_LABEL = { lowest_cost: "Laagste kosten", maximum_profit: "Maximale opbrengst", maximum_self_consumption: "Maximale zelfconsumptie",
  zero_export: "Geen teruglevering", battery_saver: "Batterij sparen", peak_shaving: "Piekbegrenzing", comfort: "Comfort", eco: "Eco",
  backup_priority: "Noodstroom eerst", custom: "Aangepast", balanced: "Gebalanceerd", profit: "Winst", aggressive: "Agressief",
  unlimited: "Onbeperkt", smart: "Smart Export", zero: "Zero Export", dynamic: "Dynamisch", fixed: "Vast", variable: "Variabel",
  production: "Productie", demo: "Demo", none: "Geen", entsoe: "ENTSO-E", manual: "Handmatig", open_meteo: "Open-Meteo" };

function resolve(schema, prop) {
  if (prop.$ref) return schema.$defs[prop.$ref.split("/").pop()];
  if (prop.allOf?.[0]?.$ref) return { ...schema.$defs[prop.allOf[0].$ref.split("/").pop()], ...prop };
  return prop;
}

function sectionForm(schema, section, values) {
  const def = resolve(schema, schema.properties[section]);
  const level = LEVELS.indexOf(state.level);
  const inputs = {};
  const els = [];
  for (const [key, raw] of Object.entries(def.properties)) {
    const p = resolve(schema, raw);
    const lvl = LEVELS.indexOf(raw.level || p.level || "expert");
    if (lvl > level) continue;
    const label = (raw.label_nl || key) + (raw.unit ? ` (${raw.unit})` : "");
    const nullable = Array.isArray(raw.anyOf) && raw.anyOf.some((a) => a.type === "null");
    const base = nullable ? raw.anyOf.find((a) => a.type !== "null") : p;
    const type = base.type || (base.enum ? "string" : p.type);
    let input;
    const v = values[key];
    if (base.enum || p.enum) {
      input = h("select", {}, (base.enum || p.enum).map((e) => h("option", { value: e }, ENUM_LABEL[e] || e)));
      input.value = v;
    } else if (type === "boolean") {
      input = h("input", { type: "checkbox", checked: v ? true : null });
    } else if (type === "number" || type === "integer") {
      input = h("input", { type: "number", step: type === "integer" ? "1" : "any", value: v ?? "", placeholder: nullable ? "leeg = automatisch" : "" });
    } else if (type === "array" || type === "object") {
      input = h("textarea", { rows: 2 }, JSON.stringify(v));
    } else {
      input = h("input", { value: v ?? "", type: key.includes("token") ? "password" : "text" });
    }
    inputs[key] = { input, type, nullable };
    els.push(type === "boolean" ? h("label", { class: "f check", title: raw.help_nl || "" }, input, label) : field(label, input, raw.help_nl));
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

export async function render(root, [tab = "general"]) {
  const [schema, values] = await Promise.all([api("/settings/schema"), api("/settings")]);
  const tabs = h("div", { class: "seg" }, [["general", "Algemeen"], ["tariff", "Energiecontract"], ["prices", "Prijzen & prognoses"], ["profile", "EMS-strategie"],
    ["users", "Gebruikers"]].map(([k, l]) => h("button", { class: k === tab ? "on" : "", onclick: () => { location.hash = `#/settings/${k}`; } }, l)));
  const lv = h("div", { class: "seg", title: "Hoeveel instellingen wilt u zien?" }, LEVELS.map((l) => h("button", { class: l === state.level ? "on" : "",
    onclick: () => { state.level = l; try { localStorage.setItem("ems.level", l); } catch { /* ignore */ } root.replaceChildren(); render(root, [tab]); } },
  { simple: "SIMPLE", advanced: "ADVANCED", expert: "EXPERT" }[l])));
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Instellingen"), lv), tabs, h("div", { style: { height: "14px" } }));
  const ro = !can("admin");
  if (ro) root.append(h("div", { class: "notice info inline" }, "Alleen-lezen: wijzigen vereist beheerdersrechten."));
  const card = (section, extra) => {
    const f = sectionForm(schema, section, values[section]);
    return h("div", { class: "card" }, h("h3", {}, SECTION_LABEL[section]), f.el, extra || null,
      h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn primary", disabled: ro || null, onclick: () => guard(async () => {
        await api("/settings", { method: "PUT", body: { [section]: f.read() } });
        state.settings = await api("/settings");
      }, `${SECTION_LABEL[section]} opgeslagen`) }, "Opslaan")));
  };
  if (tab === "general") {
    root.append(h("div", { class: "grid cols-2" }, ...["site", "grid", "battery", "heatpump", "strategy", "optimizer", "notifications", "control", "runtime"]
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
    const csv = h("textarea", { rows: 4, placeholder: "2026-10-07T00:00:00+02:00;0.105\n2026-10-07T01:00:00+02:00;0.098" });
    const howto = h("details", { class: "small", style: { marginTop: "10px" } }, h("summary", {}, "Hoe krijg ik een ENTSO-E-token? (gratis)"),
      h("ol", {},
        h("li", {}, "Maak een gratis account aan op ", h("a", { href: "https://transparency.entsoe.eu/", target: "_blank", rel: "noopener" }, "transparency.entsoe.eu"), " (Login → Register)."),
        h("li", {}, "Stuur een e-mail naar transparency@entsoe.eu met als onderwerp “Restful API access” en in de tekst het e-mailadres van uw account."),
        h("li", {}, "Na goedkeuring (meestal binnen enkele werkdagen): log in → My Account Settings → Generate a new token."),
        h("li", {}, "Kopieer het token hierboven, kies prijsbron ENTSO-E en klik op Opslaan.")),
      h("p", { class: "muted" }, "Zolang u geen token hebt, kunt u prijsbron „Handmatig” kiezen en prijzen hieronder invoeren, of een vast/variabel contract instellen bij Energiecontract."));
    root.append(h("div", { class: "grid cols-2" }, card("prices", howto), card("forecast"), h("div", { class: "card" }, h("h3", {}, "Prijzen handmatig invoeren"),
      h("p", { class: "muted small" }, "Eén regel per uur: tijdstip met tijdzone;marktprijs in €/kWh (excl. btw)."), csv,
      h("div", { class: "row", style: { marginTop: "10px" } }, h("button", { class: "btn", disabled: ro || null, onclick: () => guard(async () => {
        const points = csv.value.trim().split(/\n+/).map((l) => { const [start, price] = l.split(/[;,\t]/); return { start: start.trim(), price_eur_kwh: Number(price), resolution_min: 60 }; });
        const r = await api("/prices/manual", { method: "POST", body: { points } });
        return r;
      }, "Prijzen opgeslagen") }, "Opslaan")))));
  } else if (tab === "profile") {
    const p = await api("/profiles");
    root.append(h("div", { class: "card" }, h("h3", {}, "EMS-strategie"), h("div", { class: "grid cols-3" }, p.profiles.map((x) =>
      h("button", { class: `btn ${x.id === p.active ? "primary" : ""}`, disabled: !can("operator") || null, onclick: () => guard(() => api("/profiles/active", { method: "PUT", body: { profile: x.id } }), `Profiel: ${x.label}`)
        .then(() => { root.replaceChildren(); render(root, [tab]); }) }, x.label))),
      h("p", { class: "muted small" }, "De strategie bepaalt hoe de optimizer kosten, opbrengst, batterijslijtage, comfort en teruglevering afweegt.")), card("strategy"));
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
