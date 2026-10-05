// Converts a music archive from WAV/AIFF to FLAC into a NEW folder with the same
// structure. The source is only read. A FLAC only counts as done when its decoded
// audio is bit-identical to the original; what FLAC cannot hold exactly (32-bit or
// float WAV) is copied unchanged, other files (MP3, covers) are copied too, so the new
// folder is a complete replacement. Stopping is always safe: finished files are
// skipped next time and half-written ones (*.part) are redone.
//
// Used by the server (Docker, "Archief omzetten") and by the standalone Windows
// program "Audio OnAir Turbo Omzetter". Command line:
//   node src/convert-archive.js <source> <target> [--jobs 4]
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');

const CONVERT = new Set(['.wav', '.aif', '.aiff']);
const SKIP_DIRS = new Set(['@eaDir', '#recycle', '#snapshot', '$RECYCLE.BIN', 'System Volume Information']);

const size = (bytes) => (bytes >= 1e12 ? `${(bytes / 1e12).toFixed(2)} TB` : bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${(bytes / 1e6).toFixed(1)} MB`);

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name.startsWith('.') || SKIP_DIRS.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (entry.isFile()) yield full;
  }
}

function removeLeftovers(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) removeLeftovers(full);
    else if (entry.name.endsWith('.part')) fs.rmSync(full, { force: true });
  }
}

function checkFolders(source, target) {
  const src = fs.realpathSync(source);
  fs.mkdirSync(target, { recursive: true });
  const dst = fs.realpathSync(target);
  const inside = (a, b) => a.toLowerCase() === b.toLowerCase() || a.toLowerCase().startsWith(b.toLowerCase() + path.sep);
  if (inside(dst, src)) throw new Error('De doelmap mag niet in de bronmap liggen');
  if (inside(src, dst)) throw new Error('De bronmap mag niet in de doelmap liggen');
  return { src, dst };
}

// What will happen, without changing anything.
function plan(src, dst) {
  const items = [];
  for (const file of walk(src)) {
    const rel = path.relative(src, file);
    const ext = path.extname(file).toLowerCase();
    const convert = CONVERT.has(ext);
    const out = path.join(dst, convert ? rel.slice(0, -ext.length) + '.flac' : rel);
    const done = Boolean(dst) && (fs.existsSync(out) || (convert && fs.existsSync(out.slice(0, -5) + path.extname(file))));
    items.push({ file, rel, ext: path.extname(file), convert, out, done, bytes: fs.statSync(file).size });
  }
  return items;
}

// Overview before starting: how many files, how big, how much room is needed.
function scanArchive(source, target) {
  const src = fs.realpathSync(source);
  const dst = target && fs.existsSync(target) ? fs.realpathSync(target) : '';
  const items = plan(src, dst);
  const wav = items.filter((i) => i.convert);
  const todo = items.filter((i) => !i.done);
  return {
    files: items.length, wavFiles: wav.length, wavBytes: wav.reduce((t, i) => t + i.bytes, 0),
    otherFiles: items.length - wav.length, already: items.length - todo.length, todo: todo.length,
    // FLAC is usually 40-60% of WAV; estimate on the safe side.
    neededBytes: Math.round(todo.reduce((t, i) => t + (i.convert ? i.bytes * 0.7 : i.bytes), 0)),
  };
}

/**
 * Convert. onEvent receives { type: 'start' | 'file' | 'done', ... }; stop with signal.
 * Resolves with { stats, report, reportPath, aborted }.
 */
async function convertArchive({ source, target, jobs = 2, ffmpeg = 'ffmpeg', ffprobe = 'ffprobe', onEvent = () => {}, signal } = {}) {
  const { src, dst } = checkFolders(source, target);

  const run = (cmd, args) => new Promise((resolve, reject) => {
    execFile(cmd, args, { timeout: 30 * 60 * 1000, maxBuffer: 1024 * 1024, windowsHide: true, signal }, (err, stdout, stderr) => {
      if (err) reject(new Error(String(stderr || err.message).trim().split('\n').pop()));
      else resolve(String(stdout));
    });
  });
  // Fingerprint of the decoded samples: equal fingerprints = bit-identical audio.
  const fingerprint = async (file) => {
    const m = /MD5=([0-9a-f]{32})/.exec(await run(ffmpeg, ['-nostdin', '-v', 'error', '-i', file, '-map', '0:a:0', '-c:a', 'pcm_s32le', '-f', 'md5', '-']));
    if (!m) throw new Error('kan audio niet controleren');
    return m[1];
  };
  const isFloat = async (file) => /^(flt|dbl)/.test((await run(ffprobe, ['-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=sample_fmt', '-of', 'csv=p=0', file])).trim());

  removeLeftovers(dst);
  const items = plan(src, dst);
  const already = items.filter((i) => i.done).length;
  const waiting = items.filter((i) => !i.done);
  onEvent({ type: 'start', files: items.length, wavFiles: items.filter((i) => i.convert).length, already, todo: waiting.length });

  const stats = { converted: 0, copied: 0, keptOriginal: 0, failed: [], already };
  const copy = (from, to) => { fs.copyFileSync(from, `${to}.part`); fs.renameSync(`${to}.part`, to); };

  async function handle(t) {
    fs.mkdirSync(path.dirname(t.out), { recursive: true });
    if (!t.convert) { copy(t.file, t.out); stats.copied++; return { kind: 'copied', text: 'gekopieerd' }; }
    const keepOriginal = () => {
      copy(t.file, t.out.slice(0, -5) + t.ext);
      stats.keptOriginal++;
      return { kind: 'kept', text: 'origineel bewaard (kan niet bit-voor-bit als FLAC)' };
    };
    if (await isFloat(t.file)) return keepOriginal();
    const part = `${t.out}.part`;
    try {
      await run(ffmpeg, ['-nostdin', '-v', 'error', '-y', '-i', t.file, '-map', '0:a:0', '-map_metadata', '0', '-c:a', 'flac', '-compression_level', '8', '-f', 'flac', part]);
      const [a, b] = await Promise.all([fingerprint(t.file), fingerprint(part)]);
      if (a !== b) { fs.rmSync(part, { force: true }); return keepOriginal(); } // e.g. 32-bit WAV: FLAC would round it
      fs.renameSync(part, t.out);
      stats.converted++;
      const flacBytes = fs.statSync(t.out).size;
      return { kind: 'flac', text: `FLAC ${Math.round((1 - flacBytes / t.bytes) * 100)}% kleiner`, saved: t.bytes - flacBytes };
    } catch (err) {
      fs.rmSync(part, { force: true });
      if (signal && signal.aborted) throw err;
      if (/sample format|not supported|Unsupported|experimental/i.test(err.message)) return keepOriginal();
      throw err;
    }
  }

  let next = 0; let done = 0; let saved = 0;
  const started = Date.now();
  async function worker() {
    while (next < waiting.length && !(signal && signal.aborted)) {
      const t = waiting[next++];
      let result;
      try {
        result = await handle(t);
      } catch (err) {
        if (signal && signal.aborted) return;
        stats.failed.push(`${t.rel}: ${err.message}`);
        result = { kind: 'failed', text: `MISLUKT: ${err.message}` };
      }
      done++;
      saved += result.saved || 0;
      const etaMin = ((Date.now() - started) / done) * (waiting.length - done) / 60000;
      onEvent({ type: 'file', done, total: waiting.length, rel: t.rel, kind: result.kind, text: result.text, etaMin, savedBytes: saved });
    }
  }
  await Promise.all(Array.from({ length: Math.max(1, jobs) }, worker));
  const aborted = Boolean(signal && signal.aborted);

  // Totals for the whole archive so far, also counting earlier (paused) runs.
  let allWav = 0; let allFlac = 0;
  for (const t of items) {
    if (t.convert && fs.existsSync(t.out)) { allWav += t.bytes; allFlac += fs.statSync(t.out).size; }
  }
  const lines = [
    `Archief omzetten — ${new Date().toLocaleString('nl-NL')}${aborted ? ' (gepauzeerd)' : ''}`,
    `Bron: ${src}`, `Doel: ${dst}`, '',
    `Omgezet naar FLAC (bit-voor-bit gecontroleerd): ${stats.converted}`,
    `Gekopieerd (geen WAV): ${stats.copied}`,
    `Origineel bewaard (kan niet als FLAC): ${stats.keptOriginal}`,
    `Al klaar van een eerdere keer: ${already}`,
    `Mislukt: ${stats.failed.length}`,
  ];
  if (aborted) lines.push(`Nog te doen: ${waiting.length - done}`);
  if (allWav) lines.push(`Ruimte totaal: ${size(allWav)} WAV → ${size(allFlac)} FLAC (${size(allWav - allFlac)} bespaard, ${Math.round((1 - allFlac / allWav) * 100)}%)`);
  lines.push('');
  if (stats.failed.length) lines.push('Mislukte bestanden (staan NIET in de doelmap):', ...stats.failed, '');
  else if (!aborted) lines.push('Alles is gelukt.', '');
  lines.push('Controleer de doelmap voordat je de oude WAV-map verwijdert.');
  const report = lines.join('\n');
  const reportPath = path.join(dst, '_omzetrapport.txt');
  fs.writeFileSync(reportPath, report + '\n');
  onEvent({ type: 'done', stats, report, reportPath, aborted });
  return { stats, report, reportPath, aborted };
}

module.exports = { convertArchive, scanArchive, size };

// ---------- command line ----------
if (require.main === module) {
  const args = process.argv.slice(2);
  const jobsAt = args.indexOf('--jobs');
  const jobs = jobsAt >= 0 ? Math.max(1, Number(args.splice(jobsAt, 2)[1]) || 2) : 2;
  const [source, target] = args;
  const controller = new AbortController();
  // "Pauzeren" stops the container: stop right away; nothing is lost.
  process.on('SIGTERM', () => {
    console.log('Gepauzeerd. Klik later op "Omzetten starten / verdergaan" om verder te gaan.');
    controller.abort();
    process.exit(143);
  });
  (async () => {
    if (!source || !target) throw new Error('Gebruik: node src/convert-archive.js <bronmap> <doelmap> [--jobs 4]');
    const { stats, report } = await convertArchive({
      source, target, jobs, signal: controller.signal,
      onEvent: (e) => {
        if (e.type === 'start') console.log(`${e.files} bestanden gevonden (${e.wavFiles} WAV/AIFF), ${e.already} al klaar, ${e.todo} te doen.`);
        if (e.type === 'file') {
          const left = e.etaMin < 90 ? `${Math.ceil(e.etaMin)} min` : `${(e.etaMin / 60).toFixed(1)} uur`;
          console.log(`[${e.done}/${e.total}] ${e.rel} — ${e.text}${e.done % 25 === 0 ? ` · nog ± ${left}` : ''}`);
        }
      },
    });
    console.log(`\n${report}`);
    process.exitCode = stats.failed.length ? 2 : 0;
  })().catch((err) => { console.error(err.message); process.exit(1); });
}
