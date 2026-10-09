// Energy Manager Cloud — customer portal and platform admin portal (no framework, no inline scripts/styles: strict CSP).
const $main = document.getElementById("main");
const $nav = document.getElementById("nav");
const S = { me: null, org: null };

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}
const csrf = () => (document.cookie.match(/(?:^|;\s*)emc_csrf=([^;]+)/) || [])[1] || "";
async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = decodeURIComponent(csrf());
  const r = await fetch(`/api/v1${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" });
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) {
    const err = new Error(typeof data?.detail === "string" ? data.detail : Array.isArray(data?.detail) ? data.detail.map((d) => d.msg).join("; ") : `fout ${r.status}`);
    err.status = r.status; throw err;
  }
  return data;
}
function toast(msg) { const t = document.getElementById("toast"); t.textContent = msg; t.hidden = false; setTimeout(() => { t.hidden = true; }, 3500); }
async function act(fn, ok) { try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { toast(e.message); throw e; } }
const dt = (s) => (s ? new Date(s).toLocaleString("nl-NL", { dateStyle: "short", timeStyle: "short" }) : "—");
const input = (name, type = "text", attrs = {}) => h("input", { name, type, ...attrs });
const field = (label, el) => h("div", {}, h("label", {}, label), el);
const pill = (ok, yes, no) => h("span", { class: `pill ${ok ? "good" : "warn"}` }, ok ? yes : no);
const ROLE = { ORGANIZATION_OWNER: "Eigenaar", ORGANIZATION_ADMIN: "Beheerder", INSTALLER: "Installateur", OPERATOR: "Bediener", VIEWER: "Kijker", SUPPORT: "Support (tijdelijk)" };
const euro = (c, cur) => (c === null || c === undefined ? "prijs nog niet ingesteld" : `${(c / 100).toLocaleString("nl-NL", { style: "currency", currency: cur || "EUR" })}`);

function nav() {
  $nav.replaceChildren(...(S.me ? [h("a", { href: "#/" }, "Mijn installaties"), S.org ? h("a", { href: `#/org/${S.org}` }, "Organisatie") : null,
    h("a", { href: "#/account" }, "Account"), S.me.platform_admin ? h("a", { href: "#/admin" }, "Platformbeheer") : null,
    h("button", { onclick: () => act(() => api("/auth/logout", { method: "POST" })).finally(() => { S.me = null; location.hash = "#/login"; }) }, "Uitloggen")]
    : [h("a", { href: "#/login" }, "Inloggen"), h("a", { href: "#/register" }, "Account maken")]).filter(Boolean));
}

// ------------------------------------------------------------------ public pages
function pageLogin() {
  const email = input("email", "email", { autocomplete: "username", required: true }), pw = input("password", "password", { autocomplete: "current-password" });
  const go = async (ev) => {
    ev.preventDefault();
    const r = await act(() => api("/auth/login", { method: "POST", body: { email: email.value, password: pw.value } }));
    location.hash = r.mfa_required ? "#/mfa" : "#/";
  };
  return h("div", { class: "card narrow" }, h("h1", {}, "Inloggen"),
    h("form", { onsubmit: go }, field("E-mailadres", email), field("Wachtwoord", pw), h("button", { class: "btn primary" }, "Inloggen")),
    h("p", { class: "small" }, h("button", { class: "btn sm", onclick: () => act(() => api("/auth/magic-link", { method: "POST", body: { email: email.value } }), "Als het adres bekend is, is er een inloglink verstuurd.") }, "Inloggen met e-maillink"), " ",
      h("button", { class: "btn sm", onclick: () => act(() => api("/auth/password/forgot", { method: "POST", body: { email: email.value } }), "Als het adres bekend is, is er een e-mail verstuurd.") }, "Wachtwoord vergeten")),
    h("p", { class: "small muted" }, "Nog geen account? ", h("a", { href: "#/register" }, "Account maken")));
}
function pageRegister() {
  const email = input("email", "email", { required: true }), pw = input("password", "password", { autocomplete: "new-password", minlength: 10 });
  const org = input("org"), name = input("name"), out = h("div", {});
  return h("div", { class: "card narrow" }, h("h1", {}, "Account maken"),
    h("form", { onsubmit: async (ev) => { ev.preventDefault();
      const r = await act(() => api("/auth/register", { method: "POST", body: { email: email.value, password: pw.value || null, name: name.value, organization: org.value } }));
      out.replaceChildren(h("div", { class: "notice info" }, r.message, " Open de link in de e-mail om uw adres te bevestigen.")); } },
    field("Naam", name), field("E-mailadres", email), field("Wachtwoord (minstens 10 tekens; leeg = inloggen met e-maillink)", pw),
    field("Naam van uw organisatie of huishouden", org), h("button", { class: "btn primary" }, "Account maken")), out,
    h("p", { class: "small muted" }, "Na registratie krijgt u een proefperiode. Uw lokale Energy Manager werkt altijd, ook zonder cloud."));
}
async function pageToken(kind, token) {
  if (kind === "verify") { await act(() => api("/auth/verify", { method: "POST", body: { token } }), "E-mailadres bevestigd"); location.hash = "#/login"; return h("div"); }
  if (kind === "magic") { const r = await act(() => api("/auth/magic-link/consume", { method: "POST", body: { token } })); location.hash = r.mfa_required ? "#/mfa" : "#/"; return h("div"); }
  const pw = input("password", "password", { autocomplete: "new-password" });
  return h("div", { class: "card narrow" }, h("h1", {}, "Nieuw wachtwoord"), field("Wachtwoord (minstens 10 tekens)", pw),
    h("button", { class: "btn primary", onclick: () => act(() => api("/auth/password/reset", { method: "POST", body: { token, password: pw.value } }), "Wachtwoord ingesteld").then(() => { location.hash = "#/login"; }) }, "Opslaan"));
}
async function pageInvite(token) {
  const info = await api(`/auth/invitations/${token}`);
  const head = [h("h1", {}, "Uitnodiging"), h("p", {}, `Organisatie: ${info.organization} · rol: ${ROLE[info.role] || info.role} · ${info.email}`)];
  if (S.me) return h("div", { class: "card narrow" }, ...head, h("button", { class: "btn primary", onclick: () => act(() => api("/auth/invitations/accept", { method: "POST", body: { token } }), "Uitnodiging geaccepteerd").then(() => { location.hash = "#/"; }) }, "Accepteren"));
  if (info.account_exists) return h("div", { class: "card narrow" }, ...head, h("p", {}, "U heeft al een account. Log in en open deze link opnieuw."), h("a", { class: "btn primary", href: "#/login" }, "Inloggen"));
  const pw = input("password", "password", { autocomplete: "new-password" });
  return h("div", { class: "card narrow" }, ...head, field("Kies een wachtwoord (minstens 10 tekens)", pw),
    h("button", { class: "btn primary", onclick: () => act(() => api("/auth/invitations/register", { method: "POST", body: { token, password: pw.value } }), "Welkom!").then(() => { location.hash = "#/"; }) }, "Account aanmaken"));
}
function pageMfa() {
  const code = input("code", "text", { inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6 });
  return h("div", { class: "card narrow" }, h("h1", {}, "Tweestapsverificatie"), field("Code uit uw authenticator-app", code),
    h("button", { class: "btn primary", onclick: () => act(() => api("/auth/mfa/verify", { method: "POST", body: { code: code.value } })).then(() => { location.hash = "#/"; }) }, "Bevestigen"));
}

// ------------------------------------------------------------------ customer portal
async function pageHome() {
  if (!S.me.organizations.length) return h("div", { class: "card" }, h("h1", {}, "Mijn installaties"), h("p", {}, "U bent nog geen lid van een organisatie."));
  const org = S.org || S.me.organizations[0].id; S.org = org; nav();
  const [o, sites] = await Promise.all([api(`/orgs/${org}`), api(`/orgs/${org}/sites`)]);
  const pick = S.me.organizations.length > 1 ? h("select", { onchange: (e) => { S.org = e.target.value; route(); } },
    S.me.organizations.map((x) => h("option", { value: x.id, selected: x.id === org }, x.name))) : null;
  const name = input("site");
  return h("div", {}, h("div", { class: "row" }, h("h1", {}, "Mijn installaties"), pick),
    h("p", { class: "muted" }, `${o.name} · uw rol: ${ROLE[o.my_role] || o.my_role}`),
    o.via_support ? h("div", { class: "notice" }, "U heeft tijdelijke supporttoegang die door de klant is verleend.") : null,
    h("div", { class: "grid" }, sites.map((s) => h("div", { class: "card" }, h("div", { class: "row" }, h("h2", {}, s.name), pill(s.online, "online", "offline")),
      h("p", { class: "small muted" }, `${s.nodes.length} installatie(s) · bediening op afstand ${s.remote_control_enabled ? "aan" : "uit"}`),
      s.nodes.map((n) => h("div", { class: "small" }, `${n.name || "installatie"} · versie ${n.version || "?"} · ${n.online ? "online" : `laatst gezien ${dt(n.last_seen)}`}`)),
      h("a", { class: "btn sm", href: `#/site/${org}/${s.id}` }, "Openen")))),
    o.my_permissions.includes("sites.manage") ? h("div", { class: "card" }, h("h2", {}, "Locatie toevoegen"), field("Naam (bijv. Thuis)", name),
      h("button", { class: "btn", onclick: () => act(() => api(`/orgs/${org}/sites`, { method: "POST", body: { name: name.value } }), "Locatie toegevoegd").then(route) }, "Toevoegen")) : null);
}

async function pageSite(org, site) {
  const [o, s] = await Promise.all([api(`/orgs/${org}`), api(`/orgs/${org}/sites/${site}`)]);
  const can = (p) => o.my_permissions.includes(p);
  const codeOut = h("div", {});
  const pairing = can("nodes.pair") ? h("div", { class: "card" }, h("h2", {}, "Installatie koppelen"),
    h("p", { class: "small muted" }, "Maak een koppelcode en vul die in op uw lokale Energy Manager: Instellingen → Cloud → ‘Deze installatie aan mijn account koppelen’. De code is 15 minuten geldig en werkt één keer."),
    h("button", { class: "btn primary", onclick: async () => { const r = await act(() => api(`/orgs/${org}/sites/${site}/pairing-code`, { method: "POST" }));
      codeOut.replaceChildren(h("p", { class: "big" }, r.code), h("p", { class: "small muted" }, `geldig tot ${dt(r.expires)}`)); } }, "Koppelcode maken"), codeOut) : null;
  const remote = can("remote_access.grant") ? h("label", { class: "check" }, h("input", { type: "checkbox", checked: s.remote_control_enabled,
    onchange: (e) => act(() => api(`/orgs/${org}/sites/${site}`, { method: "PATCH", body: { remote_control_enabled: e.target.checked } }), "Opgeslagen").catch(() => route()) }),
  "Bediening op afstand toestaan voor deze locatie") : h("p", {}, `Bediening op afstand: ${s.remote_control_enabled ? "aan" : "uit"}`);
  const dev = h("select", {}, (s.devices || []).map((d) => h("option", { value: d.id }, `${d.name} (${d.id})`)));
  const action = h("select", {}, ["battery_auto", "battery_charge", "battery_discharge", "battery_standby", "pv_limit", "hp_mode", "ev_current", "switch"].map((a) => h("option", { value: a }, a)));
  const value = input("value"), dur = input("duration", "number", { value: "60", min: "1", max: "1440" });
  const cmds = can("devices.read") ? await api(`/orgs/${org}/sites/${site}/commands`) : [];
  return h("div", {}, h("a", { href: "#/" }, "← Mijn installaties"), h("h1", {}, s.name), h("div", { class: "grid" },
    h("div", { class: "card" }, h("h2", {}, "Status"), h("p", {}, pill(s.online, "online", "offline")),
      s.nodes.map((n) => h("div", { class: "small" }, `${n.name} · versie ${n.version} · ${n.platform} · EMS ${n.status.ems_status || "?"}`,
        can("nodes.pair") ? h("button", { class: "btn sm danger", onclick: () => confirm("Installatie ontkoppelen?") && act(() => api(`/orgs/${org}/nodes/${n.id}`, { method: "DELETE" }), "Ontkoppeld").then(route) }, "ontkoppelen") : null)),
      s.summary ? h("p", { class: "small" }, `Netvermogen ${s.summary.grid_w ?? "—"} W · zon ${s.summary.pv_w ?? "—"} W · batterij ${s.summary.battery_soc_pct ?? "—"}% (${dt(s.summary.ts)})`) : null,
      remote),
    h("div", { class: "card" }, h("h2", {}, "Apparaten"), h("table", {}, h("tbody", {}, (s.devices || []).map((d) => h("tr", {}, h("td", {}, d.name), h("td", { class: "muted" }, d.category), h("td", {}, pill(d.online, "online", "offline"))))))),
    pairing,
    can("devices.control") && s.remote_control_enabled ? h("div", { class: "card" }, h("h2", {}, "Opdracht op afstand"),
      h("p", { class: "small muted" }, "De installatie voert de opdracht alleen uit als bediening op afstand ook lokaal is toegestaan, en altijd via de lokale veiligheidscontrole."),
      field("Apparaat", dev), field("Actie", action), field("Waarde (W, A, modus …)", value), field("Duur (minuten)", dur),
      h("button", { class: "btn primary", onclick: () => act(() => api(`/orgs/${org}/sites/${site}/commands`, { method: "POST", body: { device: dev.value, action: action.value,
        value: value.value === "" ? null : (Number.isNaN(Number(value.value)) ? value.value : Number(value.value)), duration_min: Number(dur.value) } }), "Opdracht verstuurd").then(route) }, "Versturen")) : null,
    cmds.length ? h("div", { class: "card tbl wide" }, h("h2", {}, "Recente opdrachten"), h("table", {}, h("tbody", {}, cmds.map((c) => h("tr", {}, h("td", {}, dt(c.created)),
      h("td", {}, `${c.command.device}: ${c.command.action} ${c.command.value ?? ""}`), h("td", {}, h("span", { class: `pill ${c.status === "accepted" ? "good" : c.status === "rejected" || c.status === "failed" ? "bad" : "warn"}` }, c.status)),
      h("td", { class: "small muted" }, c.result?.reason || "")))))) : null));
}

async function pageOrg(org) {
  const o = await api(`/orgs/${org}`);
  const can = (p) => o.my_permissions.includes(p);
  const [m, sub, grants, audit] = await Promise.all([can("users.read") ? api(`/orgs/${org}/members`) : null, can("subscriptions.read") ? api(`/orgs/${org}/subscription`) : null,
    can("remote_access.grant") ? api(`/orgs/${org}/support-grants`) : [], can("audit.read") ? api(`/orgs/${org}/audit`) : []]);
  const email = input("email", "email"), role = h("select", {}, ["VIEWER", "OPERATOR", "INSTALLER", "ORGANIZATION_ADMIN", "ORGANIZATION_OWNER"].map((r) => h("option", { value: r }, ROLE[r])));
  const sEmail = input("support", "email"), sHours = input("hours", "number", { value: "24", min: "1", max: "72" }), sScope = h("select", {}, h("option", { value: "read" }, "alleen bekijken"), h("option", { value: "control" }, "bekijken en bedienen"));
  const toggle = (key, label, help) => h("label", { class: "check", title: help }, h("input", { type: "checkbox", checked: o[key], disabled: !can("org.manage"),
    onchange: (e) => act(() => api(`/orgs/${org}`, { method: "PATCH", body: { [key]: e.target.checked } }), "Opgeslagen") }), label);
  return h("div", {}, h("h1", {}, o.name), h("div", { class: "grid" },
    m ? h("div", { class: "card tbl" }, h("h2", {}, "Leden"), h("table", {}, h("tbody", {}, m.members.map((x) => h("tr", {}, h("td", {}, x.email), h("td", {}, ROLE[x.role] || x.role),
      h("td", {}, can("users.manage") && x.user_id !== S.me.id ? h("button", { class: "btn sm danger", onclick: () => confirm(`${x.email} verwijderen?`) && act(() => api(`/orgs/${org}/members/${x.user_id}`, { method: "DELETE" }), "Verwijderd").then(route) }, "×") : ""))),
    m.invitations.map((i) => h("tr", { class: "muted" }, h("td", {}, `${i.email} (uitgenodigd)`), h("td", {}, ROLE[i.role]), h("td", {}, dt(i.expires)))))),
    can("users.invite") ? h("div", {}, field("Uitnodigen (e-mail)", email), field("Rol", role), h("button", { class: "btn", onclick: () => act(() => api(`/orgs/${org}/invitations`, { method: "POST", body: { email: email.value, role: role.value } }), "Uitnodiging verstuurd").then(route) }, "Uitnodigen")) : null) : null,
    h("div", { class: "card" }, h("h2", {}, "Privacy en beveiliging"),
      toggle("sync_consent", "Toestemming om actuele waarden van installaties in de cloud op te slaan", "Intrekken verwijdert de opgeslagen waarden."),
      toggle("require_mfa", "Tweestapsverificatie verplicht voor eigenaren en beheerders"),
      h("p", { class: "small muted" }, `Gegevensregio: ${o.data_region.toUpperCase()}. Het platformbeheer ziet geen verbruiksgegevens.`)),
    sub ? h("div", { class: "card" }, h("h2", {}, "Abonnement en licentie"),
      h("p", {}, `${sub.subscription?.plan || "—"} · ${sub.subscription?.status || "—"} · tot ${dt(sub.subscription?.current_period_end)}`),
      sub.subscription?.pending_plan ? h("p", { class: "small" }, `Wijzigt naar ${sub.subscription.pending_plan} aan het einde van de periode.`) : null,
      sub.subscription?.cancel_at_period_end ? h("p", { class: "notice" }, "Opgezegd: loopt af aan het einde van de periode. Uw lokale EMS blijft werken.") : null,
      h("p", { class: "small" }, `Licentie ${sub.license?.license_id || "—"}: ${sub.license?.valid ? "geldig" : "niet geldig"} · max. ${sub.max_sites ?? "∞"} locaties, ${sub.max_nodes ?? "∞"} installaties`),
      h("p", { class: "small muted" }, `Functies: ${sub.entitlements.join(", ")}`),
      h("table", {}, h("tbody", {}, sub.plans.map((p) => h("tr", {}, h("td", {}, p.name), h("td", { class: "small" }, `${euro(p.price_month_cents, p.currency)} / maand`),
        h("td", {}, can("subscriptions.manage") && p.key !== sub.subscription?.plan ? h("button", { class: "btn sm", onclick: () => act(() => api(`/orgs/${org}/subscription/change`, { method: "POST", body: { plan: p.key } }), "Abonnement aangepast").then(route) }, "Kiezen") : ""))))),
      can("subscriptions.manage") ? h("button", { class: "btn danger", onclick: () => confirm("Abonnement opzeggen per einde periode?") && act(() => api(`/orgs/${org}/subscription/cancel`, { method: "POST" }), "Opgezegd").then(route) }, "Opzeggen") : null,
      h("p", { class: "small muted" }, "Online betalen volgt via een professionele betaalprovider; er worden hier geen betaalgegevens opgeslagen.")) : null,
    can("remote_access.grant") ? h("div", { class: "card" }, h("h2", {}, "Tijdelijke supporttoegang"),
      h("p", { class: "small muted" }, "Alleen u kunt een supportmedewerker tijdelijk toegang geven (max. 72 uur). U kunt die altijd intrekken."),
      field("E-mail supportmedewerker", sEmail), field("Uren", sHours), field("Toegang", sScope),
      h("button", { class: "btn", onclick: () => act(() => api(`/orgs/${org}/support-grants`, { method: "POST", body: { email: sEmail.value, hours: Number(sHours.value), scope: sScope.value } }), "Toegang verleend").then(route) }, "Toegang geven"),
      h("table", {}, h("tbody", {}, grants.map((g) => h("tr", {}, h("td", {}, g.email), h("td", {}, g.scope), h("td", {}, g.active ? `tot ${dt(g.expires)}` : "verlopen/ingetrokken"),
        h("td", {}, g.active ? h("button", { class: "btn sm danger", onclick: () => act(() => api(`/orgs/${org}/support-grants/${g.id}`, { method: "DELETE" }), "Ingetrokken").then(route) }, "intrekken") : "")))))) : null,
    audit.length ? h("div", { class: "card tbl wide" }, h("h2", {}, "Logboek"), h("table", {}, h("tbody", {}, audit.slice(0, 50).map((a) => h("tr", { class: "small" }, h("td", {}, dt(a.ts)), h("td", {}, a.actor), h("td", {}, a.action), h("td", {}, a.target)))))) : null));
}

async function pageAccount() {
  const sessions = await api("/auth/sessions");
  const out = h("div", {}), code = input("code", "text", { inputmode: "numeric", maxlength: 6 });
  const cur = input("cur", "password", { autocomplete: "current-password" }), nw = input("new", "password", { autocomplete: "new-password" });
  const delPw = input("delpw", "password"), delEmail = input("delemail", "email");
  const mfa = S.me.mfa_enabled ? h("p", {}, pill(true, "tweestapsverificatie staat aan", "")) : h("div", {},
    S.me.mfa_setup_required ? h("div", { class: "notice" }, "Voor uw rol is tweestapsverificatie verplicht.") : null,
    h("button", { class: "btn", onclick: async () => { const r = await act(() => api("/auth/mfa/setup", { method: "POST" }));
      out.replaceChildren(h("p", { class: "small" }, "Voeg deze sleutel toe in uw authenticator-app (bijv. via ‘sleutel invoeren’):"), h("p", {}, h("code", {}, r.secret)),
        h("p", { class: "small muted" }, h("code", {}, r.otpauth_uri)), field("Code ter bevestiging", code),
        h("button", { class: "btn primary", onclick: () => act(() => api("/auth/mfa/enable", { method: "POST", body: { code: code.value } }), "Tweestapsverificatie aan").then(boot) }, "Aanzetten")); } }, "Tweestapsverificatie instellen"), out);
  return h("div", {}, h("h1", {}, "Account"), h("div", { class: "grid" },
    h("div", { class: "card" }, h("h2", {}, S.me.email), mfa),
    h("div", { class: "card" }, h("h2", {}, "Wachtwoord wijzigen"), S.me.passwordless ? null : field("Huidig wachtwoord", cur), field("Nieuw wachtwoord", nw),
      h("button", { class: "btn", onclick: () => act(() => api("/auth/password/change", { method: "POST", body: { current: cur.value || null, new: nw.value } }), "Wachtwoord gewijzigd; andere sessies zijn afgemeld") }, "Wijzigen")),
    h("div", { class: "card tbl" }, h("h2", {}, "Actieve sessies"), h("table", {}, h("tbody", {}, sessions.map((s) => h("tr", { class: "small" }, h("td", {}, dt(s.last_seen)), h("td", {}, s.ip), h("td", {}, s.user_agent.slice(0, 40)),
      h("td", {}, s.current ? "deze" : h("button", { class: "btn sm danger", onclick: () => act(() => api(`/auth/sessions/${s.id}`, { method: "DELETE" }), "Afgemeld").then(route) }, "afmelden"))))))),
    h("div", { class: "card" }, h("h2", {}, "Mijn gegevens"), h("button", { class: "btn", onclick: async () => {
      const data = await act(() => api("/auth/me/export"));
      const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })), download: "energy-manager-cloud-export.json" }); a.click(); } }, "Gegevens exporteren (JSON)"),
      h("h2", {}, "Account verwijderen"), h("p", { class: "small muted" }, "Organisaties waarvan u het enige lid bent worden ook verwijderd; gekoppelde installaties blijven lokaal gewoon werken."),
      S.me.passwordless ? null : field("Wachtwoord", delPw), field("Typ uw e-mailadres ter bevestiging", delEmail),
      h("button", { class: "btn danger", onclick: () => confirm("Account definitief verwijderen?") && act(() => api("/auth/me", { method: "DELETE", body: { password: delPw.value || null, confirm_email: delEmail.value } }), "Account verwijderd").then(() => { S.me = null; location.hash = "#/login"; }) }, "Verwijderen"))));
}

// ------------------------------------------------------------------ platform admin
async function pageAdmin() {
  const [ov, orgs, plans] = await Promise.all([api("/admin/overview"), api("/admin/organizations"), api("/admin/plans")]);
  const f = { organization: input("o"), email: input("e", "email"), plan: h("select", {}, plans.map((p) => h("option", { value: p.key }, p.name))),
    max_sites: input("ms", "number", { min: "1" }), max_nodes: input("mn", "number", { min: "1" }), trial_days: input("td", "number", { value: "0", min: "0" }) };
  const num = (el) => (el.value === "" ? null : Number(el.value));
  const k = (label, v) => h("div", { class: "card" }, h("div", { class: "muted small" }, label), h("div", { class: "kpi" }, v));
  return h("div", {}, h("h1", {}, "Platformbeheer"), h("p", { class: "small muted" }, ov.privacy),
    h("div", { class: "grid" }, k("Organisaties", ov.organizations), k("Gebruikers", ov.users), k("Installaties online", `${ov.installations.online} / ${ov.installations.total}`),
      k("Licenties geldig", `${ov.licenses.valid} (verloopt ≤30 d: ${ov.licenses.expiring_30d})`), k("Nieuwe registraties (7 d / 30 d)", `${ov.registrations["7d"]} / ${ov.registrations["30d"]}`),
      k("Installaties met fouten", ov.installations_with_errors)),
    h("div", { class: "card small" }, `Versies: ${Object.entries(ov.versions).map(([v, n]) => `${v}: ${n}`).join(", ") || "—"} · Abonnementen: ${Object.entries(ov.subscriptions.by_status).map(([v, n]) => `${v}: ${n}`).join(", ") || "—"}`),
    h("div", { class: "card tbl" }, h("h2", {}, "Klanten"), h("table", {}, h("tbody", {}, orgs.map((o) => h("tr", { class: "small" }, h("td", {}, o.name), h("td", {}, o.owner_email || "—"), h("td", {}, `${o.plan || "—"} · ${o.subscription_status || "—"}`),
      h("td", {}, o.license?.valid ? `geldig tot ${dt(o.license.expires)}` : "niet geldig"), h("td", {}, `${o.sites} loc. · ${o.installations} inst.`),
      h("td", {}, h("button", { class: "btn sm", onclick: () => act(() => api(`/admin/organizations/${o.id}`, { method: "PATCH", body: { status: o.status === "active" ? "suspended" : "active" } }), "Bijgewerkt").then(route) }, o.status === "active" ? "opschorten" : "activeren"),
        h("button", { class: "btn sm", onclick: () => act(() => api(`/admin/organizations/${o.id}/subscription/renew`, { method: "POST" }), "Verlengd (handmatige betaling)").then(route) }, "verlengen"))))))),
    h("div", { class: "grid" },
      h("div", { class: "card" }, h("h2", {}, "Klant toevoegen"), field("Naam / organisatie", f.organization), field("E-mail eigenaar", f.email), field("Abonnement", f.plan),
        field("Max. locaties (leeg = volgens abonnement)", f.max_sites), field("Max. installaties", f.max_nodes), field("Proefperiode (dagen)", f.trial_days),
        h("button", { class: "btn primary", onclick: () => act(() => api("/admin/customers", { method: "POST", body: { organization: f.organization.value, email: f.email.value, plan: f.plan.value,
          max_sites: num(f.max_sites), max_nodes: num(f.max_nodes), trial_days: num(f.trial_days) || 0 } }), "Klant aangemaakt; uitnodiging verstuurd").then(route) }, "Aanmaken en uitnodigen")),
      h("div", { class: "card" }, h("h2", {}, "Abonnementen en prijzen"), h("p", { class: "small muted" }, "Prijzen staan niet in de code; stel ze hier in (in centen)."),
        plans.map((p) => { const m = input(`pm-${p.key}`, "number", { value: p.price_month_cents ?? "", min: "0" }), y = input(`py-${p.key}`, "number", { value: p.price_year_cents ?? "", min: "0" });
          return h("div", {}, h("b", {}, p.name), h("div", { class: "row" }, field("per maand", m), field("per jaar", y),
            h("button", { class: "btn sm", onclick: () => act(() => api(`/admin/plans/${p.key}`, { method: "PUT", body: { price_month_cents: num(m), price_year_cents: num(y) } }), "Prijs opgeslagen") }, "Opslaan")),
          h("p", { class: "small muted" }, `${p.entitlements.join(", ")} · max ${p.max_sites ?? "∞"} loc. / ${p.max_nodes ?? "∞"} inst.`)); }))));
}

// ------------------------------------------------------------------ router
async function boot() {
  try { S.me = await api("/auth/me"); } catch { S.me = null; }
  route();
}
async function route() {
  nav();
  const [, page, a, b] = (location.hash || "#/").split("/");
  const open = { login: pageLogin, register: pageRegister };
  try {
    let el;
    if (["verify", "reset", "magic"].includes(page)) el = await pageToken(page, a);
    else if (page === "invite") el = await pageInvite(a);
    else if (page === "mfa") el = pageMfa();
    else if (!S.me || open[page]) el = (open[page] || pageLogin)();
    else if (S.me.mfa_setup_required && page !== "account") { location.hash = "#/account"; return; }
    else if (page === "site") el = await pageSite(a, b);
    else if (page === "org") el = await pageOrg(a);
    else if (page === "account") el = await pageAccount();
    else if (page === "admin") el = await pageAdmin();
    else el = await pageHome();
    $main.replaceChildren(el);
  } catch (e) {
    if (e.status === 401) { S.me = null; location.hash = "#/login"; return; }
    $main.replaceChildren(h("div", { class: "card" }, h("h1", {}, "Er ging iets mis"), h("p", {}, e.message)));
  }
}
window.addEventListener("hashchange", () => { if (location.hash.startsWith("#/login") || location.hash.startsWith("#/register")) route(); else boot(); });
boot();
