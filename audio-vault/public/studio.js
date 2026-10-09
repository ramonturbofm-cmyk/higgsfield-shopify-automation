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
  normalize: true,
  loudnessTarget: -16,
  auto: true,
  playlist: [],
  cartSize: '4x4',
  cartPage: 0,
  cart: [],
  clockAuto: false,
  nonstopCollections: null, // null = every collection that doesn't look like jingles/ads
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

// A "naadloos" track (live album, mix, medley) plays from the very start to the very
// end, and the next item starts exactly where it ends.
const cueIn = (f) => (f.segue ? 0 : f.cue_in ?? 0);
const cueOut = (f) => (f.segue ? f.duration_seconds ?? f.cue_out ?? null : f.cue_out ?? f.duration_seconds ?? null);
// Starting a player takes a few hundredths of a second; start that much early so the
// join has no gap.
const SEGUE_LEAD = 0.035;
function mixPoint(f) {
  if (f.segue) { const end = cueOut(f); return end === null ? null : Math.max(0, end - SEGUE_LEAD); }
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

// ---------- audio routing ----------
// Every output (Player A, Player B, jingle panel, PFL) is its own Web Audio context on
// its own sound card. Each sound passes a gain stage: loudness correction (equal
// volume for every track) times the fade. The audio files themselves never change.
const contexts = {};
const canPickOutput = () => typeof AudioContext !== 'undefined' && typeof AudioContext.prototype.setSinkId === 'function';

function setOutput(key) {
  const ctx = contexts[key];
  if (!ctx || typeof ctx.setSinkId !== 'function') return;
  ctx.setSinkId(S.outputs[key] || '').catch((e) => { status(`Geluidskaart voor ${key} niet beschikbaar: ${e.message}`); logEvent('WARNING', `Geluidskaart voor ${outputName(key)} niet beschikbaar: ${e.message}`); });
}
function route(audio, key) {
  if (!audio._gain) {
    if (!contexts[key]) { contexts[key] = new AudioContext({ latencyHint: 'playback' }); watchOutput(key); setOutput(key); if (key !== 'pfl') addMeterTap(contexts[key]); }
    const ctx = contexts[key];
    audio._ctx = ctx;
    audio._src = ctx.createMediaElementSource(audio);
    audio._gain = ctx.createGain();
    audio._src.connect(audio._gain).connect(ctx.destination);
    if (ctx._meter) audio._gain.connect(ctx._meter);
    audio._norm = 1; audio._fade = 1;
  }
  if (audio._ctx.state === 'suspended') audio._ctx.resume().catch(() => {});
  return audio;
}
// A browser only lets sound start after the user has clicked or typed once; contexts
// created in between (e.g. the next track being cued) are woken up at the next touch.
for (const type of ['pointerdown', 'keydown']) {
  window.addEventListener(type, () => { for (const ctx of Object.values(contexts)) if (ctx.state === 'suspended') ctx.resume().catch(() => {}); }, true);
}
// ---------- level meter ----------
// Every on-air output (players, jingles; not PFL) feeds a stereo tap; the meter shows
// the loudest of them, after equal volume and fades — what actually goes out.
function addMeterTap(ctx) {
  const mix = ctx.createGain(); // mono sources go to both sides
  mix.channelCount = 2; mix.channelCountMode = 'explicit'; mix.channelInterpretation = 'speakers';
  const split = ctx.createChannelSplitter(2);
  mix.connect(split);
  ctx._analysers = [0, 1].map((ch) => { const a = ctx.createAnalyser(); a.fftSize = 1024; split.connect(a, ch); return a; });
  ctx._meter = mix;
}
const meter = { level: [-90, -90], peak: [-90, -90], peakAt: [0, 0], buf: new Float32Array(1024), last: 0 };
const VU_MIN = -48;
const vuPct = (db) => Math.max(0, Math.min(100, ((db - VU_MIN) / -VU_MIN) * 100));
function drawMeter(now) {
  const dt = Math.min(0.2, (now - (meter.last || now)) / 1000);
  meter.last = now;
  for (let ch = 0; ch < 2; ch++) {
    let peak = 0;
    for (const ctx of Object.values(contexts)) {
      if (!ctx._analysers || ctx.state !== 'running') continue;
      ctx._analysers[ch].getFloatTimeDomainData(meter.buf);
      for (let i = 0; i < meter.buf.length; i++) { const v = Math.abs(meter.buf[i]); if (v > peak) peak = v; }
    }
    const db = peak > 0 ? 20 * Math.log10(peak) : -90;
    // Instant rise, falls back 24 dB per second; the peak line holds 1.5 s.
    meter.level[ch] = Math.max(db, meter.level[ch] - 24 * dt);
    if (db >= meter.peak[ch] || now - meter.peakAt[ch] > 1500) { meter.peak[ch] = db; meter.peakAt[ch] = now; }
    $(ch ? 'vu-r' : 'vu-l').style.width = `${100 - vuPct(meter.level[ch])}%`;
    const p = $(ch ? 'vu-rp' : 'vu-lp');
    p.style.left = `calc(${vuPct(meter.peak[ch])}% - 2px)`;
    p.style.opacity = meter.peak[ch] > VU_MIN ? 1 : 0;
  }
  $('vu').classList.toggle('clip', Math.max(...meter.peak) > -0.5);
  requestAnimationFrame(drawMeter);
}
requestAnimationFrame(drawMeter);

function unroute(audio) {
  if (audio._src) { audio._src.disconnect(); audio._gain.disconnect(); audio._src = null; audio._gain = null; }
}
const applyGain = (audio) => { if (audio._gain) audio._gain.gain.value = audio._norm * audio._fade; };
const setFade = (audio, v) => { audio._fade = v; applyGain(audio); };
const getFade = (audio) => (audio._fade === undefined ? 1 : audio._fade);

// Equal volume: bring the track's measured loudness (EBU R128) to the target level.
// Turning up is limited to +6 dB and never above -1 dB true peak, so nothing clips.
function loudnessDb(file) {
  if (!S.settings.normalize || !file || file.loudness_lufs === null || file.loudness_lufs === undefined) return 0;
  let db = S.settings.loudnessTarget - file.loudness_lufs;
  if (db > 0) db = Math.max(0, Math.min(db, 6, file.true_peak_db === null || file.true_peak_db === undefined ? 6 : -1 - file.true_peak_db));
  return Math.max(-24, db);
}
function setNormalization(audio, file) {
  audio._norm = 10 ** (loudnessDb(file) / 20);
  applyGain(audio);
}
function reapplyNormalization() {
  for (const d of decks) if (d.file && !d.chained) setNormalization(d.audio, d.file);
  for (const p of S.cartPlayers.values()) setNormalization(p.audio, p.file);
  const pf = S.pfl.fileId && S.files.get(S.pfl.fileId);
  if (pf) setNormalization(S.pfl.audio, pf);
  renderDecks();
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
  deck.chained = false; deck.chainNorm = undefined;
  render();
  try {
    if (!deck.file) throw new Error('Bestand bestaat niet meer');
    const url = await fileUrl(deck.file);
    if (token !== deck.token) return;
    route(deck.audio, deck.name);
    setNormalization(deck.audio, deck.file);
    if (deck.audio.src !== url) {
      deck.audio.src = url;
      await once(deck.audio, 'loadedmetadata');
    }
    if (token !== deck.token) return;
    if (deck.file.duration_seconds === null) deck.file.duration_seconds = deck.audio.duration;
    deck.audio.currentTime = cueIn(deck.file);
    setFade(deck.audio, 1);
    deck.state = 'cued';
    // Auto mode with nothing on air (e.g. the previous item ended before this one was ready).
    if (deck.pendingStart && !S.live) startDeck(deck);
  } catch (e) {
    if (token !== deck.token) return;
    const what = deck.file ? label(deck.file) : 'Bestand';
    deck.error = { msg: `${what}: ${e.message}`, at: Date.now() };
    logEvent('ERROR', `Player ${deck.name}: ${what} kan niet worden geladen (${e.message}) — wordt overgeslagen`);
    setTimeout(render, 20500);
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
  route(deck.audio, deck.name);
  if (deck.chainNorm !== undefined) { deck.audio._norm = deck.chainNorm; deck.chained = true; } else deck.chained = false;
  deck.chainNorm = undefined;
  setFade(deck.audio, 1);
  deck.audio.play().catch((e) => status(`Afspelen geblokkeerd: ${e.message}`));
  S.live = deck;
  logEvent('INFO', `Player ${deck.name} gestart: ${label(deck.file)}`);
  api('POST', '/api/now-playing', { file_id: deck.file.id }).catch(() => {});
  cueNext();
  savePlaylistSoon();
  render();
}

function fadeDeck(deck, seconds, { stop = true } = {}) {
  deck.fade = { start: performance.now(), dur: Math.max(0.05, seconds) * 1000, from: getFade(deck.audio), stop };
}

function finishDeck(deck, { advance }) {
  const wasLive = deck === S.live;
  const item = deck.item;
  if (deck.file) logEvent('INFO', `Player ${deck.name} klaar: ${label(deck.file)}`);
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
  if (d.state === 'playing') { d.audio.pause(); d.state = 'paused'; logEvent('INFO', `PAUZE: Player ${d.name}`); }
  else if (d.state === 'paused') { d.audio.play(); d.state = 'playing'; logEvent('INFO', `Verder na pauze: Player ${d.name}`); }
  render();
}
function cmdStop() {
  if (decks.some((d) => d.state === 'playing' || d.state === 'paused')) logEvent('INFO', 'STOP');
  for (const d of decks) if (d.state === 'playing' || d.state === 'paused') finishDeck(d, { advance: false });
}
function cmdFade() {
  if (!S.live || S.live.state !== 'playing') return;
  logEvent('INFO', `FADE: Player ${S.live.name}`);
  S.live.noAdvance = true;
  fadeDeck(S.live, S.settings.fadeOut);
}
// Doorspoelen: jump to a point in the track (click or drag the progress bar, ← / → keys).
function seekDeck(deck, sec) {
  if (!deck || !deck.file || !['playing', 'paused', 'cued'].includes(deck.state)) return;
  const start = cueIn(deck.file);
  const end = cueOut(deck.file) ?? deck.audio.duration;
  if (!Number.isFinite(end)) return;
  const t = Math.max(start, Math.min(sec, end - 0.5));
  deck.audio.currentTime = t;
  // Seeking back before the mix point makes the automatic mix possible again.
  const mix = mixPoint(deck.file);
  if (deck.mixed && mix !== null && t < mix && !decks.some((d) => d !== deck && d.state === 'playing')) deck.mixed = false;
  renderTimes();
}
function seekBy(seconds) {
  const d = S.live;
  if (d) seekDeck(d, d.audio.currentTime + seconds);
}
function seekable(bar, deckFor) {
  const at = (e) => {
    const deck = deckFor();
    if (!deck || !deck.file) return;
    const r = bar().getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const start = cueIn(deck.file);
    const end = cueOut(deck.file) ?? deck.audio.duration;
    seekDeck(deck, start + frac * (end - start));
  };
  return (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    at(e);
    const move = (ev) => at(ev);
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
}

// NONSTOP: one press and the station keeps going by itself. Turns on automatic
// continuing and planning, fills the playlist when it is (almost) empty and starts.
// Hours without an uurklok get music from the nonstop collections.
const JINGLE_LIKE = /jingle|reclame|commercial|spot|sweeper|stinger|station ?id|\bid\b|nieuws|news|bed|tune|promo/i;
function nonstopCollectionIds() {
  if (Array.isArray(S.settings.nonstopCollections)) return S.settings.nonstopCollections;
  return S.collections.filter((c) => !JINGLE_LIKE.test(c.name)).map((c) => c.id);
}
const nonstopOn = () => S.settings.auto && S.settings.clockAuto;
async function cmdNonstop() {
  if (nonstopOn()) {
    S.settings.clockAuto = false;
    logEvent('INFO', 'NONSTOP uit');
    saveSettingsSoon(); render();
    status('Nonstop uit: de playlist speelt verder af, er wordt niets meer bijgepland');
    return;
  }
  S.settings.auto = true;
  S.settings.clockAuto = true;
  logEvent('INFO', 'NONSTOP aan');
  saveSettingsSoon(); render();
  const waiting = () => S.playlist.filter((i) => i.state === 'queued' && !i.marker && !i.error).length;
  if (waiting() < 3) {
    // Planning starts from the current hour again when nothing is waiting.
    if (!waiting()) S.settings.plannedUntil = null;
    status('Nonstop: playlist vullen…');
    await planNextHour({ manual: false });
  }
  if (!waiting()) return;
  status('▶ Nonstop aan: speelt automatisch door en plant zelf bij');
  if (!S.live) cmdStart();
}

function cmdAuto() {
  S.settings.auto = !S.settings.auto;
  logEvent('INFO', `AUTO ${S.settings.auto ? 'aan' : 'uit'}`);
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
      setFade(d.audio, Math.max(0, d.fade.from * (1 - p) * (1 - p)));
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
        // A naadloos track simply plays to its end.
        if (!f.segue && (f.mix_out === null || f.mix_out === undefined)) fadeDeck(d, Math.max(0.1, end - t));
        // Naadloos into naadloos (one side of an LP, a live set): keep the volume of the
        // track that is playing, so equal volume doesn't make a step at the join.
        if (f.segue && next.file && next.file.segue) next.chainNorm = d.audio._norm;
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
    route(audio, 'cart');
    setNormalization(audio, file);
    await once(audio, 'loadedmetadata');
    if (S.cartPlayers.get(index) !== player) return;
    audio.currentTime = cueIn(file);
    player.loading = false;
    await audio.play();
  } catch (e) {
    S.cartPlayers.delete(index);
    status(e.message);
    logEvent('ERROR', `Jingle ${label(file)} kan niet worden afgespeeld: ${e.message}`);
  }
  renderCart();
}
function stopCart(index) {
  const p = S.cartPlayers.get(index);
  if (p) { p.audio.pause(); unroute(p.audio); S.cartPlayers.delete(index); }
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
    route(p.audio, 'pfl');
    setNormalization(p.audio, file);
    await once(p.audio, 'loadedmetadata');
    p.audio.currentTime = cueIn(file);
    await p.audio.play();
  } catch (e) { status(e.message); p.fileId = null; updatePflButtons(); }
}
S.pfl.audio.addEventListener('ended', () => { S.pfl.fileId = null; updatePflButtons(); });

// ---------- playlist editing ----------

// One id or a list of ids (multi-select); a list keeps its order.
const newItems = (ids) => [].concat(ids).map((id) => ({ uid: newUid(), id, stopAfter: false, state: 'queued' }));
function addedStatus(prefix, items) {
  const f = S.files.get(items[0].id);
  status(items.length > 1 ? `${prefix}: ${items.length} nummers` : f ? `${prefix}: ${label(f)}` : prefix);
}
function addToPlaylist(ids, beforeUid) {
  const items = newItems(ids);
  const at = beforeUid ? S.playlist.findIndex((i) => i.uid === beforeUid) : -1;
  if (at >= 0) S.playlist.splice(at, 0, ...items); else S.playlist.push(...items);
  afterPlaylistChange();
  if (items.length > 1) addedStatus('Toegevoegd', items);
  return items[0];
}
// Insert right after the item that is on air (or before the first waiting item).
function addNext(ids) {
  const items = newItems(ids);
  const playing = S.live && S.live.item ? S.playlist.indexOf(S.live.item) : -1;
  const at = playing >= 0 ? playing + 1 : S.playlist.findIndex((i) => i.state === 'queued');
  if (at >= 0) S.playlist.splice(at, 0, ...items); else S.playlist.push(...items);
  afterPlaylistChange();
  addedStatus('Als volgende', items);
  return items[0];
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
    const { planned: [p] } = await api('POST', '/api/clocks/plan', {
      hours: [{ day: d.getDay(), hour: d.getHours() }], exclude,
      fallback: { collections: nonstopCollectionIds(), count: 15 },
    });
    S.settings.plannedUntil = start + hourMs;
    const when = d.toLocaleString('nl-NL', { weekday: 'short', hour: '2-digit', minute: '2-digit' });
    if (!p.clock) {
      if (manual || S.settings.clockAuto) status(`Niets te plannen voor ${when}: geen uurklok en geen muziek in de nonstop-collecties (⚙ Instellingen)`);
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
  $('btn-nonstop').classList.toggle('on', nonstopOn());
  $('btn-pause').classList.toggle('active', Boolean(S.live && S.live.state === 'paused'));
  $('onair-lamp').classList.toggle('live', Boolean(S.live && S.live.state === 'playing'));
}

function fmtDb(db) {
  const r = Math.round(db * 10) / 10;
  return `${r > 0 ? '+' : r < 0 ? '−' : '±'}${Math.abs(r).toFixed(1).replace('.', ',')} dB`;
}

function renderDecks() {
  for (const d of decks) {
    const el = $(`deck-${d.index}`);
    const err = d.error && Date.now() - d.error.at < 20000 ? d.error : null;
    const st = d.state === 'playing' ? (d === S.live ? 'playing' : 'tail') : d.state;
    const stateLabel = err && d.state === 'empty' ? 'FOUT' : { empty: 'LEEG', loading: 'LADEN…', cued: 'KLAAR', playing: d === S.live ? 'ON AIR' : 'UITLOOP', paused: 'PAUZE' }[d.state];
    el.className = `deck ${d.state === 'paused' ? 'playing' : d.state} st-${err && d.state === 'empty' ? 'error' : st}${d === S.live ? ' is-live' : ''}`;
    el.replaceChildren(
      h('div', { class: 'deck-head' }, h('span', {}, `PLAYER ${d.name}`), h('span', { class: 'deck-state' }, stateLabel)),
      ...(err ? [h('div', { class: 'deck-error', title: err.msg }, `⚠ Overgeslagen: ${err.msg}`)] : []),
      h('div', { class: 'deck-title' }, d.file ? d.file.title : '—'),
      h('div', { class: 'deck-artist' }, d.file ? d.file.artist || ' ' : ' ',
        d.file && S.settings.normalize && (d.chained || d.file.loudness_lufs != null) ? h('span', { class: 'deck-gain', title: d.chained ? 'Naadloos: zelfde volume als het vorige nummer' : 'Gelijk volume' }, `${d.chained ? '⇥ ' : ''}${fmtDb(20 * Math.log10(d.audio._norm || 1))}`) : null),
      h('div', { class: 'deck-bar', title: 'Klik of sleep om door te spoelen' }, h('div', { id: `deck-fill-${d.index}` })),
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
    if (item.state !== 'queued' || !f) { if (el) el.textContent = item.state === 'playing' ? '▶ NU' : ''; continue; }
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
      oncontextmenu: (e) => { if (f) { e.preventDefault(); fileMenu(e, f, { playlist: true }); } },
    },
    h('div', { class: 'pl-time' }),
    h('div', { class: 'pl-main' },
      h('div', { class: 'pl-title' }, f ? f.title : '(verwijderd bestand)', item.error ? ' ⚠' : '',
        f && f.segue ? h('span', { class: 'segue-mark', title: 'Naadloos: sluit strak aan op het volgende nummer' }, ' ⇥') : null),
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
  const fileIds = (e.dataTransfer.getData('text/aot-files') || e.dataTransfer.getData('text/aot-file')).split(',').map(Number).filter(Boolean);
  if (moved) movePlaylistItem(moved, beforeUid);
  else if (fileIds.length) { addToPlaylist(fileIds, beforeUid); clearSelection(); }
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
  const col = $('lib-collection').value;
  if (col === 'nonstop') params.set('nonstop', 'blocked');
  else if (col) params.set('collection_id', col);
  if ($('lib-search').value.trim()) params.set('q', $('lib-search').value.trim());
  if (libFilter.mode === 'new') params.set('sort', 'new');
  if (libFilter.mode === 'fav') params.set('ids', favIds().join(','));
  if (libFilter.genre) params.set('genre', libFilter.genre);
  return params;
}
// Keeps the copy the studio already has (it may carry fresher cue points), but takes
// over what only the server knows: genre and the nonstop filter mark.
const remember = (files) => files.map((f) => {
  const known = S.files.get(f.id);
  if (!known) { S.files.set(f.id, f); return f; }
  known.genre = f.genre; known.nonstop_blocked = f.nonstop_blocked; known.segue = f.segue;
  return known;
});

// New search: first page. Scrolling down loads the next pages, through the whole database.
async function searchLibrary() {
  const token = ++lib.token;
  if (libFilter.mode === 'fav' && !favIds().length) { lib.files = []; lib.total = 0; renderLibrary(); updateSelection(); return; }
  lib.loading = true;
  try {
    const { files, total } = await api('GET', `/api/files?${libraryParams(0)}`);
    if (token !== lib.token) return; // a newer search is already running
    lib.files = remember(files); lib.total = total;
    sel.ids.clear(); sel.anchor = null;
    renderLibrary();
    updateSelection();
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

// ---------- multi-select in the library ----------
// Click = select one, Ctrl+click = add/remove, Shift+click = range. The selection keeps
// the order of the list; ⤴ / + / dragging / right-click then work on all of them.
const sel = { ids: new Set(), anchor: null };
const selectedIds = () => lib.files.map((f) => f.id).filter((id) => sel.ids.has(id));
function selectRow(e, f) {
  if (e.target.closest('button')) return;
  if (e.shiftKey && sel.anchor !== null) {
    const ids = lib.files.map((x) => x.id);
    const a = ids.indexOf(sel.anchor); const b = ids.indexOf(f.id);
    if (a >= 0 && b >= 0) {
      if (!(e.ctrlKey || e.metaKey)) sel.ids.clear();
      for (let i = Math.min(a, b); i <= Math.max(a, b); i++) sel.ids.add(ids[i]);
    }
  } else if (e.ctrlKey || e.metaKey) {
    if (sel.ids.has(f.id)) sel.ids.delete(f.id); else sel.ids.add(f.id);
    sel.anchor = f.id;
  } else {
    const only = sel.ids.size === 1 && sel.ids.has(f.id);
    sel.ids.clear();
    if (!only) sel.ids.add(f.id);
    sel.anchor = f.id;
  }
  updateSelection();
}
function clearSelection() { if (sel.ids.size) { sel.ids.clear(); updateSelection(); } }
function updateSelection() {
  document.querySelectorAll('#library .lib-row').forEach((r) => r.classList.toggle('sel', sel.ids.has(Number(r.dataset.file))));
  const n = selectedIds().length;
  const bar = $('lib-selbar');
  bar.classList.toggle('idle', n < 1);
  showDetails(n === 1 ? selectedIds()[0] : null);
  if (n < 1) bar.replaceChildren(h('span', {}, 'Klik = kiezen (info onderaan) · Ctrl/Shift+klik = meer · dubbelklik = achteraan'));
  if (n >= 1) {
    bar.replaceChildren(
      h('span', {}, n === 1 ? `✓ 1 nummer geselecteerd — Ctrl/Shift+klik voor meer` : `✓ ${n} nummers geselecteerd`),
      h('button', { class: 'mini', onclick: () => { addNext(selectedIds()); clearSelection(); } }, '⤴ Als volgende'),
      h('button', { class: 'mini', onclick: () => { addToPlaylist(selectedIds()); clearSelection(); } }, '+ Achteraan'),
      h('button', { class: 'mini', title: 'Selectie opheffen (Esc)', onclick: clearSelection }, '✕'));
  }
}
// The row's own buttons act on the whole selection when that row is part of it.
const targetIds = (f) => (sel.ids.has(f.id) && sel.ids.size > 1 ? selectedIds() : [f.id]);

function libraryRow(f) {
  return h('div', {
    class: `lib-row${sel.ids.has(f.id) ? ' sel' : ''}`, draggable: 'true', 'data-file': f.id,
    onclick: (e) => selectRow(e, f),
    onmousedown: (e) => { if (e.shiftKey) e.preventDefault(); }, // no text selection on Shift+click
    ondragstart: (e) => {
      const ids = targetIds(f);
      e.dataTransfer.setData('text/aot-file', String(f.id));
      if (ids.length > 1) e.dataTransfer.setData('text/aot-files', ids.join(','));
      e.dataTransfer.effectAllowed = 'copy';
    },
    ondblclick: (e) => { if (!e.target.closest('button')) { addToPlaylist(f.id); clearSelection(); } },
    oncontextmenu: (e) => { e.preventDefault(); fileMenu(e, f); },
  },
  h('div', { class: 'pl-main' },
    h('div', { class: 'pl-title' }, isFav(f.id) ? h('span', { class: 'fav-mark', title: 'Favoriet' }, '★ ') : null,
      f.nonstop_blocked ? h('span', { class: 'ns-blocked', title: 'Komt niet in de nonstop (uurklok)' }, '🚫 ') : null, f.title,
      f.segue ? h('span', { class: 'segue-mark', title: 'Naadloos: sluit strak aan op het volgende nummer' }, ' ⇥') : null),
    h('div', { class: 'pl-artist' }, f.artist || ' ', f.genre ? h('span', { class: 'lib-genre' }, ` · ${f.genre}`) : null)),
  h('div', { class: 'pl-dur' }, fmt(cueOut(f) !== null ? cueOut(f) - cueIn(f) : null)),
  h('div', { class: 'lib-actions' },
    h('button', { title: 'Voorbeluisteren', 'data-pfl': f.id, class: S.pfl.fileId === f.id ? 'pfl-on' : '', onclick: () => togglePfl(f) }, '🎧'),
    h('button', { title: 'Als volgende afspelen', onclick: (e) => { addNext(targetIds(f)); flashRow(e.target); clearSelection(); } }, '⤴'),
    h('button', { title: 'Achteraan de playlist', onclick: (e) => { addToPlaylist(targetIds(f)); flashRow(e.target); clearSelection(); } }, '+')));
}
function flashRow(el) {
  const row = el.closest('.lib-row');
  row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
}

function renderLibrary() {
  updateLibraryCount();
  $('library').replaceChildren(...(lib.files.length ? [...lib.files.map(libraryRow), ...libraryTail()]
    : [h('div', { class: 'empty' }, libFilter.mode === 'fav' && !favIds().length ? 'Nog geen favorieten. Rechtsklik op een nummer → ★ Favoriet.'
      : $('lib-search').value.trim() || $('lib-collection').value || libFilter.mode !== 'all' || libFilter.genre ? 'Niets gevonden.' : 'De database is nog leeg. Upload muziek via ☰ Bibliotheek.')]));
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
function fileMenu(e, f, { playlist = false } = {}) {
  const menu = $('slot-menu');
  const close = () => { menu.classList.add('hidden'); menu.classList.remove('ctx'); document.removeEventListener('mousedown', outside); };
  const outside = (ev) => { if (!menu.contains(ev.target)) close(); };
  const item = (text, fn, cls) => h('button', { class: cls || '', onclick: () => { close(); fn(); } }, text);
  menu.classList.add('ctx');
  const ids = playlist ? [f.id] : targetIds(f);
  if (ids.length > 1) {
    menu.replaceChildren(
      h('div', { class: 'ctx-title' }, `${ids.length} nummers geselecteerd`),
      item('⤴  Als volgende afspelen', () => { addNext(ids); clearSelection(); }),
      item('+  Achteraan de playlist', () => { addToPlaylist(ids); clearSelection(); }),
      item(`🎵  Op jingle-knoppen zetten (${ids.length})`, () => assignToCart(ids)),
      item(`★  Favoriet maken (${ids.length})`, () => { setFav(ids, true); clearSelection(); }),
      item(`🚫  Niet in nonstop (${ids.length} nummers)`, () => { addNonstopBlocks(ids.map((id) => ({ kind: 'file', value: id }))); clearSelection(); }),
      item(`⇥  Naadloos aansluiten aan (${ids.length} nummers)`, () => { setSegue(ids, true); clearSelection(); }),
      item('⇥  Naadloos aansluiten uit', () => { setSegue(ids, false); clearSelection(); }),
      item('✕  Selectie opheffen', clearSelection));
  } else if (playlist) menu.replaceChildren(
    h('div', { class: 'ctx-title' }, label(f)),
    item('⚑  Probleem melden…', () => openReport(f.id)),
    ...nonstopMenuItems(f, item));
  else menu.replaceChildren(
    h('div', { class: 'ctx-title' }, label(f)),
    item('⤴  Als volgende afspelen', () => addNext(f.id)),
    item('+  Achteraan de playlist', () => addToPlaylist(f.id)),
    item('▶  Direct afspelen', () => playNow(f.id), 'live'),
    item('🎧  Voorbeluisteren', () => togglePfl(f)),
    item('🎵  Op een jingle-knop zetten', () => assignToCart([f.id])),
    item(isFav(f.id) ? '☆  Uit favorieten' : '★  Favoriet', () => setFav([f.id], !isFav(f.id))),
    item('⚑  Probleem melden…', () => openReport(f.id)),
    ...nonstopMenuItems(f, item));
  menu.style.left = `${Math.min(e.clientX, innerWidth - 240)}px`;
  menu.style.top = `${Math.min(e.clientY, innerHeight - 200)}px`;
  menu.classList.remove('hidden');
  setTimeout(() => document.addEventListener('mousedown', outside), 0);
}

// ---------- nonstop filter ----------
// Tracks, artists, genres and folders that the uurklok never plans for this person.
// Adding them by hand still works; they are only kept out of the automatic hours.
const NONSTOP_KINDS = { genre: 'Genre', artist: 'Artiest', folder: 'Map / bestandsnaam', file: 'Nummer' };
let nonstopBlocks = [];
async function loadNonstopBlocks() {
  try { nonstopBlocks = (await api('GET', '/api/me/nonstop-blocks')).blocks; } catch { nonstopBlocks = []; }
  return nonstopBlocks;
}
async function addNonstopBlocks(list) {
  try {
    for (const b of list) await api('POST', '/api/me/nonstop-blocks', b);
    const what = list.length > 1 ? `${list.length} nummers` : list[0].kind === 'file' ? 'Nummer' : `${NONSTOP_KINDS[list[0].kind]} "${list[0].value}"`;
    status(`🚫 ${what} komt niet meer in de nonstop`);
  } catch (e) { status(e.message); }
  await loadNonstopBlocks();
  renderNonstopBlocks();
  searchLibrary();
}
async function removeNonstopBlock(id) {
  try { await api('DELETE', `/api/me/nonstop-blocks/${id}`); } catch (e) { status(e.message); }
  await loadNonstopBlocks();
  renderNonstopBlocks();
  searchLibrary();
}
// Naadloos aansluiten: for tracks that flow into each other on the recording.
async function setSegue(ids, on) {
  try {
    const { files } = await api('PUT', '/api/files/segue', { ids, segue: on });
    for (const r of files) { const f = S.files.get(r.id); if (f) f.segue = r.segue; }
    status(`⇥ Naadloos ${on ? 'aan' : 'uit'}: ${files.length === 1 && S.files.get(files[0].id) ? label(S.files.get(files[0].id)) : `${files.length} nummers`}`);
    // A cued player moves to the new start; a playing one keeps its position and the
    // new end/mix point applies right away.
    for (const d of decks) if (d.state === 'cued' && d.file && ids.includes(d.file.id)) d.audio.currentTime = cueIn(d.file);
    document.querySelectorAll('#library .lib-row').forEach((r) => {
      const f = S.files.get(Number(r.dataset.file));
      if (f && ids.includes(f.id)) r.replaceWith(libraryRow(f));
    });
    render();
  } catch (e) { status(e.message); }
}
function nonstopMenuItems(f, item) {
  const fileBlock = nonstopBlocks.find((b) => b.kind === 'file' && Number(b.value) === f.id);
  const out = [h('div', { class: 'ctx-sep' })];
  out.push(f.segue
    ? item('⇥  Naadloos aansluiten uitzetten', () => setSegue([f.id], false))
    : item('⇥  Naadloos aansluiten (live-opname / mix)', () => setSegue([f.id], true)));
  if (fileBlock) out.push(item('✓  Weer toestaan in de nonstop', () => removeNonstopBlock(fileBlock.id)));
  else out.push(item('🚫  Dit nummer niet in de nonstop', () => addNonstopBlocks([{ kind: 'file', value: f.id }])));
  if (f.artist) out.push(item(`🚫  Artiest niet in de nonstop: ${f.artist}`, () => addNonstopBlocks([{ kind: 'artist', value: f.artist }])));
  for (const g of (f.genre || '').split(/\s*[,;/]\s*/).filter(Boolean).slice(0, 3)) {
    out.push(item(`🚫  Genre niet in de nonstop: ${g}`, () => addNonstopBlocks([{ kind: 'genre', value: g }])));
  }
  return out;
}
function renderNonstopBlocks() {
  const list = $('nonstop-list');
  if (!list) return;
  list.replaceChildren(...(nonstopBlocks.length ? nonstopBlocks.map((b) => h('div', { class: 'ns-rule' },
    h('span', { class: 'ns-kind' }, NONSTOP_KINDS[b.kind]),
    h('span', { class: 'ns-value' }, b.kind === 'file' ? (b.title ? `${b.artist ? `${b.artist} – ` : ''}${b.title}` : '(verwijderd nummer)') : b.value),
    h('button', { type: 'button', class: 'mini', title: 'Weer toestaan', onclick: () => removeNonstopBlock(b.id) }, '✕')))
    : [h('p', { class: 'muted', style: { margin: '4px 0' } }, 'Nog niets gefilterd: alles kan in de nonstop.')]));
}
async function renderNonstopGenres() {
  try {
    const { genres } = await api('GET', '/api/genres');
    $('nonstop-genres').replaceChildren(...genres.map((g) => h('option', { value: g.genre }, `${g.count} nummers`)));
  } catch { /* the list is only a help */ }
}

// Put tracks on jingle buttons: the first at `from` (or the first free button on this
// page), the rest on the next free buttons.
function assignToCart(ids, from = null) {
  const slots = cartSlots();
  const { count } = cartDims();
  const page = Array.from({ length: count }, (_, i) => cartIndex(i));
  let free = page.filter((i) => !slots[i] || !S.files.get(slots[i].id));
  if (from !== null) free = [from, ...free.filter((i) => i !== from && page.indexOf(i) > page.indexOf(from)), ...free.filter((i) => page.indexOf(i) < page.indexOf(from))];
  let placed = 0;
  for (const id of ids) {
    const at = free.shift();
    if (at === undefined) break;
    slots[at] = { id, color: (slots[at] && slots[at].color) || SLOT_COLORS[at % SLOT_COLORS.length] };
    placed++;
  }
  if (!placed) { status('Geen lege jingle-knop meer op deze pagina — kies pagina B, C of D'); return; }
  status(`🎵 ${placed} ${placed === 1 ? 'jingle' : 'jingles'} op het jingle paneel gezet${placed < ids.length ? ` (${ids.length - placed} pasten niet meer)` : ''}`);
  clearSelection();
  saveSettingsSoon(); preloadCartPage(); renderCart();
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
    if (!file) {
      return h('div', {
        ...common, class: 'slot empty-slot', title: 'Kies een jingle in de database en klik hier (of sleep hem hierheen)',
        onclick: () => {
          const ids = selectedIds();
          if (!ids.length) { status('Selecteer eerst een of meer jingles in de database (klik erop) en klik dan op een lege knop — of sleep een jingle hierheen'); return; }
          assignToCart(ids, index);
        },
      }, slot ? '(bestand weg)' : '+');
    }
    return h('button', {
      ...common,
      class: `slot${player ? ' playing' : ''}${player && player.loading ? ' loading' : ''}`,
      style: { '--slot': slot.color },
      onclick: () => cartTrigger(index),
    },
    h('div', { class: 'slot-fill' }),
    h('div', { class: 'slot-key' }, key),
    h('div', { class: 'slot-title' }, file.title),
    h('div', { class: 'slot-foot' }, h('span', { class: 'slot-cat' }, slotCategory(slot, file)),
      h('div', { class: 'slot-time' }, fmt(cueOut(file) !== null ? cueOut(file) - cueIn(file) : null))));
  }));
}

function slotMenu(e, index) {
  const menu = $('slot-menu');
  menu.classList.remove('ctx');
  const close = () => { menu.classList.add('hidden'); document.removeEventListener('mousedown', outside); };
  const outside = (ev) => { if (!menu.contains(ev.target)) close(); };
  menu.replaceChildren(
    ...SLOT_COLORS.map((c) => h('button', { class: 'sw', style: { background: c }, onclick: () => { cartSlots()[index].color = c; saveSettingsSoon(); renderCart(); close(); } })),
    ...(selectedIds().length === 1 ? [h('button', { class: 'mini wide', onclick: () => { replaceSlot(index, selectedIds()[0]); close(); } }, 'Vervangen door selectie')] : []),
    h('button', { class: 'mini wide', onclick: () => { close(); renameSlotCategory(index); } }, 'Categorie…'),
    h('button', { class: 'mini wide', onclick: () => { close(); showSlotInfo(index); } }, 'Eigenschappen'),
    h('button', { class: 'mini wide', onclick: () => {
      close();
      const f = S.files.get(cartSlots()[index].id);
      if (!confirm(`Jingle "${f ? f.title : ''}" van deze knop halen?\n(Het bestand zelf blijft gewoon in de database.)`)) return;
      stopCart(index); cartSlots()[index] = null; saveSettingsSoon(); renderCart();
      logEvent('INFO', `Jingle-knop ${index + 1} leeggemaakt${f ? `: ${f.title}` : ''}`);
    } }, 'Leegmaken…'));
  menu.style.left = `${Math.min(e.clientX, innerWidth - 180)}px`;
  menu.style.top = `${Math.min(e.clientY, innerHeight - 230)}px`;
  menu.classList.remove('hidden');
  setTimeout(() => document.addEventListener('mousedown', outside), 0);
}

function renderOutputsStatus() {
  const sinks = canPickOutput();
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

async function renderLoudnessStats() {
  try {
    const stats = await api('GET', '/api/library/stats');
    $('loudness-stats').textContent = stats.count
      ? `Gemeten: ${stats.measured.toLocaleString('nl-NL')} van ${stats.count.toLocaleString('nl-NL')} nummers${stats.measured < stats.count ? ' (de rest wordt op de achtergrond gemeten)' : ''}.`
      : '';
  } catch { $('loudness-stats').textContent = ''; }
}

async function renderSettings() {
  const st = S.settings;
  $('set-station').value = st.stationName;
  $('set-crossfade').value = st.crossfade;
  $('set-fadeout').value = st.fadeOut;
  $('set-autocue').checked = st.autoCue;
  $('set-normalize').checked = st.normalize;
  $('set-target').value = String(st.loudnessTarget);
  renderLoudnessStats();
  $('set-quality').value = st.quality || 'auto';
  $('set-clockauto').checked = st.clockAuto;
  const nsIds = new Set(nonstopCollectionIds());
  $('set-nonstop-collections').replaceChildren(...S.collections.map((c) => h('label', { class: 'check' },
    h('input', { type: 'checkbox', checked: nsIds.has(c.id), onchange: () => {
      S.settings.nonstopCollections = [...document.querySelectorAll('#set-nonstop-collections input')]
        .map((el, i) => (el.checked ? S.collections[i].id : null)).filter((id) => id !== null);
      saveSettingsSoon();
    } }), ` ${c.name}`)));
  loadNonstopBlocks().then(renderNonstopBlocks);
  renderNonstopGenres();
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

  const sinks = canPickOutput();
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
      setOutput(key);
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
  $('set-clockauto').addEventListener('change', (e) => { S.settings.clockAuto = e.target.checked; saveSettingsSoon(); render(); });
  $('set-quality').addEventListener('change', (e) => {
    S.settings.quality = e.target.value; saveSettingsSoon(); renderQuality();
    // New loads use the new quality; what is already loaded keeps playing.
    blobCache.clear();
    cueNext(); preloadCartPage();
  });
  $('nonstop-add').addEventListener('click', () => {
    const value = $('nonstop-value').value.trim();
    if (value.length < 2) { $('nonstop-value').focus(); return; }
    addNonstopBlocks([{ kind: $('nonstop-kind').value, value }]);
    $('nonstop-value').value = '';
  });
  $('nonstop-value').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); $('nonstop-add').click(); } });
  $('nonstop-kind').addEventListener('change', (e) => { $('nonstop-value').setAttribute('list', e.target.value === 'genre' ? 'nonstop-genres' : ''); });
  $('nonstop-show').addEventListener('click', () => { $('settings').close(); $('lib-collection').value = 'nonstop'; $('lib-search').value = ''; searchLibrary(); });
  $('set-autocue').addEventListener('change', (e) => { S.settings.autoCue = e.target.checked; saveSettingsSoon(); });
  $('set-normalize').addEventListener('change', (e) => { S.settings.normalize = e.target.checked; saveSettingsSoon(); reapplyNormalization(); });
  $('set-target').addEventListener('change', (e) => { S.settings.loudnessTarget = Number(e.target.value); saveSettingsSoon(); reapplyNormalization(); });
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
  // No studio shortcuts while a window (settings, jingle info, report, wishes, …) is open.
  if (document.querySelector('dialog[open]:not(#dashboard)')) return;
  const k = e.key.toLowerCase();
  if (k === 'd') { toggleDashboard(); return; }
  if ($('dashboard').open) return;
  if (k === 'escape') clearSelection();
  else if (k === ' ') { e.preventDefault(); cmdStart(); }
  else if (k === 'n') cmdStart();
  else if (k === 'p') cmdPause();
  else if (k === 'f') cmdFade();
  else if (k === 'a') cmdAuto();
  else if (k === 'o') cmdNonstop();
  else if (k === 'arrowright') { e.preventDefault(); seekBy(e.shiftKey ? 30 : 5); }
  else if (k === 'arrowleft') { e.preventDefault(); seekBy(e.shiftKey ? -30 : -5); }
  else if (/^[0-9]$/.test(k)) {
    const i = k === '0' ? 9 : Number(k) - 1;
    if (i < cartDims().count) cartTrigger(cartIndex(i));
  }
});

// ---------- start-up ----------

for (const d of decks) {
  $(`deck-${d.index}`).addEventListener('pointerdown', (e) => {
    const bar = e.target.closest('.deck-bar');
    if (bar) seekable(() => $(`deck-${d.index}`).querySelector('.deck-bar'), () => d)(e);
  });
}
$('now').querySelector('.now-bar').addEventListener('pointerdown', seekable(() => $('now').querySelector('.now-bar'), () => S.live));

// Update icon (only in the Windows app): red dot when a new version is out; one
// click downloads, installs and restarts, and the server is updated afterwards.
function setupUpdateButton() {
  const u = window.onairUpdate;
  const btn = $('btn-update');
  if (!u) return;
  btn.classList.remove('hidden');
  const show = (st) => {
    btn.classList.toggle('has-update', Boolean(st.available));
    btn.title = st.installing ? 'Update wordt geïnstalleerd…'
      : st.downloading !== undefined ? `Update downloaden… ${st.downloading}%`
        : st.available ? `Nieuwe versie ${st.version} — klik om bij te werken` : `Je hebt de nieuwste versie (${st.current})`;
    if (st.downloading !== undefined) status(`⬆ Update ${st.version} downloaden… ${st.downloading}%`);
    if (st.installing) status('⬆ Update installeren — de app start zo opnieuw');
    if (st.error) status(`Bijwerken mislukt: ${st.error}`);
  };
  u.onStatus(show);
  u.check().then(show).catch(() => {});
  btn.addEventListener('click', async () => {
    const st = await u.check();
    if (st.ok && !st.available) { show(st); status(`✓ Je hebt de nieuwste versie (${st.current})`); return; }
    if (!st.ok) { status(`Kon niet controleren op updates: ${st.error}`); return; }
    await u.install();
  });
}

// ---------- problem reports ----------
// Anyone drags a track (or right-clicks it) to report what is wrong with it; the
// owner/admins see a ⚑ with the number of open reports and fix them in the library.
const REPORT_REASONS = [
  ['quality', 'Slechte geluidskwaliteit'], ['tags', 'Verkeerde titel of artiest'], ['cue', 'Begint of stopt verkeerd'],
  ['volume', 'Te zacht of te hard'], ['wrong', 'Verkeerd nummer / kapot bestand'], ['other', 'Anders'],
];
let reportFile = null;
function openReport(fileId) {
  const f = S.files.get(fileId);
  if (!f) return;
  reportFile = f;
  $('report-track').textContent = label(f);
  $('report-note').value = '';
  $('report-reasons').replaceChildren(...REPORT_REASONS.map(([value, text], i) => h('label', { class: 'check' },
    h('input', { type: 'radio', name: 'report-reason', value, checked: i === 0 }), ` ${text}`)));
  $('report').showModal();
}
async function sendReport() {
  const reason = (document.querySelector('input[name="report-reason"]:checked') || {}).value;
  try {
    await api('POST', `/api/files/${reportFile.id}/report`, { reason, note: $('report-note').value });
    $('report').close();
    status(`⚑ Doorgegeven aan de beheerder: ${label(reportFile)}`);
  } catch (e) { status(`Melden mislukt: ${e.message}`); }
}
// ---------- music wishes ----------
const WISH_STATUS = { open: ['Aangevraagd', 'open'], added: ['✓ Toegevoegd', 'added'], rejected: ['Niet mogelijk', 'rejected'] };
async function renderWishes() {
  try {
    const { wishes } = await api('GET', '/api/wishes/mine');
    $('wish-list').replaceChildren(...(wishes.length ? wishes.map((w) => h('div', { class: 'wish-row' },
      h('div', {}, h('strong', {}, [w.artist, w.title].filter(Boolean).join(' – ')),
        w.note ? h('div', { class: 'muted' }, w.note) : null,
        w.reply ? h('div', { class: 'wish-reply' }, `Beheerder: ${w.reply}`) : null),
      h('span', { class: `wish-status ${WISH_STATUS[w.status][1]}` }, WISH_STATUS[w.status][0]),
      w.status === 'open' ? h('button', { type: 'button', class: 'mini', title: 'Wens intrekken', onclick: async () => { await api('DELETE', `/api/wishes/${w.id}`); renderWishes(); } }, '✕') : h('span')))
      : [h('p', { class: 'muted', style: { margin: '4px 0' } }, 'Nog geen wensen doorgegeven.')]));
  } catch (e) { $('wish-list').textContent = e.message; }
}
function openWishes() {
  $('wish-msg').textContent = '';
  $('wishes').showModal();
  renderWishes();
  $('wish-artist').focus();
}
async function sendWish() {
  try {
    await api('POST', '/api/wishes', { artist: $('wish-artist').value, title: $('wish-title').value, note: $('wish-note').value });
    $('wish-msg').textContent = '✓ Doorgegeven aan de beheerder';
    $('wish-artist').value = ''; $('wish-title').value = ''; $('wish-note').value = '';
    $('wish-artist').focus();
    renderWishes();
  } catch (e) { $('wish-msg').textContent = e.message; }
}

function setupReports() {
  $('btn-wishes').addEventListener('click', openWishes);
  $('wish-send').addEventListener('click', sendWish);
  for (const id of ['wish-artist', 'wish-title', 'wish-note']) $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); sendWish(); } });
  const drop = $('report-drop');
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault(); drop.classList.remove('over');
    const uid = e.dataTransfer.getData('text/aot-item');
    const item = uid && S.playlist.find((i) => i.uid === uid);
    const id = item ? item.id : Number(e.dataTransfer.getData('text/aot-file'));
    if (id) openReport(id);
  });
  $('report-send').addEventListener('click', sendReport);
  $('report-cancel').addEventListener('click', () => $('report').close());
  if (!S.me || !['owner', 'admin'].includes(S.me.role)) return;
  // Admins: badge with the number of open reports, and a notice when a new one comes in.
  let last = null;
  const check = async () => {
    try {
      const { open, reports, wishes } = await api('GET', '/api/reports/count');
      $('btn-reports').classList.toggle('hidden', !open);
      $('reports-count').textContent = open ? String(open) : '';
      $('btn-reports').title = `${reports} ${reports === 1 ? 'melding' : 'meldingen'} en ${wishes} muziek${wishes === 1 ? 'wens' : 'wensen'} — klik om ze te bekijken`;
      if (last !== null && open > last) {
        status(`⚑ Nieuw: ${reports} melding(en), ${wishes} muziekwens(en) open`);
        try { if (Notification.permission === 'granted') new Notification('Audio OnAir Turbo Database', { body: 'Er is een nieuwe melding of muziekwens.' }); } catch { /* no notifications */ }
      }
      last = open;
    } catch { /* try again later */ }
  };
  try { if (typeof Notification !== 'undefined' && Notification.permission === 'default') Notification.requestPermission(); } catch { /* ignore */ }
  check();
  setInterval(check, 60 * 1000);
}

async function boot() {
  setupUpdateButton();
  try { S.me = (await api('GET', '/api/me')).user; } catch { location.href = '/'; return; }
  const [{ settings }, { collections }] = await Promise.all([api('GET', '/api/me/settings'), api('GET', '/api/collections')]);
  S.settings = { ...DEFAULT_SETTINGS, ...settings };
  S.collections = collections;
  loadNonstopBlocks();
  setupReports();
  await ensureFiles([...(S.settings.playlist || []).map((i) => i.id), ...(S.settings.cart || []).filter(Boolean).map((s) => s.id)]);
  S.playlist = (S.settings.playlist || []).filter((i) => i.marker || S.files.has(i.id))
    .map((i) => (i.marker
      ? { uid: newUid(), marker: i.marker, color: i.color, state: i.played ? 'played' : 'queued' }
      : { uid: newUid(), id: i.id, stopAfter: Boolean(i.stopAfter), state: i.played ? 'played' : 'queued' }));

  $('lib-collection').replaceChildren(h('option', { value: '' }, 'Alle collecties'), ...collections.map((c) => h('option', { value: c.id }, c.name)),
    h('option', { value: 'nonstop' }, '🚫 Niet in nonstop'));
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
  $('btn-nonstop').addEventListener('click', cmdNonstop);
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
  initExtras();
  applyTheme();
  renderOutputsStatus();
  renderQuality();
  searchLibrary();
  checkNewMusic();
  setInterval(checkNewMusic, 60 * 1000);
  render();
  cueNext();
  preloadCartPage();
}

// ---------- event log ----------
// Kept in this browser (last 500 lines, older ones drop off), so it never grows without
// limit and writing it never touches the server or the audio.
const LOG_MAX = 500;
const eventLog = readLocal('aot_eventlog', []).slice(-LOG_MAX);
let logUnseen = { WARNING: 0, ERROR: 0 };
let logSaveTimer = null;
function logEvent(level, msg) {
  eventLog.push({ t: Date.now(), level, msg: String(msg).slice(0, 300) });
  if (eventLog.length > LOG_MAX) eventLog.splice(0, eventLog.length - LOG_MAX);
  if (!logSaveTimer) logSaveTimer = setTimeout(() => { logSaveTimer = null; writeLocal('aot_eventlog', eventLog); }, 3000);
  if (level !== 'INFO' && $('logpanel').classList.contains('hidden')) logUnseen[level]++;
  if (!$('logpanel').classList.contains('hidden')) renderLog(); else renderLogBadge();
}
function renderLogBadge() {
  const n = logUnseen.WARNING + logUnseen.ERROR;
  const b = $('log-badge');
  b.textContent = n > 99 ? '99+' : String(n);
  b.className = `log-badge${n ? '' : ' hidden'}${!logUnseen.ERROR && n ? ' warn' : ''}`;
}
const fmtTime = (t) => new Date(t).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
function renderLog() {
  const problems = $('log-problems').checked;
  const rows = eventLog.filter((e) => !problems || e.level !== 'INFO').slice(-300).reverse();
  $('log-count').textContent = `${rows.length} regels${problems ? ' (alleen problemen)' : ''} · max. ${LOG_MAX} bewaard`;
  $('log-list').replaceChildren(...(rows.length ? rows.map((e) => h('div', { class: `log-row ${e.level}` },
    h('span', {}, fmtTime(e.t)), h('span', { class: 'lv' }, e.level), h('span', { class: 'm' }, e.msg)))
    : [h('div', { class: 'muted' }, 'Nog niets gelogd.')]));
}
function toggleLog(open = $('logpanel').classList.contains('hidden')) {
  $('logpanel').classList.toggle('hidden', !open);
  if (open) { logUnseen = { WARNING: 0, ERROR: 0 }; renderLog(); }
  renderLogBadge();
}

// ---------- system status ----------
// Only real checks: the server and database answer /api/status, the audio outputs are
// watched for device errors and checked that their clock really runs.
const OUTPUT_NAMES = Object.fromEntries(OUTPUTS);
const outputName = (key) => OUTPUT_NAMES[key] || key;
const outState = {};
function watchOutput(key) {
  const ctx = contexts[key];
  ctx.addEventListener('error', () => {
    (outState[key] ||= {}).failedAt = Date.now();
    logEvent('ERROR', `Geluidsuitgang ${outputName(key)}: fout van het audioapparaat of de audio-engine`);
    renderSystem();
  });
}
const sys = { server: null, db: null, clients: null, checking: false, devices: [] };
async function checkSystem() {
  if (sys.checking) return;
  sys.checking = true;
  const t0 = performance.now();
  let server, db = null, clients = null;
  try {
    const r = await fetch('/api/status', { cache: 'no-store', signal: AbortSignal.timeout(6000) });
    const ms = Math.round(performance.now() - t0);
    if (r.status === 401) server = { ok: false, error: 'niet meer ingelogd' };
    else if (!r.ok) server = { ok: false, error: `fout ${r.status}` };
    else { const data = await r.json(); server = { ok: true, ms }; db = data.db; clients = data.active_clients; }
  } catch (e) {
    server = { ok: false, error: e.name === 'TimeoutError' ? 'geen antwoord binnen 6 seconden' : 'niet bereikbaar' };
  } finally { sys.checking = false; }
  if (sys.server && sys.server.ok !== server.ok) logEvent(server.ok ? 'INFO' : 'ERROR', server.ok ? `Server weer bereikbaar (${server.ms} ms)` : `Server: ${server.error}`);
  else if (!sys.server && !server.ok) logEvent('ERROR', `Server: ${server.error}`);
  if (db && sys.db && sys.db.ok !== db.ok) logEvent(db.ok ? 'INFO' : 'ERROR', db.ok ? 'Database weer bereikbaar' : `Database: ${db.error}`);
  else if (db && !sys.db && !db.ok) logEvent('ERROR', `Database: ${db.error}`);
  sys.server = server; sys.db = db; sys.clients = clients;
  renderSystem();
}
// Audio: the players' outputs (the on-air path). An output that reported a device error
// counts as faulty until its clock is seen running again.
function audioHealth() {
  const keys = ['A', 'B'].filter((k) => contexts[k]);
  if (!keys.length) return { level: '', text: 'nog niet gebruikt' };
  const problems = [];
  for (const k of keys) {
    const c = contexts[k];
    const o = outState[k] ||= {};
    const now = c.currentTime;
    const running = c.state === 'running' && o.lastTime !== undefined && now > o.lastTime;
    o.lastTime = now;
    if (running && o.failedAt) { o.failedAt = null; logEvent('INFO', `Geluidsuitgang ${outputName(k)} werkt weer`); }
    if (c.state === 'closed') problems.push(`${outputName(k)} gesloten`);
    else if (o.failedAt) problems.push(`${outputName(k)}: apparaatfout`);
  }
  const live = S.live && S.live.state === 'playing' ? S.live : null;
  if (live && live.audio.error) problems.push(`Player ${live.name}: afspeelfout`);
  if (live && live.audio._ctx && live.audio._ctx.state !== 'running') problems.push(`Player ${live.name}: uitgang ${live.audio._ctx.state}`);
  if (problems.length) return { level: 'bad', text: problems.join(' · ') };
  const ctx = contexts[keys[0]];
  const id = S.outputs.A || '';
  const dev = sys.devices.find((d) => d.deviceId === id);
  const device = id ? (dev && dev.label) || 'gekozen geluidskaart' : 'standaard geluidskaart';
  return { level: 'ok', text: `OK · ${(ctx.sampleRate / 1000).toLocaleString('nl-NL')} kHz · ${device}`, rate: ctx.sampleRate, device };
}
function setSys(id, level, text) {
  const el = $(id);
  el.className = `sys ${level}`;
  el.querySelector('b').textContent = text;
}
function renderSystem() {
  const s = sys.server;
  setSys('sys-server', !s ? '' : s.ok ? (s.ms > 800 ? 'warn' : 'ok') : 'bad', !s ? '…' : s.ok ? `${s.ms} ms` : s.error);
  const d = sys.db;
  setSys('sys-db', !s ? '' : !s.ok || !d ? 'warn' : d.ok ? 'ok' : 'bad', !s ? '…' : !s.ok || !d ? 'onbekend' : d.ok ? `OK (${d.ms} ms)` : d.error);
  const a = audioHealth();
  setSys('sys-audio', a.level, a.text);
  const c = $('sys-clients');
  c.classList.toggle('hidden', sys.clients === null || sys.clients === undefined);
  if (sys.clients !== null && sys.clients !== undefined) c.querySelector('b').textContent = String(sys.clients);
}
async function refreshDevices() {
  try { sys.devices = await listDevices(); } catch { sys.devices = []; }
}

// ---------- favourites and quick filters ----------
// Favourites live in the studio settings (per person), like the playlist and jingles.
const favIds = () => (Array.isArray(S.settings.favorites) ? S.settings.favorites : []);
const isFav = (id) => favIds().includes(id);
function setFav(ids, on) {
  const set = new Set(favIds());
  for (const id of ids) { if (on) set.add(id); else set.delete(id); }
  S.settings.favorites = [...set].slice(-1000);
  saveSettingsSoon();
  status(on ? `★ ${ids.length === 1 ? 'Favoriet' : `${ids.length} favorieten`} toegevoegd` : '☆ Uit favorieten');
  for (const id of ids) {
    const row = document.querySelector(`#library .lib-row[data-file="${id}"]`);
    const f = S.files.get(id);
    if (row && f) row.replaceWith(libraryRow(f));
  }
  if (libFilter.mode === 'fav') searchLibrary();
  if (details.id && ids.includes(details.id)) renderDetails();
}
const libFilter = { mode: 'all', genre: '' };
function bindQuickFilters() {
  document.querySelectorAll('#lib-quick .qf').forEach((b) => b.addEventListener('click', () => {
    libFilter.mode = b.dataset.mode;
    document.querySelectorAll('#lib-quick .qf').forEach((x) => x.classList.toggle('on', x === b));
    searchLibrary();
  }));
  $('lib-genre').addEventListener('change', () => { libFilter.genre = $('lib-genre').value; searchLibrary(); });
  loadGenres();
}
// Genres come from the tags in the database itself, so new genres show up by themselves.
async function loadGenres() {
  try {
    const { genres } = await api('GET', '/api/genres');
    const keep = $('lib-genre').value;
    $('lib-genre').replaceChildren(h('option', { value: '' }, 'Alle genres'), ...genres.map((g) => h('option', { value: g.genre }, `${g.genre} (${g.count.toLocaleString('nl-NL')})`)));
    $('lib-genre').value = keep;
  } catch { /* keep the list as it is */ }
}

// ---------- track details ----------
const details = { id: null, data: null, timer: null, token: 0 };
// Shown a moment after a single click, so a double-click never lands on a moved row.
function showDetails(id) {
  clearTimeout(details.timer);
  if (!id) { details.id = null; $('lib-details').classList.add('hidden'); return; }
  details.timer = setTimeout(async () => {
    const token = ++details.token;
    details.id = id; details.data = null;
    renderDetails();
    try {
      const data = await api('GET', `/api/files/${id}/details`);
      if (token === details.token) { details.data = data; renderDetails(); }
    } catch (e) { if (token === details.token) { details.data = { error: e.message }; renderDetails(); } }
  }, 450);
}
const kHz = (hz) => `${(hz / 1000).toLocaleString('nl-NL')} kHz`;
function detailRows(f, d) {
  const len = cueOut(f) !== null ? cueOut(f) - cueIn(f) : f.duration_seconds;
  const fmtName = d && (d.codec || d.container) ? `${d.codec || d.container}${d.lossless === true ? ' (verliesvrij)' : ''}` : (f.mime_type || '').replace('audio/', '').toUpperCase();
  const rows = [
    ['Artiest', f.artist], ['Titel', f.title], ['Album', d && d.album], ['Genre', f.genre], ['Jaar', d && d.year],
    ['Speelduur', len ? fmt(len) : null], ['Formaat', fmtName], ['Samplerate', d && d.sample_rate ? kHz(d.sample_rate) : null],
    ['Bitdiepte', d && d.bits_per_sample ? `${d.bits_per_sample} bit` : null], ['Bitrate', d && d.bitrate ? `${Math.round(d.bitrate / 1000)} kbps` : null],
    ['Kanalen', d && d.channels ? (d.channels === 1 ? 'mono' : d.channels === 2 ? 'stereo' : String(d.channels)) : null],
    ['Loudness', f.loudness_lufs != null ? `${String(f.loudness_lufs).replace('.', ',')} LUFS` : null],
    ['Grootte', f.size_bytes ? `${(f.size_bytes / 1048576).toLocaleString('nl-NL', { maximumFractionDigits: 1 })} MB` : null],
  ];
  return rows.filter(([, v]) => v !== null && v !== undefined && v !== ''); // missing = not shown, never guessed
}
function renderDetails() {
  const box = $('lib-details');
  const f = details.id && S.files.get(details.id);
  if (!f) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const d = details.data && details.data.details;
  const file = (details.data && details.data.file) ? { ...f, ...details.data.file } : f;
  box.replaceChildren(
    h('div', { class: 'd-head' }, h('span', { class: 'd-title' }, label(f)),
      h('button', { class: 'mini', title: 'Favoriet aan/uit', onclick: () => setFav([f.id], !isFav(f.id)) }, isFav(f.id) ? '★ Favoriet' : '☆ Favoriet'),
      h('button', { class: 'mini', title: 'Sluiten (Esc)', onclick: clearSelection }, '✕')),
    details.data && details.data.error ? h('div', { class: 'muted' }, `Details niet beschikbaar: ${details.data.error}`)
      : h('dl', {}, detailRows(file, d).filter(([k]) => k !== 'Artiest' && k !== 'Titel').map(([k, v]) => [h('dt', {}, k), h('dd', { title: String(v) }, String(v))])),
    ...(!details.data ? [h('div', { class: 'muted' }, 'Bestandsgegevens lezen…')] : []));
}

// ---------- jingle buttons ----------
function slotCategory(slot, file) {
  if (slot.cat) return slot.cat;
  const c = S.collections.find((x) => x.id === file.collection_id);
  return c ? c.name : '';
}
function replaceSlot(index, id) {
  const slot = cartSlots()[index];
  const old = slot && S.files.get(slot.id);
  stopCart(index);
  cartSlots()[index] = { ...slot, id };
  saveSettingsSoon(); preloadCartPage(); renderCart();
  const f = S.files.get(id);
  logEvent('INFO', `Jingle-knop ${index + 1}: ${old ? old.title : '—'} → ${f ? f.title : id}`);
}
function renameSlotCategory(index) {
  const slot = cartSlots()[index];
  const f = slot && S.files.get(slot.id);
  if (!slot || !f) return;
  const v = prompt('Categorie op deze knop (leeg = naam van de collectie)', slot.cat || '');
  if (v === null) return;
  slot.cat = v.trim().slice(0, 30) || undefined;
  saveSettingsSoon(); renderCart();
}
function showSlotInfo(index) {
  const slot = cartSlots()[index];
  const f = slot && S.files.get(slot.id);
  if (!f) return;
  const page = 'ABCD'[Math.floor(index / cartDims().count)] || '';
  const key = index % cartDims().count;
  $('slot-info-body').replaceChildren(h('dl', { class: 'info-grid' },
    [['Knop', `${page} ${key + 1}${key < 10 ? ` (toets ${(key + 1) % 10})` : ''}`], ['Categorie', slotCategory(slot, f) || '—'],
      ...detailRows(f, null), ['Gelijk volume', S.settings.normalize ? (f.loudness_lufs != null ? 'aan' : 'aan (nog niet gemeten)') : 'uit']]
      .map(([k, v]) => [h('dt', {}, k), h('dd', {}, String(v))])),
  h('p', { class: 'muted', style: 'margin:12px 0 0' }, 'Kleur, categorie en vervangen: rechtsklik op de knop.'));
  $('slot-info').showModal();
}

// ---------- dashboard ----------
// One overview of everything that matters on air. Purely a view: nothing here changes
// the playlist, the players or the jingles.
let dashTimer = null;
function upcoming() {
  const live = S.live && S.live.file ? S.live : null;
  let at = Date.now();
  if (live) at += ((mixPoint(live.file) ?? cueOut(live.file)) - live.audio.currentTime) * 1000;
  let known = true;
  let total = 0;
  const list = [];
  for (const item of S.playlist) {
    if (item.marker || item.state !== 'queued' || item.error) continue;
    const f = S.files.get(item.id);
    if (!f) continue;
    list.push({ f, at: known ? at : null });
    const len = playLength(f);
    if (len === null || item.stopAfter) known = false;
    if (len !== null) { at += len * 1000; total += len; }
  }
  return { list, total };
}
function dashBox(title, body, cls = '') { return h('div', { class: `dash-box ${cls}` }, h('h3', {}, title), body); }
const stLine = (level, text) => h('div', { class: `st ${level}` }, h('i'), text);
function renderDashboard() {
  $('dash-time').textContent = fmtClock(new Date());
  const live = S.live && S.live.file ? S.live : null;
  const { list, total } = upcoming();
  const a = audioHealth();
  const s = sys.server;
  const d = sys.db;
  const remaining = live ? (cueOut(live.file) ?? live.audio.duration) - live.audio.currentTime : null;
  const warnings = [];
  if (s && !s.ok) warnings.push(['ERROR', `Server: ${s.error}`]);
  if (d && !d.ok) warnings.push(['ERROR', `Database: ${d.error}`]);
  if (a.level === 'bad') warnings.push(['ERROR', `Audio: ${a.text}`]);
  for (const dk of decks) if (dk.error && Date.now() - dk.error.at < 120000) warnings.push(['WARNING', `Player ${dk.name} sloeg over: ${dk.error.msg}`]);
  if (S.settings.auto && !live && list.length === 0) warnings.push(['WARNING', 'Er speelt niets en de playlist is leeg']);
  else if (S.settings.auto && live && list.length === 0 && !nonstopOn()) warnings.push(['WARNING', 'Na dit nummer is de playlist leeg']);
  const recent = eventLog.filter((e) => e.level !== 'INFO' && Date.now() - e.t < 10 * 60 * 1000).slice(-5).reverse();
  $('dash-grid').replaceChildren(
    dashBox('NU OP DE RADIO', live ? [h('div', { class: 'big' }, label(live.file)),
      h('div', { class: 'num' }, fmt(remaining, { sign: '-' })),
      h('div', { class: 'sub' }, `Player ${live.name}${live.state === 'paused' ? ' · PAUZE' : ''}`)] : h('div', { class: 'sub' }, 'Er speelt niets'), 'wide'),
    dashBox('PLAYLIST', [h('div', { class: 'num' }, String(list.length)), h('div', { class: 'sub' }, `nummers · nog ${total ? fmt(total) : '0:00'}`),
      h('div', { class: 'sub' }, `AUTO ${S.settings.auto ? 'aan' : 'uit'} · NONSTOP ${nonstopOn() ? 'aan' : 'uit'}`)]),
    dashBox('HIERNA', list.length ? h('ol', {}, list.slice(0, 3).map((x) => h('li', {}, h('span', { class: 't' }, x.at ? fmtClock(new Date(x.at)).slice(0, 5) : '--:--'), label(x.f))))
      : h('div', { class: 'sub' }, 'Niets meer in de playlist'), 'wide'),
    dashBox('SERVER', [stLine(!s ? '' : s.ok ? 'ok' : 'bad', !s ? 'nog niet gecontroleerd' : s.ok ? `Online · ${s.ms} ms` : `Offline · ${s.error}`),
      h('div', { class: 'sub' }, sys.clients !== null && sys.clients !== undefined ? `Actieve gebruikers (2 min): ${sys.clients}` : '')]),
    dashBox('DATABASE', stLine(!d ? 'warn' : d.ok ? 'ok' : 'bad', !d ? 'onbekend (server niet bereikt)' : d.ok ? `Verbonden · ${d.ms} ms` : `Niet verbonden · ${d.error}`)),
    dashBox('AUDIO', [stLine(a.level, a.level === 'ok' ? 'OK' : a.text), a.rate ? h('div', { class: 'sub' }, `${a.device} · ${kHz(a.rate)}`) : null]),
    ...(warnings.length || recent.length ? [dashBox('WAARSCHUWINGEN', h('ul', {}, [...warnings.map(([lv, m]) => h('li', { class: lv }, m)),
      ...recent.map((e) => h('li', { class: e.level }, `${fmtTime(e.t)} ${e.msg}`))]), 'full alert')] : []));
}
function toggleDashboard(open = !$('dashboard').open) {
  if (open) {
    renderDashboard();
    $('dashboard').showModal();
    clearInterval(dashTimer);
    dashTimer = setInterval(renderDashboard, 500);
  } else $('dashboard').close();
}

function initExtras() {
  $('btn-dashboard').addEventListener('click', () => toggleDashboard(true));
  $('dash-close').addEventListener('click', () => toggleDashboard(false));
  $('dashboard').addEventListener('close', () => { clearInterval(dashTimer); dashTimer = null; });
  $('slot-info-close').addEventListener('click', () => $('slot-info').close());
  $('btn-log').addEventListener('click', () => toggleLog());
  $('log-close').addEventListener('click', () => toggleLog(false));
  $('log-problems').addEventListener('change', renderLog);
  $('log-clear').addEventListener('click', () => {
    if (!confirm('Logboek leegmaken? (Alleen dit overzicht; muziek, playlist en instellingen blijven.)')) return;
    eventLog.length = 0; writeLocal('aot_eventlog', eventLog); renderLog();
  });
  $('settings').addEventListener('close', refreshDevices);
  bindQuickFilters();
  updateSelection();
  renderLogBadge();
  refreshDevices();
  logEvent('INFO', 'Studio gestart');
  checkSystem();
  setInterval(checkSystem, 15000);
  setInterval(renderSystem, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkSystem(); });
}

boot();
