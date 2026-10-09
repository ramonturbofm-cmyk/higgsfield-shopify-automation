// Small SVG charts: one y-axis per chart, direct labels, hover crosshair + tooltip.
import { h, svg, num, time, dateTime } from "./lib.js";

function niceTicks(lo, hi, n = 4) {
  if (hi - lo < 1e-9) hi = lo + 1;
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw);
  const start = Math.floor(lo / step) * step;
  const t = [start];
  while (t[t.length - 1] < hi - step * 1e-6) t.push(+(t[t.length - 1] + step).toFixed(10));
  return t;
}

/**
 * lineChart({times: ISO[], series: [{name, values, cls, step?, area?}], unit, decimals, height, zero})
 */
export function lineChart(opts) {
  const { times, series, unit = "", decimals = 1, height = 230, zero = false, marker = null } = opts;
  const W = 900, H = height, L = 50, R = 14, T = 10, B = 26;
  const wrap = h("div", { class: "chart" });
  if (!times.length) { wrap.append(h("div", { class: "empty" }, "Geen gegevens beschikbaar")); return wrap; }
  const vals = series.flatMap((s) => [...s.values, ...(s.min || []), ...(s.max || [])]).filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  if (!vals.length) { wrap.append(h("div", { class: "empty" }, "Geen gegevens beschikbaar")); return wrap; }
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  const ticks = niceTicks(lo, hi); lo = ticks[0]; hi = ticks[ticks.length - 1];
  const n = times.length, pw = W - L - R, ph = H - T - B;
  const x = (i) => L + (n === 1 ? pw / 2 : (pw * i) / (n - 1));
  const y = (v) => T + ph * (1 - (v - lo) / (hi - lo));
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.title || "grafiek" });
  for (const t of ticks) {
    s.append(svg("line", { class: Math.abs(t) < 1e-9 && zero ? "zero-l" : "grid-l", x1: L, x2: W - R, y1: y(t), y2: y(t) }));
    s.append(svg("text", { class: "ax", x: L - 6, y: y(t) + 4, "text-anchor": "end" }, num(t, Math.abs(hi - lo) < 2 ? 2 : Math.abs(hi - lo) < 20 ? 1 : 0)));
  }
  const step = Math.max(1, Math.ceil(n / 8));
  const span = n > 1 ? new Date(times[n - 1]) - new Date(times[0]) : 0;
  const tick = span > 20 * 3600e3 ? (t) => time(t, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : (t) => time(t);
  for (let i = 0; i < n; i += step) {
    s.append(svg("text", { class: "ax", x: x(i), y: H - 6, "text-anchor": "middle" }, tick(times[i])));
  }
  if (marker !== null && marker >= 0 && marker < n) {
    s.append(svg("line", { class: "zero-l", x1: x(marker), x2: x(marker), y1: T, y2: H - B, "stroke-dasharray": "3 4" }));
  }
  series.forEach((ser, k) => {
    const cls = ser.cls || `c${k + 1}`;
    let d = "", pen = false;
    ser.values.forEach((v, i) => {
      if (v === null || v === undefined || Number.isNaN(v)) { pen = false; return; }
      if (ser.step && pen) d += `H${x(i).toFixed(1)}`;
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    if (ser.min && ser.max) {          // min/max band of each time bucket: short peaks stay visible
      let up = "", down = "";
      ser.max.forEach((v, i) => { if (v !== null && v !== undefined) up += `${up ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`; });
      for (let i = n - 1; i >= 0; i--) { const v = ser.min[i]; if (v !== null && v !== undefined) down += `L${x(i).toFixed(1)},${y(v).toFixed(1)}`; }
      if (up && down) s.append(svg("path", { class: `area ${cls}`, d: `${up}${down}Z`, stroke: "none", opacity: 0.25 }));
    }
    if (ser.area && d) {
      const base = y(Math.max(lo, Math.min(0, hi)));
      s.append(svg("path", { class: `area ${cls}`, d: `${d}V${base}H${x(0)}Z`, stroke: "none" }));
    }
    s.append(svg("path", { class: `line ${cls}`, d }));
  });
  const cross = svg("line", { class: "cross", x1: 0, x2: 0, y1: T, y2: H - B, visibility: "hidden" });
  s.append(cross);
  const tip = h("div", { class: "tip", hidden: true });
  const legend = series.length > 1 ? h("div", { class: "legend" },
    series.map((ser, k) => h("span", {}, h("i", { class: `sw ${ser.cls || `c${k + 1}`}` }), ser.name))) : null;
  if (legend) wrap.append(legend);
  wrap.append(s, tip);
  const move = (ev) => {
    const r = s.getBoundingClientRect();
    const sx = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) * W / r.width;
    if (sx < L || sx > W - R) { tip.hidden = true; cross.setAttribute("visibility", "hidden"); return; }
    const i = Math.max(0, Math.min(n - 1, Math.round(((sx - L) / pw) * (n - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    tip.replaceChildren(h("b", {}, dateTime(times[i])), ...series.map((ser, k) => h("div", {},
      h("i", { class: `sw ${ser.cls || `c${k + 1}`}` }), `${ser.name}: `,
      ser.values[i] === null || ser.values[i] === undefined ? "—" : `${num(ser.values[i], ser.decimals ?? decimals)} ${ser.unit || unit}`)));
    tip.hidden = false;
    const px = x(i) * r.width / W;
    tip.style.left = `${px > r.width / 2 ? px - tip.offsetWidth - 12 : px + 12}px`;
  };
  s.addEventListener("mousemove", move);
  s.addEventListener("touchmove", move, { passive: true });
  s.addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  return wrap;
}

/** Price bars: positive in slot-1 blue, negative in red; marker = index of "now". */
export function priceBars({ times, values, unit = "€/kWh", height = 200, marker = null, estimated = [] }) {
  const W = 900, H = height, L = 50, R = 14, T = 10, B = 26;
  const wrap = h("div", { class: "chart" });
  const vals = values.filter((v) => v !== null);
  if (!vals.length) { wrap.append(h("div", { class: "empty" }, "Geen prijsdata beschikbaar")); return wrap; }
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
  const lo = ticks[0], hi = ticks[ticks.length - 1], n = values.length, pw = W - L - R, ph = H - T - B;
  const bw = pw / n, y = (v) => T + ph * (1 - (v - lo) / (hi - lo));
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "prijzen" });
  for (const t of ticks) {
    s.append(svg("line", { class: Math.abs(t) < 1e-9 ? "zero-l" : "grid-l", x1: L, x2: W - R, y1: y(t), y2: y(t) }));
    s.append(svg("text", { class: "ax", x: L - 6, y: y(t) + 4, "text-anchor": "end" }, num(t, 2)));
  }
  values.forEach((v, i) => {
    if (v === null) return;
    const y0 = y(0), y1 = y(v);
    s.append(svg("rect", { class: v < 0 ? "bar-neg" : "bar-pos", x: L + i * bw + 0.5, width: Math.max(1, bw - 1),
      y: Math.min(y0, y1), height: Math.max(1, Math.abs(y1 - y0)), opacity: estimated[i] ? 0.35 : (marker !== null && i < marker ? 0.45 : 0.9),
      rx: Math.min(2, bw / 3) }));
  });
  const step = Math.max(1, Math.ceil(n / 8));
  for (let i = 0; i < n; i += step) s.append(svg("text", { class: "ax", x: L + i * bw + bw / 2, y: H - 6, "text-anchor": "middle" }, time(times[i])));
  if (marker !== null) s.append(svg("line", { class: "zero-l", x1: L + marker * bw, x2: L + marker * bw, y1: T, y2: H - B, "stroke-dasharray": "3 4" }));
  const tip = h("div", { class: "tip", hidden: true });
  wrap.append(s, tip);
  s.addEventListener("mousemove", (ev) => {
    const r = s.getBoundingClientRect(), sx = (ev.clientX - r.left) * W / r.width;
    const i = Math.floor((sx - L) / bw);
    if (i < 0 || i >= n) { tip.hidden = true; return; }
    tip.replaceChildren(h("b", {}, dateTime(times[i])), `${num(values[i], 3)} ${unit}`, estimated[i] ? " (schatting)" : "");
    tip.hidden = false;
    const px = (L + i * bw) * r.width / W;
    tip.style.left = `${px > r.width / 2 ? px - tip.offsetWidth - 12 : px + 12}px`;
  });
  s.addEventListener("mouseleave", () => { tip.hidden = true; });
  return wrap;
}

/**
 * Price line by status: solid = published (OFFICIAL_DAY_AHEAD), dashed = forecast/estimate (with the p10–p90
 * band as uncertainty), grey = stale; MISSING intervals are a gap with a grey strip on the axis.
 * priceLine({times, values, status, low, high, marker, unit})
 */
export function priceLine({ times, values, status, low = [], high = [], unit = "€/kWh", height = 220, marker = null, title = "prijzen" }) {
  const W = 900, H = height, L = 50, R = 14, T = 10, B = 26;
  const wrap = h("div", { class: "chart" });
  const vals = [...values, ...low, ...high].filter((v) => v !== null && v !== undefined);
  if (!vals.length) { wrap.append(h("div", { class: "empty" }, "Geen prijsdata beschikbaar")); return wrap; }
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals));
  const lo = ticks[0], hi = ticks[ticks.length - 1], n = values.length, pw = W - L - R, ph = H - T - B;
  const x = (i) => L + (n === 1 ? pw / 2 : (pw * i) / (n - 1));
  const y = (v) => T + ph * (1 - (v - lo) / (hi - lo));
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title });
  for (const t of ticks) {
    s.append(svg("line", { class: Math.abs(t) < 1e-9 ? "zero-l" : "grid-l", x1: L, x2: W - R, y1: y(t), y2: y(t) }));
    s.append(svg("text", { class: "ax", x: L - 6, y: y(t) + 4, "text-anchor": "end" }, num(t, 2)));
  }
  const kind = (st) => (st === "OFFICIAL_DAY_AHEAD" ? "official" : st === "STALE" ? "stale" : st === "MISSING" ? "missing" : "forecast");
  // Uncertainty band (forecast intervals only).
  let up = "", down = "";
  for (let i = 0; i < n; i++) if (high[i] !== null && high[i] !== undefined && kind(status[i]) === "forecast") up += `${up ? "L" : "M"}${x(i).toFixed(1)},${y(high[i]).toFixed(1)}`;
  for (let i = n - 1; i >= 0; i--) if (low[i] !== null && low[i] !== undefined && kind(status[i]) === "forecast") down += `L${x(i).toFixed(1)},${y(low[i]).toFixed(1)}`;
  if (up && down) s.append(svg("path", { class: "price-band", d: `${up}${down}Z` }));
  // Segments of equal kind; a segment starts at the last point of the previous one (no visual jumps).
  let i = 0;
  while (i < n) {
    const k = kind(status[i]);
    if (k === "missing" || values[i] === null || values[i] === undefined) {
      const j0 = i;
      while (i < n && (kind(status[i]) === "missing" || values[i] === null || values[i] === undefined)) i++;
      s.append(svg("rect", { class: "price-missing", x: x(j0), y: H - B - 4, width: Math.max(2, x(Math.min(n - 1, i)) - x(j0)), height: 4 }));
      continue;
    }
    let d = i > 0 && values[i - 1] !== null && values[i - 1] !== undefined ? `M${x(i - 1).toFixed(1)},${y(values[i - 1]).toFixed(1)}L` : "M";
    d += `${x(i).toFixed(1)},${y(values[i]).toFixed(1)}`;
    i++;
    while (i < n && kind(status[i]) === k && values[i] !== null && values[i] !== undefined) { d += `L${x(i).toFixed(1)},${y(values[i]).toFixed(1)}`; i++; }
    s.append(svg("path", { class: `price-line ${k}`, d }));
  }
  const step = Math.max(1, Math.ceil(n / 8));
  for (let t = 0; t < n; t += step) s.append(svg("text", { class: "ax", x: x(t), y: H - 6, "text-anchor": "middle" }, time(times[t])));
  if (marker !== null && marker >= 0) s.append(svg("line", { class: "zero-l", x1: x(marker), x2: x(marker), y1: T, y2: H - B, "stroke-dasharray": "3 4" }));
  const cross = svg("line", { class: "cross", x1: 0, x2: 0, y1: T, y2: H - B, visibility: "hidden" });
  s.append(cross);
  const LBL = { official: "beursprijs (gepubliceerd)", forecast: "prognose", stale: "verouderd", missing: "ontbreekt" };
  const legend = h("div", { class: "legend" }, ["official", "forecast", "stale", "missing"].filter((k) => status.some((st) => kind(st) === k))
    .map((k) => h("span", {}, h("i", { class: `sw price-${k}` }), LBL[k])));
  const tip = h("div", { class: "tip", hidden: true });
  wrap.append(legend, s, tip);
  s.addEventListener("mousemove", (ev) => {
    const r = s.getBoundingClientRect(), sx = (ev.clientX - r.left) * W / r.width;
    if (sx < L || sx > W - R) { tip.hidden = true; cross.setAttribute("visibility", "hidden"); return; }
    const k = Math.max(0, Math.min(n - 1, Math.round(((sx - L) / pw) * (n - 1))));
    cross.setAttribute("x1", x(k)); cross.setAttribute("x2", x(k)); cross.setAttribute("visibility", "visible");
    const band = kind(status[k]) === "forecast" && low[k] !== null && low[k] !== undefined ? ` (${num(low[k], 3)} – ${num(high[k], 3)})` : "";
    tip.replaceChildren(h("b", {}, dateTime(times[k])), values[k] === null || values[k] === undefined ? "—" : `${num(values[k], 3)} ${unit}${band}`,
      h("div", { class: "small" }, LBL[kind(status[k])]));
    tip.hidden = false;
    const px = x(k) * r.width / W;
    tip.style.left = `${px > r.width / 2 ? px - tip.offsetWidth - 12 : px + 12}px`;
  });
  s.addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  return wrap;
}
