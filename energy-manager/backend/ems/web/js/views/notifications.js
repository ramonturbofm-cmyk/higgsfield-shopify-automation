import { api, can, dateTime, guard, h, on } from "../lib.js";

export async function render(root) {
  const box = h("div", { class: "grid" });
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Meldingen"),
    can("operator") ? h("button", { class: "btn", onclick: () => guard(() => api("/notifications/ack-all", { method: "POST" }), "Alles gelezen").then(load) }, "Alles als gelezen markeren") : null), box);
  async function load() {
    const rows = await api("/notifications?limit=200");
    box.replaceChildren(...(rows.length ? rows.map((n) => h("div", { class: `card flat` },
      h("div", { class: "row spread" }, h("b", {}, n.message), h("span", { class: `pill ${n.level === "critical" ? "bad" : n.level === "warning" ? "warn" : ""}` }, n.level)),
      h("div", { class: "row spread small muted" }, dateTime(n.timestamp), n.acknowledged ? "gelezen" :
        can("operator") ? h("button", { class: "btn sm", onclick: () => guard(() => api(`/notifications/${n.id}/ack`, { method: "POST" })).then(load) }, "Gelezen") : ""))) :
      [h("div", { class: "card empty" }, "Geen meldingen.")]));
  }
  await load();
  return on((m) => { if (m.type === "notification") load(); });
}
