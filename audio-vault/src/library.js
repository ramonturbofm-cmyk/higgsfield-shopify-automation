// Shared helpers for permissions and how files are named in links / WebDAV.
const path = require('path');

const isAdmin = (user) => user.role === 'owner' || user.role === 'admin';
// Download, M3U links, WebDAV and API tokens hand out the files themselves.
const canDownload = (user) => isAdmin(user) || user.can_download === true;

// Number of files and the average play length (cue-in to mix point) per collection,
// used by the clock editor so it never has to load the whole library.
const STATS = `coalesce(s.n, 0)::int AS file_count, s.avg_length`;
const STATS_JOIN = `LEFT JOIN (SELECT collection_id, count(*) AS n,
    avg(coalesce(mix_out - coalesce(cue_in, 0), duration_seconds))::float8 AS avg_length
    FROM audio_files GROUP BY collection_id) s ON s.collection_id = c.id`;

async function listCollections(pool, user) {
  if (isAdmin(user)) {
    const { rows } = await pool.query(`SELECT c.*, TRUE AS can_upload, ${STATS} FROM collections c ${STATS_JOIN} ORDER BY c.name`);
    return rows;
  }
  const { rows } = await pool.query(
    `SELECT c.*, a.can_upload, ${STATS}
       FROM collections c JOIN collection_access a ON a.collection_id = c.id AND a.user_id = $1 ${STATS_JOIN}
      ORDER BY c.name`, [user.id]);
  return rows;
}

async function collectionAccess(pool, user, collectionId) {
  if (isAdmin(user)) {
    const { rowCount } = await pool.query('SELECT 1 FROM collections WHERE id = $1', [collectionId]);
    return rowCount ? { read: true, upload: true } : { read: false, upload: false };
  }
  const { rows } = await pool.query(
    'SELECT can_upload FROM collection_access WHERE user_id = $1 AND collection_id = $2', [user.id, collectionId]);
  return rows.length ? { read: true, upload: rows[0].can_upload } : { read: false, upload: false };
}

// Returns the file only when the user may read its collection.
async function readableFile(pool, user, fileId) {
  if (!Number.isInteger(fileId)) return null;
  const { rows } = await pool.query('SELECT * FROM audio_files WHERE id = $1', [fileId]);
  if (!rows.length) return null;
  const access = await collectionAccess(pool, user, rows[0].collection_id);
  return access.read ? rows[0] : null;
}

function safeName(value) {
  return value.replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').replace(/\s+/g, ' ').trim();
}

// "Artist - Title [12].mp3" — the [id] keeps names unique and lets us map back to the row.
function fileName(file) {
  const ext = path.extname(file.storage_key);
  const label = file.artist ? `${file.artist} - ${file.title}` : file.title;
  return `${safeName(label).slice(0, 180)} [${file.id}]${ext}`;
}

function fileIdFromName(name) {
  const m = /\[(\d+)\]\.[^.]+$/.exec(name);
  return m ? Number(m[1]) : null;
}

async function logAccess(pool, userId, fileId, action, client) {
  await pool.query('INSERT INTO access_log (user_id, file_id, action, client) VALUES ($1, $2, $3, $4)',
    [userId, fileId, action, (client || '').slice(0, 200)]).catch(() => {});
}

module.exports = { isAdmin, canDownload, listCollections, collectionAccess, readableFile, safeName, fileName, fileIdFromName, logAccess };
