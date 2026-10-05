// Converts a music archive from WAV/AIFF to FLAC into a NEW folder with the same
// structure. The source is only read. Every FLAC is checked against the original
// (same length) before it counts as done; other files are copied unchanged so the
// new folder is a complete replacement. Safe to stop and run again: finished files
// are skipped.
//
//   node src/convert-archive.js <source> <target> [--jobs 4]
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');

const CONVERT = new Set(['.wav', '.aif', '.aiff']);
const SKIP_DIRS = new Set(['@eaDir', '#recycle', '#snapshot', '$RECYCLE.BIN', 'System Volume Information']);

function run(cmd, args) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { timeout: 30 * 60 * 1000, maxBuffer: 1024 * 1024 }, (err, stdout, stderr) => {
      if (err) reject(new Error(String(stderr || err.message).trim().split('\n').pop()));
      else resolve(String(stdout));
    });
  });
}

// Fingerprint of the decoded audio samples (not the file): equal fingerprints mean
// the FLAC holds exactly the same sound as the original, bit for bit.
async function audioFingerprint(file) {
  const out = await run('ffmpeg', ['-v', 'error', '-i', file, '-map', '0:a:0', '-c:a', 'pcm_s32le', '-f', 'md5', '-']);
  const m = /MD5=([0-9a-f]{32})/.exec(out);
  if (!m) throw new Error('kan audio niet controleren');
  return m[1];
}

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name.startsWith('.') || SKIP_DIRS.has(entry.name)) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (entry.isFile()) yield full;
  }
}

// Float WAVs (32/64-bit floating point) cannot be stored bit-exact in FLAC.
async function isFloat(file) {
  const out = await run('ffprobe', ['-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=sample_fmt', '-of', 'csv=p=0', file]);
  return /^(flt|dbl)/.test(out.trim());
}

const size = (bytes) => (bytes >= 1e12 ? `${(bytes / 1e12).toFixed(2)} TB` : bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${(bytes / 1e6).toFixed(1)} MB`);

// "Pauzeren" stops the container: stop right away. Half-written files end in .part
// and are redone on the next run, so nothing is lost.
process.on('SIGTERM', () => {
  console.log('Gepauzeerd. Klik later op "Omzetten starten / verdergaan" om verder te gaan.');
  process.exit(143);
});

function removeLeftovers(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) removeLeftovers(full);
    else if (entry.name.endsWith('.part')) fs.rmSync(full, { force: true });
  }
}

async function main() {
  const args = process.argv.slice(2);
  const jobsAt = args.indexOf('--jobs');
  const jobs = jobsAt >= 0 ? Math.max(1, Number(args.splice(jobsAt, 2)[1]) || 2) : 2;
  const [source, target] = args;
  if (!source || !target) throw new Error('Gebruik: node src/convert-archive.js <bronmap> <doelmap> [--jobs 4]');
  const src = fs.realpathSync(source);
  fs.mkdirSync(target, { recursive: true });
  const dst = fs.realpathSync(target);
  if (dst === src || dst.startsWith(src + path.sep)) throw new Error('De doelmap mag niet in de bronmap liggen');

  removeLeftovers(dst);
  const files = [...walk(src)];
  const todo = files.map((file) => {
    const rel = path.relative(src, file);
    const ext = path.extname(file).toLowerCase();
    const convert = CONVERT.has(ext);
    return { file, rel, convert, out: path.join(dst, convert ? rel.slice(0, -ext.length) + '.flac' : rel) };
  });
  const already = todo.filter((t) => fs.existsSync(t.out) || (t.convert && fs.existsSync(t.out.replace(/\.flac$/, path.extname(t.file)))));
  const waiting = todo.filter((t) => !already.includes(t));
  console.log(`${files.length} bestanden gevonden (${todo.filter((t) => t.convert).length} WAV/AIFF), ${already.length} al klaar, ${waiting.length} te doen.`);

  const stats = { converted: 0, copied: 0, keptOriginal: 0, failed: [], srcBytes: 0, dstBytes: 0 };
  let next = 0; let done = 0;
  const started = Date.now();

  async function handle(t) {
    fs.mkdirSync(path.dirname(t.out), { recursive: true });
    const srcSize = fs.statSync(t.file).size;
    if (!t.convert) {
      fs.copyFileSync(t.file, `${t.out}.part`);
      fs.renameSync(`${t.out}.part`, t.out);
      stats.copied++;
      return 'gekopieerd';
    }
    const part = `${t.out}.part`;
    const keepOriginal = () => {
      const keep = t.out.replace(/\.flac$/, path.extname(t.file));
      fs.copyFileSync(t.file, `${keep}.part`);
      fs.renameSync(`${keep}.part`, keep);
      stats.keptOriginal++;
      return 'origineel bewaard (kan niet bit-voor-bit als FLAC)';
    };
    if (await isFloat(t.file)) return keepOriginal();
    try {
      await run('ffmpeg', ['-v', 'error', '-y', '-i', t.file, '-map', '0:a:0', '-map_metadata', '0', '-c:a', 'flac', '-compression_level', '8', '-f', 'flac', part]);
      const [a, b] = await Promise.all([audioFingerprint(t.file), audioFingerprint(part)]);
      if (a !== b) { fs.rmSync(part, { force: true }); return keepOriginal(); } // e.g. 32-bit WAV: FLAC would round it
      fs.renameSync(part, t.out);
      stats.converted++;
      stats.srcBytes += srcSize;
      stats.dstBytes += fs.statSync(t.out).size;
      return `FLAC ${Math.round((1 - fs.statSync(t.out).size / srcSize) * 100)}% kleiner`;
    } catch (err) {
      fs.rmSync(part, { force: true });
      // FLAC cannot hold everything (e.g. 32-bit float WAV): keep the original, lose nothing.
      if (/sample format|not supported|Unsupported|experimental/i.test(err.message)) return keepOriginal();
      throw err;
    }
  }

  async function worker() {
    while (next < waiting.length) {
      const t = waiting[next++];
      let result;
      try { result = await handle(t); } catch (err) { stats.failed.push(`${t.rel}: ${err.message}`); result = `MISLUKT: ${err.message}`; }
      done++;
      const left = ((Date.now() - started) / done) * (waiting.length - done) / 60000;
      console.log(`[${done}/${waiting.length}] ${t.rel} — ${result}${done % 25 === 0 ? ` · nog ± ${left < 90 ? `${Math.ceil(left)} min` : `${(left / 60).toFixed(1)} uur`}` : ''}`);
    }
  }
  await Promise.all(Array.from({ length: jobs }, worker));

  // Space for the whole archive so far, also counting earlier (paused) runs.
  let allWav = 0; let allFlac = 0;
  for (const t of todo) {
    if (t.convert && fs.existsSync(t.out)) { allWav += fs.statSync(t.file).size; allFlac += fs.statSync(t.out).size; }
  }
  const report = [
    `Archief omzetten — ${new Date().toLocaleString('nl-NL')}`,
    `Bron: ${src}`, `Doel: ${dst}`, '',
    `Omgezet naar FLAC (bit-voor-bit gecontroleerd): ${stats.converted}`,
    `Gekopieerd (geen WAV): ${stats.copied}`,
    `Origineel bewaard (kan niet als FLAC): ${stats.keptOriginal}`,
    `Al klaar van een eerdere keer: ${already.length}`,
    `Mislukt: ${stats.failed.length}`,
    allWav ? `Ruimte totaal: ${size(allWav)} WAV → ${size(allFlac)} FLAC (${size(allWav - allFlac)} bespaard, ${Math.round((1 - allFlac / allWav) * 100)}%)` : '',
    '', ...(stats.failed.length ? ['Mislukte bestanden (staan NIET in de doelmap):', ...stats.failed] : ['Alles is gelukt.']),
    '', 'Controleer de doelmap voordat je de oude WAV-map verwijdert.',
  ].join('\n');
  fs.writeFileSync(path.join(dst, '_omzetrapport.txt'), report + '\n');
  console.log(`\n${report}`);
  process.exitCode = stats.failed.length ? 2 : 0;
}

main().catch((err) => { console.error(err.message); process.exit(1); });
