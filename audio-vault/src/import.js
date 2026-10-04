// Bulk import of an existing music folder (e.g. a Synology share) into the database.
//
//   node src/import.js <map> --collection "Muziek"
//   node src/import.js <map> --per-folder          (each top-level folder becomes a collection)
//
// WAV/AIFF are converted to FLAC (lossless) unless CONVERT_TO_FLAC=false. The source
// folder is only read, never changed. Files imported before are skipped, so the
// command can simply be run again after adding new music.
require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { createPool, migrate } = require('./db');
const { AUDIO_TYPES, ingestFile, hasFfmpeg } = require('./ingest');

function parseArgs(argv) {
  const args = { dir: null, collection: null, perFolder: false, dryRun: false, jobs: 2 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--collection') args.collection = argv[++i];
    else if (a === '--per-folder') args.perFolder = true;
    else if (a === '--dry-run') args.dryRun = true;
    else if (a === '--jobs') args.jobs = Math.max(1, Number(argv[++i]) || 2);
    else if (!args.dir) args.dir = a;
    else throw new Error(`Onbekende optie: ${a}`);
  }
  if (!args.dir || (!args.collection && !args.perFolder)) {
    throw new Error('Gebruik: node src/import.js <map> (--collection "Naam" | --per-folder) [--dry-run] [--jobs 2]');
  }
  return args;
}

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.') || entry.name.startsWith('@')) continue; // hidden + Synology @eaDir
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (entry.isFile() && AUDIO_TYPES[path.extname(entry.name).toLowerCase()]) yield full;
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const root = fs.realpathSync(args.dir);
  const pool = createPool(process.env.DATABASE_URL);
  await migrate(pool);
  const filesDir = path.join(path.resolve(process.env.STORAGE_DIR || './storage'), 'files');
  fs.mkdirSync(filesDir, { recursive: true });

  const { rows: [owner] } = await pool.query("SELECT id FROM users WHERE role = 'owner'");
  const collectionIds = new Map();
  async function collectionFor(file) {
    const rel = path.relative(root, file).split(path.sep);
    const name = args.perFolder && rel.length > 1 ? rel[0] : (args.collection || 'Algemeen');
    if (!collectionIds.has(name)) {
      const { rows } = await pool.query(
        `INSERT INTO collections (name) VALUES ($1) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id`, [name]);
      collectionIds.set(name, rows[0].id);
    }
    return collectionIds.get(name);
  }

  const files = [...walk(root)];
  const { rows: known } = await pool.query('SELECT source_path FROM audio_files WHERE source_path IS NOT NULL');
  const done = new Set(known.map((r) => r.source_path));
  const todo = files.filter((f) => !done.has(f));
  console.log(`${files.length} audiobestanden gevonden, ${files.length - todo.length} al geïmporteerd, ${todo.length} nieuw.`);
  if (!(await hasFfmpeg())) console.log('Let op: ffmpeg niet gevonden, WAV-bestanden worden niet naar FLAC omgezet.');
  if (args.dryRun) { todo.slice(0, 50).forEach((f) => console.log('  ', path.relative(root, f))); await pool.end(); return; }

  let next = 0; let ok = 0; let failed = 0;
  async function worker() {
    while (next < todo.length) {
      const file = todo[next++];
      try {
        const row = await ingestFile({
          pool, filesDir, source: file, move: false, originalName: path.basename(file),
          collectionId: await collectionFor(file), userId: owner ? owner.id : null, sourcePath: file,
        });
        ok++;
        console.log(`[${ok + failed}/${todo.length}] ${row.artist ? `${row.artist} - ` : ''}${row.title} → ${row.mime_type}`);
      } catch (err) {
        failed++;
        console.error(`[${ok + failed}/${todo.length}] MISLUKT ${path.relative(root, file)}: ${err.message}`);
      }
    }
  }
  await Promise.all(Array.from({ length: args.jobs }, worker));
  console.log(`Klaar: ${ok} geïmporteerd, ${failed} mislukt.`);
  await pool.end();
}

main().catch((err) => { console.error(err.message); process.exit(1); });
