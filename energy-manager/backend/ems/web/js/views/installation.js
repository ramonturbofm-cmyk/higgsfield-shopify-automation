// "Mijn installatie": every part of the site, its state, warnings and the EMS health check.
import { api, h } from "../lib.js";

const STATE = { ONLINE: ["good", "online"], CONFIGURED: ["", "ingesteld"], NOT_CONFIGURED: ["warn", "niet ingesteld"],
  ERROR: ["bad", "fout"] };
const LINKS = { grid_meter: "#/devices", pv: "#/devices", battery: "#/devices", heat_pump: "#/devices", ev: "#/devices",
  contract: "#/settings/tariff", nodes: "#/nodes", strategy: "#/settings/profile", grid: "#/settings/general", site: "#/settings/general" };
const ICON = { ok: "✓", warn: "⚠", error: "✗" };

export async function render(root) {
  const [inst, health] = await Promise.all([api("/installation"), api("/health")]);
  root.append(h("h1", {}, "Mijn installatie"),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("h3", {}, "Onderdelen"),
        h("table", { class: "stack" }, h("tbody", {}, inst.items.map((i) => {
          const [cls, label] = STATE[i.state] || ["", i.state];
          return h("tr", {}, h("td", {}, h("a", { href: LINKS[i.key] || "#/installation" }, i.label)),
            h("td", {}, h("span", { class: `pill ${cls}` }, label)), h("td", { class: "small muted" }, i.detail));
        })))),
      h("div", { class: "card" }, h("div", { class: "row spread" }, h("h3", {}, "EMS-gezondheid"),
        h("span", { class: `pill ${health.status.state === "AUTOMATIC" ? "good" : "warn"}` }, health.status.label)),
        h("div", { class: "row" }, h("div", { class: "score" }, `${health.score}%`), h("div", { class: "small muted" }, health.status.reason || "")),
        h("ul", { class: "checklist", style: { marginTop: "12px" } }, health.checks.map((c) => h("li", {},
          h("span", { class: `ic ${c.state}` }, ICON[c.state]), h("span", {}, c.label), c.detail ? h("span", { class: "small muted" }, `— ${c.detail}`) : null))))),
    inst.warnings.length ? h("div", { class: "card", style: { marginTop: "14px" } }, h("h3", {}, "Controle van de instellingen"),
      h("ul", {}, inst.warnings.map((w) => h("li", { class: w.level === "error" ? "nok" : "" }, w.message)))) : null,
    h("p", { class: "muted small" }, "Stappen voor een complete installatie: netmeter koppelen → energiecontract instellen → apparaten koppelen → strategie kiezen."));
}
