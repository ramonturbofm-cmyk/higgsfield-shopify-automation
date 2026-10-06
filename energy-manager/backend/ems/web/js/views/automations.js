import { api, can, guard, h } from "../lib.js";

const OP_LABEL = { "<": "kleiner dan", "<=": "≤", ">": "groter dan", ">=": "≥", "==": "gelijk aan", "!=": "niet gelijk aan", between: "tussen", in: "is een van" };
const ACTION_LABEL = { battery_charge: "batterij laden (W)", battery_discharge: "batterij ontladen (W)", battery_standby: "batterij stand-by",
  battery_auto: "batterij automatisch", pv_limit: "PV begrenzen (W)", hp_mode: "warmtepompmodus", ev_current: "laadstroom (A)", switch: "schakelen" };

export async function render(root) {
  const cat = await api("/automations/catalog");
  const listBox = h("div", { class: "grid" });
  const editor = h("div", {});
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Automatiseringen"),
    can("admin") ? h("button", { class: "btn primary", onclick: () => edit(null) }, "+ Nieuwe automatisering") : null),
  h("p", { class: "muted small" }, "Regels werken naast de optimizer: een DAN-actie zet een tijdelijke handmatige bediening die vanzelf verloopt."),
  editor, listBox);

  async function load() {
    const rules = await api("/automations");
    listBox.replaceChildren(...(rules.length ? rules.map((r) => h("div", { class: "card" },
      h("div", { class: "row spread" }, h("b", {}, r.name), h("div", { class: "row" },
        h("span", { class: `pill ${r.enabled ? "good" : ""}` }, r.enabled ? "actief" : "uit"),
        r.last_state === true ? h("span", { class: "pill warn" }, "voorwaarde waar") : null,
        h("button", { class: "btn sm", onclick: async () => {
          const ev = await api(`/automations/${r.id}/evaluate`, { method: "POST" });
          alert(`Nu: ${ev.meaning}`);
        } }, "Nu testen"),
        can("admin") ? h("button", { class: "btn sm", onclick: () => edit(r) }, "Bewerken") : null,
        can("admin") ? h("button", { class: "btn sm danger", onclick: () => confirm("Verwijderen?") && guard(() => api(`/automations/${r.id}`, { method: "DELETE" }), "Verwijderd").then(load) }, "×") : null)),
      h("div", { class: "small muted" }, describe(r.definition)))) : [h("div", { class: "card empty" }, "Nog geen automatiseringen.")]));
  }

  function describe(d) {
    const c = (x) => x.all ? x.all.map(c).join(" EN ") : x.any ? `(${x.any.map(c).join(" OF ")})` : x.not ? `NIET ${c(x.not)}`
      : `${cat.metrics[x.metric] || x.metric} ${OP_LABEL[x.op]} ${Array.isArray(x.value) ? x.value.join(" en ") : x.value}`;
    const a = (list) => (list || []).map((x) => x.type === "override" ? `${ACTION_LABEL[x.action] || x.action}${x.value !== null && x.value !== undefined ? ` ${x.value}` : ""} (${x.device}, ${x.duration_min} min)`
      : x.type === "notify" ? `melding "${x.message}"` : x.type === "set_profile" ? `profiel ${x.profile}` : x.type).join(", ");
    return `ALS ${c(d.if)} DAN ${a(d.then)}${d.else?.length ? ` ANDERS ${a(d.else)}` : ""}`;
  }

  function condRow(c = { metric: "price.import", op: "<", value: 0 }) {
    const metric = h("select", {}, Object.entries(cat.metrics).map(([k, l]) => h("option", { value: k }, l)));
    metric.value = c.metric;
    const op = h("select", {}, Object.entries(OP_LABEL).map(([k, l]) => h("option", { value: k }, l)));
    op.value = c.op;
    const val = h("input", { value: Array.isArray(c.value) ? c.value.join(",") : c.value });
    const row = h("div", { class: "row" }, metric, op, val, h("button", { class: "btn sm", onclick: () => row.remove() }, "×"));
    row.get = () => {
      const raw = val.value.trim();
      const parse = (s) => (s !== "" && !Number.isNaN(Number(s)) && metric.value !== "time" ? Number(s) : s);
      return { metric: metric.value, op: op.value, value: ["between", "in"].includes(op.value) ? raw.split(",").map((s) => parse(s.trim())) : parse(raw) };
    };
    return row;
  }

  function actionRow(a = { type: "override", device: cat.devices[0]?.id, action: "battery_charge", value: 3000, duration_min: 30 }) {
    const type = h("select", {}, [["override", "Apparaat bedienen"], ["notify", "Melding sturen"], ["set_profile", "Profiel kiezen"], ["replan", "Opnieuw plannen"]].map(([v, l]) => h("option", { value: v }, l)));
    type.value = a.type;
    const device = h("select", {}, cat.devices.map((d) => h("option", { value: d.id }, d.name)));
    if (a.device) device.value = a.device;
    const action = h("select", {}, cat.device_actions.map((x) => h("option", { value: x }, ACTION_LABEL[x] || x)));
    if (a.action) action.value = a.action;
    const value = h("input", { value: a.value ?? a.message ?? a.profile ?? "", placeholder: "waarde / tekst" });
    const dur = h("input", { type: "number", value: a.duration_min ?? 30, title: "duur in minuten" });
    const row = h("div", { class: "row" }, type, device, action, value, dur, h("span", { class: "small muted" }, "min"), h("button", { class: "btn sm", onclick: () => row.remove() }, "×"));
    const sync = () => { const ov = type.value === "override"; [device, action, dur].forEach((e) => { e.style.display = ov ? "" : "none"; }); value.style.display = type.value === "replan" ? "none" : ""; };
    type.addEventListener("change", sync); sync();
    row.get = () => {
      if (type.value === "override") {
        const v = value.value.trim();
        return { type: "override", device: device.value, action: action.value, value: v === "" ? null : Number.isNaN(Number(v)) ? v : Number(v), duration_min: Number(dur.value) };
      }
      if (type.value === "notify") return { type: "notify", message: value.value };
      if (type.value === "set_profile") return { type: "set_profile", profile: value.value };
      return { type: "replan" };
    };
    return row;
  }

  function edit(rule) {
    const def = rule?.definition || { if: { all: [{ metric: "price.import", op: "<", value: 0 }] }, then: [], else: [] };
    const name = h("input", { value: rule?.name || "Nieuwe automatisering" });
    const enabled = h("input", { type: "checkbox", checked: rule ? (rule.enabled ? true : null) : true });
    const mode = h("select", {}, h("option", { value: "all" }, "ALLE voorwaarden (EN)"), h("option", { value: "any" }, "EEN van de voorwaarden (OF)"));
    mode.value = def.if.any ? "any" : "all";
    const conds = h("div", {}, (def.if.all || def.if.any || [def.if]).map(condRow));
    const thens = h("div", {}, (def.then || []).map(actionRow));
    const elses = h("div", {}, (def.else || []).map(actionRow));
    const cooldown = h("input", { type: "number", value: def.cooldown_s ?? 300 });
    editor.replaceChildren(h("div", { class: "card" }, h("h3", {}, rule ? "Automatisering bewerken" : "Nieuwe automatisering"),
      h("div", { class: "form" }, h("label", { class: "f" }, "Naam", name), h("label", { class: "f check" }, enabled, "Actief"),
        h("label", { class: "f" }, "Minimale tijd tussen acties (s)", cooldown)),
      h("div", { class: "rule" }, h("div", { class: "row spread" }, h("b", {}, "ALS"), mode), conds,
        h("button", { class: "btn sm", onclick: () => conds.append(condRow()) }, "+ voorwaarde")),
      h("div", { class: "rule" }, h("b", {}, "DAN"), thens, h("button", { class: "btn sm", onclick: () => thens.append(actionRow()) }, "+ actie")),
      h("div", { class: "rule" }, h("b", {}, "ANDERS (optioneel, wanneer de voorwaarde weer onwaar wordt)"), elses,
        h("button", { class: "btn sm", onclick: () => elses.append(actionRow({ type: "override", device: cat.devices[0]?.id, action: "battery_auto", value: null, duration_min: 5 })) }, "+ actie")),
      h("div", { class: "row" }, h("button", { class: "btn primary", onclick: () => guard(async () => {
        const body = { name: name.value, enabled: enabled.checked, definition: { if: { [mode.value]: [...conds.children].map((r) => r.get()) },
          then: [...thens.children].map((r) => r.get()), else: [...elses.children].map((r) => r.get()), cooldown_s: Number(cooldown.value) } };
        await api(rule ? `/automations/${rule.id}` : "/automations", { method: rule ? "PUT" : "POST", body });
        editor.replaceChildren(); load();
      }, "Opgeslagen") }, "Opslaan"), h("button", { class: "btn", onclick: () => editor.replaceChildren() }, "Annuleren"))));
  }
  await load();
}
