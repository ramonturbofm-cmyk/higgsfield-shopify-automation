"""Self-contained HTML report of one or more simulation runs.

No external assets: inline CSS, inline SVG charts and a few lines of JS for
hover tooltips, so the file works offline and can be shared as-is.
Colours follow the validated categorical palette (light + dark steps).
"""

from __future__ import annotations

import html
import json
import math
from datetime import datetime, timedelta, tzinfo

from ems.simulator.runner import SimulationResult

W, H = 960, 230
PAD_L, PAD_R, PAD_T, PAD_B = 52, 120, 14, 28
MAX_POINTS = 700
DAYS_NL = ("ma", "di", "wo", "do", "vr", "za", "zo")

CONTROLLER_NAMES = {"self_consumption": "Met EMS", "native": "Zonder EMS"}


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi - lo < 1e-9:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, v = [start], start
    while v < hi - step * 1e-6:   # last tick must be >= hi so nothing overshoots the plot
        v += step
        ticks.append(round(v, 10))
    return ticks


def _fmt(v: float, decimals: int) -> str:
    return f"{v:.{decimals}f}".replace(".", ",")


def _chart(chart_id: str, title: str, unit: str, times: list[datetime], series: list[tuple[str, list]],
           decimals: int = 1, zero_line: bool = False) -> str:
    """One SVG line chart (single y-axis) + data for the hover layer."""
    vals = [v for _, s in series for v in s if v is not None]
    if not vals or len(times) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if zero_line:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    ticks = _nice_ticks(lo, hi)
    lo, hi = ticks[0], ticks[-1]
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    n = len(times)

    def x(i: int) -> float:
        return PAD_L + pw * i / (n - 1)

    def y(v: float) -> float:
        return PAD_T + ph * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{_esc(title)}" preserveAspectRatio="none">']
    for t in ticks:
        cls = "grid zero" if zero_line and abs(t) < 1e-9 else "grid"
        parts.append(f'<line class="{cls}" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        parts.append(f'<text class="axis" x="{PAD_L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">'
                     f'{_fmt(t, 2 if abs(hi - lo) < 1 else 0 if abs(hi - lo) >= 10 else 1)}</text>')
    # x ticks on local clock boundaries: 6 h, daily or weekly depending on span
    t0, span_s = times[0], (times[-1] - times[0]).total_seconds()
    every_h = 6 if span_s <= 3 * 86400 else 24 if span_s <= 14 * 86400 else 168
    tick = t0.replace(minute=0, second=0, microsecond=0)
    while tick < t0 or tick.hour % min(every_h, 24) or (every_h == 168 and tick.weekday()):
        tick += timedelta(hours=1)
    while tick <= times[-1]:
        tx = PAD_L + pw * (tick - t0).total_seconds() / span_s
        label = f"{tick:%d-%m}" if tick.hour == 0 else f"{tick:%H:%M}"
        parts.append(f'<line class="tick" x1="{tx:.1f}" x2="{tx:.1f}" y1="{PAD_T}" y2="{H - PAD_B}"/>')
        parts.append(f'<text class="axis" x="{tx:.1f}" y="{H - 8}" text-anchor="middle">{label}</text>')
        tick += timedelta(hours=every_h)
    labels = []
    for k, (name, data) in enumerate(series, start=1):
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(data) if v is not None)
        parts.append(f'<polyline class="line s{k}" points="{pts}"/>')
        last = next((v for v in reversed(data) if v is not None), None)
        if last is not None:
            labels.append([y(last), name, k])
    # Direct labels at the right edge, spread to avoid collisions.
    labels.sort()
    for j in range(1, len(labels)):
        labels[j][0] = max(labels[j][0], labels[j - 1][0] + 14)
    for ly, name, k in labels:
        parts.append(f'<line class="line s{k}" x1="{W - PAD_R + 8}" x2="{W - PAD_R + 20}" y1="{ly:.1f}" y2="{ly:.1f}"/>')
        parts.append(f'<text class="dlabel" x="{W - PAD_R + 24}" y="{ly + 4:.1f}">{_esc(name)}</text>')
    parts.append(f'<line class="cross" x1="0" x2="0" y1="{PAD_T}" y2="{H - PAD_B}" visibility="hidden"/>')
    parts.append("</svg>")

    payload = {
        "t": [f"{DAYS_NL[t.weekday()]} {t:%d-%m %H:%M}" for t in times],
        "s": [{"n": name, "v": [None if v is None else round(v, decimals) for v in data]} for name, data in series],
        "u": unit, "x0": PAD_L, "x1": W - PAD_R, "w": W, "d": decimals,
    }
    legend = "" if len(series) < 2 else '<div class="legend">' + "".join(
        f'<span><i class="sw s{k}"></i>{_esc(name)}</span>' for k, (name, _) in enumerate(series, start=1)) + "</div>"
    return (f'<figure class="chart" id="{chart_id}"><figcaption>{_esc(title)} <small>({_esc(unit)})</small></figcaption>'
            f'{legend}<div class="plot">{"".join(parts)}<div class="tip" hidden></div></div>'
            f'<script type="application/json">{json.dumps(payload)}</script></figure>')


def _series(rows: list[dict], key: str, scale: float = 1.0) -> list:
    return [None if r.get(key) is None else float(r[key]) * scale for r in rows]


def _kpis(results: list[SimulationResult]) -> str:
    main = results[0].summary
    tiles = [
        ("Netafname", f"{_fmt(main.import_kwh, 1)} kWh"),
        ("Teruglevering", f"{_fmt(main.export_kwh, 1)} kWh"),
        ("Zelfvoorzienend", "n.v.t." if main.self_sufficiency_pct is None else f"{_fmt(main.self_sufficiency_pct, 0)} %"),
        ("PV-opwek", f"{_fmt(main.pv_kwh, 1)} kWh"),
        ("Batterijcycli", _fmt(main.battery_equivalent_full_cycles, 2)),
        ("Max. fasestroom", f"{_fmt(main.max_phase_current_a, 1)} A"),
    ]
    return '<div class="kpis">' + "".join(
        f'<div class="kpi"><div class="kl">{_esc(k)}</div><div class="kv">{_esc(v)}</div></div>' for k, v in tiles) + "</div>"


_ROWS = [
    ("PV-opwek", "pv_kwh", "kWh", 1), ("Huisverbruik", "base_load_kwh", "kWh", 1),
    ("Warmtepomp", "heat_pump_kwh", "kWh", 1), ("Elektrische auto", "ev_kwh", "kWh", 1),
    ("Netafname", "import_kwh", "kWh", 1), ("Teruglevering", "export_kwh", "kWh", 1),
    ("Batterij geladen", "battery_charged_kwh", "kWh", 1), ("Batterij ontladen", "battery_discharged_kwh", "kWh", 1),
    ("Batterijcycli", "battery_equivalent_full_cycles", "", 2),
    ("Zelfconsumptie", "self_consumption_pct", "%", 1), ("Zelfvoorzienendheid", "self_sufficiency_pct", "%", 1),
    ("Max. fasestroom", "max_phase_current_a", "A", 1), ("Fase overbelast", "phase_overload_s", "s", 0),
    ("Comforttekort", "comfort_deficit_kh", "Kh", 2),
    ("Spotwaarde (indicatief)", "spot_value_eur", "EUR", 2),
    ("Commando's", "commands_sent", "", 0), ("Fail-safe", "failsafe_events", "", 0),
]


def _table(results: list[SimulationResult]) -> str:
    heads = [CONTROLLER_NAMES.get(r.summary.controller, r.summary.controller) for r in results]
    diff = len(results) == 2
    out = ['<table><thead><tr><th>Grootheid</th>']
    out += [f'<th class="num">{_esc(h)}</th>' for h in heads]
    out += ['<th class="num">Verschil</th>' if diff else "", "</tr></thead><tbody>"]
    for label, key, unit, dec in _ROWS:
        vals = [getattr(r.summary, key) for r in results]
        cells = "".join(f'<td class="num">{"–" if v is None else _esc(_fmt(float(v), dec))}</td>' for v in vals)
        d = ""
        if diff:
            d = "–" if None in vals else _fmt(float(vals[0]) - float(vals[1]), dec)
            d = f'<td class="num">{_esc(d if d.startswith(("-", "–")) else "+" + d)}</td>'
        out.append(f'<tr><td>{_esc(label)}{f" <small>({unit})</small>" if unit else ""}</td>{cells}{d}</tr>')
    out.append("</tbody></table>")
    note = ""
    if diff and abs(results[0].summary.ev_kwh - results[1].summary.ev_kwh) > 0.5:
        note = ('<p class="note">Let op: de auto is niet in beide gevallen even ver geladen, dus de vergelijking is '
                'niet gelijkwaardig. Een gewenste laadtoestand bij vertrek komt in een volgende fase.</p>')
    return "".join(out) + note


def _decisions(result: SimulationResult, tz: tzinfo, limit: int = 12) -> str:
    if result.journal is None:
        return ""
    entries = [e for e in result.journal.recent if e.outcome in ("sent", "dry_run", "released")][-limit:]
    if not entries:
        return ""
    items = []
    for e in reversed(entries):
        reasons = "".join(f"<li>{_esc(r)}</li>" for r in e.reasons)
        items.append(f'<li><time>{e.timestamp.astimezone(tz):%d-%m %H:%M}</time> <b>{_esc(e.summary)}</b>'
                     f'<ul>{reasons}</ul></li>')
    return f'<h2>Laatste EMS-beslissingen</h2><ol class="decisions">{"".join(items)}</ol>'


def render_report(results: list[SimulationResult], tz: tzinfo, title: str = "Simulatierapport",
                  csv_href: str | None = None) -> str:
    """HTML report; ``results[0]`` is the main run (charts), others are compared in the table."""
    main = results[0]
    rows = main.rows
    step = max(1, math.ceil(len(rows) / MAX_POINTS))
    rows = rows[::step]
    times = [datetime.fromisoformat(r["timestamp"]).astimezone(tz) for r in rows]
    consumption = [r["base_load_w"] + r["heat_pump_w"] + r["ev_w"] for r in rows]
    charts = [
        _chart("power", "Vermogen", "kW", times, [
            ("PV", _series(rows, "pv_w", 1e-3)),
            ("Verbruik", [c / 1000 for c in consumption]),
            ("Net (+afname)", _series(rows, "grid_w", 1e-3)),
            ("Batterij (+laden)", _series(rows, "battery_w", 1e-3)),
        ], decimals=2, zero_line=True),
        _chart("soc", "Batterij-laadtoestand", "%", times, [("SOC", _series(rows, "battery_soc_pct"))], decimals=0),
        _chart("price", "Day-ahead-prijs (synthetisch)", "EUR/kWh", times,
               [("Prijs", _series(rows, "spot_price_eur_kwh"))], decimals=3, zero_line=True),
        _chart("temp", "Binnentemperatuur", "°C", times, [("Binnen", _series(rows, "indoor_temp_c"))], decimals=1),
    ]
    s = main.summary
    start = datetime.fromisoformat(s.start).astimezone(tz)
    subtitle = f"{start:%d-%m-%Y} · {s.hours:.0f} uur · {CONTROLLER_NAMES.get(s.controller, s.controller)}"
    csv = f' · <a href="{_esc(csv_href)}">Tijdreeks downloaden (CSV)</a>' if csv_href else ""
    return f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><style>{CSS}</style></head>
<body><main class="viz-root">
<header><h1>{_esc(title)}</h1><p class="sub">{_esc(subtitle)}{csv}</p></header>
{_kpis(results)}
{"".join(c for c in charts if c)}
<h2>Overzicht</h2>{_table(results)}
{_decisions(main, tz)}
<p class="note">Gesimuleerde woning met synthetische weer- en prijsdata. Bedragen zijn indicatief
(zonder opslagen, energiebelasting en btw).</p>
</main><script>{JS}</script></body></html>"""


CSS = """
.viz-root{color-scheme:light;--surface:#fcfcfb;--panel:#ffffff;--border:#e4e3df;--text:#0b0b0b;--text2:#52514e;
--muted:#8a8984;--grid:#ecebe7;--zero:#b9b8b2;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface:#1a1a19;
--panel:#222221;--border:#383835;--text:#ffffff;--text2:#c3c2b7;--muted:#8f8e86;--grid:#2c2c2a;--zero:#5c5b57;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface:#1a1a19;--panel:#222221;--border:#383835;--text:#ffffff;
--text2:#c3c2b7;--muted:#8f8e86;--grid:#2c2c2a;--zero:#5c5b57;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500}
html,body{margin:0;background:#fcfcfb}@media (prefers-color-scheme:dark){html,body{background:#1a1a19}}
.viz-root{background:var(--surface);color:var(--text);font:15px/1.45 "Segoe UI",system-ui,sans-serif;
max-width:1040px;margin:0 auto;padding:24px 16px 48px;box-sizing:border-box;min-height:100vh}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 12px}.sub,.note{color:var(--text2);margin:0}
.note{font-size:13px;margin-top:12px}a{color:var(--s1)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:20px 0}
.kpi{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.kl{color:var(--text2);font-size:13px}.kv{font-size:21px;font-weight:600;margin-top:2px;font-variant-numeric:tabular-nums}
.chart{background:var(--panel);border:1px solid var(--border);border-radius:10px;margin:14px 0;padding:12px 14px 6px}
figcaption{font-weight:600}figcaption small{color:var(--muted);font-weight:400}
.legend{display:flex;flex-wrap:wrap;gap:14px;color:var(--text2);font-size:13px;margin:6px 0 2px}
.sw{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.plot{position:relative}svg{width:100%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--grid);stroke-width:1}.zero{stroke:var(--zero)}.tick{stroke:var(--grid);stroke-dasharray:2 4}
.axis{fill:var(--muted);font-size:11px}.dlabel{fill:var(--text2);font-size:12px}
.line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}
.s1{stroke:var(--s1);background:var(--s1)}.s2{stroke:var(--s2);background:var(--s2)}
.s3{stroke:var(--s3);background:var(--s3)}.s4{stroke:var(--s4);background:var(--s4)}
.cross{stroke:var(--muted);stroke-width:1}
.tip{position:absolute;top:4px;pointer-events:none;background:var(--panel);border:1px solid var(--border);border-radius:8px;
padding:6px 9px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);white-space:nowrap;color:var(--text)}
.tip b{display:block;color:var(--text2);font-weight:500;margin-bottom:2px}.tip i{display:inline-block;width:10px;height:3px;
border-radius:2px;margin-right:6px;vertical-align:middle}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--border);border-radius:10px;
overflow:hidden;font-variant-numeric:tabular-nums}th,td{padding:7px 12px;border-bottom:1px solid var(--border);text-align:left}
th{color:var(--text2);font-weight:600;font-size:13px}.num{text-align:right}td small{color:var(--muted)}
.decisions{padding-left:20px}.decisions>li{margin-bottom:10px}
.decisions time{color:var(--muted);font-variant-numeric:tabular-nums}
.decisions ul{margin:2px 0 0;padding-left:18px;color:var(--text2);font-size:14px}
@media (max-width:640px){.kv{font-size:18px}th,td{padding:6px 8px}}
"""

JS = """
document.querySelectorAll('.chart').forEach(fig=>{
 const d=JSON.parse(fig.querySelector('script').textContent),svg=fig.querySelector('svg'),
  tip=fig.querySelector('.tip'),cross=svg.querySelector('.cross'),n=d.t.length;
 const fmt=v=>v==null?'–':v.toFixed(d.d).replace('.',',');
 svg.addEventListener('mousemove',e=>{
  const r=svg.getBoundingClientRect(),sx=(e.clientX-r.left)*d.w/r.width;
  if(sx<d.x0||sx>d.x1){tip.hidden=true;cross.setAttribute('visibility','hidden');return}
  const i=Math.round((sx-d.x0)/(d.x1-d.x0)*(n-1)),cx=d.x0+(d.x1-d.x0)*i/(n-1);
  cross.setAttribute('x1',cx);cross.setAttribute('x2',cx);cross.setAttribute('visibility','visible');
  tip.innerHTML='<b>'+d.t[i]+'</b>'+d.s.map((s,k)=>
   '<div><i class="s'+(k+1)+'"></i>'+s.n+': '+fmt(s.v[i])+' '+d.u+'</div>').join('');
  tip.hidden=false;const px=cx*r.width/d.w;tip.style.left=(px>r.width/2?px-tip.offsetWidth-12:px+12)+'px';
 });
 svg.addEventListener('mouseleave',()=>{tip.hidden=true;cross.setAttribute('visibility','hidden')});
});
"""
