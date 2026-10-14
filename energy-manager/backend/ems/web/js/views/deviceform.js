import { h, state } from "../lib.js";

export const PHASES = [["3P", "Driefasig (L1 + L2 + L3)"], ["L1", "Alleen L1"], ["L2", "Alleen L2"], ["L3", "Alleen L3"]];
const LEVEL_CLASS = { advanced: "advanced-only", expert: "expert-only" };

export function checksList(result) {
  return h("ul", { class: "checks" }, (result.checks || []).map((c) => h("li", { class: c.ok ? "ok" : "nok" },
    `${c.ok ? "✓" : "✗"} ${c.label}`, c.detail ? h("span", { class: "muted small" }, ` — ${c.detail}`) : null)));
}

const STATUS_PILL = {
  SUPPORTED_READ: "good", SUPPORTED_WRITE: "good", REQUIRES_COMMISSIONING: "st", REQUIRES_CONFIGURATION: "warn",
  NOT_SUPPORTED: "", UNVERIFIED: "warn",
};

/** Capability report: what this device can do with this integration, per capability, with an honest status. */
export function capabilityTable(rows) {
  if (!rows?.length) return h("p", { class: "muted small" }, "Geen functies gevonden.");
  return h("div", { class: "tbl-wrap" }, h("table", { class: "capreport" },
    h("thead", {}, h("tr", {}, ["Functie", "Soort", "Status", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map((r) => h("tr", {},
      h("td", {}, r.label, state.level === "expert" ? h("span", { class: "muted small" }, ` (${r.capability})`) : null),
      h("td", { class: "small" }, r.kind === "control" ? "bediening" : "meten"),
      h("td", {}, h("span", { class: `pill ${STATUS_PILL[r.status] ?? ""}`, title: r.status }, r.status_label)),
      h("td", { class: "small muted" }, r.detail || ""))))));
}

/** Schema-driven device form (wizard and edit).
 *
 *  * Connection fields come from the driver's ``connection_schema``. Their metadata decides how they are shown:
 *    ``level`` (advanced / expert fields are hidden at lower UI levels but keep their value), ``show_if``
 *    ([field, value]: only relevant for one transport), ``secret`` (password input; never guessed from the name),
 *    ``readonly`` (taken over from the chosen profile / existing device, never edited here).
 *  * ``preset`` fills connection values (discovery result, brand/model choice); ``paramPreset`` fills the
 *    datasheet defaults of the chosen profile — the user can change both.
 *  * Parameters: the type's own ranges and help; phase only where electrically relevant. */
export function deviceForm({ schema, driver, caps, existing = null, preset = {}, paramPreset = {}, defaultName = null }) {
  const category = schema.category;
  const name = h("input", { value: existing?.name ?? defaultName ?? driver.name, required: true, maxlength: 80, "aria-label": "naam" });
  const phase = schema.phase_relevant ? h("select", { "aria-label": "fase" }, PHASES.map(([v, l]) => h("option", { value: v }, l))) : null;
  if (phase) phase.value = existing?.phase && existing.phase !== "NA" ? existing.phase : "3P";
  const conn = {};
  const fixed = {};            // readonly values (profile id …): carried, never edited
  const wrappers = [];
  const cschema = driver.connection_schema || {};
  for (const [k, s] of Object.entries(cschema)) {
    const cur = existing?.connection?.[k] ?? preset[k];
    if (s.readonly) { if (cur !== undefined && cur !== null) fixed[k] = cur; continue; }
    if (s.categories && category && !s.categories.includes(category)) continue;   // not relevant for this kind of device
    let input;
    if (s.enum) input = h("select", { "aria-label": s.label_nl || k, name: k }, s.enum.map((v) => h("option", { value: v }, s.enum_labels?.[v] || v)));
    else if (s.type === "boolean") input = h("input", { type: "checkbox", checked: (cur ?? s.default) ? true : null });
    else if (s.type === "array") input = h("textarea", { rows: 6, class: "mono", spellcheck: "false", placeholder: s.example ? JSON.stringify(s.example, null, 1) : "[]" });
    else input = h("input", { type: s.secret ? "password" : s.type === "integer" || s.type === "number" ? "number" : "text",
      autocomplete: s.secret ? "new-password" : "off", value: cur ?? s.default ?? "", "aria-label": s.label_nl || k });
    if (s.enum) input.value = cur ?? s.default ?? s.enum[0];
    if (s.type === "array" && cur) input.value = JSON.stringify(cur, null, 1);
    conn[k] = [input, s];
    const wrap = h("label", { class: `${s.type === "boolean" ? "f check" : "f"} ${LEVEL_CLASS[s.level] || ""}` }, s.label_nl || k, input,
      s.help_nl ? h("span", { class: "muted small" }, s.help_nl) : null,
      s.secret ? h("span", { class: "muted small" }, "Wordt versleuteld opgeslagen en nooit getoond.") : null);
    wrappers.push([wrap, s]);
  }
  const applyShowIf = () => {
    for (const [wrap, s] of wrappers) {
      if (!s.show_if) continue;
      const ctl = conn[s.show_if[0]];
      const val = ctl ? ctl[0].value : (fixed[s.show_if[0]] ?? cschema[s.show_if[0]]?.default);
      wrap.hidden = String(val) !== String(s.show_if[1]);
    }
  };
  for (const [input] of Object.values(conn)) input.addEventListener("change", applyShowIf);
  applyShowIf();

  const params = {};
  const capSet = new Set(caps || []);
  const paramFields = (schema.params || []).filter((p) => !p.show_if || capSet.has(p.show_if[0]) === p.show_if[1]).map((p) => {
    const cur = existing?.params?.[p.key] ?? paramPreset[p.key];
    let input;
    if (p.kind === "enum") { input = h("select", {}, p.options.map((o) => h("option", { value: o }, o))); input.value = cur ?? p.options[0]; }
    else if (p.kind === "time") input = h("input", { type: "time", value: cur ?? "" });
    else input = h("input", { type: "number", min: p.min ?? null, max: p.max ?? null, step: p.step ?? "any", value: cur ?? "", placeholder: p.recommended ?? "" });
    params[p.key] = [input, p];
    const range = p.kind === "number" && (p.min !== null || p.max !== null) ? ` (${p.min ?? "…"}–${p.max ?? "…"} ${p.unit || ""})` : "";
    return h("label", { class: "f" }, `${p.label}${p.unit && !range ? ` (${p.unit})` : ""}${range}${p.required_for_control ? " *" : ""}`, input,
      p.help ? h("span", { class: "muted small" }, p.help) : null);
  });
  const fixedInfo = Object.keys(fixed).length ? h("p", { class: "small muted expert-only" },
    Object.entries(fixed).map(([k, v]) => `${cschema[k]?.label_nl || k}: ${v}`).join(" · ")) : null;
  const el = h("div", {}, h("div", { class: "form" }, h("label", { class: "f" }, "Naam", name),
    phase ? h("label", { class: "f" }, "Aansluiting", phase, h("span", { class: "muted small" }, "Op welke fase(n) het apparaat is aangesloten (voor fasebewaking).")) : null,
    ...wrappers.map(([w]) => w)), fixedInfo,
  paramFields.length ? h("div", {}, h("h4", {}, "Apparaatgegevens"), h("p", { class: "small muted" }, "* nodig voordat het EMS dit apparaat mag regelen. Neem de waarden over van het typeplaatje of datablad."),
    h("div", { class: "form" }, ...paramFields)) : null);
  const collect = () => {
    if (!name.value.trim()) throw new Error("Naam is verplicht");
    const c = { ...fixed };
    for (const [k, [input, s]] of Object.entries(conn)) {
      if (s.type === "boolean") c[k] = input.checked;
      else if (s.type === "array") {
        if (!input.value.trim()) continue;
        try { c[k] = JSON.parse(input.value); } catch (e) { throw new Error(`${s.label_nl || k}: ongeldige JSON (${e.message})`); }
      } else if (input.value !== "") c[k] = s.type === "integer" || s.type === "number" ? Number(input.value) : input.value;
    }
    for (const [k, s] of Object.entries(cschema)) {
      const visible = conn[k] && !wrappers.find(([, ss]) => ss === s)?.[0].hidden;
      if (s.required && visible && (c[k] === undefined || c[k] === "")) throw new Error(`${s.label_nl || k} is verplicht`);
    }
    const pr = {};
    for (const [k, [input, p]] of Object.entries(params)) {
      if (input.value === "") { if (existing?.params?.[k] !== undefined) pr[k] = null; continue; }
      if (p.kind === "number") {
        const v = Number(input.value);
        if ((p.min !== null && v < p.min) || (p.max !== null && v > p.max)) throw new Error(`${p.label}: toegestaan ${p.min}–${p.max} ${p.unit || ""}`);
        pr[k] = v;
      } else pr[k] = input.value;
    }
    return { name: name.value.trim(), phase: phase ? phase.value : "NA", connection: c, params: pr };
  };
  /** Set a connection field from code (e.g. the Home Assistant entity picker fills the mapping). */
  const set = (k, v) => {
    const f = conn[k];
    if (!f) return;
    const [input, sch] = f;
    if (sch.type === "boolean") input.checked = Boolean(v);
    else if (sch.type === "array") input.value = v && v.length ? JSON.stringify(v, null, 1) : "";
    else input.value = v ?? "";
    input.dispatchEvent(new Event("change"));
  };
  return { el, collect, set };
}
