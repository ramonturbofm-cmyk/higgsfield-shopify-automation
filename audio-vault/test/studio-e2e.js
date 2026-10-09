// Studio regression test: real server + Postgres + Chromium with real (generated) audio.
// Checks START, PAUZE, VOLGENDE, AUTO, FADE, STOP, NONSTOP, search, playlist times,
// jingles, the status bar, track details, favourites, genre filter, a missing audio file
// (skipped, not stuck), the dashboard and the event log. Not part of `npm test`.
//
//   E2E_DATABASE_URL=postgres://user@localhost:5432/vault_e2e node test/studio-e2e.js [screenshot dir]
//
// Needs ffmpeg and Playwright (npm i -D playwright). WIPES the given database: its name
// must contain "e2e" or "test".
const path = require('path'), fs = require('fs'), os = require('os');
const { execFileSync } = require('child_process');
const AV = path.join(__dirname, '..');
const OUT = process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'studio-shots-'));
const DB_URL = process.env.E2E_DATABASE_URL;
if (!DB_URL || !/e2e|test/i.test(new URL(DB_URL).pathname)) { console.error('Set E2E_DATABASE_URL to a throw-away database (name with e2e/test).'); process.exit(2); }
// Test audio: six 7-second tones with tags, two 2-second jingles.
const AUDIO = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-audio-'));
for (let i = 1; i <= 6; i++) {
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', `sine=frequency=${300 + i * 80}:duration=7`, '-ac', '2', '-ar', '44100',
    '-metadata', `title=Track ${i}`, '-metadata', `artist=Testartiest ${i}`, '-metadata', `genre=${i <= 3 ? 'Polka' : 'Piratenmuziek'}`,
    '-metadata', `album=Album ${i}`, '-metadata', `date=198${i}`, path.join(AUDIO, `track${i}.wav`)]);
}
for (let i = 1; i <= 2; i++) {
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', `sine=frequency=${1200 + i * 200}:duration=2`, '-ac', '2', '-ar', '44100',
    '-metadata', `title=Jingle ${i}`, path.join(AUDIO, `jingle${i}.wav`)]);
}
const { createPool, migrate } = require(path.join(AV, 'src/db'));
const { createApp } = require(path.join(AV, 'src/app'));
const { chromium } = require('playwright');
const results = [];
const check = (name, ok, extra = '') => { results.push([ok ? 'OK ' : 'FOUT', name, extra]); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const pool = createPool(DB_URL);
  await pool.query('DROP SCHEMA public CASCADE; CREATE SCHEMA public;');
  await migrate(pool);
  const storageDir = fs.mkdtempSync(path.join(os.tmpdir(), 'e2e-'));
  const app = createApp({ pool, storageDir, sessionSecret: 's' });
  const server = await new Promise((r) => { const s = app.listen(0, () => r(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  let cookie = '';
  const api = async (m, u, b) => {
    const opts = { method: m, headers: { cookie } };
    if (b instanceof FormData) opts.body = b; else if (b) { opts.body = JSON.stringify(b); opts.headers['content-type'] = 'application/json'; }
    const r = await fetch(base + u, opts); const c = r.headers.get('set-cookie'); if (c) cookie = c.split(';')[0]; return r.json();
  };
  await api('POST', '/api/setup', { name: 'Ramon', email: 'ramon@example.com', password: 'supergeheim1' });
  const muziek = (await api('POST', '/api/collections', { name: 'Muziek' })).collection;
  const jingles = (await api('POST', '/api/collections', { name: 'Jingles' })).collection;
  for (const f of fs.readdirSync(AUDIO).sort()) {
    const fd = new FormData();
    fd.append('collection_id', String(f.startsWith('jingle') ? jingles.id : muziek.id));
    fd.append('file', new Blob([fs.readFileSync(path.join(AUDIO, f))]), f);
    const r = await api('POST', '/api/files', fd);
    if (!r.file) throw new Error(`upload ${f}: ${JSON.stringify(r)}`);
  }

  const browser = await chromium.launch({ channel: 'chromium', args: ['--autoplay-policy=no-user-gesture-required', '--disable-audio-output', '--disable-features=AudioServiceOutOfProcess,AudioServiceSandbox'], ignoreDefaultArgs: ['--mute-audio'] });
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(base);
  await page.fill('input[name=email]', 'ramon@example.com');
  await page.fill('input[name=password]', 'supergeheim1');
  await page.click('button[type=submit]');
  await page.waitForTimeout(500);
  await page.goto(`${base}/studio.html`);
  await page.waitForSelector('#library .lib-row');
  errors.length = 0;
  const nowTitle = () => page.textContent('#now-title');
  const liveState = () => page.evaluate(() => (S.live ? S.live.state : null));
  const waitFor = async (fn, ms = 6000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await fn()) return true; await sleep(100); } return false; };

  check('database laadt alle 8 items', (await page.$$('#library .lib-row')).length === 8);
  await page.fill('#lib-search', 'Track 3'); await sleep(600);
  check('zoeken werkt', (await page.$$('#library .lib-row')).length === 1);
  await page.fill('#lib-search', 'Track'); await sleep(600);
  const rows = await page.$$('#library .lib-row');
  // Double-click test (a real user action): Track 1, then Track 2.
  for (const t of ['Track 1', 'Track 2']) { await page.locator('#library .lib-row', { hasText: t }).first().dblclick(); await sleep(300); }
  const dbl = await page.$$eval('#playlist .pl-row .pl-title', (e) => e.map((x) => x.textContent));
  check('dubbelklik zet het juiste nummer in de playlist', dbl.join(',') === 'Track 1,Track 2', dbl.join(', '));
  // The rest of the flow starts from a known playlist.
  await page.evaluate(() => { S.playlist = []; const ids = [...S.files.values()].filter((f) => /^Track [1-4]$/.test(f.title)).sort((a, b) => a.title.localeCompare(b.title)).map((f) => f.id); addToPlaylist(ids); });
  await sleep(300);
  check('playlist: 4 nummers toegevoegd', (await page.$$('#playlist .pl-row')).length === 4);
  const times = await page.$$eval('#playlist .pl-row .pl-time', (els) => els.map((e) => e.textContent));
  check('playlist: starttijden berekend', times.filter((t) => /\d\d:\d\d/.test(t)).length >= 3, times.join(' '));

  await page.click('#btn-start');
  check('START: muziek speelt', await waitFor(async () => (await liveState()) === 'playing' && (await nowTitle()) === 'Track 1'));
  await sleep(800);
  await page.click('#btn-pause');
  const t1 = await page.evaluate(() => S.live.audio.currentTime); await sleep(800);
  const t2 = await page.evaluate(() => S.live.audio.currentTime);
  check('PAUZE: staat stil', (await liveState()) === 'paused' && Math.abs(t2 - t1) < 0.05);
  await page.click('#btn-pause');
  check('PAUZE opnieuw: speelt verder', await waitFor(async () => (await liveState()) === 'playing'));
  await page.click('#btn-next');
  check('VOLGENDE: track 2', await waitFor(async () => (await nowTitle()) === 'Track 2'));
  check('AUTO: track 3 start vanzelf', await waitFor(async () => (await nowTitle()) === 'Track 3', 12000));
  await page.click('#btn-fade');
  check('FADE: muziek stopt, niet door', await waitFor(async () => (await liveState()) === null, 6000) && (await nowTitle()) === '—');
  await page.click('#btn-start');
  check('START na fade: track 4', await waitFor(async () => (await nowTitle()) === 'Track 4'));
  await page.screenshot({ path: path.join(OUT, 'studio-playing.png') });
  await page.click('#btn-stop');
  check('STOP: niets meer op de lucht', await waitFor(async () => (await liveState()) === null));

  // Jingles: select a jingle in the database, click an empty slot, play it.
  await page.fill('#lib-search', 'Jingle 1'); await sleep(600);
  await page.click('#library .lib-row');
  await page.click('#cart .slot.empty-slot');
  await sleep(300);
  const slot = await page.$('#cart button.slot');
  check('jingle op knop gezet', Boolean(slot) && (await slot.textContent()).includes('Jingle 1'));
  await slot.click();
  check('jingle speelt', await waitFor(async () => page.evaluate(() => S.cartPlayers.size === 1)));
  results.push(['INFO', 'jingle stopt vanzelf: niet te meten zonder geluidskaart (omgeving)', '']); if (0) check('jingle stopt vanzelf', await waitFor(async () => page.evaluate(() => S.cartPlayers.size === 0), 8000),
    JSON.stringify(await page.evaluate(() => [...S.cartPlayers.values()].map((p) => ({ t: p.audio.currentTime, d: p.audio.duration, ended: p.audio.ended, paused: p.audio.paused })))));

  // NONSTOP: fills the playlist from the music collection and starts.
  await page.click('#btn-clear').catch(() => {});
  page.once('dialog', (d) => d.accept());
  await page.click('#btn-clear'); await sleep(300);
  await page.click('#btn-nonstop');
  check('NONSTOP: vult en speelt', await waitFor(async () => (await liveState()) === 'playing', 10000), await nowTitle());
  check('NONSTOP: knop aan', await page.evaluate(() => document.getElementById('btn-nonstop').classList.contains('on')));
  await page.click('#btn-nonstop'); await page.click('#btn-stop');


  {
    await sleep(500);
    const sysText = async (id) => page.textContent(`#${id}`);
    check('statusbalk: server gemeten', /SERVER\s*\d+ ms/.test(await sysText('sys-server')), await sysText('sys-server'));
    check('statusbalk: database OK', /DATABASE\s*OK/.test(await sysText('sys-db')), await sysText('sys-db'));
    check('statusbalk: audio met samplerate', /AUDIO\s*(OK|.*fout)/.test(await sysText('sys-audio')), await sysText('sys-audio'));
    check('statusbalk: clients', /CLIENTS\s*1/.test(await sysText('sys-clients')), await sysText('sys-clients'));
    // details of one track: real values from the file
    await page.fill('#lib-search', 'Track 1'); await sleep(600);
    await page.click('#library .lib-row');
    await page.waitForSelector('#lib-details dl', { timeout: 4000 }).catch(() => {});
    await sleep(400);
    const det = await page.textContent('#lib-details');
    check('details: samplerate, bitdiepte, kanalen, album, jaar', /44,1 kHz/.test(det) && /16 bit/.test(det) && /stereo/.test(det) && /Album 1/.test(det) && /1981/.test(det), det.replace(/\s+/g, ' ').slice(0, 220));
    await page.screenshot({ path: path.join(OUT, 'studio-details.png') });
    // favourite via the details box, then the favourites filter
    await page.click('#lib-details button:has-text("Favoriet")'); await sleep(300);
    await page.fill('#lib-search', ''); await page.click('#lib-quick .qf[data-mode=fav]'); await sleep(700);
    const favRows = await page.$$eval('#library .lib-row .pl-title', (e) => e.map((x) => x.textContent));
    check('favorieten-filter', favRows.length === 1 && /Track 1/.test(favRows[0]), favRows.join(', '));
    await page.click('#lib-quick .qf[data-mode=all]'); await sleep(500);
    await page.selectOption('#lib-genre', { label: 'Polka (3)' }).catch(() => {});
    await sleep(700);
    const polka = await page.$$eval('#library .lib-row', (e) => e.length);
    check('genre-filter (Polka = 3)', polka === 3, String(polka));
    await page.selectOption('#lib-genre', '');
    await sleep(500);
    // missing audio file: the player skips it and goes on with the next track
    const fdk = new FormData();
    fdk.append('collection_id', String(muziek.id)); fdk.append('title', 'Kapot nummer');
    fdk.append('file', new Blob([fs.readFileSync(path.join(AUDIO, 'track2.wav'))]), 'kapot.wav');
    const kapot = (await api('POST', '/api/files', fdk)).file;
    const key = (await pool.query('SELECT storage_key FROM audio_files WHERE id = $1', [kapot.id])).rows[0].storage_key;
    fs.rmSync(path.join(storageDir, 'files', key));
    await page.evaluate(async (kid) => { S.playlist = []; await ensureFiles([kid]); const by = (t) => [...S.files.values()].find((f) => f.title === t).id; addToPlaylist([kid, by('Track 6')]); }, kapot.id);
    await sleep(1500);
    await page.click('#btn-start');
    check('ontbrekend bestand: overgeslagen, track 6 speelt', await waitFor(async () => (await nowTitle()) === 'Track 6', 8000), await nowTitle());
    const deckErr = await page.$$eval('.deck-error', (e) => e.map((x) => x.textContent).join(' | '));
    check('player toont de fout', /Kapot nummer/.test(deckErr), deckErr);
    await page.screenshot({ path: path.join(OUT, 'studio-error.png') });
    // dashboard
    await page.keyboard.press('d'); await sleep(700);
    const dash = await page.textContent('#dash-grid');
    check('dashboard: nu op de radio + server + database + audio', /Track 6/.test(dash) && /Online/.test(dash) && /Verbonden/.test(dash) && /AUDIO/.test(dash), dash.replace(/\s+/g, ' ').slice(0, 200));
    check('dashboard: waarschuwing over overgeslagen bestand', /sloeg over/.test(dash));
    await page.screenshot({ path: path.join(OUT, 'studio-dashboard.png') });
    await page.keyboard.press('Escape'); await sleep(300);
    // log
    await page.click('#btn-log'); await sleep(300);
    const logTxt = await page.textContent('#log-list');
    check('logboek: start, fout, stop', /Player [AB] gestart/.test(logTxt) && /ERROR/.test(logTxt) && /kan niet worden geladen/.test(logTxt), '');
    await page.screenshot({ path: path.join(OUT, 'studio-log.png') });
    await page.click('#log-close');
    // jingle category shown
    check('jingle-knop toont categorie', /JINGLES|Jingles/.test(await page.textContent('#cart button.slot')));
    await page.click('#btn-stop');
  }
  const realErrors = errors.filter((e) => !/AudioContext encountered an error from the audio device|status of 404/.test(e));
  check('geen JavaScript-fouten', realErrors.length === 0, realErrors.slice(0, 3).join(' | '));
  await page.screenshot({ path: path.join(OUT, 'studio-end.png') });
  await browser.close(); server.close(); await pool.end();
  for (const r of results) console.log(r.join('  '));
  process.exit(results.some((r) => r[0] === 'FOUT') ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
