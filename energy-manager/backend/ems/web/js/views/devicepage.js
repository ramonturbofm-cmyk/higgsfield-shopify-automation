import { api, h, on, state, statusPill } from "../lib.js";
import { lineChart } from "../charts.js";
import { activeOverrides, overridePanel, overridesBox } from "./common.js";

/** Generic page for one device category: status, values, manual override, history chart. */
export async function devicePage(root, { title, categories, values, history, extra, empty }) {
  const devices = (await api("/devices")).filter((d) => categories.includes(d.category) && d.enabled);
  root.append(h("h1", {}, title));
  if (!devices.length) {
    root.append(h("div", { class: "card empty" }, empty, " ", h("a", { href: "#/devices/add" }, "Apparaat toevoegen")));
    return null;
  }
  const boxes = [];
  for (const d of devices) {
    const valBox = h("div", { class: "grid cols-4" });
    const ovBox = h("div", {});
    const histBox = h("div", {});
    const refresh = async () => {
      const [ovs, panel] = await Promise.all([activeOverrides(), overridePanel(d.id, refresh)]);
      ovBox.replaceChildren(overridesBox(ovs, d.id), h("div", { style: { height: "10px" } }), panel);
    };
    const paint = (live) => {
      const dev = live?.devices?.[d.id];
      if (!dev) return;
      valBox.replaceChildren(...values(dev.values || {}, live).map(([l, v, s]) => h("div", { class: "card kpi flat" },
        h("div", { class: "l" }, l), h("div", { class: "v" }, v), s ? h("div", { class: "s" }, s) : null)));
    };
    boxes.push(paint);
    root.append(h("div", { class: "card" },
      h("div", { class: "row spread" }, h("h3", {}, d.name), h("div", { class: "row" }, statusPill(d.status),
        h("a", { href: `#/devices/${d.id}`, class: "btn sm" }, "Details"))),
      d.error ? h("div", { class: "notice warn inline" }, d.error) : null,
      valBox, h("h3", { style: { marginTop: "16px" } }, "Bediening"), ovBox, histBox));
    paint(state.live);
    refresh();
    if (history) {
      const rows = await api(`/devices/${d.id}/history?hours=24`);
      const ts = rows.map((r) => new Date(r.ts * 1000).toISOString());
      histBox.replaceChildren(h("h3", { style: { marginTop: "16px" } }, history.title),
        lineChart({ times: ts, unit: history.unit, decimals: history.decimals ?? 1, height: 180, zero: history.zero,
          series: history.series.map((s) => ({ ...s, values: rows.map((r) => s.get(r.values || {})) })) }));
    }
  }
  if (extra) root.append(await extra());
  return on((m) => { if (m.type === "live") boxes.forEach((p) => p(m.data)); });
}
