// Loudness measurement (EBU R128): how loud a track sounds (integrated loudness in
// LUFS) and its true peak. The studio uses it to play every track at the same level;
// the audio files themselves are never changed.
const path = require('path');
const { execFile } = require('child_process');
const { hasFfmpeg } = require('./ingest');

function measure(file) {
  return new Promise((resolve, reject) => {
    execFile('ffmpeg', ['-nostdin', '-hide_banner', '-i', file, '-map', '0:a:0', '-af', 'ebur128=peak=true', '-f', 'null', '-'],
      { timeout: 10 * 60 * 1000, maxBuffer: 64 * 1024 * 1024 }, (err, stdout, stderr) => {
        if (err) { reject(new Error(String(stderr).trim().split('\n').pop() || err.message)); return; }
        const summary = String(stderr).split('Summary:').pop();
        const lufs = /I:\s+(-?[\d.]+|-inf) LUFS/.exec(summary);
        const peak = /Peak:\s+(-?[\d.]+|-inf) dBFS/.exec(summary);
        if (!lufs) { reject(new Error('geen meting')); return; }
        const num = (m) => (m && m[1] !== '-inf' ? Number(m[1]) : null);
        resolve({ lufs: num(lufs), peak: num(peak) });
      });
  });
}

// Measures tracks that have not been measured yet: new uploads and imports within a
// minute, an existing library gradually in the background.
function startLoudnessWorker({ pool, filesDir, batch = 10, idleMs = 60 * 1000, log = () => {} }) {
  let stopped = false;
  let timer = null;
  async function loop() {
    if (stopped) return;
    let worked = 0;
    try {
      if (await hasFfmpeg()) {
        const { rows } = await pool.query('SELECT id, storage_key FROM audio_files WHERE NOT loudness_checked ORDER BY id DESC LIMIT $1', [batch]);
        for (const row of rows) {
          if (stopped) return;
          let m = { lufs: null, peak: null };
          try { m = await measure(path.join(filesDir, row.storage_key)); } catch (err) { log(`Volume meten mislukt (${row.storage_key}): ${err.message}`); }
          await pool.query('UPDATE audio_files SET loudness_lufs = $1, true_peak_db = $2, loudness_checked = TRUE WHERE id = $3', [m.lufs, m.peak, row.id]);
          worked++;
        }
      }
    } catch (err) {
      log(`Volume meten: ${err.message}`);
    }
    if (!stopped) timer = setTimeout(loop, worked ? 200 : idleMs);
  }
  timer = setTimeout(loop, 5000);
  return { stop() { stopped = true; clearTimeout(timer); }, runOnce: async () => { const s = stopped; stopped = false; clearTimeout(timer); await loop(); clearTimeout(timer); stopped = s; } };
}

module.exports = { measure, startLoudnessWorker };
