// Import of a music folder (e.g. a Synology share) into the database. Used by hand
// ("Muziek importeren") and automatically by the server every few minutes.
//
//   node src/import.js <map> --collection "Muziek"
//   node src/import.js <map> --per-folder          (each top-level folder becomes a collection)
//
// WAV/AIFF are converted to FLAC (lossless) unless CONVERT_TO_FLAC=false. The source
// folder is only read, never changed. Files imported before are skipped, so it can
// simply run again after adding new music.
require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { AUDIO_TYPES, ingestFile, hasFfmpeg } = require('./ingest');

// Only one import at a time (manual or automatic), across processes.
const IMPORT_LOCK = 72727;

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.') || entry.name.startsWith('@') || entry.name.startsWith('#')) continue; // hidden, @eaDir, #recycle
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (entry.isFile() && AUDIO_TYPES[path.extname(entry.name).toLowerCase()]) yield full;
  }
}

/**
 * Import new files from `dir`. Files changed less than minAgeMs ago are left for the
 * next round (they may still be copying). `skip(file, mtimeMs)` can exclude files
 * that failed before. Resolves with { busy } when another import is running.
 */
async function importFolder({ pool, filesDir, dir, perFolder = true, collection = 'Muziek', jobs = 2, minAgeMs = 0, log = console.log, skip = () => false, onFailed = () => {} }) {
  const lock = await pool.connect();
  try {
    const { rows: [{ ok }] } = await lock.query('SELECT pg_try_advisory_lock($1) AS ok', [IMPORT_LOCK]);
    if (!ok) return { busy: true, found: 0, added: 0, failed: 0, waiting: 0 };
    try {
      const root = fs.realpathSync(dir);
      fs.mkdirSync(filesDir, { recursive: true });
      const { rows: [owner] } = await pool.query("SELECT id FROM users WHERE role = 'owner'");
      const collectionIds = new Map();
      async function collectionFor(file) {
        const rel = path.relative(root, file).split(path.sep);
        const name = perFolder && rel.length > 1 ? rel[0] : (collection || 'Algemeen');
        if (!collectionIds.has(name)) {
          const { rows } = await pool.query(
            'INSERT INTO collections (name) VALUES ($1) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id', [name]);
          collectionIds.set(name, rows[0].id);
        }
        return collectionIds.get(name);
      }

      const files = [...walk(root)];
      const { rows: known } = await pool.query('SELECT source_path FROM audio_files WHERE source_path IS NOT NULL');
      const done = new Set(known.map((r) => r.source_path));
      const now = Date.now();
      let waiting = 0;
      const todo = [];
      for (const file of files) {
        if (done.has(file)) continue;
        let st;
        try { st = fs.statSync(file); } catch { continue; } // removed in the meantime
        if (now - st.mtimeMs < minAgeMs) { waiting++; continue; } // still being copied?
        if (skip(file, st.mtimeMs)) continue;
        todo.push({ file, mtimeMs: st.mtimeMs });
      }
      log(`${files.length} audiobestanden gevonden, ${files.length - todo.length - waiting} al geïmporteerd, ${todo.length} nieuw${waiting ? `, ${waiting} nog aan het kopiëren` : ''}.`);
      if (todo.length && !(await hasFfmpeg())) log('Let op: ffmpeg niet gevonden, WAV-bestanden worden niet naar FLAC omgezet.');

      let next = 0; let added = 0; let failed = 0;
      async function worker() {
        while (next < todo.length) {
          const { file, mtimeMs } = todo[next++];
          try {
            const row = await ingestFile({
              pool, filesDir, source: file, move: false, originalName: path.basename(file),
              collectionId: await collectionFor(file), userId: owner ? owner.id : null, sourcePath: file,
            });
            added++;
            log(`[${added + failed}/${todo.length}] ${row.artist ? `${row.artist} - ` : ''}${row.title} → ${row.mime_type}`);
          } catch (err) {
            failed++;
            onFailed(file, mtimeMs, err);
            log(`[${added + failed}/${todo.length}] MISLUKT ${path.relative(root, file)}: ${err.message}`);
          }
        }
      }
      await Promise.all(Array.from({ length: Math.max(1, jobs) }, worker));
      log(`Klaar: ${added} geïmporteerd, ${failed} mislukt.`);
      return { busy: false, found: files.length, added, failed, waiting };
    } finally {
      await lock.query('SELECT pg_advisory_unlock($1)', [IMPORT_LOCK]);
    }
  } finally {
    lock.release();
  }
}

module.exports = { importFolder };

// ---------- command line ----------
if (require.main === module) {
  const { createPool, migrate } = require('./db');
  const argv = process.argv.slice(2);
  const args = { dir: null, collection: null, perFolder: false, jobs: 2 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--collection') args.collection = argv[++i];
    else if (a === '--per-folder') args.perFolder = true;
    else if (a === '--jobs') args.jobs = Math.max(1, Number(argv[++i]) || 2);
    else if (!args.dir) args.dir = a;
    else { console.error(`Onbekende optie: ${a}`); process.exit(1); }
  }
  if (!args.dir || (!args.collection && !args.perFolder)) {
    console.error('Gebruik: node src/import.js <map> (--collection "Naam" | --per-folder) [--jobs 2]');
    process.exit(1);
  }
  (async () => {
    const pool = createPool(process.env.DATABASE_URL);
    await migrate(pool);
    const filesDir = path.join(path.resolve(process.env.STORAGE_DIR || './storage'), 'files');
    const result = await importFolder({ pool, filesDir, dir: args.dir, perFolder: args.perFolder, collection: args.collection, jobs: args.jobs });
    if (result.busy) console.log('Er loopt al een import (bijv. het automatisch bijwerken). Probeer het over een paar minuten opnieuw.');
    await pool.end();
    process.exitCode = result.busy ? 3 : 0;
  })().catch((err) => { console.error(err.message); process.exit(1); });
}
