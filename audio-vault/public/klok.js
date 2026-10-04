'use strict';
// Hour clock editor: the ring shows how one hour is built, the list edits the
// blocks, and the week grid decides which clock runs when.

const BACKGROUNDS = {
  zwart: ['#000000', '#0e0e10', '#18181b', '#26262b', '#f2f2f2', '#8a8a92'],
  antraciet: ['#141518', '#1c1d21', '#25272c', '#33363c', '#f0f0f0', '#9a9ca3'],
  nachtblauw: ['#050a18', '#0b1326', '#121d36', '#1e2b4a', '#eef2ff', '#8d9abb'],
  licht: ['#e9e9ec', '#ffffff', '#f2f2f5', '#d6d6dc', '#141416', '#6b6b74'],
};
const TYPE_LABEL = { muziek: 'Muziek', jingle: 'Jingle', vast: 'Vast' };
const TYPE_COLOR = { muziek: '#2f7bff', jingle: '#ff3b30', vast: '#ffd60a' };
const DAYS = [[1, 'ma'], [2, 'di'], [3, 'wo'], [4, 'do'], [5, 'vr'], [6, 'za'], [0, 'zo']];
const HOUR = 3600;

// fixed: the files used by "Vast nummer" blocks — the full library is never loaded.
const K = { me: null, clocks: [], schedule: new Map(), collections: [], fixed: new Map(), selected: null, brush: null, highlight: null };
const $ = (id) => document.getElementById(id);

function h(tag, attrs = {}, ...children) {
  const el = tag.startsWith('svg:') ? document.createElementNS('http://www.w3.org/2000/svg', tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.setAttribute('class', v);
    else if (k === 'value') el.value = v;
    else if (k === 'selected' || k === 'disabled') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) el.append(c.nodeType ? c : String(c));
  return el;
}

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Fout ${res.status}`);
  return data;
}

const fmt = (sec) => `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}`;
const isAdmin = () => K.me && (K.me.role === 'owner' || K.me.role === 'admin');
function msg(text) { $('msg').textContent = text || ''; clearTimeout(msg.t); if (text) msg.t = setTimeout(() => ($('msg').textContent = ''), 4000); }

// Same length the studio uses: from cue-in to the mix point.
function fileLength(f) {
  if (f.mix_out != null) return f.mix_out - (f.cue_in || 0);
  return f.duration_seconds || 0;
}
function slotLength(slot) {
  if (slot.type === 'vast') {
    const f = K.fixed.get(slot.file_id);
    return f ? fileLength(f) : 0;
  }
  const c = K.collections.find((x) => x.id === slot.collection_id);
  return c && c.avg_length ? c.avg_length : 0;
}
const slotColor = (slot) => slot.color || TYPE_COLOR[slot.type];

function slotLabel(slot) {
  if (slot.type === 'vast') {
    const f = K.fixed.get(slot.file_id);
    return f ? (f.artist ? `${f.artist} – ${f.title}` : f.title) : 'Kies een nummer';
  }
  const c = K.collections.find((x) => x.id === slot.collection_id);
  return `${TYPE_LABEL[slot.type]} · ${c ? c.name : 'kies collectie'}`;
}

// ---------- saving ----------

let saveTimer;
function saveClockSoon() {
  const clock = current();
  if (!clock || !isAdmin()) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try { await api('PUT', `/api/clocks/${clock.id}`, clock); msg('Opgeslagen'); } catch (e) { msg(e.message); }
  }, 500);
}
let scheduleTimer;
function saveScheduleSoon() {
  clearTimeout(scheduleTimer);
  scheduleTimer = setTimeout(async () => {
    const cells = [...K.schedule].map(([key, clockId]) => { const [day, hour] = key.split(':').map(Number); return { day, hour, clock_id: clockId }; });
    try { await api('PUT', '/api/clock-schedule', { cells }); msg('Weekplanning opgeslagen'); } catch (e) { msg(e.message); }
  }, 500);
}

const current = () => K.clocks.find((c) => c.id === K.selected);

// ---------- rendering ----------

function renderClocks() {
  $('clocks').replaceChildren(...(K.clocks.length ? K.clocks.map((c) => h('button', {
    class: `item${c.id === K.selected ? ' active' : ''}`, onclick: () => { K.selected = c.id; render(); },
  }, h('span', { class: 'dot', style: `background:${c.color}` }), c.name)) : [h('p', { class: 'muted' }, 'Nog geen klokken.')]));
}

function arc(cx, cy, r1, r2, a0, a1) {
  const p = (r, a) => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const [x0, y0] = p(r2, a0); const [x1, y1] = p(r2, a1); const [x2, y2] = p(r1, a1); const [x3, y3] = p(r1, a0);
  return `M${x0} ${y0} A${r2} ${r2} 0 ${large} 1 ${x1} ${y1} L${x2} ${y2} A${r1} ${r1} 0 ${large} 0 ${x3} ${y3} Z`;
}

function renderRing() {
  const clock = current();
  const svg = h('svg:svg', { viewBox: '0 0 400 400' });
  const cx = 200; const cy = 200;
  svg.append(h('svg:circle', { cx, cy, r: 160, fill: 'none', stroke: 'var(--panel2)', 'stroke-width': 70 }));
  const total = clock ? clock.slots.reduce((t, s) => t + slotLength(s), 0) : 0;
  const scale = Math.max(HOUR, total);
  let at = 0;
  (clock ? clock.slots : []).forEach((slot, i) => {
    const len = slotLength(slot) || 20;
    const a0 = (at / scale) * 2 * Math.PI; const a1 = ((at + len) / scale) * 2 * Math.PI;
    const path = h('svg:path', {
      d: arc(cx, cy, 125, 195, a0, Math.max(a0 + 0.004, a1 - 0.006)), fill: slotColor(slot),
      opacity: K.highlight === null || K.highlight === i ? 1 : 0.35, style: 'cursor:pointer;transition:opacity .15s',
      onmouseenter: () => { K.highlight = i; renderRing(); highlightRow(); },
      onmouseleave: () => { K.highlight = null; renderRing(); highlightRow(); },
    });
    path.append(h('svg:title', {}, `${i + 1}. ${slotLabel(slot)} (${fmt(len)}) start ±${fmt(at)}`));
    svg.append(path);
    at += len;
  });
  for (let m = 0; m < 60; m += 5) {
    const a = (m / 60) * 2 * Math.PI * (HOUR / scale);
    const major = m % 15 === 0;
    svg.append(h('svg:line', {
      x1: cx + 112 * Math.sin(a), y1: cy - 112 * Math.cos(a), x2: cx + (major ? 98 : 105) * Math.sin(a), y2: cy - (major ? 98 : 105) * Math.cos(a),
      stroke: 'var(--muted)', 'stroke-width': major ? 2 : 1,
    }));
    if (major) {
      svg.append(h('svg:text', { x: cx + 84 * Math.sin(a), y: cy - 84 * Math.cos(a) + 4, 'text-anchor': 'middle', fill: 'var(--muted)', 'font-size': 12, 'font-family': 'ui-monospace, Consolas, monospace' }, `:${String(m).padStart(2, '0')}`));
    }
  }
  svg.append(h('svg:text', { x: cx, y: cy - 6, 'text-anchor': 'middle', fill: 'var(--text)', 'font-size': 20, 'font-weight': 700 }, clock ? clock.name : '—'));
  svg.append(h('svg:text', { x: cx, y: cy + 20, 'text-anchor': 'middle', fill: total > HOUR ? 'var(--warn)' : 'var(--muted)', 'font-size': 14, 'font-family': 'ui-monospace, Consolas, monospace' },
    clock ? `${fmt(total)} / 60:00` : ''));
  $('ring').replaceChildren(svg);
  $('total').textContent = clock ? `${clock.slots.length} blokken · ${total > HOUR ? `${fmt(total - HOUR)} te lang` : `nog ${fmt(HOUR - total)} vrij`}` : '';
  $('total').className = total > HOUR ? 'over' : 'muted';
}

function highlightRow() {
  document.querySelectorAll('.slot-row').forEach((el, i) => el.classList.toggle('hl', i === K.highlight));
}

function renderSlots() {
  const clock = current();
  if (!clock) { $('slots').replaceChildren(h('p', { class: 'muted' }, isAdmin() ? 'Maak links een klok aan.' : 'Geen klok gekozen.')); return; }
  const admin = isAdmin();
  let at = 0;
  $('slots').replaceChildren(...clock.slots.map((slot, i) => {
    const start = at; const len = slotLength(slot); at += len;
    const change = () => { renderRing(); renderSlots(); saveClockSoon(); };
    const color = h('input', { type: 'color', value: slotColor(slot), disabled: !admin });
    color.addEventListener('input', () => { slot.color = color.value; renderRing(); saveClockSoon(); });
    const type = h('select', { disabled: !admin }, Object.entries(TYPE_LABEL).map(([v, l]) => h('option', { value: v, selected: slot.type === v }, l)));
    type.addEventListener('change', () => {
      slot.type = type.value; delete slot.color;
      if (slot.type === 'vast') { delete slot.collection_id; slot.file_id = null; } else { delete slot.file_id; slot.collection_id = slot.collection_id || (K.collections[0] && K.collections[0].id); }
      change();
    });
    let source;
    if (slot.type === 'vast') {
      source = fileSearch(slot, admin, change);
    } else {
      source = h('select', { disabled: !admin }, h('option', { value: '' }, 'Kies collectie…'),
        K.collections.map((c) => h('option', { value: c.id, selected: c.id === slot.collection_id }, `${c.name} (${c.file_count})`)));
      source.addEventListener('change', () => { slot.collection_id = Number(source.value) || null; change(); });
    }
    const move = (d) => { const j = i + d; if (j < 0 || j >= clock.slots.length) return; [clock.slots[i], clock.slots[j]] = [clock.slots[j], clock.slots[i]]; change(); };
    return h('div', {
      class: `slot-row${K.highlight === i ? ' hl' : ''}`,
      onmouseenter: () => { K.highlight = i; renderRing(); },
      onmouseleave: () => { K.highlight = null; renderRing(); },
    },
    h('span', { class: 'num' }, `:${String(Math.floor(start / 60)).padStart(2, '0')}`),
    color, type, source,
    h('span', { class: 'len' }, len ? fmt(len) : '–'),
    h('span', { class: 'acts' }, admin ? [
      h('button', { title: 'Omhoog', onclick: () => move(-1) }, '↑'),
      h('button', { title: 'Omlaag', onclick: () => move(1) }, '↓'),
      h('button', { title: 'Verwijderen', onclick: () => { clock.slots.splice(i, 1); change(); } }, '✕')] : null));
  }));
}

// Search box for a "Vast nummer" block: searches the database on the server.
function fileSearch(slot, admin, change) {
  const chosen = K.fixed.get(slot.file_id);
  const input = h('input', { type: 'search', disabled: !admin, placeholder: 'Zoek een nummer…', value: chosen ? slotLabel(slot) : '' });
  const results = h('div', { class: 'search-results hidden' });
  let timer;
  input.addEventListener('focus', () => input.select());
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (!q) { results.classList.add('hidden'); return; }
      const { files, total } = await api('GET', `/api/files?limit=12&sort=name&q=${encodeURIComponent(q)}`);
      results.replaceChildren(...files.map((f) => h('button', {
        type: 'button', onmousedown: (e) => {
          e.preventDefault();
          K.fixed.set(f.id, f); slot.file_id = f.id; results.classList.add('hidden'); change();
        },
      }, h('b', {}, f.title), f.artist ? ` – ${f.artist}` : '')),
      files.length ? (total > files.length ? h('div', { class: 'more' }, `${total - files.length} meer — typ verder om te verfijnen`) : '') : h('div', { class: 'more' }, 'Niets gevonden'));
      results.classList.remove('hidden');
    }, 200);
  });
  input.addEventListener('blur', () => setTimeout(() => { results.classList.add('hidden'); input.value = K.fixed.get(slot.file_id) ? slotLabel(slot) : ''; }, 150));
  return h('div', { class: 'search-wrap' }, input, results);
}

function renderMeta() {
  const clock = current();
  $('clock-name').value = clock ? clock.name : '';
  $('clock-color').value = clock ? clock.color : '#2f7bff';
}

function renderBrush() {
  $('brush').replaceChildren(...[null, ...K.clocks].map((c) => h('button', {
    class: `b${K.brush === (c ? c.id : null) ? ' sel' : ''}`, onclick: () => { K.brush = c ? c.id : null; renderBrush(); },
  }, h('span', { class: 'dot', style: `background:${c ? c.color : 'transparent'};border:1px solid var(--line)` }), c ? c.name : 'Leeg')),
  h('button', { class: 'b', onclick: () => {
    if (!current() || !confirm(`Alle 168 uren van de week vullen met "${current().name}"?`)) return;
    for (const [d] of DAYS) for (let hr = 0; hr < 24; hr++) K.schedule.set(`${d}:${hr}`, current().id);
    renderGrid(); saveScheduleSoon();
  } }, 'Hele week = gekozen klok'));
}

let painting = false;
document.addEventListener('mouseup', () => { painting = false; });
function renderGrid() {
  const now = new Date();
  const cells = [h('div'), ...Array.from({ length: 24 }, (_, hr) => h('div', { class: 'h' }, String(hr).padStart(2, '0')))];
  for (const [d, name] of DAYS) {
    cells.push(h('div', { class: 'd' }, name));
    for (let hr = 0; hr < 24; hr++) {
      const key = `${d}:${hr}`;
      const clock = K.clocks.find((c) => c.id === K.schedule.get(key));
      const paint = () => {
        if (!isAdmin()) return;
        if (K.brush === null) K.schedule.delete(key); else K.schedule.set(key, K.brush);
        renderGrid(); saveScheduleSoon();
      };
      cells.push(h('div', {
        class: `cell${now.getDay() === d && now.getHours() === hr ? ' now' : ''}`,
        style: clock ? `background:${clock.color};border-color:${clock.color}` : '',
        title: `${name} ${String(hr).padStart(2, '0')}:00 – ${clock ? clock.name : 'geen klok'}`,
        onmousedown: (e) => { e.preventDefault(); painting = true; paint(); },
        onmouseenter: () => { if (painting) paint(); },
        onclick: () => { if (!isAdmin() && clock) { K.selected = clock.id; render(); } },
      }, clock ? clock.name.slice(0, 3) : ''));
    }
  }
  $('grid').replaceChildren(...cells);
}

function render() {
  renderClocks(); renderRing(); renderSlots(); renderMeta(); renderBrush(); renderGrid();
}

function tickClock() {
  const now = new Date();
  $('clock-time').textContent = now.toLocaleTimeString('nl-NL');
  $('clock-date').textContent = now.toLocaleDateString('nl-NL', { weekday: 'long', day: 'numeric', month: 'long' });
}

async function boot() {
  try { K.me = (await api('GET', '/api/me')).user; } catch { location.href = '/'; return; }
  const [{ settings }, { clocks, schedule }, { collections }] = await Promise.all([
    api('GET', '/api/me/settings'), api('GET', '/api/clocks'), api('GET', '/api/collections'),
  ]);
  const fixedIds = [...new Set(clocks.flatMap((c) => c.slots).filter((s) => s.type === 'vast' && s.file_id).map((s) => s.file_id))];
  if (fixedIds.length) {
    const { files } = await api('GET', `/api/files?limit=1000&ids=${fixedIds.slice(0, 1000).join(',')}`);
    for (const f of files) K.fixed.set(f.id, f);
  }
  const bg = BACKGROUNDS[settings.background] || BACKGROUNDS.zwart;
  ['bg', 'panel', 'panel2', 'line', 'text', 'muted'].forEach((k, i) => document.documentElement.style.setProperty(`--${k}`, bg[i]));
  if (settings.accent) document.documentElement.style.setProperty('--accent', settings.accent);
  $('station-name').textContent = settings.stationName || '';
  Object.assign(K, { clocks, collections });
  K.schedule = new Map(schedule.map((s) => [`${s.day}:${s.hour}`, s.clock_id]));
  const nowClock = K.schedule.get(`${new Date().getDay()}:${new Date().getHours()}`);
  K.selected = nowClock || (clocks[0] && clocks[0].id) || null;
  K.brush = K.selected;
  if (!isAdmin()) document.body.classList.add('readonly');

  $('btn-new').addEventListener('click', async () => {
    const name = prompt('Naam van de nieuwe klok', `Klok ${K.clocks.length + 1}`);
    if (!name) return;
    const colors = ['#2f7bff', '#30d158', '#ff8a00', '#b06cff', '#32d2ff', '#ff4fa3', '#ffd60a'];
    try {
      const { clock } = await api('POST', '/api/clocks', { name, color: colors[K.clocks.length % colors.length], slots: [] });
      K.clocks.push(clock); K.selected = clock.id; K.brush = clock.id; render();
    } catch (e) { alert(e.message); }
  });
  $('btn-delete').addEventListener('click', async () => {
    const clock = current();
    if (!clock || !confirm(`Klok "${clock.name}" verwijderen? De uren in de weekplanning worden leeg.`)) return;
    await api('DELETE', `/api/clocks/${clock.id}`);
    K.clocks = K.clocks.filter((c) => c !== clock);
    for (const [k, v] of K.schedule) if (v === clock.id) K.schedule.delete(k);
    K.selected = K.clocks[0] ? K.clocks[0].id : null; render();
  });
  $('clock-name').addEventListener('change', (e) => { const c = current(); if (c && e.target.value.trim()) { c.name = e.target.value.trim(); render(); saveClockSoon(); } });
  $('clock-color').addEventListener('input', (e) => { const c = current(); if (c) { c.color = e.target.value; renderClocks(); renderBrush(); renderGrid(); saveClockSoon(); } });
  document.querySelectorAll('[data-add]').forEach((b) => b.addEventListener('click', () => {
    const clock = current();
    if (!clock) return;
    const type = b.dataset.add;
    const guess = K.collections.find((c) => (type === 'jingle' ? /jingle|id|sweep/i : /muziek|music|hits/i).test(c.name)) || K.collections[0];
    clock.slots.push(type === 'vast' ? { type, file_id: null } : { type, collection_id: guess ? guess.id : null });
    render(); saveClockSoon();
  }));

  render();
  tickClock();
  setInterval(tickClock, 1000);
}

boot();
