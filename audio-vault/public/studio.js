'use strict';
// Audio OnAir Turbo — browser playout in the spirit of mAirList.
// Two players (A/B) alternate: while one is on air the other already holds the next
// item, fully buffered and parked at its cue-in point, so it starts without a gap.

const BACKGROUNDS = {
  zwart: { label: 'Zwart', bg: '#000000', panel: '#0e0e10', panel2: '#18181b', line: '#26262b', text: '#f2f2f2', muted: '#8a8a92' },
  antraciet: { label: 'Antraciet', bg: '#141518', panel: '#1c1d21', panel2: '#25272c', line: '#33363c', text: '#f0f0f0', muted: '#9a9ca3' },
  nachtblauw: { label: 'Nachtblauw', bg: '#050a18', panel: '#0b1326', panel2: '#121d36', line: '#1e2b4a', text: '#eef2ff', muted: '#8d9abb' },
  licht: { label: 'Licht', bg: '#e9e9ec', panel: '#ffffff', panel2: '#f2f2f5', line: '#d6d6dc', text: '#141416', muted: '#6b6b74' },
};
const ACCENTS = ['#ff3b30', '#ff8a00', '#ffd60a', '#30d158', '#32d2ff', '#2f7bff', '#b06cff', '#ff4fa3'];
const SLOT_COLORS = ['#c0392b', '#d35400', '#b7950b', '#1e8449', '#117a65', '#1f618d', '#6c3483', '#a93279', '#2c3e50', '#555555'];
const OUTPUTS = [['A', 'Player A'], ['B', 'Player B'], ['cart', 'Jingle paneel'], ['pfl', 'Voorbeluisteren (PFL)']];
const CART_PAGES = 4;

const DEFAULT_SETTINGS = {
  stationName: '',
  background: 'zwart',
  accent: '#ff3b30',
  crossfade: 1,
  fadeOut: 3,
  autoCue: true,
  auto: true,
  playlist: [],
  cartSize: '4x4',
  cartPage: 0,
  cart: [],
  clockAuto: false,
  quality: 'auto',
  plannedUntil: null,
};

const S = {
  me: null,
  settings: { ...DEFAULT_SETTINGS },
  outputs: readLocal('aot_outputs', {}),
  files: new Map(),
  collections: [],
  playlist: [], // { uid, id, stopAfter, state: queued|playing|played, error }
  live: null, // deck that is on air
  pfl: { audio: new Audio(), fileId: null },
  cartPlayers: new Map(), // slotIndex -> { audio, fileId }
};

const $ = (id) => document.getElementById(id);
let uidCounter = 0;
const newUid = () => `i${Date.now().toString(36)}${(uidCounter++).toString(36)}`;

function readLocal(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch { return fallback; }
}
function writeLocal(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') for (const [sk, sv] of Object.entries(v)) el.style.setProperty(sk.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`), sv);
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
  if (!res.ok) throw Object.assign(new Error(data.error || `Fout ${res.status}`), { status: res.status });
  return data;
}

function fmt(sec, { sign = '' } = {}) {
  if (sec === null || sec === undefined || !Number.isFinite(sec)) return '–:––';
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  return `${sign}${m}:${String(s % 60).padStart(2, '0')}`;
}
const fmtClock = (d) => d.toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const label = (f) => (f.artist ? `${f.artist} – ${f.title}` : f.title);

function status(msg) {
  $('status-msg').textContent = msg || '';
  if (msg) { clearTimeout(status.t); status.t = setTimeout(() => ($('status-msg').textContent = ''), 6000); }
}

// ---------- cue points ----------

const cueIn = (f) => (f.cue_in ?? 0);
const cueOut = (f) => (f.cue_out ?? f.duration_seconds ?? null);
function mixPoint(f) {
  if (f.mix_out !== null && f.mix_out !== undefined) return f.mix_out;
  const end = cueOut(f);
  return end === null ? null : Math.max(cueIn(f), end - S.settings.crossfade);
}
const playLength = (f) => {
  const mix = mixPoint(f);
  return mix === null ? null : mix - cueIn(f);
};

// Find leading/trailing silence and the point where the ending gets quiet enough
// for the next item to take over — the same idea as mAirList's auto-cue.
async function analyse(file, blob) {
  if (blob.size > 120e6) return;
  const ctx = new OfflineAudioContext(1, 1, 22050);
  const buffer = await ctx.decodeAudioData(await blob.arrayBuffer());
  const rate = buffer.sampleRate;
  const block = Math.round(rate * 0.01);
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, i) => buffer.getChannelData(i));
  const n = Math.floor(buffer.length / block);
  const peaks = new Float32Array(n);
  let top = 0;
  for (let b = 0; b < n; b++) {
    let m = 0;
    for (const c of channels) {
      for (let i = b * block, e = i + block; i < e; i++) { const v = c[i] < 0 ? -c[i] : c[i]; if (v > m) m = v; }
    }
    peaks[b] = m;
    if (m > top) top = m;
  }
  if (!top) return;
  const silence = Math.min(10 ** (-48 / 20), top * 10 ** (-42 / 20));
  const quiet = top * 10 ** (-18 / 20);
  let first = 0; while (first < n && peaks[first] < silence) first++;
  let last = n - 1; while (last > first && peaks[last] < silence) last--;
  let loud = last; while (loud > first && peaks[loud] < quiet) loud--;
  const cues = { cue_in: first / 100, cue_out: (last + 1) / 100, mix_out: (loud + 1) / 100 };
  cues.mix_out = Math.min(cues.cue_out, Math.max(cues.mix_out, cues.cue_out - 10, cues.cue_in));
  for (const k of Object.keys(cues)) cues[k] = Math.round(cues[k] * 1000) / 1000;
  Object.assign(file, cues);
  api('PUT', `/api/files/${file.id}/cues`, cues).catch(() => {});
}

// ---------- audio loading (fully buffered in memory so playback never stalls) ----------

const blobCache = new Map(); // fileId -> Promise<objectURL>
// Zuinige modus: MP3 320 instead of the lossless original, ± 3x less data. "auto"
// uses the original inside your own network and the small version over the internet.
function isLocalNetwork() {
  const h = location.hostname;
  return h === 'localhost' || h.endsWith('.local') || /^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h) || h === '[::1]';
}
function useSmall() {
  return S.settings.quality === 'zuinig' || (S.settings.quality !== 'original' && !isLocalNetwork());
}
function renderQuality() {
  $('status-quality').textContent = useSmall() ? 'Kwaliteit: zuinig (MP3 320)' : 'Kwaliteit: origineel (verliesvrij)';
}

function fileUrl(file) {
  if (!blobCache.has(file.id)) {
    const p = fetch(`/api/files/${file.id}/stream${useSmall() ? '?format=mp3' : ''}`)
      .then((r) => { if (!r.ok) throw new Error(`Kan "${file.title}" niet laden (${r.status})`); return r.blob(); })
      .then(async (blob) => {
        if (S.settings.autoCue && file.cue_out === null) await analyse(file, blob).catch(() => {});
        return URL.createObjectURL(blob);
      });
    p.catch(() => blobCache.delete(file.id));
    blobCache.set(file.id, p);
    trimCache();
  }
  return blobCache.get(file.id);
}

function trimCache() {
  const keep = new Set([...decks.map((d) => d.item && d.item.id), S.pfl.fileId, ...cartFileIds()]);
  const excess = blobCache.size - 24;
  if (excess <= 0) return;
  let removed = 0;
  for (const [id, p] of blobCache) {
    if (removed >= excess) break;
    if (keep.has(id)) continue;
    blobCache.delete(id);
    p.then((url) => URL.revokeObjectURL(url)).catch(() => {});
    removed++;
  }
}

function applySink(audio, key) {
  const id = S.outputs[key];
  if (typeof audio.setSinkId !== 'function') return;
  audio.setSinkId(id || '').catch((e) => status(`Geluidskaart voor ${key} niet beschikbaar: ${e.message}`));
}

const once = (el, ev) => new Promise((resolve, reject) => {
  const ok = () => { el.removeEventListener('error', bad); resolve(); };
  const bad = () => { el.removeEventListener(ev, ok); reject(new Error('Audio kan niet worden afgespeeld')); };
  el.addEventListener(ev, ok, { once: true });
  el.addEventListener('error', bad, { once: true });
});

// ---------- players ----------

function makeDeck(index) {
  const audio = new Audio();
  audio.preload = 'auto';
  return { index, name: index ? 'B' : 'A', audio, item: null, file: null, state: 'empty', fade: null, mixed: false, noAdvance: false, token: 0 };
}
const decks = [makeDeck(0), makeDeck(1)];

function nextQueued() {
  return S.playlist.find((i) => i.state === 'queued' && !i.error && !i.marker);
}

async function loadDeck(deck, item) {
  const token = ++deck.token;
  deck.item = item; deck.file = S.files.get(item.id); deck.state = 'loading'; deck.mixed = false; deck.noAdvance = false; deck.fade = null;
  render();
  try {
    if (!deck.file) throw new Error('Bestand bestaat niet meer');
    const url = await fileUrl(deck.file);
    if (token !== deck.token) return;
    applySink(deck.audio, deck.name);
    if (deck.audio.src !== url) {
      deck.audio.src = url;
      await once(deck.audio, 'loadedmetadata');
    }
    if (token !== deck.token) return;
    if (deck.file.duration_seconds === null) deck.file.duration_seconds = deck.audio.duration;
    deck.audio.currentTime = cueIn(deck.file);
    deck.audio.volume = 1;
    deck.state = 'cued';
    // Auto mode with nothing on air (e.g. the previous item ended before this one was ready).
    if (deck.pendingStart && !S.live) startDeck(deck);
  } catch (e) {
    if (token !== deck.token) return;
    item.error = true; deck.item = null; deck.file = null; deck.state = 'empty';
    status(e.message);
    cueNext();
  } finally {
    deck.pendingStart = false;
    render();
  }
}

function unloadDeck(deck) {
  deck.token++;
  deck.audio.pause();
  deck.item = null; deck.file = null; deck.state = 'empty'; deck.fade = null; deck.mixed = false;
}

// Make sure an idle player holds the next queued item (and only that one).
function cueNext({ startWhenReady = false } = {}) {
  const target = nextQueued();
  const free = decks.filter((d) => d !== S.live && d.state !== 'playing' && d.state !== 'paused');
  if (!free.length) return;
  const holding = target && free.find((d) => d.item === target && (d.state === 'cued' || d.state === 'loading'));
  for (const d of free) if (d !== holding && (d.state === 'cued' || d.state === 'loading')) unloadDeck(d);
  if (!target) { render(); return; }
  if (holding) {
    if (startWhenReady) { if (holding.state === 'cued') startDeck(holding); else holding.pendingStart = true; }
    return;
  }
  const deck = free[0];
  deck.pendingStart = startWhenReady;
  loadDeck(deck, target);
}

function startDeck(deck) {
  if (deck.state !== 'cued') return;
  for (const i of S.playlist) if (i.state === 'playing' && i !== deck.item) i.state = 'played';
  // Hour markers above the item that starts now have been passed.
  const at = S.playlist.indexOf(deck.item);
  S.playlist.forEach((i, k) => { if (i.marker && k < at) i.state = 'played'; });
  deck.item.state = 'playing';
  deck.state = 'playing';
  deck.mixed = false;
  deck.audio.volume = 1;
  deck.audio.play().catch((e) => status(`Afspelen geblokkeerd: ${e.message}`));
  S.live = deck;
  api('POST', '/api/now-playing', { file_id: deck.file.id }).catch(() => {});
  cueNext();
  savePlaylistSoon();
  render();
}

function fadeDeck(deck, seconds, { stop = true } = {}) {
  deck.fade = { start: performance.now(), dur: Math.max(0.05, seconds) * 1000, from: deck.audio.volume, stop };
}

function finishDeck(deck, { advance }) {
  const wasLive = deck === S.live;
  const item = deck.item;
  if (item && item.state === 'playing') item.state = 'played';
  unloadDeck(deck);
  if (wasLive) {
    S.live = null;
    const next = decks.find((d) => d.state === 'playing');
    if (next) S.live = next;
    else if (advance && S.settings.auto && !(item && item.stopAfter) && nextQueued()) cueNext({ startWhenReady: true });
    else api('POST', '/api/now-playing', { file_id: null }).catch(() => {});
  }
  cueNext();
  savePlaylistSoon();
  render();
}

// START: nothing on air → start the cued item. Something on air → take over with a short fade.
function cmdStart() {
  const cued = decks.find((d) => d !== S.live && d.state === 'cued');
  if (!cued) { cueNext({ startWhenReady: true }); return; }
  const prev = S.live && S.live.state === 'playing' ? S.live : null;
  startDeck(cued);
  if (prev) { prev.noAdvance = true; fadeDeck(prev, Math.max(0.3, S.settings.crossfade)); }
}
function cmdPause() {
  const d = S.live;
  if (!d) return;
  if (d.state === 'playing') { d.audio.pause(); d.state = 'paused'; }
  else if (d.state === 'paused') { d.audio.play(); d.state = 'playing'; }
  render();
}
function cmdStop() {
  for (const d of decks) if (d.state === 'playing' || d.state === 'paused') finishDeck(d, { advance: false });
}
function cmdFade() {
  if (!S.live || S.live.state !== 'playing') return;
  S.live.noAdvance = true;
  fadeDeck(S.live, S.settings.fadeOut);
}
function cmdAuto() {
  S.settings.auto = !S.settings.auto;
  saveSettingsSoon();
  render();
}

// Runs every ~20 ms from a worker so it keeps ticking when the tab is in the background.
function tick() {
  const now = performance.now();
  for (const d of decks) {
    if (d.state !== 'playing') continue;
    const f = d.file;
    const t = d.audio.currentTime;
    if (d.fade) {
      const p = Math.min(1, (now - d.fade.start) / d.fade.dur);
      d.audio.volume = Math.max(0, d.fade.from * (1 - p) * (1 - p));
      if (p >= 1 && d.fade.stop) { finishDeck(d, { advance: !d.noAdvance }); continue; }
    }
    const end = cueOut(f) ?? d.audio.duration;
    if (d.audio.ended || (Number.isFinite(end) && t >= end - 0.01)) { finishDeck(d, { advance: !d.noAdvance }); continue; }
    if (d === S.live && S.settings.auto && !d.mixed && !d.noAdvance && !(d.item && d.item.stopAfter)) {
      const mix = mixPoint(f);
      const next = decks.find((x) => x !== d && x.state === 'cued');
      if (next && mix !== null && t >= mix) {
        d.mixed = true;
        // With analysed cue points the tail is already quiet: let it ring out. Without, fade it.
        if (f.mix_out === null || f.mix_out === undefined) fadeDeck(d, Math.max(0.1, end - t));
        startDeck(next);
      }
    }
  }
  tickCart();
  if (now - lastPaint > 90) { lastPaint = now; renderTimes(); }
}
let lastPaint = 0;

const worker = new Worker(URL.createObjectURL(new Blob(['setInterval(() => postMessage(0), 20);'], { type: 'text/javascript' })));
worker.onmessage = tick;

// ---------- jingle panel ----------

function cartDims() {
  const [cols, rows] = S.settings.cartSize.split('x').map(Number);
  return { cols, rows, count: cols * rows };
}
function cartSlots() {
  const { count } = cartDims();
  const all = S.settings.cart;
  while (all.length < CART_PAGES * count) all.push(null);
  return all;
}
const cartIndex = (i) => S.settings.cartPage * cartDims().count + i;
const cartFileIds = () => cartSlots().filter(Boolean).map((s) => s.id);

async function cartTrigger(index) {
  const slot = cartSlots()[index];
  if (!slot) return;
  const running = S.cartPlayers.get(index);
  if (running) { stopCart(index); return; }
  const file = S.files.get(slot.id);
  if (!file) { status('Dit jingle-bestand bestaat niet meer'); return; }
  const audio = new Audio();
  const player = { audio, file, loading: true };
  S.cartPlayers.set(index, player);
  renderCart();
  try {
    audio.src = await fileUrl(file);
    applySink(audio, 'cart');
    await once(audio, 'loadedmetadata');
    if (S.cartPlayers.get(index) !== player) return;
    audio.currentTime = cueIn(file);
    player.loading = false;
    await audio.play();
  } catch (e) {
    S.cartPlayers.delete(index);
    status(e.message);
  }
  renderCart();
}
function stopCart(index) {
  const p = S.cartPlayers.get(index);
  if (p) { p.audio.pause(); S.cartPlayers.delete(index); }
  renderCart();
}
function tickCart() {
  for (const [index, p] of S.cartPlayers) {
    if (p.loading) continue;
    const end = cueOut(p.file) ?? p.audio.duration;
    if (p.audio.ended || p.audio.currentTime >= end - 0.01) stopCart(index);
  }
}
function preloadCartPage() {
  const { count } = cartDims();
  for (let i = 0; i < count; i++) {
    const slot = cartSlots()[cartIndex(i)];
    const file = slot && S.files.get(slot.id);
    if (file) fileUrl(file).catch(() => {});
  }
}

// ---------- PFL (pre-listen on its own output) ----------

async function togglePfl(file) {
  const p = S.pfl;
  if (p.fileId === file.id) { p.audio.pause(); p.fileId = null; updatePflButtons(); return; }
  p.fileId = file.id;
  updatePflButtons();
  try {
    p.audio.src = await fileUrl(file);
    applySink(p.audio, 'pfl');
    await once(p.audio, 'loadedmetadata');
    p.audio.currentTime = cueIn(file);
    await p.audio.play();
  } catch (e) { status(e.message); p.fileId = null; updatePflButtons(); }
}
S.pfl.audio.addEventListener('ended', () => { S.pfl.fileId = null; updatePflButtons(); });

// ---------- playlist editing ----------

function addToPlaylist(id, beforeUid) {
  const item = { uid: newUid(), id, stopAfter: false, state: 'queued' };
  const at = beforeUid ? S.playlist.findIndex((i) => i.uid === beforeUid) : -1;
  if (at >= 0) S.playlist.splice(at, 0, item); else S.playlist.push(item);
  afterPlaylistChange();
}
// Insert right after the item that is on air (or before the first waiting item).
function addNext(id) {
  const item = { uid: newUid(), id, stopAfter: false, state: 'queued' };
  const playing = S.live && S.live.item ? S.playlist.indexOf(S.live.item) : -1;
  const at = playing >= 0 ? playing + 1 : S.playlist.findIndex((i) => i.state === 'queued');
  if (at >= 0) S.playlist.splice(at, 0, item); else S.playlist.push(item);
  afterPlaylistChange();
  const f = S.files.get(id);
  if (f) status(`Als volgende: ${label(f)}`);
  return item;
}
// Put it next and take over right away with a short crossfade, like a hot start.
async function playNow(id) {
  const item = addNext(id);
  const end = Date.now() + 20000;
  while (Date.now() < end) {
    if (decks.some((d) => d.item === item && d.state === 'cued')) { cmdStart(); return; }
    await new Promise((r) => setTimeout(r, 100));
  }
  status('Laden duurde te lang; het nummer staat als volgende klaar');
}
function movePlaylistItem(uid, beforeUid) {
  const from = S.playlist.findIndex((i) => i.uid === uid);
  if (from < 0 || uid === beforeUid) return;
  const [item] = S.playlist.splice(from, 1);
  const at = beforeUid ? S.playlist.findIndex((i) => i.uid === beforeUid) : -1;
  if (at >= 0) S.playlist.splice(at, 0, item); else S.playlist.push(item);
  afterPlaylistChange();
}
function removeItem(uid) {
  const item = S.playlist.find((i) => i.uid === uid);
  if (!item) return;
  if (item.state === 'playing') { status('Dit item speelt nu — stop het eerst'); return; }
  S.playlist = S.playlist.filter((i) => i !== item);
  afterPlaylistChange();
}
function afterPlaylistChange() {
  cueNext();
  savePlaylistSoon();
  render();
}

// ---------- hour clock planning ----------

let planning = false;
// Fill the next hour after what has already been planned, following the week schedule.
async function planNextHour({ manual }) {
  if (planning) return;
  planning = true;
  try {
    const hourMs = 3600 * 1000;
    const thisHour = Math.floor(Date.now() / hourMs) * hourMs;
    let start = S.settings.plannedUntil && S.settings.plannedUntil > thisHour ? S.settings.plannedUntil : thisHour;
    // Never plan more than a day ahead when the schedule is empty.
    if (start > Date.now() + 24 * hourMs) { if (manual) status('Er is al 24 uur vooruit gepland'); return; }
    const d = new Date(start);
    const exclude = S.playlist.filter((i) => i.state === 'queued' && i.id).map((i) => i.id);
    const { planned: [p] } = await api('POST', '/api/clocks/plan', { hours: [{ day: d.getDay(), hour: d.getHours() }], exclude });
    S.settings.plannedUntil = start + hourMs;
    const when = d.toLocaleString('nl-NL', { weekday: 'short', hour: '2-digit', minute: '2-digit' });
    if (!p.clock) {
      if (manual) status(`Geen uurklok gepland voor ${when} — stel de weekplanning in via ◔`);
      saveSettingsSoon();
      return;
    }
    S.playlist.push({ uid: newUid(), marker: `${when} · ${p.clock.name}`, color: p.clock.color, state: 'queued' });
    await ensureFiles(p.items);
    for (const id of p.items) if (S.files.has(id)) S.playlist.push({ uid: newUid(), id, stopAfter: false, state: 'queued' });
    if (p.missing) status(`${p.missing} blok(ken) van "${p.clock.name}" konden niet gevuld worden (lege collectie?)`);
    afterPlaylistChange();
  } catch (e) {
    status(`Plannen mislukt: ${e.message}`);
  } finally {
    planning = false;
  }
}

// ---------- persistence ----------

let saveTimer = null;
function settingsPayload() {
  S.settings.playlist = S.playlist.map(({ id, stopAfter, state, marker, color }) => (marker
    ? { marker, color, played: state === 'played' }
    : { id, stopAfter, played: state === 'played' }));
  return JSON.stringify({ settings: S.settings });
}
function saveSettingsSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    fetch('/api/me/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: settingsPayload() })
      .then((r) => { if (!r.ok) throw new Error(`fout ${r.status}`); })
      .catch((e) => status(`Opslaan mislukt: ${e.message}`));
  }, 600);
}
// Closing the window or app right after a change: save immediately; keepalive lets
// the request finish while the page goes away.
window.addEventListener('pagehide', () => {
  if (saveTimer === null) return;
  clearTimeout(saveTimer); saveTimer = null;
  fetch('/api/me/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: settingsPayload(), keepalive: true });
});
const savePlaylistSoon = saveSettingsSoon;

// ---------- theme ----------

function applyTheme() {
  const bg = BACKGROUNDS[S.settings.background] || BACKGROUNDS.zwart;
  const root = document.documentElement.style;
  for (const k of ['bg', 'panel', 'panel2', 'line', 'text', 'muted']) root.setProperty(`--${k}`, bg[k]);
  root.setProperty('--accent', S.settings.accent);
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(S.settings.accent.slice(i, i + 2), 16));
  root.setProperty('--accent-text', 0.299 * r + 0.587 * g + 0.114 * b > 150 ? '#000' : '#fff');
  $('station-name').textContent = S.settings.stationName;
  document.title = S.settings.stationName ? `${S.settings.stationName} · Audio OnAir Turbo Database` : 'Audio OnAir Turbo Database';
}

// ---------- rendering ----------

function render() {
  renderDecks();
  renderTransport();
  renderPlaylist();
  renderCart();
  renderTimes();
}

function renderTransport() {
  $('btn-auto').classList.toggle('on', S.settings.auto);
  $('btn-pause').classList.toggle('active', Boolean(S.live && S.live.state === 'paused'));
  $('onair-lamp').classList.toggle('live', Boolean(S.live && S.live.state === 'playing'));
}

function renderDecks() {
  for (const d of decks) {
    const el = $(`deck-${d.index}`);
    const stateLabel = { empty: 'LEEG', loading: 'LADEN…', cued: 'KLAAR', playing: d === S.live ? 'ON AIR' : 'UITLOOP', paused: 'PAUZE' }[d.state];
    el.className = `deck ${d.state === 'paused' ? 'playing' : d.state}`;
    el.replaceChildren(
      h('div', { class: 'deck-head' }, h('span', {}, `PLAYER ${d.name}`), h('span', { class: 'deck-state' }, stateLabel)),
      h('div', { class: 'deck-title' }, d.file ? d.file.title : '—'),
      h('div', { class: 'deck-artist' }, d.file ? d.file.artist || ' ' : ' '),
      h('div', { class: 'deck-bar' }, h('div', { id: `deck-fill-${d.index}` })),
      h('div', { class: 'deck-time' }, h('span', { id: `deck-pos-${d.index}` }, ''), h('span', { id: `deck-rem-${d.index}` }, '')));
  }
}

function renderTimes() {
  const now = new Date();
  $('clock-time').textContent = fmtClock(now);
  $('clock-date').textContent = now.toLocaleDateString('nl-NL', { weekday: 'long', day: 'numeric', month: 'long' });

  for (const d of decks) {
    const fill = $(`deck-fill-${d.index}`);
    if (!fill) continue;
    if (!d.file) { fill.style.width = '0'; $(`deck-pos-${d.index}`).textContent = ''; $(`deck-rem-${d.index}`).textContent = ''; continue; }
    const start = cueIn(d.file);
    const end = cueOut(d.file) ?? d.audio.duration;
    const t = d.state === 'cued' || d.state === 'loading' ? start : d.audio.currentTime;
    fill.style.width = `${Math.min(100, ((t - start) / (end - start)) * 100) || 0}%`;
    $(`deck-pos-${d.index}`).textContent = fmt(t - start);
    $(`deck-rem-${d.index}`).textContent = fmt(end - t, { sign: '-' });
  }

  const live = S.live;
  const countdown = $('now-remaining');
  if (live && live.file) {
    const f = live.file;
    const start = cueIn(f);
    const end = cueOut(f) ?? live.audio.duration;
    const t = live.audio.currentTime;
    const remaining = end - t;
    $('now-title').textContent = f.title;
    $('now-artist').textContent = f.artist || ' ';
    $('now-fill').style.width = `${Math.min(100, ((t - start) / (end - start)) * 100)}%`;
    const mix = mixPoint(f);
    const mixEl = $('now-mix');
    mixEl.style.display = mix !== null && mix < end ? 'block' : 'none';
    mixEl.style.left = `${((mix - start) / (end - start)) * 100}%`;
    $('now-elapsed').textContent = `${fmt(t - start)} / ${fmt(end - start)}`;
    countdown.textContent = fmt(remaining, { sign: '-' });
    countdown.className = `countdown${remaining < 5 ? ' end' : remaining < 15 ? ' warn' : ''}`;
  } else {
    $('now-title').textContent = '—';
    $('now-artist').textContent = 'Niets op de lucht';
    $('now-fill').style.width = '0';
    $('now-mix').style.display = 'none';
    $('now-elapsed').textContent = '';
    countdown.textContent = '-0:00';
    countdown.className = 'countdown';
  }
  const next = nextQueued();
  const nextFile = next && S.files.get(next.id);
  $('now-next').textContent = nextFile ? `Hierna: ${label(nextFile)}` : '';

  // Estimated start time of every upcoming item.
  let at = Date.now();
  if (live && live.file) at += ((mixPoint(live.file) ?? cueOut(live.file)) - live.audio.currentTime) * 1000;
  let known = true;
  let total = 0;
  for (const item of S.playlist) {
    const el = document.querySelector(`[data-uid="${item.uid}"] .pl-time`);
    if (item.marker) { if (el) el.textContent = item.state === 'queued' && known ? fmtClock(new Date(at)).slice(0, 5) : ''; continue; }
    const f = S.files.get(item.id);
    if (item.state !== 'queued' || !f) { if (el) el.textContent = item.state === 'playing' ? 'NU' : ''; continue; }
    if (el) el.textContent = known ? fmtClock(new Date(at)) : '';
    const len = playLength(f);
    if (len === null || item.stopAfter) known = false;
    if (len !== null) { at += len * 1000; total += len; }
  }
  $('playlist-total').textContent = total ? `nog ${fmt(total)}` : '';

  for (const [index, p] of S.cartPlayers) {
    const el = document.querySelector(`[data-slot="${index}"]`);
    if (!el || p.loading) continue;
    const start = cueIn(p.file);
    const end = cueOut(p.file) ?? p.audio.duration;
    el.querySelector('.slot-fill').style.width = `${Math.min(100, ((p.audio.currentTime - start) / (end - start)) * 100)}%`;
    el.querySelector('.slot-time').textContent = fmt(end - p.audio.currentTime, { sign: '-' });
  }
}

let dragOverUid = null;
function renderPlaylist() {
  const root = $('playlist');
  const cuedDeck = decks.find((d) => d.state === 'cued' || d.state === 'loading');
  if (!S.playlist.length) {
    root.replaceChildren(h('div', { class: 'empty' }, 'Playlist is leeg. Sleep nummers uit de database hierheen.'));
    return;
  }
  root.replaceChildren(...S.playlist.map((item) => {
    const drag = {
      'data-uid': item.uid, draggable: 'true',
      ondragstart: (e) => { e.dataTransfer.setData('text/aot-item', item.uid); e.dataTransfer.effectAllowed = 'move'; },
      ondragover: (e) => { e.preventDefault(); if (dragOverUid !== item.uid) { dragOverUid = item.uid; renderPlaylist(); } },
      ondrop: (e) => { e.preventDefault(); e.stopPropagation(); dropOnPlaylist(e, item.uid); },
    };
    if (item.marker) {
      return h('div', { ...drag, class: `pl-marker ${item.state}${dragOverUid === item.uid ? ' drop-before' : ''}`, style: { '--mk': item.color || '' } },
        h('div', { class: 'pl-time' }), h('div', { class: 'mk-label' }, `◔ ${item.marker}`),
        h('button', { title: 'Markering verwijderen', onclick: () => { S.playlist = S.playlist.filter((i) => i !== item); afterPlaylistChange(); } }, '✕'));
    }
    const f = S.files.get(item.id);
    const cls = ['pl-row', item.state, item.error ? 'error' : '', cuedDeck && cuedDeck.item === item ? 'cued' : '', dragOverUid === item.uid ? 'drop-before' : ''].join(' ');
    return h('div', {
      class: cls, 'data-uid': item.uid, draggable: 'true',
      ondragstart: (e) => { e.dataTransfer.setData('text/aot-item', item.uid); e.dataTransfer.effectAllowed = 'move'; },
      ondragover: (e) => { e.preventDefault(); if (dragOverUid !== item.uid) { dragOverUid = item.uid; renderPlaylist(); } },
      ondrop: (e) => { e.preventDefault(); e.stopPropagation(); dropOnPlaylist(e, item.uid); },
    },
    h('div', { class: 'pl-time' }),
    h('div', { class: 'pl-main' },
      h('div', { class: 'pl-title' }, f ? f.title : '(verwijderd bestand)', item.error ? ' ⚠' : ''),
      h('div', { class: 'pl-artist' }, f ? f.artist || ' ' : ' ', item.stopAfter ? h('span', { class: 'pl-stop' }, '  ■ STOP NA DIT ITEM') : null)),
    h('div', { class: 'pl-dur' }, f ? fmt(playLength(f)) : ''),
    h('div', { class: 'pl-actions' },
      h('button', { title: 'Stop na dit item', class: item.stopAfter ? 'on' : '', onclick: () => { item.stopAfter = !item.stopAfter; savePlaylistSoon(); render(); } }, '■'),
      h('button', { title: 'Verwijderen', onclick: () => removeItem(item.uid) }, '✕')));
  }));
}

function dropOnPlaylist(e, beforeUid) {
  dragOverUid = null;
  const moved = e.dataTransfer.getData('text/aot-item');
  const fileId = Number(e.dataTransfer.getData('text/aot-file'));
  if (moved) movePlaylistItem(moved, beforeUid);
  else if (fileId) addToPlaylist(fileId, beforeUid);
  else render();
}

// Files the studio knows about: search results plus everything in the playlist,
// on a deck or on the jingle panel. The full library is never loaded.
async function ensureFiles(ids) {
  const missing = [...new Set(ids.filter((id) => id && !S.files.has(id)))];
  for (let i = 0; i < missing.length; i += 500) {
    const { files } = await api('GET', `/api/files?limit=500&ids=${missing.slice(i, i + 500).join(',')}`);
    for (const f of files) S.files.set(f.id, f);
  }
}

const LIBRARY_PAGE = 200;
const lib = { files: [], total: 0, token: 0, loading: false };
let libTimer;
function searchLibrarySoon() { clearTimeout(libTimer); libTimer = setTimeout(searchLibrary, 200); }

function libraryParams(offset) {
  const params = new URLSearchParams({ limit: LIBRARY_PAGE, offset, sort: $('lib-sort').value });
  if ($('lib-collection').value) params.set('collection_id', $('lib-collection').value);
  if ($('lib-search').value.trim()) params.set('q', $('lib-search').value.trim());
  return params;
}
const remember = (files) => files.map((f) => { if (!S.files.has(f.id)) S.files.set(f.id, f); return S.files.get(f.id); });

// New search: first page. Scrolling down loads the next pages, through the whole database.
async function searchLibrary() {
  const token = ++lib.token;
  lib.loading = true;
  try {
    const { files, total } = await api('GET', `/api/files?${libraryParams(0)}`);
    if (token !== lib.token) return; // a newer search is already running
    lib.files = remember(files); lib.total = total;
    renderLibrary();
    $('library').scrollTop = 0;
  } catch (e) { status(`Zoeken mislukt: ${e.message}`); } finally { if (token === lib.token) lib.loading = false; }
}
async function loadMoreLibrary() {
  if (lib.loading || lib.files.length >= lib.total) return;
  const token = lib.token;
  lib.loading = true;
  try {
    const { files } = await api('GET', `/api/files?${libraryParams(lib.files.length)}`);
    if (token !== lib.token) return;
    const added = remember(files);
    lib.files.push(...added);
    const more = $('library').querySelector('.lib-more');
    if (more) more.remove();
    $('library').append(...added.map(libraryRow), ...libraryTail());
    updateLibraryCount();
  } catch (e) { status(`Laden mislukt: ${e.message}`); } finally { if (token === lib.token) lib.loading = false; }
}

function updateLibraryCount() {
  const n = (x) => x.toLocaleString('nl-NL');
  $('library-count').textContent = lib.total > lib.files.length ? `${n(lib.files.length)} van ${n(lib.total)}` : `${n(lib.total)} items`;
}
const libraryTail = () => (lib.files.length < lib.total ? [h('div', { class: 'lib-more' }, 'Scroll verder voor meer…')] : []);

function libraryRow(f) {
  return h('div', {
    class: 'lib-row', draggable: 'true', 'data-file': f.id,
    ondragstart: (e) => { e.dataTransfer.setData('text/aot-file', String(f.id)); e.dataTransfer.effectAllowed = 'copy'; },
    ondblclick: () => addToPlaylist(f.id),
    oncontextmenu: (e) => { e.preventDefault(); fileMenu(e, f); },
  },
  h('div', { class: 'pl-main' }, h('div', { class: 'pl-title' }, f.title), h('div', { class: 'pl-artist' }, f.artist || ' ')),
  h('div', { class: 'pl-dur' }, fmt(cueOut(f) !== null ? cueOut(f) - cueIn(f) : null)),
  h('div', { class: 'lib-actions' },
    h('button', { title: 'Voorbeluisteren', 'data-pfl': f.id, class: S.pfl.fileId === f.id ? 'pfl-on' : '', onclick: () => togglePfl(f) }, '🎧'),
    h('button', { title: 'Als volgende afspelen', onclick: (e) => { addNext(f.id); flashRow(e.target); } }, '⤴'),
    h('button', { title: 'Achteraan de playlist', onclick: (e) => { addToPlaylist(f.id); flashRow(e.target); } }, '+')));
}
function flashRow(el) {
  const row = el.closest('.lib-row');
  row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
}

function renderLibrary() {
  updateLibraryCount();
  $('library').replaceChildren(...(lib.files.length ? [...lib.files.map(libraryRow), ...libraryTail()]
    : [h('div', { class: 'empty' }, $('lib-search').value.trim() || $('lib-collection').value ? 'Niets gevonden.' : 'De database is nog leeg. Upload muziek via ☰ Bibliotheek.')]));
}
// PFL on/off without rebuilding the (possibly very long) list.
function updatePflButtons() {
  document.querySelectorAll('[data-pfl]').forEach((b) => b.classList.toggle('pfl-on', Number(b.dataset.pfl) === S.pfl.fileId));
}

// New music added on the server (automatically or by hand): tell the DJ and refresh
// the list, unless they are searching or scrolled down.
let libraryCount = null;
async function checkNewMusic() {
  try {
    const { count } = await api('GET', '/api/library/stats');
    if (libraryCount !== null && count > libraryCount) {
      const n = count - libraryCount;
      status(`🎵 ${n} ${n === 1 ? 'nieuw nummer' : 'nieuwe nummers'} in de database`);
      if (!$('lib-search').value.trim() && $('library').scrollTop < 40) searchLibrary();
    }
    libraryCount = count;
  } catch { /* try again next time */ }
}

// Right-click menu on a library row.
function fileMenu(e, f) {
  const menu = $('slot-menu');
  const close = () => { menu.classList.add('hidden'); menu.classList.remove('ctx'); document.removeEventListener('mousedown', outside); };
  const outside = (ev) => { if (!menu.contains(ev.target)) close(); };
  const item = (text, fn, cls) => h('button', { class: cls || '', onclick: () => { close(); fn(); } }, text);
  menu.classList.add('ctx');
  menu.replaceChildren(
    h('div', { class: 'ctx-title' }, label(f)),
    item('⤴  Als volgende afspelen', () => addNext(f.id)),
    item('+  Achteraan de playlist', () => addToPlaylist(f.id)),
    item('▶  Direct afspelen', () => playNow(f.id), 'live'),
    item('🎧  Voorbeluisteren', () => togglePfl(f)));
  menu.style.left = `${Math.min(e.clientX, innerWidth - 240)}px`;
  menu.style.top = `${Math.min(e.clientY, innerHeight - 200)}px`;
  menu.classList.remove('hidden');
  setTimeout(() => document.addEventListener('mousedown', outside), 0);
}

function renderCart() {
  const { cols, rows, count } = cartDims();
  const root = $('cart');
  root.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  root.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
  $('cart-pages').replaceChildren(...Array.from({ length: CART_PAGES }, (_, p) => h('button', {
    class: p === S.settings.cartPage ? 'active' : '',
    onclick: () => { S.settings.cartPage = p; saveSettingsSoon(); preloadCartPage(); renderCart(); },
  }, 'ABCD'[p])));
  const slots = cartSlots();
  root.replaceChildren(...Array.from({ length: count }, (_, i) => {
    const index = cartIndex(i);
    const slot = slots[index];
    const file = slot && S.files.get(slot.id);
    const player = S.cartPlayers.get(index);
    const key = i < 10 ? String((i + 1) % 10) : '';
    const common = {
      'data-slot': index,
      ondragover: (e) => { e.preventDefault(); e.currentTarget.classList.add('over'); },
      ondragleave: (e) => e.currentTarget.classList.remove('over'),
      ondrop: (e) => {
        e.preventDefault();
        const id = Number(e.dataTransfer.getData('text/aot-file'));
        if (!id) return;
        slots[index] = { id, color: (slot && slot.color) || SLOT_COLORS[index % SLOT_COLORS.length] };
        saveSettingsSoon(); preloadCartPage(); renderCart();
      },
      oncontextmenu: (e) => { e.preventDefault(); if (slot) slotMenu(e, index); },
    };
    if (!file) return h('div', { ...common, class: 'slot empty-slot' }, slot ? '(bestand weg)' : '+');
    return h('button', {
      ...common,
      class: `slot${player ? ' playing' : ''}${player && player.loading ? ' loading' : ''}`,
      style: { '--slot': slot.color },
      onclick: () => cartTrigger(index),
    },
    h('div', { class: 'slot-fill' }),
    h('div', { class: 'slot-key' }, key),
    h('div', { class: 'slot-title' }, file.title),
    h('div', { class: 'slot-time' }, fmt(cueOut(file) !== null ? cueOut(file) - cueIn(file) : null)));
  }));
}

function slotMenu(e, index) {
  const menu = $('slot-menu');
  menu.classList.remove('ctx');
  const close = () => { menu.classList.add('hidden'); document.removeEventListener('mousedown', outside); };
  const outside = (ev) => { if (!menu.contains(ev.target)) close(); };
  menu.replaceChildren(
    ...SLOT_COLORS.map((c) => h('button', { class: 'sw', style: { background: c }, onclick: () => { cartSlots()[index].color = c; saveSettingsSoon(); renderCart(); close(); } })),
    h('button', { class: 'mini wide', onclick: () => { stopCart(index); cartSlots()[index] = null; saveSettingsSoon(); renderCart(); close(); } }, 'Leegmaken'));
  menu.style.left = `${Math.min(e.clientX, innerWidth - 180)}px`;
  menu.style.top = `${Math.min(e.clientY, innerHeight - 120)}px`;
  menu.classList.remove('hidden');
  setTimeout(() => document.addEventListener('mousedown', outside), 0);
}

function renderOutputsStatus() {
  const sinks = typeof HTMLMediaElement.prototype.setSinkId === 'function';
  const named = OUTPUTS.filter(([k]) => S.outputs[k]).length;
  $('status-outputs').textContent = sinks
    ? `Uitgangen: ${named ? `${named} van 4 toegewezen` : 'alles via standaard geluidskaart'}`
    : 'Meerdere geluidskaarten: gebruik Chrome of Edge';
}

// ---------- settings dialog ----------

async function listDevices() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return [];
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((d) => d.kind === 'audiooutput');
}

async function renderSettings() {
  const st = S.settings;
  $('set-station').value = st.stationName;
  $('set-crossfade').value = st.crossfade;
  $('set-fadeout').value = st.fadeOut;
  $('set-autocue').checked = st.autoCue;
  $('set-quality').value = st.quality || 'auto';
  $('set-clockauto').checked = st.clockAuto;
  $('set-cartsize').value = st.cartSize;
  $('set-background').replaceChildren(...Object.entries(BACKGROUNDS).map(([key, b]) => h('button', {
    type: 'button', class: `choice${st.background === key ? ' sel' : ''}`, style: { background: b.bg, color: b.text },
    onclick: () => { st.background = key; applyTheme(); saveSettingsSoon(); renderSettings(); },
  }, b.label)));
  const picker = h('input', { type: 'color', value: st.accent, title: 'Eigen kleur' });
  picker.addEventListener('input', () => { st.accent = picker.value; applyTheme(); saveSettingsSoon(); });
  $('set-accent').replaceChildren(...ACCENTS.map((c) => h('button', {
    type: 'button', class: `swatch${st.accent === c ? ' sel' : ''}`, style: { background: c },
    onclick: () => { st.accent = c; applyTheme(); saveSettingsSoon(); renderSettings(); },
  })), picker);

  const sinks = typeof HTMLMediaElement.prototype.setSinkId === 'function';
  $('sink-support').textContent = sinks
    ? 'Kies per onderdeel een eigen uitgang. Klik eerst op "Geluidskaarten zoeken" zodat de browser de namen mag tonen.'
    : 'Deze browser kan het geluid niet naar meerdere geluidskaarten sturen. Gebruik Google Chrome of Microsoft Edge.';
  const devices = sinks ? await listDevices() : [];
  $('set-outputs').replaceChildren(...OUTPUTS.map(([key, name]) => {
    const select = h('select', { disabled: !sinks },
      h('option', { value: '' }, 'Standaard geluidskaart'),
      devices.filter((d) => d.deviceId && d.deviceId !== 'default').map((d, i) => h('option', { value: d.deviceId }, d.label || `Geluidskaart ${i + 1}`)));
    select.value = S.outputs[key] || '';
    select.addEventListener('change', () => {
      S.outputs[key] = select.value;
      writeLocal('aot_outputs', S.outputs);
      for (const d of decks) if (d.name === key) applySink(d.audio, key);
      if (key === 'pfl') applySink(S.pfl.audio, 'pfl');
      if (key === 'cart') for (const p of S.cartPlayers.values()) applySink(p.audio, 'cart');
      renderOutputsStatus();
    });
    return h('div', {}, h('label', {}, name), select);
  }));
}

function bindSettings() {
  const dlg = $('settings');
  $('btn-settings').addEventListener('click', () => {
    renderSettings(); dlg.showModal();
    api('GET', '/api/me/station-link').then(({ url }) => { $('set-nowlink').value = url; }).catch(() => {});
  });
  $('btn-copy-nowlink').addEventListener('click', async (e) => {
    $('set-nowlink').select();
    await navigator.clipboard.writeText($('set-nowlink').value).catch(() => {});
    e.target.textContent = 'Gekopieerd'; setTimeout(() => (e.target.textContent = 'Kopieer'), 1500);
  });
  $('set-station').addEventListener('input', (e) => { S.settings.stationName = e.target.value; applyTheme(); saveSettingsSoon(); });
  $('set-crossfade').addEventListener('change', (e) => { S.settings.crossfade = Math.min(10, Math.max(0, Number(e.target.value) || 0)); saveSettingsSoon(); render(); });
  $('set-fadeout').addEventListener('change', (e) => { S.settings.fadeOut = Math.min(15, Math.max(0.5, Number(e.target.value) || 3)); saveSettingsSoon(); });
  $('set-clockauto').addEventListener('change', (e) => { S.settings.clockAuto = e.target.checked; saveSettingsSoon(); });
  $('set-quality').addEventListener('change', (e) => {
    S.settings.quality = e.target.value; saveSettingsSoon(); renderQuality();
    // New loads use the new quality; what is already loaded keeps playing.
    blobCache.clear();
    cueNext(); preloadCartPage();
  });
  $('set-autocue').addEventListener('change', (e) => { S.settings.autoCue = e.target.checked; saveSettingsSoon(); });
  $('set-cartsize').addEventListener('change', (e) => {
    // Keep each page's buttons in place when the grid size changes.
    const oldCount = cartDims().count;
    const old = cartSlots();
    S.settings.cartSize = e.target.value;
    const { count } = cartDims();
    const next = [];
    for (let p = 0; p < CART_PAGES; p++) for (let i = 0; i < count; i++) next.push(i < oldCount ? old[p * oldCount + i] || null : null);
    S.settings.cart = next;
    S.cartPlayers.forEach((_, i) => stopCart(i));
    saveSettingsSoon(); renderCart();
  });
  $('btn-find-devices').addEventListener('click', async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
    } catch { status('Zonder toestemming toont de browser geen namen van geluidskaarten'); }
    renderSettings();
  });
}

// ---------- keyboard ----------

document.addEventListener('keydown', (e) => {
  if (e.target.matches('input, select, textarea') || e.ctrlKey || e.metaKey || e.altKey) return;
  if ($('settings').open) return;
  const k = e.key.toLowerCase();
  if (k === ' ') { e.preventDefault(); cmdStart(); }
  else if (k === 'n') cmdStart();
  else if (k === 'p') cmdPause();
  else if (k === 'f') cmdFade();
  else if (k === 'a') cmdAuto();
  else if (/^[0-9]$/.test(k)) {
    const i = k === '0' ? 9 : Number(k) - 1;
    if (i < cartDims().count) cartTrigger(cartIndex(i));
  }
});

// ---------- start-up ----------

async function boot() {
  try { S.me = (await api('GET', '/api/me')).user; } catch { location.href = '/'; return; }
  const [{ settings }, { collections }] = await Promise.all([api('GET', '/api/me/settings'), api('GET', '/api/collections')]);
  S.settings = { ...DEFAULT_SETTINGS, ...settings };
  S.collections = collections;
  await ensureFiles([...(S.settings.playlist || []).map((i) => i.id), ...(S.settings.cart || []).filter(Boolean).map((s) => s.id)]);
  S.playlist = (S.settings.playlist || []).filter((i) => i.marker || S.files.has(i.id))
    .map((i) => (i.marker
      ? { uid: newUid(), marker: i.marker, color: i.color, state: i.played ? 'played' : 'queued' }
      : { uid: newUid(), id: i.id, stopAfter: Boolean(i.stopAfter), state: i.played ? 'played' : 'queued' }));

  $('lib-collection').replaceChildren(h('option', { value: '' }, 'Alle collecties'), ...collections.map((c) => h('option', { value: c.id }, c.name)));
  $('lib-collection').addEventListener('change', searchLibrary);
  $('lib-search').addEventListener('input', searchLibrarySoon);
  $('lib-sort').value = S.settings.librarySort || 'name';
  $('lib-sort').addEventListener('change', () => { S.settings.librarySort = $('lib-sort').value; saveSettingsSoon(); searchLibrary(); });
  $('library').addEventListener('scroll', () => {
    const el = $('library');
    if (el.scrollTop + el.clientHeight > el.scrollHeight - 600) loadMoreLibrary();
  });

  $('btn-start').addEventListener('click', cmdStart);
  $('btn-next').addEventListener('click', cmdStart);
  $('btn-pause').addEventListener('click', cmdPause);
  $('btn-stop').addEventListener('click', cmdStop);
  $('btn-fade').addEventListener('click', cmdFade);
  $('btn-auto').addEventListener('click', cmdAuto);
  $('btn-cart-stop').addEventListener('click', () => [...S.cartPlayers.keys()].forEach(stopCart));
  $('btn-plan').addEventListener('click', () => planNextHour({ manual: true }));
  setInterval(() => {
    const waiting = S.playlist.filter((i) => i.state === 'queued' && !i.marker && !i.error).length;
    if (S.settings.clockAuto && waiting < 3) planNextHour({ manual: false });
  }, 5000);
  $('btn-clean').addEventListener('click', () => { S.playlist = S.playlist.filter((i) => i.state !== 'played'); afterPlaylistChange(); });
  $('btn-clear').addEventListener('click', () => {
    if (!confirm('Playlist leegmaken? (wat nu speelt, speelt door)')) return;
    S.playlist = S.playlist.filter((i) => i.state === 'playing'); afterPlaylistChange();
  });
  const pl = $('playlist');
  pl.addEventListener('dragover', (e) => e.preventDefault());
  pl.addEventListener('drop', (e) => { e.preventDefault(); dropOnPlaylist(e, null); });
  pl.addEventListener('dragleave', (e) => { if (!pl.contains(e.relatedTarget)) { dragOverUid = null; renderPlaylist(); } });

  bindSettings();
  applyTheme();
  applySink(S.pfl.audio, 'pfl');
  renderOutputsStatus();
  renderQuality();
  searchLibrary();
  checkNewMusic();
  setInterval(checkNewMusic, 60 * 1000);
  render();
  cueNext();
  preloadCartPage();
}

boot();
