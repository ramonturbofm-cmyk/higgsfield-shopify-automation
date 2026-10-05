const express = require('express');
const fs = require('fs');
const path = require('path');
const multer = require('multer');
const auth = require('./auth');
const lib = require('./library');
const { createDavRouter } = require('./dav');
const { AUDIO_TYPES, ingestFile } = require('./ingest');
const { createClockRouter } = require('./clocks');
const { createTranscoder } = require('./transcode');

const INVITE_DAYS = 7;

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

const wrap = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

function publicUser(u) {
  return {
    id: u.id, email: u.email, name: u.name, role: u.role, disabled: u.disabled,
    has_password: Boolean(u.password_hash), has_api_token: Boolean(u.api_token_hash),
    can_download: lib.canDownload(u),
    invite_pending: Boolean(u.invite_token_hash), created_at: u.created_at,
  };
}

const num = (v) => (v === null || v === undefined ? null : Number(v));

function publicFile(f) {
  return {
    id: f.id, collection_id: f.collection_id, title: f.title, artist: f.artist,
    original_name: f.original_name, file_name: lib.fileName(f), mime_type: f.mime_type,
    size_bytes: Number(f.size_bytes), duration_seconds: num(f.duration_seconds),
    tags: f.tags, created_at: f.created_at,
    cue_in: num(f.cue_in), mix_out: num(f.mix_out), cue_out: num(f.cue_out),
    loudness_lufs: num(f.loudness_lufs), true_peak_db: num(f.true_peak_db),
  };
}

function createApp({ pool, storageDir, sessionSecret, publicUrl, maxUploadMb = 500, cacheMaxBytes = 20e9 }) {
  const filesDir = path.join(storageDir, 'files');
  const tmpDir = path.join(storageDir, 'tmp');
  fs.mkdirSync(filesDir, { recursive: true });
  fs.mkdirSync(tmpDir, { recursive: true });
  const transcoder = createTranscoder({ filesDir, cacheDir: path.join(storageDir, 'cache'), maxBytes: cacheMaxBytes });

  const app = express();
  app.set('trust proxy', 1);
  app.disable('x-powered-by');
  app.use(express.json({ limit: '1mb' }));
  app.use((req, res, next) => {
    res.set({
      'X-Content-Type-Options': 'nosniff',
      // Links contain personal tokens: never leak them to other sites.
      'Referrer-Policy': 'no-referrer',
      'Permissions-Policy': 'camera=(), geolocation=()',
    });
    // Only the public now-playing page may be embedded in another website.
    if (!req.path.startsWith('/nu')) res.set('X-Frame-Options', 'DENY');
    if (req.secure) res.set('Strict-Transport-Security', 'max-age=31536000');
    next();
  });

  const baseUrl = (req) => (publicUrl || `${req.protocol}://${req.get('host')}`).replace(/\/$/, '');

  // ---------- sessions ----------

  async function loadSessionUser(req) {
    const session = auth.readSession(sessionSecret, auth.parseCookies(req.headers.cookie)[auth.SESSION_COOKIE]);
    if (!session) return null;
    const { rows } = await pool.query('SELECT * FROM users WHERE id = $1', [session.userId]);
    const user = rows[0];
    if (!user || user.disabled || user.session_version !== session.sessionVersion) return null;
    return user;
  }

  function setSession(req, res, user) {
    res.cookie(auth.SESSION_COOKIE, auth.signSession(sessionSecret, user.id, user.session_version), {
      httpOnly: true, sameSite: 'lax', secure: req.secure, maxAge: auth.SESSION_DAYS * 24 * 3600 * 1000, path: '/',
    });
  }

  const requireUser = wrap(async (req, res, next) => {
    req.user = await loadSessionUser(req);
    if (!req.user) throw new HttpError(401, 'Niet ingelogd');
    next();
  });
  const requireAdmin = (req, res, next) => (lib.isAdmin(req.user) ? next() : next(new HttpError(403, 'Alleen voor beheerders')));

  // Very small brute-force guard for login / invite endpoints.
  const attempts = new Map();
  function throttle(req) {
    const key = req.ip;
    const now = Date.now();
    const entry = attempts.get(key) || { count: 0, reset: now + 15 * 60 * 1000 };
    if (entry.reset < now) { entry.count = 0; entry.reset = now + 15 * 60 * 1000; }
    entry.count += 1;
    attempts.set(key, entry);
    if (entry.count > 20) throw new HttpError(429, 'Te veel pogingen, probeer het over een kwartier opnieuw');
  }

  function validPassword(password) {
    if (typeof password !== 'string' || password.length < 10) throw new HttpError(400, 'Wachtwoord moet minstens 10 tekens zijn');
  }

  async function createInvite(userId) {
    const token = auth.randomToken();
    await pool.query(
      `UPDATE users SET invite_token_hash = $1, invite_expires_at = now() + interval '${INVITE_DAYS} days' WHERE id = $2`,
      [auth.sha256(token), userId]);
    return token;
  }

  // ---------- setup / login ----------

  app.get('/health', (req, res) => res.json({ ok: true }));

  app.get('/api/setup', wrap(async (req, res) => {
    const { rows } = await pool.query('SELECT count(*)::int AS n FROM users');
    res.json({ needs_setup: rows[0].n === 0 });
  }));

  app.post('/api/setup', wrap(async (req, res) => {
    const { email, name, password } = req.body || {};
    if (!email || !name) throw new HttpError(400, 'Naam en e-mail zijn verplicht');
    validPassword(password);
    // Only the very first account can be created this way; it becomes the owner.
    const { rows } = await pool.query(
      `INSERT INTO users (email, name, role, password_hash)
       SELECT $1, $2, 'owner', $3 WHERE NOT EXISTS (SELECT 1 FROM users) RETURNING *`,
      [String(email).trim().toLowerCase(), String(name).trim(), auth.hashPassword(password)]);
    if (!rows.length) throw new HttpError(409, 'Er is al een eigenaar ingesteld');
    setSession(req, res, rows[0]);
    res.json({ user: publicUser(rows[0]) });
  }));

  app.post('/api/login', wrap(async (req, res) => {
    throttle(req);
    const { email, password } = req.body || {};
    const { rows } = await pool.query('SELECT * FROM users WHERE email = $1', [String(email || '').trim().toLowerCase()]);
    const user = rows[0];
    if (!user || user.disabled || !auth.verifyPassword(String(password || ''), user.password_hash)) {
      throw new HttpError(401, 'Onjuiste e-mail of wachtwoord');
    }
    setSession(req, res, user);
    res.json({ user: publicUser(user) });
  }));

  app.post('/api/logout', (req, res) => {
    res.clearCookie(auth.SESSION_COOKIE, { path: '/' });
    res.json({ ok: true });
  });

  app.get('/api/invite/:token', wrap(async (req, res) => {
    throttle(req);
    const { rows } = await pool.query(
      'SELECT name, email FROM users WHERE invite_token_hash = $1 AND invite_expires_at > now() AND NOT disabled',
      [auth.sha256(req.params.token)]);
    if (!rows.length) throw new HttpError(404, 'Uitnodiging is ongeldig of verlopen');
    res.json(rows[0]);
  }));

  app.post('/api/invite/:token', wrap(async (req, res) => {
    throttle(req);
    validPassword(req.body && req.body.password);
    const { rows } = await pool.query(
      `UPDATE users SET password_hash = $1, invite_token_hash = NULL, invite_expires_at = NULL,
              session_version = session_version + 1
        WHERE invite_token_hash = $2 AND invite_expires_at > now() AND NOT disabled RETURNING *`,
      [auth.hashPassword(req.body.password), auth.sha256(req.params.token)]);
    if (!rows.length) throw new HttpError(404, 'Uitnodiging is ongeldig of verlopen');
    setSession(req, res, rows[0]);
    res.json({ user: publicUser(rows[0]) });
  }));

  // ---------- own account ----------

  app.get('/api/me', requireUser, (req, res) => res.json({ user: publicUser(req.user) }));

  app.post('/api/me/password', requireUser, wrap(async (req, res) => {
    const { current_password: current, new_password: next } = req.body || {};
    if (!auth.verifyPassword(String(current || ''), req.user.password_hash)) throw new HttpError(400, 'Huidig wachtwoord klopt niet');
    validPassword(next);
    const { rows } = await pool.query(
      'UPDATE users SET password_hash = $1, session_version = session_version + 1 WHERE id = $2 RETURNING *',
      [auth.hashPassword(next), req.user.id]);
    setSession(req, res, rows[0]);
    res.json({ ok: true });
  }));

  // The personal token used by mAirList / other software. Shown once, stored hashed.
  app.post('/api/me/token', requireUser, wrap(async (req, res) => {
    if (!lib.canDownload(req.user)) throw new HttpError(403, 'Je mag alleen afspelen in de studio; vraag de beheerder om koppel-rechten');
    const token = auth.randomToken();
    await pool.query('UPDATE users SET api_token_hash = $1 WHERE id = $2', [auth.sha256(token), req.user.id]);
    res.json({ token });
  }));

  app.delete('/api/me/token', requireUser, wrap(async (req, res) => {
    await pool.query('UPDATE users SET api_token_hash = NULL WHERE id = $1', [req.user.id]);
    res.json({ ok: true });
  }));

  // ---------- users (admin) ----------

  app.get('/api/users', requireUser, requireAdmin, wrap(async (req, res) => {
    const { rows: users } = await pool.query('SELECT * FROM users ORDER BY role = \'owner\' DESC, name');
    const { rows: access } = await pool.query('SELECT * FROM collection_access');
    res.json({
      users: users.map((u) => ({
        ...publicUser(u),
        access: access.filter((a) => a.user_id === u.id).map(({ collection_id, can_upload }) => ({ collection_id, can_upload })),
      })),
    });
  }));

  function assertCanManage(actor, target, newRole) {
    if (target.role === 'owner') throw new HttpError(403, 'De eigenaar kan niet worden aangepast');
    const touchesAdmin = target.role === 'admin' || newRole === 'admin';
    if (touchesAdmin && actor.role !== 'owner') throw new HttpError(403, 'Alleen de eigenaar beheert beheerders');
  }

  app.post('/api/users', requireUser, requireAdmin, wrap(async (req, res) => {
    const { email, name, role = 'member' } = req.body || {};
    if (!email || !name) throw new HttpError(400, 'Naam en e-mail zijn verplicht');
    if (!['member', 'admin'].includes(role)) throw new HttpError(400, 'Ongeldige rol');
    if (role === 'admin' && req.user.role !== 'owner') throw new HttpError(403, 'Alleen de eigenaar maakt beheerders aan');
    let user;
    try {
      ({ rows: [user] } = await pool.query(
        'INSERT INTO users (email, name, role) VALUES ($1, $2, $3) RETURNING *',
        [String(email).trim().toLowerCase(), String(name).trim(), role]));
    } catch (err) {
      if (err.code === '23505') throw new HttpError(409, 'Dit e-mailadres bestaat al');
      throw err;
    }
    const token = await createInvite(user.id);
    res.status(201).json({ user: publicUser(user), invite_url: `${baseUrl(req)}/#invite=${token}` });
  }));

  async function loadTarget(id) {
    const { rows } = await pool.query('SELECT * FROM users WHERE id = $1', [Number(id)]);
    if (!rows.length) throw new HttpError(404, 'Gebruiker niet gevonden');
    return rows[0];
  }

  app.patch('/api/users/:id', requireUser, requireAdmin, wrap(async (req, res) => {
    const target = await loadTarget(req.params.id);
    const { name, role, disabled, can_download: canDl } = req.body || {};
    if (role !== undefined && !['member', 'admin'].includes(role)) throw new HttpError(400, 'Ongeldige rol');
    assertCanManage(req.user, target, role);
    const { rows } = await pool.query(
      `UPDATE users SET name = COALESCE($1, name), role = COALESCE($2, role),
              disabled = COALESCE($3, disabled), can_download = COALESCE($5, can_download),
              session_version = session_version + CASE WHEN $3::boolean THEN 1 ELSE 0 END
        WHERE id = $4 RETURNING *`,
      [name ?? null, role ?? null, typeof disabled === 'boolean' ? disabled : null, target.id,
        typeof canDl === 'boolean' ? canDl : null]);
    res.json({ user: publicUser(rows[0]) });
  }));

  // New invite link; doubles as "password reset" for someone who is locked out.
  app.post('/api/users/:id/invite', requireUser, requireAdmin, wrap(async (req, res) => {
    const target = await loadTarget(req.params.id);
    assertCanManage(req.user, target);
    const token = await createInvite(target.id);
    res.json({ invite_url: `${baseUrl(req)}/#invite=${token}` });
  }));

  app.delete('/api/users/:id', requireUser, requireAdmin, wrap(async (req, res) => {
    const target = await loadTarget(req.params.id);
    assertCanManage(req.user, target);
    await pool.query('DELETE FROM users WHERE id = $1', [target.id]);
    res.json({ ok: true });
  }));

  // Replace a member's collection permissions: [{ collection_id, can_upload }]
  app.put('/api/users/:id/access', requireUser, requireAdmin, wrap(async (req, res) => {
    const target = await loadTarget(req.params.id);
    assertCanManage(req.user, target);
    const access = Array.isArray(req.body && req.body.access) ? req.body.access : null;
    if (!access) throw new HttpError(400, 'access moet een lijst zijn');
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      await client.query('DELETE FROM collection_access WHERE user_id = $1', [target.id]);
      for (const a of access) {
        await client.query(
          'INSERT INTO collection_access (user_id, collection_id, can_upload) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING',
          [target.id, Number(a.collection_id), Boolean(a.can_upload)]);
      }
      await client.query('COMMIT');
    } catch (err) {
      await client.query('ROLLBACK');
      if (err.code === '23503') throw new HttpError(400, 'Onbekende collectie');
      throw err;
    } finally {
      client.release();
    }
    res.json({ ok: true });
  }));

  // ---------- collections ----------

  app.get('/api/collections', requireUser, wrap(async (req, res) => {
    res.json({ collections: await lib.listCollections(pool, req.user) });
  }));

  function collectionName(name) {
    const clean = String(name || '').trim();
    if (!clean || /[\\/]/.test(clean)) throw new HttpError(400, 'Geef een naam zonder / of \\');
    return clean.slice(0, 120);
  }

  app.post('/api/collections', requireUser, requireAdmin, wrap(async (req, res) => {
    try {
      const { rows } = await pool.query('INSERT INTO collections (name, description) VALUES ($1, $2) RETURNING *',
        [collectionName(req.body && req.body.name), String((req.body && req.body.description) || '')]);
      res.status(201).json({ collection: rows[0] });
    } catch (err) {
      if (err.code === '23505') throw new HttpError(409, 'Er bestaat al een collectie met deze naam');
      throw err;
    }
  }));

  app.patch('/api/collections/:id', requireUser, requireAdmin, wrap(async (req, res) => {
    const { name, description } = req.body || {};
    try {
      const { rows } = await pool.query(
        'UPDATE collections SET name = COALESCE($1, name), description = COALESCE($2, description) WHERE id = $3 RETURNING *',
        [name === undefined ? null : collectionName(name), description ?? null, Number(req.params.id)]);
      if (!rows.length) throw new HttpError(404, 'Collectie niet gevonden');
      res.json({ collection: rows[0] });
    } catch (err) {
      if (err.code === '23505') throw new HttpError(409, 'Er bestaat al een collectie met deze naam');
      throw err;
    }
  }));

  app.delete('/api/collections/:id', requireUser, requireAdmin, wrap(async (req, res) => {
    try {
      const { rowCount } = await pool.query('DELETE FROM collections WHERE id = $1', [Number(req.params.id)]);
      if (!rowCount) throw new HttpError(404, 'Collectie niet gevonden');
    } catch (err) {
      if (err.code === '23503') throw new HttpError(409, 'Verwijder of verplaats eerst de bestanden in deze collectie');
      throw err;
    }
    res.json({ ok: true });
  }));

  // ---------- files ----------

  app.get('/api/files', requireUser, wrap(async (req, res) => {
    const visible = (await lib.listCollections(pool, req.user)).map((c) => c.id);
    const params = [visible];
    let where = 'collection_id = ANY($1)';
    if (req.query.collection_id) { params.push(Number(req.query.collection_id)); where += ` AND collection_id = $${params.length}`; }
    if (req.query.ids) {
      // Fetch specific files (playlist, jingle panel, clocks) — never the whole library.
      const ids = String(req.query.ids).split(',').map(Number).filter(Number.isInteger).slice(0, 1000);
      params.push(ids); where += ` AND id = ANY($${params.length})`;
    }
    if (req.query.q) {
      // Every word must appear in title, artist or tags: "turbo id" finds "Turbo FM – Station ID".
      for (const word of String(req.query.q).trim().split(/\s+/).slice(0, 6)) {
        params.push(`%${word.replace(/[\\%_]/g, (c) => `\\${c}`)}%`);
        where += ` AND (title || ' ' || artist || ' ' || array_to_string(tags, ' ')) ILIKE $${params.length}`;
      }
    }
    const limit = Math.min(1000, Math.max(1, Number(req.query.limit) || 1000));
    const offset = Math.max(0, Number(req.query.offset) || 0);
    const order = { name: 'lower(artist), lower(title), id', title: 'lower(title), lower(artist), id' }[req.query.sort] || 'created_at DESC, id DESC';
    const [{ rows }, { rows: [{ n }] }] = await Promise.all([
      pool.query(`SELECT * FROM audio_files WHERE ${where} ORDER BY ${order} LIMIT ${limit} OFFSET ${offset}`, params),
      pool.query(`SELECT count(*)::int AS n FROM audio_files WHERE ${where}`, params),
    ]);
    res.json({ files: rows.map(publicFile), total: n });
  }));

  // Lets the studio notice new music without loading the library.
  app.get('/api/library/stats', requireUser, wrap(async (req, res) => {
    const visible = (await lib.listCollections(pool, req.user)).map((c) => c.id);
    const { rows: [r] } = await pool.query(
      `SELECT count(*)::int AS count, coalesce(max(id), 0) AS newest, count(*) FILTER (WHERE loudness_checked)::int AS measured
         FROM audio_files WHERE collection_id = ANY($1)`, [visible]);
    res.json(r);
  }));

  const upload = multer({
    dest: tmpDir,
    limits: { fileSize: maxUploadMb * 1024 * 1024 },
    fileFilter: (req, file, cb) => cb(null, Boolean(AUDIO_TYPES[path.extname(file.originalname).toLowerCase()])),
  });

  app.post('/api/files', requireUser, upload.single('file'), wrap(async (req, res) => {
    const tmp = req.file && req.file.path;
    try {
      if (!req.file) throw new HttpError(400, `Geen (ondersteund) audiobestand. Toegestaan: ${Object.keys(AUDIO_TYPES).join(', ')}`);
      const collectionId = Number(req.body.collection_id);
      if (!(await lib.collectionAccess(pool, req.user, collectionId)).upload) throw new HttpError(403, 'Geen uploadrechten voor deze collectie');

      // Original names arrive as latin1 from multer; restore UTF-8 (é, ü, ...).
      const originalName = Buffer.from(req.file.originalname, 'latin1').toString('utf8');
      const row = await ingestFile({
        pool, filesDir, source: tmp, move: true, originalName, collectionId, userId: req.user.id,
        overrides: { title: req.body.title, artist: req.body.artist, tags: req.body.tags },
      });
      const rows = [row];
      lib.logAccess(pool, req.user.id, rows[0].id, 'upload', 'web');
      res.status(201).json({ file: publicFile(rows[0]) });
    } finally {
      if (tmp && fs.existsSync(tmp)) fs.unlinkSync(tmp);
    }
  }));

  async function editableFile(req) {
    const file = await lib.readableFile(pool, req.user, Number(req.params.id));
    if (!file) throw new HttpError(404, 'Bestand niet gevonden');
    if (!(await lib.collectionAccess(pool, req.user, file.collection_id)).upload) throw new HttpError(403, 'Geen bewerkrechten');
    return file;
  }

  app.patch('/api/files/:id', requireUser, wrap(async (req, res) => {
    const file = await editableFile(req);
    const { title, artist, tags, collection_id: collectionId } = req.body || {};
    if (collectionId !== undefined && !(await lib.collectionAccess(pool, req.user, Number(collectionId))).upload) {
      throw new HttpError(403, 'Geen rechten op de doelcollectie');
    }
    const { rows } = await pool.query(
      `UPDATE audio_files SET title = COALESCE($1, title), artist = COALESCE($2, artist), tags = COALESCE($3, tags),
              collection_id = COALESCE($4, collection_id) WHERE id = $5 RETURNING *`,
      [title ?? null, artist ?? null, Array.isArray(tags) ? tags.map(String) : null,
        collectionId === undefined ? null : Number(collectionId), file.id]);
    res.json({ file: publicFile(rows[0]) });
  }));

  app.delete('/api/files/:id', requireUser, wrap(async (req, res) => {
    const file = await editableFile(req);
    await pool.query('DELETE FROM audio_files WHERE id = $1', [file.id]);
    fs.rm(path.join(filesDir, file.storage_key), { force: true }, () => {});
    transcoder.forget(file);
    res.json({ ok: true });
  }));

  function sendAudio(req, res, file, { download } = {}) {
    res.type(file.mime_type);
    if (download) res.attachment(lib.fileName(file));
    res.sendFile(path.join(filesDir, file.storage_key), (err) => {
      if (err && !res.headersSent) res.status(err.status || 500).end();
    });
  }

  const isFirstChunk = (req) => req.method === 'GET' && !/^bytes=(?!0-)/.test(req.headers.range || '');

  app.get('/api/files/:id/stream', requireUser, wrap(async (req, res) => {
    const file = await lib.readableFile(pool, req.user, Number(req.params.id));
    if (!file) throw new HttpError(404, 'Bestand niet gevonden');
    const download = req.query.download === '1';
    if (download && !lib.canDownload(req.user)) throw new HttpError(403, 'Downloaden is voor jou niet toegestaan');
    res.set('Cache-Control', 'private, no-store');
    // Downloads are always logged (they hand out the file). Plain playing is only
    // logged for owners/admins: what customers play is their own business.
    if (isFirstChunk(req) && (download || lib.isAdmin(req.user))) lib.logAccess(pool, req.user.id, file.id, download ? 'download' : 'play', 'web');
    // Zuinige modus: an MP3 320 version, about 3x less data (never for downloads).
    if (req.query.format === 'mp3' && !download) {
      const small = await transcoder.mp3(file);
      if (small) {
        res.type('audio/mpeg').set('X-Audio-Format', 'mp3-320');
        return res.sendFile(small, (err) => { if (err && !res.headersSent) res.status(err.status || 500).end(); });
      }
    }
    res.set('X-Audio-Format', 'original');
    sendAudio(req, res, file, { download });
  }));

  // Cue points found by the studio's silence analysis. Anyone who may play the file
  // may store them: they are derived from the audio itself, not editorial data.
  app.put('/api/files/:id/cues', requireUser, wrap(async (req, res) => {
    const file = await lib.readableFile(pool, req.user, Number(req.params.id));
    if (!file) throw new HttpError(404, 'Bestand niet gevonden');
    const { cue_in: cueIn, mix_out: mixOut, cue_out: cueOut } = req.body || {};
    const values = [cueIn, mixOut, cueOut].map(Number);
    if (values.some((v) => !Number.isFinite(v) || v < 0) || !(values[0] <= values[1] && values[1] <= values[2] && values[0] < values[2])) {
      throw new HttpError(400, 'Ongeldige cue-punten');
    }
    const { rows } = await pool.query(
      'UPDATE audio_files SET cue_in = $1, mix_out = $2, cue_out = $3 WHERE id = $4 RETURNING *', [...values, file.id]);
    res.json({ file: publicFile(rows[0]) });
  }));

  // ---------- studio (Audio OnAir Turbo) ----------

  app.get('/api/me/settings', requireUser, wrap(async (req, res) => {
    const { rows } = await pool.query('SELECT data FROM user_settings WHERE user_id = $1', [req.user.id]);
    res.json({ settings: rows.length ? rows[0].data : {} });
  }));

  app.put('/api/me/settings', requireUser, wrap(async (req, res) => {
    const settings = req.body && req.body.settings;
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw new HttpError(400, 'settings moet een object zijn');
    if (JSON.stringify(settings).length > 200000) throw new HttpError(413, 'Instellingen zijn te groot');
    await pool.query(
      `INSERT INTO user_settings (user_id, data, updated_at) VALUES ($1, $2, now())
       ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data, updated_at = now()`, [req.user.id, settings]);
    res.json({ ok: true });
  }));

  // The studio reports each item it starts; null file_id means "nothing on air".
  app.post('/api/now-playing', requireUser, wrap(async (req, res) => {
    const fileId = req.body && req.body.file_id;
    if (fileId === null) {
      await pool.query('DELETE FROM station_now_playing WHERE user_id = $1', [req.user.id]);
      return res.json({ now_playing: null });
    }
    const file = await lib.readableFile(pool, req.user, Number(fileId));
    if (!file) throw new HttpError(404, 'Bestand niet gevonden');
    const length = file.cue_out !== null ? Number(file.cue_out) - Number(file.cue_in || 0) : file.duration_seconds;
    await pool.query(
      `INSERT INTO station_now_playing (user_id, file_id, title, artist, duration_seconds, started_at)
       VALUES ($1, $2, $3, $4, $5, now())
       ON CONFLICT (user_id) DO UPDATE SET file_id = EXCLUDED.file_id, title = EXCLUDED.title, artist = EXCLUDED.artist,
         duration_seconds = EXCLUDED.duration_seconds, started_at = now()`,
      [req.user.id, file.id, file.title, file.artist, length]);
    lib.logAccess(pool, req.user.id, file.id, 'onair', 'studio');
    res.json({ ok: true });
  }));

  // Your own private link for the public "Nu op de radio" page.
  app.get('/api/me/station-link', requireUser, wrap(async (req, res) => {
    let key = req.user.station_key;
    if (!key) {
      key = auth.randomToken();
      await pool.query('UPDATE users SET station_key = $1 WHERE id = $2', [key, req.user.id]);
    }
    res.json({ url: req.user.role === 'owner' ? `${baseUrl(req)}/nu.html` : `${baseUrl(req)}/nu.html?station=${key}` });
  }));

  // Public: what is on air now plus the last few items (for a website, RDS, a studio screen).
  // Without ?station= it shows the owner's station; with it, that person's station.
  app.get('/api/now-playing', wrap(async (req, res) => {
    const station = String(req.query.station || '');
    const { rows: [who] } = station
      ? await pool.query('SELECT id FROM users WHERE station_key = $1 AND NOT disabled', [station])
      : await pool.query("SELECT id FROM users WHERE role = 'owner' LIMIT 1");
    if (!who) throw new HttpError(404, 'Onbekend station');
    const { rows } = await pool.query('SELECT title, artist, duration_seconds, started_at FROM station_now_playing WHERE user_id = $1', [who.id]);
    const { rows: recent } = await pool.query(
      `SELECT f.title, f.artist, l.created_at AS started_at FROM access_log l JOIN audio_files f ON f.id = l.file_id
        WHERE l.action = 'onair' AND l.user_id = $1 ORDER BY l.created_at DESC LIMIT 11`, [who.id]);
    const current = rows[0] ? { ...rows[0], duration_seconds: num(rows[0].duration_seconds) } : null;
    res.set('Access-Control-Allow-Origin', '*').json({ now_playing: current, recent: current ? recent.slice(1) : recent.slice(0, 10) });
  }));

  app.use('/api', createClockRouter({ pool, requireUser, requireAdmin, wrap, HttpError }));

  app.get('/api/activity', requireUser, requireAdmin, wrap(async (req, res) => {
    const { rows } = await pool.query(
      `SELECT l.action, l.client, l.created_at, u.name AS user_name, f.title, f.artist
         FROM access_log l LEFT JOIN users u ON u.id = l.user_id LEFT JOIN audio_files f ON f.id = l.file_id
        -- What customers play stays private; downloads, links and uploads stay visible.
        WHERE NOT (l.action IN ('onair', 'play') AND (u.role IS NULL OR u.role = 'member'))
        ORDER BY l.created_at DESC LIMIT 200`);
    res.json({ activity: rows });
  }));

  // ---------- integrations: mAirList & other playout software ----------
  // Every link carries the user's personal API token, so permissions follow the user
  // and revoking/regenerating the token immediately cuts off all old links.

  const tokenUser = wrap(async (req, res, next) => {
    const { rows } = await pool.query('SELECT * FROM users WHERE api_token_hash = $1 AND NOT disabled', [auth.sha256(req.params.token)]);
    if (!rows.length || !lib.canDownload(rows[0])) throw new HttpError(401, 'Ongeldige of ingetrokken token');
    req.user = rows[0];
    next();
  });

  const streamUrl = (req, f) => `${baseUrl(req)}/m/${req.params.token}/files/${f.id}/${encodeURIComponent(lib.fileName(f))}`;

  function m3u(req, res, files, name) {
    const lines = ['#EXTM3U', `#PLAYLIST:${name}`];
    for (const f of files) {
      const label = f.artist ? `${f.artist} - ${f.title}` : f.title;
      lines.push(`#EXTINF:${f.duration_seconds ? Math.round(Number(f.duration_seconds)) : -1},${label.replace(/[\r\n]/g, ' ')}`);
      lines.push(streamUrl(req, f));
    }
    res.type('audio/x-mpegurl; charset=utf-8').attachment(`${lib.safeName(name)}.m3u8`).send(lines.join('\r\n') + '\r\n');
  }

  app.get('/m/:token/files/:id/:name?', tokenUser, wrap(async (req, res) => {
    const file = await lib.readableFile(pool, req.user, Number(req.params.id));
    if (!file) throw new HttpError(404, 'Bestand niet gevonden');
    if (isFirstChunk(req)) lib.logAccess(pool, req.user.id, file.id, 'stream', req.headers['user-agent']);
    sendAudio(req, res, file);
  }));

  app.get('/m/:token/collections/:id.m3u8?', tokenUser, wrap(async (req, res) => {
    const collectionId = Number(req.params.id);
    if (!(await lib.collectionAccess(pool, req.user, collectionId)).read) throw new HttpError(404, 'Collectie niet gevonden');
    const { rows: [collection] } = await pool.query('SELECT * FROM collections WHERE id = $1', [collectionId]);
    const { rows } = await pool.query('SELECT * FROM audio_files WHERE collection_id = $1 ORDER BY artist, title', [collectionId]);
    m3u(req, res, rows, collection.name);
  }));

  app.get('/m/:token/all.m3u8?', tokenUser, wrap(async (req, res) => {
    const visible = (await lib.listCollections(pool, req.user)).map((c) => c.id);
    const { rows } = await pool.query('SELECT * FROM audio_files WHERE collection_id = ANY($1) ORDER BY artist, title', [visible]);
    m3u(req, res, rows, 'Audio Vault');
  }));

  // Machine-readable catalogue for scripts or other software.
  app.get('/m/:token/library.json', tokenUser, wrap(async (req, res) => {
    const collections = await lib.listCollections(pool, req.user);
    const { rows } = await pool.query('SELECT * FROM audio_files WHERE collection_id = ANY($1) ORDER BY artist, title', [collections.map((c) => c.id)]);
    res.json({
      collections: collections.map((c) => ({
        id: c.id, name: c.name, description: c.description,
        files: rows.filter((f) => f.collection_id === c.id).map((f) => ({ ...publicFile(f), url: streamUrl(req, f) })),
      })),
    });
  }));

  // The Windows WebDAV client probes the server root before mounting /dav/.
  app.options('/', (req, res) => res.set({ DAV: '1', 'MS-Author-Via': 'DAV', Allow: 'OPTIONS, GET, HEAD' }).end());
  app.use('/dav', createDavRouter({ pool, filesDir }));

  // ---------- web UI ----------

  app.use(express.static(path.join(__dirname, '..', 'public')));

  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    if (err instanceof multer.MulterError) {
      return res.status(400).json({ error: err.code === 'LIMIT_FILE_SIZE' ? `Bestand is groter dan ${maxUploadMb} MB` : err.message });
    }
    if (!err.status) console.error(err);
    res.status(err.status || 500).json({ error: err.status ? err.message : 'Er ging iets mis op de server' });
  });

  return app;
}

module.exports = { createApp };
