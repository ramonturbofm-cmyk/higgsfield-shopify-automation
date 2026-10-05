// Nonstop filter: tracks, artists, genres and folders someone never wants in their
// automatically planned hours (uurklok / 24/7). Adding a track by hand still works.
const path = require('path');
const mm = require('music-metadata');

const KINDS = ['file', 'artist', 'genre', 'folder'];

async function loadBlocks(pool, userId) {
  const { rows } = await pool.query('SELECT kind, value FROM nonstop_blocks WHERE user_id = $1', [userId]);
  const of = (kind) => rows.filter((r) => r.kind === kind).map((r) => r.value);
  return {
    files: of('file').map(Number).filter(Number.isInteger),
    artists: of('artist'), genres: of('genre'), folders: of('folder'),
    any: rows.length > 0,
  };
}

// SQL expression "this row is filtered out", for a table alias. Pushes its parameters
// onto `params`. Text rules match case-insensitively on "contains".
function blockedSql(alias, blocks, params) {
  const p = (v) => { params.push(v); return `$${params.length}`; };
  const contains = (expr, list) => `EXISTS (SELECT 1 FROM unnest(${p(list)}::text[]) b WHERE strpos(lower(${expr}), b) > 0)`;
  return `(${alias}.id = ANY(${p(blocks.files)}::int[])
    OR ${contains(`${alias}.artist`, blocks.artists)}
    OR ${contains(`coalesce(${alias}.genre, '')`, blocks.genres)}
    OR ${contains(`coalesce(${alias}.source_path, ${alias}.original_name)`, blocks.folders)})`;
}

function cleanBlock(body) {
  const kind = String(body && body.kind || '');
  if (!KINDS.includes(kind)) throw Object.assign(new Error('Onbekend soort filter'), { status: 400 });
  let value = String(body.value ?? '').trim();
  if (kind === 'file') {
    if (!Number.isInteger(Number(value)) || Number(value) <= 0) throw Object.assign(new Error('Ongeldig nummer'), { status: 400 });
    value = String(Number(value));
  } else {
    value = value.toLowerCase().replace(/\\/g, '/').slice(0, 200);
    if (value.length < 2) throw Object.assign(new Error('Vul minstens 2 tekens in'), { status: 400 });
  }
  return { kind, value };
}

// Reads the genre tag of files imported before genres were stored (and of anything
// whose tags could not be read at import), a batch at a time in the background.
function startGenreWorker({ pool, filesDir, batch = 200, idleMs = 5 * 60 * 1000, log = () => {} }) {
  let stopped = false;
  let timer = null;
  async function loop() {
    if (stopped) return;
    let worked = 0;
    try {
      const { rows } = await pool.query('SELECT id, storage_key, source_path FROM audio_files WHERE genre IS NULL ORDER BY id DESC LIMIT $1', [batch]);
      for (const row of rows) {
        if (stopped) return;
        await pool.query('UPDATE audio_files SET genre = $1 WHERE id = $2', [await readGenre(path.join(filesDir, row.storage_key), row.source_path), row.id]);
        worked++;
      }
    } catch (err) {
      log(`Genres lezen: ${err.message}`);
    }
    if (!stopped) timer = setTimeout(loop, worked ? 100 : idleMs);
  }
  timer = setTimeout(loop, 3000);
  return { stop() { stopped = true; clearTimeout(timer); }, runOnce: async () => { clearTimeout(timer); const s = stopped; stopped = false; await loop(); clearTimeout(timer); stopped = s; } };
}

const genreOf = (common) => [...new Set((common && common.genre) || [])].map((g) => String(g).trim()).filter(Boolean).join(', ').slice(0, 200);

async function readGenre(file, sourcePath) {
  for (const f of [file, sourcePath]) {
    if (!f) continue;
    try {
      const g = genreOf((await mm.parseFile(f, { duration: false, skipCovers: true })).common);
      if (g) return g;
    } catch { /* try the next one */ }
  }
  return '';
}

module.exports = { KINDS, loadBlocks, blockedSql, cleanBlock, startGenreWorker, genreOf };
