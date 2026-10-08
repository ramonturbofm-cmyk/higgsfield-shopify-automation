// Energy Manager nodes: this computer plus paired Raspberry Pi / Linux / Windows nodes.
import { ago, api, can, guard, h, toast } from "../lib.js";

const PLATFORM = { WINDOWS: "Windows", RASPBERRY_PI: "Raspberry Pi", LINUX: "Linux", OTHER: "Overig" };
const RELATION = { self: "deze computer", gateway: "apparaatgateway (deze node regelt)", controller: "controller (regelt mijn apparaten)" };

export async function render(root) {
  const body = h("div", {});
  root.append(h("div", { class: "row spread" }, h("h1", {}, "Nodes"),
    h("span", { class: "muted small" }, "Meerdere computers (Windows, Raspberry Pi, Linux) vormen samen één Energy Manager.")), body);
  const load = async () => {
    const data = await api("/nodes");
    const labels = data.role_labels || {};
    const me = data.self;
    body.replaceChildren(
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("h3", {}, "Deze computer"),
          h("table", {}, h("tbody", {},
            h("tr", {}, h("td", {}, "Naam"), h("td", {}, me.name)),
            h("tr", {}, h("td", {}, "Platform"), h("td", {}, `${PLATFORM[me.platform] || me.platform} · ${me.os}`)),
            h("tr", {}, h("td", {}, "Rollen"), h("td", {}, me.roles.map((r) => h("span", { class: "pill", style: { margin: "2px" } }, labels[r] || r)))),
            h("tr", {}, h("td", {}, "Node-ID"), h("td", { class: "small muted" }, me.node_id)),
            h("tr", {}, h("td", {}, "Regelrecht eigen apparaten"), h("td", {}, data.lease.holder
              ? (data.lease.holder_is_self ? "deze computer" : `node ${data.lease.holder.slice(0, 8)}…`) + ` (epoch ${data.lease.epoch})` : "vrij")))),
          h("p", { class: "small muted" }, "Rol wijzigen: Instellingen → Algemeen → Node (ADVANCED).")),
        pairingCard(load)),
      h("h2", {}, "Gekoppelde nodes"),
      data.nodes.filter((n) => n.relation !== "self").length
        ? h("div", { class: "grid" }, data.nodes.filter((n) => n.relation !== "self").map((n) => nodeCard(n, labels, load)))
        : h("div", { class: "empty" }, "Nog geen andere nodes gekoppeld. Alles draait op deze computer."));
  };
  await load();
}

function nodeCard(n, labels, reload) {
  const devs = n.remote_devices || [];
  return h("div", { class: "card" },
    h("div", { class: "row spread" }, h("div", {}, h("b", {}, n.name), h("div", { class: "small muted" },
      `${PLATFORM[n.platform] || n.platform || "?"} · ${n.address || "adres onbekend"} · ${RELATION[n.relation] || n.relation}`)),
      h("div", { class: "row" },
        h("span", { class: `pill ${n.online ? "good" : "bad"}` }, h("span", { class: "dot" }), n.online ? "online" : "offline"),
        n.lease ? h("span", { class: `pill ${n.lease.granted ? "good" : "warn"}`, title: n.lease.error || "" },
          n.lease.granted ? `regelrecht (epoch ${n.lease.epoch})` : "geen regelrecht") : null,
        can("installer") ? h("button", { class: "btn sm danger", onclick: () => {
          if (confirm(`Koppeling met ${n.name} verwijderen?`)) guard(() => api(`/nodes/${n.node_id}`, { method: "DELETE" }), "Ontkoppeld").then(reload);
        } }, "Ontkoppelen") : null)),
    n.error || n.lease?.error ? h("div", { class: "notice warn inline" }, n.lease?.error || n.error) : null,
    h("div", { class: "small muted" }, `Laatst gezien ${n.last_seen ? ago(new Date(n.last_seen * 1000).toISOString()) : "—"}`,
      n.latency_ms ? ` · ${n.latency_ms} ms` : "", n.version ? ` · versie ${n.version}` : "",
      (n.roles || []).length ? ` · rollen: ${(n.roles || []).map((r) => labels[r] || r).join(", ")}` : ""),
    devs.length ? h("table", { style: { marginTop: "10px" } }, h("thead", {}, h("tr", {}, ["Apparaat op deze node", "Status", "Functies", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, devs.map((d) => h("tr", {}, h("td", {}, d.name, h("div", { class: "small muted" }, d.category)),
        h("td", {}, h("span", { class: `pill ${d.status === "online" ? "good" : "warn"}` }, d.status)),
        h("td", { class: "small" }, d.capabilities.length ? d.capabilities.join(", ") : "—"),
        h("td", {}, (n.imported || {})[d.id] ? h("a", { class: "pill good", href: `#/devices/${n.imported[d.id]}` }, "toegevoegd") : can("installer") ? h("div", { class: "row" },
          h("button", { class: "btn sm", onclick: () => guard(() => api(`/nodes/${n.node_id}/devices/${d.id}/import`, { method: "POST", body: {} }),
            `${d.name} toegevoegd (alleen lezen)`).then(reload) }, "Toevoegen"),
          d.grid_meter_kind ? h("button", { class: "btn sm", onclick: () => guard(() => api(`/nodes/${n.node_id}/devices/${d.id}/import`,
            { method: "POST", body: { primary_grid_meter: true } }), `${d.name} is nu de primaire netmeter`).then(reload) }, "Als netmeter") : null) : null)))))
      : (n.relation === "gateway" ? h("div", { class: "muted small" }, "Geen apparaten op deze node (of nog niet opgehaald).") : null));
}

function pairingCard(reload) {
  if (!can("installer")) return h("div", { class: "card" }, h("h3", {}, "Node koppelen"), h("div", { class: "muted" }, "Vereist installateursrechten."));
  const codeBox = h("div", {});
  const addr = h("input", { placeholder: "adres, bijv. 192.168.1.31 of meterkast.local" });
  const code = h("input", { placeholder: "6 cijfers", inputmode: "numeric", maxlength: 6, style: { maxWidth: "140px" } });
  const found = h("div", { class: "list" });
  return h("div", { class: "card" }, h("h3", {}, "Node koppelen"),
    h("p", { class: "small" }, "1. Open Energy Manager op de andere computer (bijv. de Raspberry Pi) → Nodes → ", h("b", {}, "Koppelcode tonen"), ".",
      h("br"), "2. Vul hier het adres en de code in. Een nieuwe node krijgt nooit automatisch toegang."),
    h("div", { class: "row" }, h("button", { class: "btn", onclick: async () => {
      found.replaceChildren(h("span", { class: "muted small" }, "Zoeken op het netwerk (mDNS)…"));
      try {
        const nodes = await api("/nodes/discover");
        found.replaceChildren(...(nodes.length ? nodes.map((n) => h("div", { class: "item" },
          h("div", {}, h("b", {}, n.name), h("div", { class: "small muted" }, `${n.address} · ${PLATFORM[n.platform] || n.platform}${n.paired ? " · al gekoppeld" : ""}`)),
          h("button", { class: "btn sm", onclick: () => { addr.value = n.address; code.focus(); } }, "Kiezen")))
          : [h("span", { class: "muted small" }, "Geen andere Energy Manager-nodes gevonden. Vul het adres handmatig in.")]));
      } catch (e) { found.replaceChildren(h("span", { class: "nok small" }, e.message)); }
    } }, "Zoeken op het netwerk")), found,
    h("div", { class: "row", style: { marginTop: "8px" } }, addr, code,
      h("button", { class: "btn primary", onclick: () => guard(() => api("/nodes/pair", { method: "POST", body: { address: addr.value, code: code.value.trim() } }),
        "Node gekoppeld").then(reload) }, "Koppelen")),
    h("hr", { style: { margin: "14px 0", border: 0, borderTop: "1px solid var(--border)" } }),
    h("div", { class: "row spread" }, h("span", { class: "small" }, "Deze computer aan een andere node koppelen:"),
      h("button", { class: "btn", onclick: async () => {
        const r = await api("/nodes/pairing-code", { method: "POST" });
        codeBox.replaceChildren(h("div", { class: "code" }, r.code),
          h("div", { class: "small muted" }, `Geldig 10 minuten, één keer te gebruiken. Node: ${r.node.name}`));
        toast("Koppelcode aangemaakt");
      } }, "Koppelcode tonen")), codeBox);
}
