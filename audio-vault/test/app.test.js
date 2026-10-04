// End-to-end test against a real Postgres. Run with:
//   TEST_DATABASE_URL=postgres://user@localhost:5432/vault_test npm test
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { createPool, migrate } = require('../src/db');
const { createApp } = require('../src/app');

const DB = process.env.TEST_DATABASE_URL;
let server, base, pool, storageDir;

// One second of 8 kHz mono silence as a WAV file.
function wav() {
  const samples = 8000;
  const buf = Buffer.alloc(44 + samples * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + samples * 2, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(8000, 24); buf.writeUInt32LE(16000, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36); buf.writeUInt32LE(samples * 2, 40);
  return buf;
}

function client() {
  let cookie = '';
  return async (method, url, body, headers = {}) => {
    const opts = { method, headers: { ...headers, cookie } };
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['content-type'] = 'application/json'; }
    const res = await fetch(base + url, opts);
    const set = res.headers.get('set-cookie');
    if (set) cookie = set.split(';')[0];
    const type = res.headers.get('content-type') || '';
    const data = type.includes('json') ? await res.json() : await res.text();
    return { status: res.status, data, headers: res.headers };
  };
}

before(async () => {
  if (!DB) return;
  pool = createPool(DB);
  await pool.query('DROP TABLE IF EXISTS now_playing, user_settings, access_log, collection_access, audio_files, collections, users CASCADE');
  await migrate(pool);
  storageDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vault-'));
  const app = createApp({ pool, storageDir, sessionSecret: 'test-secret' });
  await new Promise((r) => { server = app.listen(0, r); });
  base = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  if (!DB) return;
  server.close();
  await pool.end();
  fs.rmSync(storageDir, { recursive: true, force: true });
});

test('full flow: owner, upload, invite member, permissions, mAirList links, WebDAV', { skip: !DB && 'TEST_DATABASE_URL not set' }, async () => {
  const owner = client();
  assert.equal((await owner('GET', '/api/setup')).data.needs_setup, true);
  assert.equal((await owner('POST', '/api/setup', { name: 'Ramon', email: 'Owner@Example.com', password: 'kort' })).status, 400);
  assert.equal((await owner('POST', '/api/setup', { name: 'Ramon', email: 'Owner@Example.com', password: 'supergeheim1' })).status, 200);
  assert.equal((await client()('POST', '/api/setup', { name: 'X', email: 'x@x.nl', password: 'supergeheim1' })).status, 409);
  assert.equal((await owner('GET', '/api/me')).data.user.role, 'owner');

  const jingles = (await owner('POST', '/api/collections', { name: 'Jingles' })).data.collection;
  const music = (await owner('POST', '/api/collections', { name: 'Muziek' })).data.collection;
  assert.equal((await owner('POST', '/api/collections', { name: 'a/b' })).status, 400);

  const fd = new FormData();
  fd.append('collection_id', String(jingles.id));
  fd.append('artist', 'Turbo FM');
  fd.append('file', new Blob([wav()], { type: 'audio/wav' }), 'Station ID é.wav');
  const up = await owner('POST', '/api/files', fd);
  assert.equal(up.status, 201, JSON.stringify(up.data));
  const file = up.data.file;
  assert.equal(file.title, 'Station ID é');
  assert.equal(file.duration_seconds, 1);
  // WAV is stored as lossless FLAC (ffmpeg is available in the test environment).
  assert.equal(file.mime_type, 'audio/flac');
  assert.match(file.file_name, /\.flac$/);

  const bad = new FormData();
  bad.append('collection_id', String(jingles.id));
  bad.append('file', new Blob(['nope']), 'virus.exe');
  assert.equal((await owner('POST', '/api/files', bad)).status, 400);

  assert.equal((await owner('DELETE', `/api/collections/${jingles.id}`)).status, 409);

  // Invite a member; they set their own password through the link.
  const inv = await owner('POST', '/api/users', { name: 'DJ Sam', email: 'sam@example.com' });
  assert.equal(inv.status, 201);
  const inviteToken = inv.data.invite_url.split('#invite=')[1];
  const sam = client();
  assert.equal((await sam('GET', `/api/invite/${inviteToken}`)).data.name, 'DJ Sam');
  assert.equal((await sam('POST', `/api/invite/${inviteToken}`, { password: 'djsam-wachtwoord' })).status, 200);
  assert.equal((await sam('POST', `/api/invite/${inviteToken}`, { password: 'djsam-wachtwoord' })).status, 404);
  assert.equal((await sam('POST', '/api/collections', { name: 'Hack' })).status, 403);
  assert.equal((await sam('GET', '/api/users')).status, 403);

  // No access yet.
  assert.deepEqual((await sam('GET', '/api/collections')).data.collections, []);
  assert.equal((await sam('GET', `/api/files/${file.id}/stream`)).status, 404);

  // Grant read access to Jingles only.
  const samId = inv.data.user.id;
  assert.equal((await owner('PUT', `/api/users/${samId}/access`, { access: [{ collection_id: jingles.id, can_upload: false }] })).status, 200);
  assert.deepEqual((await sam('GET', '/api/collections')).data.collections.map((c) => c.name), ['Jingles']);
  assert.equal((await sam('GET', '/api/files')).data.files.length, 1);
  const upload = new FormData();
  upload.append('collection_id', String(jingles.id));
  upload.append('file', new Blob([wav()]), 'mine.wav');
  assert.equal((await sam('POST', '/api/files', upload)).status, 403);
  assert.equal((await sam('DELETE', `/api/files/${file.id}`)).status, 403);

  // Web streaming supports Range requests (needed for seeking).
  const ranged = await fetch(`${base}/api/files/${file.id}/stream`, { headers: { cookie: '' } });
  assert.equal(ranged.status, 401);

  // Members may only play in the studio until they get download/link rights.
  assert.equal((await sam('GET', `/api/files/${file.id}/stream`)).status, 200);
  assert.equal((await sam('GET', `/api/files/${file.id}/stream?download=1`)).status, 403);
  assert.equal((await sam('POST', '/api/me/token')).status, 403);
  assert.equal((await sam('GET', '/api/me')).data.user.can_download, false);
  assert.equal((await owner('PATCH', `/api/users/${samId}`, { can_download: true })).status, 200);
  assert.equal((await sam('GET', `/api/files/${file.id}/stream?download=1`)).status, 200);

  // mAirList / other software via personal token.
  const token = (await sam('POST', '/api/me/token')).data.token;
  const m3u = await sam('GET', `/m/${token}/collections/${jingles.id}.m3u8`);
  assert.equal(m3u.status, 200);
  assert.match(m3u.data, /^#EXTM3U/);
  assert.match(m3u.data, /#EXTINF:1,Turbo FM - Station ID é/);
  const streamUrl = m3u.data.split('\r\n').find((l) => l.startsWith('http'));
  const part = await fetch(streamUrl, { headers: { range: 'bytes=0-9' } });
  assert.equal(part.status, 206);
  assert.equal((await part.arrayBuffer()).byteLength, 10);
  assert.equal((await sam('GET', `/m/${token}/collections/${music.id}.m3u8`)).status, 404);
  const all = await sam('GET', `/m/${token}/all.m3u`);
  assert.equal(all.data.split('\r\n').filter((l) => l.startsWith('http')).length, 1);
  const json = await sam('GET', `/m/${token}/library.json`);
  assert.equal(json.data.collections[0].files[0].url, streamUrl);
  assert.equal((await sam('GET', '/m/wrongtoken/all.m3u8')).status, 401);

  // WebDAV: basic auth with e-mail + token.
  const basic = { authorization: 'Basic ' + Buffer.from(`sam@example.com:${token}`).toString('base64') };
  assert.equal((await fetch(`${base}/dav/`, { method: 'PROPFIND' })).status, 401);
  const rootList = await fetch(`${base}/dav/`, { method: 'PROPFIND', headers: { ...basic, depth: '1' } });
  assert.equal(rootList.status, 207);
  const rootXml = await rootList.text();
  assert.match(rootXml, /<D:href>\/dav\/Jingles\/<\/D:href>/);
  assert.doesNotMatch(rootXml, /Muziek/);
  const folder = await (await fetch(`${base}/dav/Jingles/`, { method: 'PROPFIND', headers: { ...basic, depth: '1' } })).text();
  const fileHref = /<D:href>(\/dav\/Jingles\/[^<]+)<\/D:href>/.exec(folder)[1];
  assert.match(decodeURIComponent(fileHref), /Turbo FM - Station ID é \[\d+\]\.flac$/);
  const got = await fetch(base + fileHref, { headers: basic });
  assert.equal(got.status, 200);
  assert.equal((await got.arrayBuffer()).byteLength, file.size_bytes);
  assert.equal(got.headers.get('referrer-policy'), 'no-referrer');
  assert.equal((await fetch(`${base}/dav/Muziek/`, { method: 'PROPFIND', headers: basic })).status, 404);
  assert.equal((await fetch(base + fileHref, { method: 'DELETE', headers: basic })).status, 405);

  // Taking the right away again closes the token and WebDAV routes at once.
  await owner('PATCH', `/api/users/${samId}`, { can_download: false });
  assert.equal((await fetch(streamUrl)).status, 401);
  assert.equal((await fetch(`${base}/dav/`, { method: 'PROPFIND', headers: basic })).status, 401);
  await owner('PATCH', `/api/users/${samId}`, { can_download: true });

  // Activity log saw the stream.
  const activity = (await owner('GET', '/api/activity')).data.activity.map((a) => a.action);
  assert.ok(activity.includes('stream') && activity.includes('webdav') && activity.includes('upload'));

  // Studio: settings, cue points and "now playing".
  assert.deepEqual((await sam('GET', '/api/me/settings')).data.settings, {});
  assert.equal((await sam('PUT', '/api/me/settings', { settings: { background: 'zwart', playlist: [{ id: file.id }] } })).status, 200);
  assert.equal((await sam('GET', '/api/me/settings')).data.settings.background, 'zwart');
  assert.equal((await sam('PUT', '/api/me/settings', { settings: [1] })).status, 400);
  assert.equal((await sam('PUT', `/api/files/${file.id}/cues`, { cue_in: 0.5, mix_out: 0.2, cue_out: 0.9 })).status, 400);
  const cued = await sam('PUT', `/api/files/${file.id}/cues`, { cue_in: 0.05, mix_out: 0.8, cue_out: 0.95 });
  assert.equal(cued.status, 200);
  assert.equal(cued.data.file.mix_out, 0.8);
  assert.equal((await sam('PUT', `/api/files/${file.id}/cues`, { cue_in: 0, mix_out: 0, cue_out: 0 })).status, 400);
  assert.equal((await client()('GET', '/api/now-playing')).data.now_playing, null);
  assert.equal((await sam('POST', '/api/now-playing', { file_id: file.id })).status, 200);
  const np = (await client()('GET', '/api/now-playing')).data;
  assert.equal(np.now_playing.title, 'Station ID é');
  assert.equal(np.now_playing.duration_seconds, 0.9);
  assert.equal((await sam('POST', '/api/now-playing', { file_id: null })).status, 200);
  const after = (await client()('GET', '/api/now-playing')).data;
  assert.equal(after.now_playing, null);
  assert.equal(after.recent[0].title, 'Station ID é');

  // Blocking a user kills both their session and their token immediately.
  assert.equal((await owner('PATCH', `/api/users/${samId}`, { disabled: true })).status, 200);
  assert.equal((await sam('GET', '/api/me')).status, 401);
  assert.equal((await fetch(streamUrl)).status, 401);
  assert.equal((await fetch(`${base}/dav/`, { method: 'PROPFIND', headers: basic })).status, 401);

  // Owner cannot be touched by anyone.
  const ownerId = (await owner('GET', '/api/me')).data.user.id;
  assert.equal((await owner('DELETE', `/api/users/${ownerId}`)).status, 403);

  // Deleting the file removes it from disk too.
  assert.equal((await owner('DELETE', `/api/files/${file.id}`)).status, 200);
  await new Promise((r) => setTimeout(r, 50));
  assert.deepEqual(fs.readdirSync(path.join(storageDir, 'files')), []);
  assert.equal((await owner('DELETE', `/api/collections/${jingles.id}`)).status, 200);
});

test('bulk import converts WAV to FLAC, makes collections per folder and skips known files', { skip: !DB && 'TEST_DATABASE_URL not set' }, async () => {
  const { execFileSync } = require('child_process');
  const src = fs.mkdtempSync(path.join(os.tmpdir(), 'import-'));
  fs.mkdirSync(path.join(src, 'Reclames'));
  fs.mkdirSync(path.join(src, '@eaDir'));
  fs.writeFileSync(path.join(src, 'Reclames', 'Bakker Jansen.wav'), wav());
  fs.writeFileSync(path.join(src, '@eaDir', 'thumb.wav'), wav());
  fs.writeFileSync(path.join(src, 'los nummer.wav'), wav());
  fs.writeFileSync(path.join(src, 'notes.txt'), 'geen audio');
  const env = { ...process.env, DATABASE_URL: DB, STORAGE_DIR: storageDir };
  const run = () => execFileSync(process.execPath, [path.join(__dirname, '..', 'src', 'import.js'), src, '--per-folder', '--collection', 'Import'], { env }).toString();
  assert.match(run(), /Klaar: 2 geïmporteerd, 0 mislukt/);
  assert.match(run(), /Klaar: 0 geïmporteerd/);
  const { rows } = await pool.query(
    `SELECT f.title, f.mime_type, c.name FROM audio_files f JOIN collections c ON c.id = f.collection_id WHERE f.source_path IS NOT NULL ORDER BY f.title`);
  assert.deepEqual(rows.map((r) => [r.title, r.mime_type, r.name]), [
    ['Bakker Jansen', 'audio/flac', 'Reclames'],
    ['los nummer', 'audio/flac', 'Import'],
  ]);
  // The source folder is left untouched.
  assert.ok(fs.existsSync(path.join(src, 'Reclames', 'Bakker Jansen.wav')));
  fs.rmSync(src, { recursive: true, force: true });
});
