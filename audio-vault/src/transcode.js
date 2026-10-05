// "Zuinige modus": serves an MP3 320 version of a track instead of the lossless
// original, about three times less data for listeners and DJs over the internet.
// Each track is converted once and kept in a size-limited cache (least recently
// used tracks are removed first). Downloads, M3U links and WebDAV keep the original.
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');
const { hasFfmpeg } = require('./ingest');

const MAX_PARALLEL = 2;

function createTranscoder({ filesDir, cacheDir, maxBytes }) {
  fs.mkdirSync(cacheDir, { recursive: true });
  for (const f of fs.readdirSync(cacheDir)) if (f.endsWith('.part')) fs.rmSync(path.join(cacheDir, f), { force: true });
  let total = fs.readdirSync(cacheDir).reduce((t, f) => t + fs.statSync(path.join(cacheDir, f)).size, 0);
  const pending = new Map();
  let active = 0;
  const waiting = [];

  async function slot(fn) {
    while (active >= MAX_PARALLEL) await new Promise((resolve) => waiting.push(resolve));
    active++;
    try { return await fn(); } finally { active--; const next = waiting.shift(); if (next) next(); }
  }

  function encode(source, target) {
    const part = `${target}.part`;
    return new Promise((resolve, reject) => {
      execFile('ffmpeg', ['-nostdin', '-v', 'error', '-y', '-i', source, '-map', '0:a:0', '-map_metadata', '-1',
        '-c:a', 'libmp3lame', '-b:a', '320k', '-ar', '44100', '-f', 'mp3', part], { timeout: 10 * 60 * 1000 }, (err, stdout, stderr) => {
        if (err) { fs.rmSync(part, { force: true }); reject(new Error(String(stderr || err.message).trim())); return; }
        fs.renameSync(part, target);
        resolve(target);
      });
    });
  }

  // Keep the cache under its limit: remove the least recently used versions.
  function evict() {
    if (total <= maxBytes) return;
    const files = fs.readdirSync(cacheDir).filter((f) => f.endsWith('.mp3'))
      .map((f) => { const p = path.join(cacheDir, f); const st = fs.statSync(p); return { p, size: st.size, used: st.mtimeMs }; })
      .sort((a, b) => a.used - b.used);
    for (const f of files) {
      if (total <= maxBytes * 0.9) break;
      fs.rmSync(f.p, { force: true });
      total -= f.size;
    }
  }

  const cachePath = (file) => path.join(cacheDir, `${path.parse(file.storage_key).name}.mp3`);

  // Path of the MP3 version, or null when it cannot be made (no ffmpeg, odd file).
  async function mp3(file) {
    if (path.extname(file.storage_key).toLowerCase() === '.mp3') return null; // already small
    if (!(await hasFfmpeg())) return null;
    const target = cachePath(file);
    if (fs.existsSync(target)) {
      const now = new Date();
      fs.utimes(target, now, now, () => {}); // mark as recently used
      return target;
    }
    if (!pending.has(target)) {
      const job = slot(() => encode(path.join(filesDir, file.storage_key), target))
        .then((p) => { total += fs.statSync(p).size; evict(); return fs.existsSync(p) ? p : null; })
        .catch((err) => { console.warn(`Zuinige versie mislukt voor ${file.storage_key}: ${err.message}`); return null; })
        .finally(() => pending.delete(target));
      pending.set(target, job);
    }
    return pending.get(target);
  }

  function forget(file) {
    const target = cachePath(file);
    if (fs.existsSync(target)) { total -= fs.statSync(target).size; fs.rmSync(target, { force: true }); }
  }

  return { mp3, forget, get usedBytes() { return total; } };
}

module.exports = { createTranscoder };
