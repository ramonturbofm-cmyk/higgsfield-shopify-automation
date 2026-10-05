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
const hasFfmpeg = (() => { try { require('child_process').execFileSync('ffmpeg', ['-version']); return true; } catch { return false; } })();
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
  const call = async (method, url, body, headers = {}) => {
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
  call.cookie = () => cookie;
  return call;
}

before(async () => {
  if (!DB) return;
  pool = createPool(DB);
  await pool.query('DROP TABLE IF EXISTS nonstop_blocks, clock_schedule, clocks, station_now_playing, now_playing, user_settings, access_log, collection_access, audio_files, collections, users CASCADE');
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

  const samCookie = () => sam.cookie();
  // Zuinige modus: MP3 320 version, made once and then served from the cache.
  const small = await fetch(`${base}/api/files/${file.id}/stream?format=mp3`, { headers: { cookie: samCookie() } });
  assert.equal(small.status, 200);
  assert.equal(small.headers.get('x-audio-format'), 'mp3-320');
  assert.match(small.headers.get('content-type'), /audio\/mpeg/);
  const mp3 = Buffer.from(await small.arrayBuffer());
  assert.ok(mp3[0] === 0xff || mp3.slice(0, 3).toString() === 'ID3', 'an MP3 stream');
  const again = await fetch(`${base}/api/files/${file.id}/stream?format=mp3`, { headers: { cookie: samCookie(), range: 'bytes=0-99' } });
  assert.equal(again.status, 206, 'seeking works on the small version');
  const orig = await fetch(`${base}/api/files/${file.id}/stream?format=mp3&download=1`, { headers: { cookie: samCookie() } });
  assert.equal(orig.headers.get('x-audio-format'), 'original', 'downloads always get the original');
  assert.equal(fs.readdirSync(path.join(storageDir, 'cache')).filter((f) => f.endsWith('.mp3')).length, 1);

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
  // Every customer has their own station: what Sam plays shows on Sam's page only.
  assert.equal((await client()('GET', '/api/now-playing')).data.now_playing, null);
  assert.equal((await sam('POST', '/api/now-playing', { file_id: file.id })).status, 200);
  const samLink = (await sam('GET', '/api/me/station-link')).data.url;
  assert.match(samLink, /\/nu\.html\?station=[\w-]{20,}$/);
  const samStation = `/api/now-playing?station=${samLink.split('station=')[1]}`;
  const np = (await client()('GET', samStation)).data;
  assert.equal(np.now_playing.title, 'Station ID é');
  assert.equal(np.now_playing.duration_seconds, 0.9);
  assert.equal((await client()('GET', '/api/now-playing')).data.now_playing, null, "the owner's page stays empty");
  assert.equal((await client()('GET', '/api/now-playing?station=geraden')).status, 404);
  assert.equal((await owner('GET', '/api/me/station-link')).data.url.endsWith('/nu.html'), true);
  // The owner does not see what customers play.
  const seen = (await owner('GET', '/api/activity')).data.activity;
  assert.ok(!seen.some((a) => a.action === 'onair' || a.action === 'play'), 'customer plays are private');
  assert.ok(seen.some((a) => a.action === 'download'), 'downloads stay visible');
  assert.equal((await sam('POST', '/api/now-playing', { file_id: null })).status, 200);
  const after = (await client()('GET', samStation)).data;
  assert.equal(after.now_playing, null);
  assert.equal(after.recent[0].title, 'Station ID é');
  // The owner's own station.
  assert.equal((await owner('POST', '/api/now-playing', { file_id: file.id })).status, 200);
  assert.equal((await client()('GET', '/api/now-playing')).data.now_playing.title, 'Station ID é');
  assert.equal((await client()('GET', samStation)).data.now_playing, null, "Sam's page is not affected");

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

test('hour clocks: schedule, rotation and artist separation', { skip: !DB && 'TEST_DATABASE_URL not set' }, async () => {
  const owner = client();
  assert.equal((await owner('POST', '/api/login', { email: 'owner@example.com', password: 'supergeheim1' })).status, 200);
  const music = (await owner('POST', '/api/collections', { name: 'Klokmuziek' })).data.collection;
  const ids = (await owner('POST', '/api/collections', { name: 'Klokjingles' })).data.collection;
  const upload = async (collection, artist, title) => {
    const fd = new FormData();
    fd.append('collection_id', String(collection.id)); fd.append('artist', artist); fd.append('title', title);
    fd.append('file', new Blob([wav()]), `${title}.wav`);
    return (await owner('POST', '/api/files', fd)).data.file.id;
  };
  const songs = [];
  for (const [artist, title] of [['A', 'a1'], ['A', 'a2'], ['B', 'b1'], ['C', 'c1'], ['D', 'd1'], ['E', 'e1']]) songs.push(await upload(music, artist, title));
  const jingle = await upload(ids, 'Station', 'ID');

  assert.equal((await client()('GET', '/api/clocks')).status, 401);
  assert.equal((await owner('POST', '/api/clocks', { name: 'Ochtend', slots: [{ type: 'onzin' }] })).status, 400);
  const slots = [{ type: 'vast', file_id: jingle }, ...Array(4).fill({ type: 'muziek', collection_id: music.id })];
  const clock = (await owner('POST', '/api/clocks', { name: 'Ochtend', color: '#30d158', slots })).data.clock;
  assert.equal(clock.slots.length, 5);
  assert.equal((await owner('POST', '/api/clocks', { name: 'Ochtend', slots: [] })).status, 409);
  assert.equal((await owner('PUT', '/api/clock-schedule', { cells: [{ day: 1, hour: 10, clock_id: clock.id }] })).status, 200);
  assert.deepEqual((await owner('GET', '/api/clocks')).data.schedule, [{ day: 1, hour: 10, clock_id: clock.id }]);

  for (let round = 0; round < 10; round++) {
    const { planned } = (await owner('POST', '/api/clocks/plan', { hours: [{ day: 1, hour: 10 }, { day: 1, hour: 11 }] })).data;
    assert.equal(planned[0].clock.name, 'Ochtend');
    assert.equal(planned[0].items[0], jingle);
    const picks = planned[0].items.slice(1);
    assert.equal(picks.length, 4);
    assert.equal(new Set(picks).size, 4, 'no song twice in one hour');
    const artists = picks.map((id) => (id === songs[0] || id === songs[1] ? 'A' : id));
    assert.equal(new Set(artists).size, 4, 'no artist twice within the separation window');
    assert.equal(planned[1].clock, null, 'unscheduled hour stays empty');
  }
  // Everything excluded: the hour is still filled rather than left with holes.
  const full = (await owner('POST', '/api/clocks/plan', { hours: [{ day: 1, hour: 10 }], exclude: songs })).data.planned[0];
  assert.equal(full.items.length, 5);
  assert.equal(full.missing, 0);

  // A slot pointing at an empty collection is reported, not fatal.
  const empty = (await owner('POST', '/api/collections', { name: 'Leeg' })).data.collection;
  await owner('PUT', `/api/clocks/${clock.id}`, { name: 'Ochtend', slots: [...slots, { type: 'muziek', collection_id: empty.id }] });
  assert.equal((await owner('POST', '/api/clocks/plan', { hours: [{ day: 1, hour: 10 }] })).data.planned[0].missing, 1);

  assert.equal((await owner('DELETE', `/api/clocks/${clock.id}`)).status, 200);
  assert.deepEqual((await owner('GET', '/api/clocks')).data.schedule, []);
});

test('nonstop filter: blocked tracks, artists, genres and folders are never planned', { skip: !DB && 'TEST_DATABASE_URL not set' }, async () => {
  const owner = client();
  assert.equal((await owner('POST', '/api/login', { email: 'owner@example.com', password: 'supergeheim1' })).status, 200);
  const music = (await owner('POST', '/api/collections', { name: 'Nonstop' })).data.collection;
  const upload = async (artist, title) => {
    const fd = new FormData();
    fd.append('collection_id', String(music.id)); fd.append('artist', artist); fd.append('title', title);
    fd.append('file', new Blob([wav()]), `${title}.wav`);
    return (await owner('POST', '/api/files', fd)).data.file.id;
  };
  const ok = [await upload('Goed 1', 'g1'), await upload('Goed 2', 'g2'), await upload('Goed 3', 'g3')];
  const single = await upload('Prima', 'een nummer niet');
  const artist = await upload('Hans Polkaband feat. X', 'polka');
  const genre = await upload('Iemand', 'schlager');
  const folder = await upload('Ander', 'kerst');
  await pool.query("UPDATE audio_files SET genre = 'Volksmusik, Schlager' WHERE id = $1", [genre]);
  await pool.query("UPDATE audio_files SET source_path = '/import/Kerst 2024/kerst.flac' WHERE id = $1", [folder]);

  assert.equal((await owner('POST', '/api/me/nonstop-blocks', { kind: 'onzin', value: 'x' })).status, 400);
  assert.equal((await owner('POST', '/api/me/nonstop-blocks', { kind: 'artist', value: ' ' })).status, 400);
  for (const b of [{ kind: 'file', value: single }, { kind: 'artist', value: 'POLKABAND' }, { kind: 'genre', value: 'schlager' }, { kind: 'folder', value: 'Kerst 2024' }]) {
    assert.equal((await owner('POST', '/api/me/nonstop-blocks', b)).status, 200);
  }
  assert.equal((await owner('POST', '/api/me/nonstop-blocks', { kind: 'genre', value: 'Schlager' })).status, 200, 'adding twice is fine');
  const { blocks } = (await owner('GET', '/api/me/nonstop-blocks')).data;
  assert.deepEqual(blocks.map((b) => [b.kind, b.value]), [['artist', 'polkaband'], ['file', String(single)], ['folder', 'kerst 2024'], ['genre', 'schlager']]);
  assert.equal(blocks.find((b) => b.kind === 'file').title, 'een nummer niet');

  // The library marks what the filter keeps out, and can list only those.
  const { files } = (await owner('GET', `/api/files?collection_id=${music.id}`)).data;
  assert.deepEqual(files.filter((f) => f.nonstop_blocked).map((f) => f.id).sort(), [single, artist, genre, folder].sort());
  const only = (await owner('GET', `/api/files?collection_id=${music.id}&nonstop=blocked`)).data;
  assert.equal(only.total, 4);
  assert.ok((await owner('GET', '/api/genres')).data.genres.some((g) => g.genre === 'Schlager'));
  assert.equal((await owner('GET', '/api/files?q=volksmusik')).data.total, 1, 'search finds genres');

  const clock = (await owner('POST', '/api/clocks', { name: 'Nonstop', slots: Array(3).fill({ type: 'muziek', collection_id: music.id }) })).data.clock;
  await owner('PUT', '/api/clock-schedule', { cells: [{ day: 2, hour: 9, clock_id: clock.id }] });
  for (let round = 0; round < 15; round++) {
    const { items } = (await owner('POST', '/api/clocks/plan', { hours: [{ day: 2, hour: 9 }] })).data.planned[0];
    assert.deepEqual([...items].sort(), [...ok].sort(), 'only unfiltered tracks are planned');
  }
  // An hour without a clock: empty, unless the studio asks for nonstop fallback music.
  assert.deepEqual((await owner('POST', '/api/clocks/plan', { hours: [{ day: 3, hour: 9 }] })).data.planned[0].items, []);
  for (let round = 0; round < 10; round++) {
    const fb = (await owner('POST', '/api/clocks/plan', { hours: [{ day: 3, hour: 9 }], fallback: { collections: [music.id, 999999], count: 3 } })).data.planned[0];
    assert.equal(fb.clock.name, 'Nonstop');
    assert.deepEqual([...fb.items].sort(), [...ok].sort(), 'fallback follows the filter and rotation too');
  }
  // Someone else's filter does not apply to me.
  const other = client();
  const inv = (await owner('POST', '/api/users', { name: 'Noor', email: 'noor-nonstop@example.com', role: 'admin' })).data;
  assert.equal((await other('POST', `/api/invite/${inv.invite_url.split('#invite=')[1]}`, { password: 'noor-wachtwoord-1' })).status, 200);
  const seen = new Set();
  for (let round = 0; round < 25; round++) for (const id of (await other('POST', '/api/clocks/plan', { hours: [{ day: 2, hour: 9 }] })).data.planned[0].items) seen.add(id);
  assert.ok(seen.size > 3, 'filters are personal');

  // Naadloos aansluiten: admins may switch it, members without upload rights may not.
  const seg = await owner('PUT', '/api/files/segue', { ids: [ok[0], ok[1]], segue: true });
  assert.deepEqual(seg.data.files.map((f) => f.segue), [true, true]);
  assert.equal((await owner('GET', `/api/files?ids=${ok[0]}`)).data.files[0].segue, true);
  assert.equal((await owner('PUT', '/api/files/segue', { ids: [] })).status, 400);
  const member = (await owner('POST', '/api/users', { name: 'Lid', email: 'lid-segue@example.com' })).data;
  const lid = client();
  await lid('POST', `/api/invite/${member.invite_url.split('#invite=')[1]}`, { password: 'lid-wachtwoord-1' });
  await owner('PUT', `/api/users/${member.user.id}/access`, { access: [{ collection_id: music.id, can_upload: false }] });
  assert.equal((await lid('PUT', '/api/files/segue', { ids: [ok[0]], segue: false })).status, 403);
  await owner('PUT', '/api/files/segue', { ids: [ok[0], ok[1]], segue: false });

  // Removing a rule brings the tracks back.
  for (const b of blocks) assert.equal((await owner('DELETE', `/api/me/nonstop-blocks/${b.id}`)).status, 200);
  assert.equal((await owner('GET', `/api/files?collection_id=${music.id}&nonstop=blocked`)).data.total, 0);
  await owner('DELETE', `/api/clocks/${clock.id}`);
});

test('genres are read from the tags, also for files imported earlier', { skip: (!DB && 'TEST_DATABASE_URL not set') || (!hasFfmpeg && 'ffmpeg not installed') }, async () => {
  const { startGenreWorker } = require('../src/nonstop');
  const filesDir = path.join(storageDir, 'files');
  const { execFileSync } = require('child_process');
  execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=d=1', '-metadata', 'genre=Polka', '-c:a', 'flac', path.join(filesDir, 'genre-test.flac')]);
  const { rows: [c] } = await pool.query("INSERT INTO collections (name) VALUES ('Genres') RETURNING id");
  const { rows: [f] } = await pool.query(
    `INSERT INTO audio_files (collection_id, title, original_name, storage_key, mime_type, size_bytes)
     VALUES ($1, 'oud', 'oud.flac', 'genre-test.flac', 'audio/flac', 1) RETURNING id, genre`, [c.id]);
  assert.equal(f.genre, null, 'not read yet');
  const worker = startGenreWorker({ pool, filesDir });
  await worker.runOnce();
  worker.stop();
  const { rows: [after] } = await pool.query('SELECT genre FROM audio_files WHERE id = $1', [f.id]);
  assert.equal(after.genre, 'Polka');
  const { rows: [{ n }] } = await pool.query('SELECT count(*)::int AS n FROM audio_files WHERE genre IS NULL');
  assert.equal(n, 0, 'files without a genre tag get an empty genre, not read again');
});

test('new music is added automatically; files still copying wait; one import at a time', { skip: !DB && 'TEST_DATABASE_URL not set' }, async () => {
  const { importFolder } = require('../src/import');
  const { startAutoImport } = require('../src/autoimport');
  const src = fs.mkdtempSync(path.join(os.tmpdir(), 'auto-'));
  fs.mkdirSync(path.join(src, 'Nieuw'));
  const old = (file) => { const t = new Date(Date.now() - 10 * 60 * 1000); fs.utimesSync(file, t, t); };
  fs.writeFileSync(path.join(src, 'Nieuw', 'klaar.wav'), wav()); old(path.join(src, 'Nieuw', 'klaar.wav'));
  fs.writeFileSync(path.join(src, 'Nieuw', 'nog-bezig.wav'), wav()); // just written: may still be copying
  const filesDir = path.join(storageDir, 'files');
  const quiet = () => {};

  // Two imports at the same moment: the second one steps aside.
  const [a, b] = await Promise.all([
    importFolder({ pool, filesDir, dir: src, minAgeMs: 120000, log: quiet }),
    importFolder({ pool, filesDir, dir: src, minAgeMs: 120000, log: quiet }),
  ]);
  const first = a.busy ? b : a;
  assert.ok(a.busy !== b.busy, 'exactly one of them ran');
  assert.equal(first.added, 1);
  assert.equal(first.waiting, 1, 'the file that is still being written waits');

  // Through the automatic loop: once the file is done copying it is added.
  old(path.join(src, 'Nieuw', 'nog-bezig.wav'));
  const statusFile = path.join(storageDir, 'autoimport.json');
  const auto = startAutoImport({ pool, filesDir, statusFile, dir: src, minutes: 60 });
  await auto.round();
  auto.stop();
  const st = JSON.parse(fs.readFileSync(statusFile, 'utf8'));
  assert.equal(st.enabled, true);
  assert.equal(st.lastAdded, 1);
  assert.equal(st.waiting, 0);
  assert.ok(st.nextRun && !st.lastError);
  const { rows } = await pool.query(
    `SELECT f.title, c.name FROM audio_files f JOIN collections c ON c.id = f.collection_id WHERE f.source_path LIKE $1 ORDER BY f.title`, [`${src}%`]);
  assert.deepEqual(rows.map((r) => [r.title, r.name]), [['klaar', 'Nieuw'], ['nog-bezig', 'Nieuw']]);

  // Nothing new: nothing happens. A folder that is gone gives a clear message.
  assert.equal((await importFolder({ pool, filesDir, dir: src, log: quiet })).added, 0);
  fs.rmSync(src, { recursive: true, force: true });
  const gone = startAutoImport({ pool, filesDir, statusFile, dir: src, minutes: 60 });
  await gone.round(); gone.stop();
  assert.match(JSON.parse(fs.readFileSync(statusFile, 'utf8')).lastError, /niet bereikbaar/);
});
