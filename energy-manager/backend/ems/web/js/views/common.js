// View helpers shared across pages.
import { api, can, eur, guard, h, num, time } from "../lib.js";

export const kpi = (label, value, sub) => h("div", { class: "card kpi" }, h("div", { class: "l" }, label),
  h("div", { class: "v" }, value), sub ? h("div", { class: "s" }, sub) : null);

export function decisionList(rows, empty = "Nog geen beslissingen") {
  if (!rows?.length) return h("div", { class: "empty" }, empty);
  return h("div", { class: "grid" }, rows.map((d) => h("div", { class: "card flat" },
    h("div", { class: "row spread" }, h("b", {}, d.summary),
      h("span", { class: `pill ${d.outcome === "sent" ? "good" : d.outcome === "shadow" || d.outcome === "dry_run" ? "warn" : ""}` },
        { sent: "uitgevoerd", shadow: "schaduw: EMS zou", dry_run: "proef: EMS zou", released: "vrijgegeven",
          failed: "mislukt", blocked: "geblokkeerd" }[d.outcome] || d.outcome)),
    h("div", { class: "muted small" }, time(d.timestamp || d.ts), d.device ? ` · ${d.device}` : ""),
    d.reasons?.length ? h("ul", { class: "small" }, d.reasons.map((r) => h("li", {}, r))) : null,
    d.expected_profit !== null && d.expected_profit !== undefined ? h("div", { class: "small" }, `Verwacht voordeel planning: ${eur(d.expected_profit)}`) : null)));
}

const DURATIONS = [[30, "30 min"], [60, "1 uur"], [120, "2 uur"], [240, "4 uur"], [null, "tot ik stop"]];

// One authoritative control state per device (from the backend); never derived here.
const STATE_CLASS = { full_control: "good", limited_control: "good", manual_override: "warn", shadow: "warn",
  read_only: "", safe_mode: "bad", offline: "bad", error: "bad" };
const STATE_SHORT = { full_control: "Volledige regeling", limited_control: "Beperkte regeling", manual_override: "Handmatig",
  shadow: "Schaduwmodus", read_only: "Alleen lezen", safe_mode: "Veilige stand", offline: "Offline", error: "Fout" };

export function controlStatePill(state, label) {
  return h("span", { class: `pill ${STATE_CLASS[state] || ""}`, title: label || "", role: "status" },
    h("span", { class: "dot" }), STATE_SHORT[state] || state);
}

const EXEC_LABEL = { sent: "verzonden — wacht op terugmelding", confirmed: "bevestigd door apparaat",
  unconfirmed: "niet bevestigd", no_feedback: "verzonden (apparaat meldt niets terug)", shadow: "niet uitgevoerd",
  rejected: "geweigerd", failed: "mislukt" };

/** "EMS zou doen" (decision) versus "EMS doet nu" (sent + confirmation), side by side. */
export function wouldVsDoes(view) {
  const wd = view.ems_would_do;
  const does = view.ems_does_now || [];
  return h("div", { class: "grid cols-2" },
    h("div", { class: "card flat" }, h("b", {}, "EMS zou doen"),
      wd ? h("div", { class: "small" }, h("div", {}, wd.summary), h("div", { class: "muted" }, (wd.reasons || []).slice(0, 3).join("; ")))
        : h("div", { class: "small muted" }, "Nog geen beslissing")),
    h("div", { class: "card flat" }, h("b", {}, "EMS doet nu"),
      does.length ? h("div", { class: "small" }, does.map((x) => h("div", {},
        `${x.sent?.description || x.validated?.description || ""} — ${EXEC_LABEL[x.status] || x.status}`,
        x.detail && x.status !== "sent" ? h("span", { class: "muted" }, ` (${x.detail})`) : null)))
        : h("div", { class: "small muted" }, ["shadow", "read_only"].includes(view.control_state)
          ? "Niets — dit apparaat wordt niet aangestuurd" : "Geen actieve opdracht")));
}

function valueControl(spec) {
  if (!spec) return null;
  if (spec.type === "enum") return h("select", { "aria-label": "waarde" }, spec.options.map((o) => h("option", { value: o }, o)));
  const attrs = { type: "number", step: spec.step ?? 1, style: { width: "110px" }, "aria-label": `waarde in ${spec.unit}` };
  if (spec.min !== null && spec.min !== undefined) attrs.min = spec.min;
  if (spec.max !== null && spec.max !== undefined) attrs.max = spec.max;
  // Start at half the device maximum, never at the limit itself.
  attrs.value = spec.max !== null && spec.max !== undefined
    ? Math.max(spec.min ?? 0, Math.round(spec.max / 2 / (spec.step || 1)) * (spec.step || 1)) : spec.min ?? 0;
  return h("input", attrs);
}

/** Manual override panel: exactly the actions this device supports, with its own value ranges. */
export async function overridePanel(deviceId, onDone) {
  const view = await api(`/devices/${deviceId}/actions`);
  const head = h("div", { class: "row" }, controlStatePill(view.control_state, view.control_state_label),
    h("span", { class: "small muted" }, view.control_state_label));
  if (!view.actions.length) return h("div", { class: "grid" }, head, h("div", { class: "muted small" }, "Dit apparaat is niet bestuurbaar (alleen meten)."));
  if (!can("operator")) return h("div", { class: "grid" }, head, wouldVsDoes(view), h("div", { class: "muted small" }, "Handmatige bediening vereist operatorrechten."));
  const dur = h("select", { "aria-label": "duur", style: { width: "auto" } }, DURATIONS.map(([v, l]) => h("option", { value: v === null ? "" : v }, l)));
  dur.value = "60";
  const rows = view.actions.map((a) => {
    const input = valueControl(a.value);
    const range = a.value?.type === "number" ? `${a.value.min ?? "?"}–${a.value.max ?? "?"} ${a.value.unit}` : "";
    return h("div", { class: "row" },
      h("button", { class: "btn", disabled: !a.available, title: a.reasons.join("; "), onclick: () => guard(async () => {
        const raw = input ? input.value : null;
        const value = raw === null ? null : a.value?.type === "number" ? Number(raw) : raw;
        if (a.value?.type === "number" && (value < a.value.min || (a.value.max !== null && value > a.value.max)))
          throw new Error(`Waarde buiten bereik (${range})`);
        const r = await api("/overrides", { method: "POST", body: { device: deviceId, action: a.action, value,
          duration_min: dur.value === "" ? null : Number(dur.value) } });
        if (!r.executes) toastNote(r.notes);
      }, `${a.label} ingesteld`).then(onDone) }, a.label),
      input, range ? h("span", { class: "small muted" }, range) : null,
      a.reasons.length ? h("span", { class: `small ${a.available ? "muted" : "nok"}` }, a.reasons.join("; ")) : null);
  });
  return h("div", { class: "grid" }, head, wouldVsDoes(view),
    h("div", { class: "row" }, h("span", { class: "small muted" }, "Duur:"), dur,
      h("span", { class: "small muted" }, "Daarna neemt het EMS de regeling weer over."),
      h("button", { class: "btn", onclick: () => guard(() => api(`/overrides/${deviceId}`, { method: "DELETE" }), "Handmatige bediening beëindigd").then(onDone) }, "Handmatig beëindigen")),
    ...rows);
}

function toastNote(notes) {
  import("../lib.js").then(({ toast }) => toast(`Opgeslagen, maar niet uitgevoerd: ${(notes || []).join("; ")}`, false));
}

export async function activeOverrides() {
  try { return await api("/overrides"); } catch { return []; }
}

export function overridesBox(list, deviceId) {
  const mine = list.filter((o) => !deviceId || o.device === deviceId);
  if (!mine.length) return null;
  return h("div", { class: "grid" }, mine.map((o) => h("div", { class: "pill warn" },
    `Handmatig: ${o.description} ${o.expires ? `tot ${time(o.expires)}` : "(tot handmatig beëindigd)"} — ${o.user}`)));
}

export const fmtPrice = (v) => v === null || v === undefined ? "—" : `€ ${num(v, 3)}/kWh`;
