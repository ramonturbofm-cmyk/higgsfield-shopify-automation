import { api, can, guard, h, num, on } from "../lib.js";

/* Testomgeving: overzicht van de virtuele apparaten op deze computer en storingen om te testen
 * (offline, Modbus-foutcode, vertraging). Alles hier is gesimuleerd. */

const EXC = [["", "geen"], ["2", "2 — ongeldig adres"], ["3", "3 — ongeldige waarde"], ["4", "4 — apparaatfout"], ["6", "6 — apparaat bezet"]];

export async function render(root) {
  const box = h("div", {});
  root.append(h("h1", {}, "Testomgeving"),
    h("div", { class: "notice warn inline" }, h("div", {}, h("b", {}, "Alles op deze pagina is virtueel. "),
      "De testwoning draait op deze computer. De apparaten spreken echte protocollen (Modbus TCP met SunSpec, HomeWizard-API, Shelly-RPC), ",
      "zodat de hele keten — zoeken, testen, functierapport, inbedrijfstelling, sturen en terugmelden — getest kan worden zonder hardware. ",
      "Voeg ze toe via Apparaten → Apparaat toevoegen.")), box);
  const load = async () => {
    const lab = await api("/lab");
    if (!lab.active) { box.replaceChildren(h("div", { class: "card" }, "De Testomgeving is niet actief. Kies bij het opstarten van de app ‘Testomgeving’.")); return; }
    const house = Object.entries(lab.house || {});
    box.replaceChildren(
      h("div", { class: "card" }, h("h3", {}, "Virtuele woning"),
        h("p", { class: "small muted" }, `Netmeting (virtuele P1): ${num(lab.meter_w, 0)} W · simulatietijd ${new Date(lab.sim_time).toLocaleString("nl-NL")}`),
        h("div", { class: "tbl-wrap" }, h("table", {}, h("tbody", {}, house.map(([id, v]) => h("tr", {}, h("th", { scope: "row" }, id.replace("lab_", "")),
          h("td", { class: "small" }, Object.entries(v).map(([k, x]) => `${k} ${typeof x === "number" ? num(x, 1) : x}`).join(" · ")))))))),
      h("div", { class: "card" }, h("h3", {}, "Virtuele apparaten en storingen"),
        h("p", { class: "small muted" }, "Zet een storing aan en kijk wat het EMS doet: het apparaat wordt ‘niet verbonden’, opdrachten worden geweigerd of verlopen, en het EMS valt terug op een veilige stand."),
        h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Apparaat", "Adres", "Storing", ""].map((t) => h("th", {}, t)))),
          h("tbody", {}, lab.devices.map((d) => {
            const off = h("input", { type: "checkbox", checked: d.fault.offline || null });
            const exc = h("select", { "aria-label": "Modbus-foutcode", disabled: d.protocol !== "modbus_tcp" || null }, EXC.map(([v, l]) => h("option", { value: v }, l)));
            exc.value = d.fault.exception ? String(d.fault.exception) : "";
            const delay = h("input", { type: "number", min: 0, max: 30, step: 0.5, value: d.fault.delay_s || 0, style: { width: "80px" }, "aria-label": "vertraging (s)" });
            return h("tr", { "data-lab": d.key }, h("td", {}, d.label), h("td", { class: "small mono" }, `${d.host}:${d.port}`),
              h("td", { class: "small" }, h("label", { class: "f check" }, off, "offline"), " ", exc, " vertraging ", delay, " s"),
              h("td", {}, can("installer") ? h("button", { class: "btn sm", onclick: () => guard(() => api(`/lab/faults/${d.key}`, { method: "PUT",
                body: { offline: off.checked, exception: exc.value ? Number(exc.value) : null, delay_s: Number(delay.value || 0) } }), "Storing ingesteld").then(load) }, "Toepassen") : null));
          }))))));
  };
  await load();
  // Only the woning card changes often; refresh it on every live tick without touching the fault inputs.
  let busy = false;
  return on(async (m) => {
    if (m.type !== "live" || busy || box.contains(document.activeElement)) return;
    busy = true;
    try { await load(); } catch { /* ignore */ }
    busy = false;
  });
}
