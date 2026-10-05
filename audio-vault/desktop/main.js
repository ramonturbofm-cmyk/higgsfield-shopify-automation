// Audio OnAir Turbo desktop app: the studio from your own server in a dedicated,
// branded window. Keeps playing in the background, keeps the PC awake while it runs,
// and asks before closing while something is on air.
const { app, BrowserWindow, Menu, dialog, ipcMain, net, powerSaveBlocker, session, shell } = require('electron');
const fs = require('fs');
const os = require('os');
const { createServerManager } = require('./server-manager');
const path = require('path');

const ICON = path.join(__dirname, 'build', 'icon.png');
const configFile = () => path.join(app.getPath('userData'), 'config.json');

// Playout must start sound without a click and must never be throttled.
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
app.commandLine.appendSwitch('disable-background-timer-throttling');
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.setAppUserModelId('fm.turbo.audio-onair');
// Keep using the settings folder of the earlier name ("Audio OnAir Turbo"): server
// address, login and server folder survive the rename and the update.
app.setPath('userData', path.join(app.getPath('appData'), 'Audio OnAir Turbo'));

function readConfig() {
  try { return JSON.parse(fs.readFileSync(configFile(), 'utf8')); } catch { return {}; }
}
function writeConfig(patch) {
  const next = { ...readConfig(), ...patch };
  fs.mkdirSync(path.dirname(configFile()), { recursive: true });
  fs.writeFileSync(configFile(), JSON.stringify(next, null, 2));
  return next;
}

function normaliseUrl(input) {
  let value = String(input || '').trim();
  if (!value) throw new Error('Vul het adres van je server in');
  if (!/^https?:\/\//i.test(value)) value = `${/^(localhost|127\.|192\.168\.|10\.)/.test(value) ? 'http' : 'https'}://${value}`;
  const url = new URL(value);
  return url.origin;
}

let win = null;
let serverOrigin = null;

// ---------- databases on this PC ----------
// Each database is a fully separate server (own folder, port, music, users, backup).
const DEFAULT_SERVER_DIR = process.env.AOT_SERVER_DIR || (process.platform === 'win32' ? 'C:\\AudioOnAir' : path.join(os.homedir(), 'AudioOnAir'));
const managers = new Map();

function databases() {
  const cfg = readConfig();
  if (Array.isArray(cfg.databases) && cfg.databases.length) return cfg.databases;
  // First run after the update: the existing server becomes the first database.
  const list = [{ id: 'main', name: 'Hoofddatabase', dir: cfg.serverDir || DEFAULT_SERVER_DIR, project: 'audioonair', port: 3000, cookie: '' }];
  writeConfig({ databases: list, activeDb: 'main' });
  return list;
}
function activeDb() {
  const list = databases();
  return list.find((d) => d.id === readConfig().activeDb) || list[0];
}
function managerFor(db) {
  if (!managers.has(db.id)) {
    managers.set(db.id, createServerManager({
      sourceDir: app.isPackaged ? path.join(process.resourcesPath, 'server') : path.join(__dirname, '..'),
      version: app.getVersion(),
      dir: db.dir, project: db.project, port: db.port, cookie: db.cookie,
      onLog: (text) => sendAll('server-log', text),
    }));
  }
  return managers.get(db.id);
}
const current = () => managerFor(activeDb());
const busyManager = () => [...managers.values()].find((m) => m.busy);
const databaseForOrigin = (origin) => databases().find((d) => origin === `http://localhost:${d.port}`);

function updateDatabase(id, patch) {
  writeConfig({ databases: databases().map((d) => (d.id === id ? { ...d, ...patch } : d)) });
}

function createDatabase(name) {
  const clean = String(name || '').trim().slice(0, 40);
  if (!clean) throw new Error('Geef de database een naam');
  const list = databases();
  if (list.some((d) => d.name.toLowerCase() === clean.toLowerCase())) throw new Error('Er is al een database met deze naam');
  const base = clean.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'db';
  let slug = base; let n = 2;
  while (list.some((d) => d.id === slug)) slug = `${base}-${n++}`;
  const folder = clean.replace(/[^A-Za-z0-9 _-]/g, '').replace(/\s+/g, '') || slug;
  const db = {
    id: slug, name: clean, project: `audioonair-${slug}`,
    port: Math.max(...list.map((d) => d.port)) + 1,
    dir: `${DEFAULT_SERVER_DIR}-${folder}`,
    cookie: `av_session_${slug.replace(/-/g, '_')}`,
  };
  writeConfig({ databases: [...list, db], activeDb: db.id });
  buildMenu();
  return db;
}

// ---------- windows ----------
// The studio keeps its own window so the music never stops: every other screen
// (server management, library, clocks, …) opens in a second window while the studio
// is open, instead of replacing it.
let side = null;
const isStudioUrl = (url) => /\/studio\.html(\?|#|$)/.test(url || '');
const studioOpen = () => win && !win.isDestroyed() && isStudioUrl(win.webContents.getURL());
const allWindows = () => BrowserWindow.getAllWindows().filter((w) => !w.isDestroyed());
const sendAll = (channel, payload) => { for (const w of allWindows()) w.webContents.send(channel, payload); };
function sideWindow() {
  if (side && !side.isDestroyed()) { if (side.isMinimized()) side.restore(); side.focus(); return side; }
  const [x, y] = win.getPosition();
  side = new BrowserWindow({
    width: 1300, height: 900, x: x + 40, y: y + 40, minWidth: 900, minHeight: 600,
    title: 'Audio OnAir Turbo Database', icon: ICON, backgroundColor: '#000000', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  guardNavigation(side);
  side.webContents.on('did-fail-load', (e, code, description, url, isMainFrame) => {
    if (isMainFrame && !url.startsWith('file:')) side.loadFile(path.join(__dirname, 'server.html'));
  });
  side.on('closed', () => { side = null; });
  return side;
}
// Where a page goes: the studio window, unless the studio is open there.
function target() { return studioOpen() ? sideWindow() : win; }
function loadPage(page) {
  if (isStudioUrl(page)) return openStudio(page);
  return target().loadURL(serverOrigin + page);
}

function showServer(hash) {
  target().loadFile(path.join(__dirname, 'server.html'), hash ? { hash } : {});
}

function showConnect(error) {
  target().loadFile(path.join(__dirname, 'connect.html'), { query: error ? { error } : {} });
}

function openStudio(page = '/studio.html', { reload = false } = {}) {
  const url = serverOrigin + page;
  // Already playing in the studio window: just bring it forward, never reload it.
  if (!reload && studioOpen() && win.webContents.getURL().startsWith(serverOrigin)) { if (win.isMinimized()) win.restore(); win.focus(); return; }
  win.loadURL(url);
  win.focus();
}

async function checkServer(origin) {
  const res = await net.fetch(`${origin}/health`, { cache: 'no-store' });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.ok) throw new Error('Dit adres is geen Audio OnAir Turbo Database-server');
}

async function switchTo(db) {
  writeConfig({ activeDb: db.id });
  buildMenu();
  const { web } = await managerFor(db).status();
  if (web) { serverOrigin = `http://localhost:${db.port}`; writeConfig({ server: serverOrigin }); openStudio(); } else showServer();
}

function buildMenu() {
  const go = (page) => () => serverOrigin && loadPage(page);
  const active = activeDb();
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'Audio OnAir Turbo Database',
      submenu: [
        { label: 'Studio', accelerator: 'CmdOrCtrl+1', click: go('/studio.html') },
        { label: 'Uurklokken', accelerator: 'CmdOrCtrl+2', click: go('/klok.html') },
        { label: 'Bibliotheek en gebruikers', accelerator: 'CmdOrCtrl+3', click: go('/') },
        { label: 'Nu op de radio', accelerator: 'CmdOrCtrl+4', click: go('/nu.html') },
        { type: 'separator' },
        { label: 'Server beheren (deze pc)…', accelerator: 'CmdOrCtrl+5', click: () => showServer() },
        { label: 'Andere server kiezen…', click: () => showConnect() },
        { type: 'separator' },
        { role: 'quit', label: 'Afsluiten' },
      ],
    },
    {
      label: 'Database',
      submenu: [
        ...databases().map((db) => ({
          label: `${db.name}   (poort ${db.port})`, type: 'radio', checked: db.id === active.id, click: () => switchTo(db),
        })),
        { type: 'separator' },
        { label: 'Nieuwe database…', click: () => showServer('nieuw') },
      ],
    },
    {
      label: 'Beeld',
      submenu: [
        { role: 'togglefullscreen', label: 'Volledig scherm' },
        { role: 'resetZoom', label: 'Normale grootte' },
        { role: 'zoomIn', label: 'Inzoomen' },
        { role: 'zoomOut', label: 'Uitzoomen' },
        { type: 'separator' },
        { role: 'reload', label: 'Vernieuwen' },
      ],
    },
    {
      label: 'Help',
      submenu: [{
        label: 'Bijwerken / controleren op updates',
        click: () => checkForUpdates({ manual: true }),
      }, {
        label: 'Alle updates (downloadpagina)',
        click: () => shell.openExternal(UPDATES_PAGE),
      }, { type: 'separator' }, {
        label: 'Over Audio OnAir Turbo Database',
        click: () => dialog.showMessageBox(win, {
          type: 'info', icon: ICON, title: 'Over Audio OnAir Turbo Database',
          message: `Audio OnAir Turbo Database ${app.getVersion()}`,
          detail: `Radio playout studio\nVerbonden met: ${serverOrigin || '—'}\n\n© 2026 Turbo FM`,
        }),
      }],
    },
  ]));
}

// ---------- updates ----------
// Every new version is published on the updates page (GitHub Releases). The app looks
// there at start and every few hours, and offers the new installer. Nothing is
// installed without asking, and the playout keeps running while the dialog is open.
const UPDATES_PAGE = 'https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation/releases';
const UPDATES_API = 'https://api.github.com/repos/ramonturbofm-cmyk/higgsfield-shopify-automation/releases/latest';

function newerVersion(a, b) {
  const pa = String(a).replace(/^v/, '').split('.').map(Number);
  const pb = String(b).replace(/^v/, '').split('.').map(Number);
  for (let i = 0; i < 3; i++) if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0);
  return false;
}

// Latest release on the updates page, or null when this is the newest version.
let updateInfo = null;
async function fetchLatest() {
  const res = await net.fetch(UPDATES_API, { headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'Audio-OnAir-Turbo' } });
  if (!res.ok) throw new Error(`updatepagina gaf ${res.status}`);
  const release = await res.json();
  const latest = String(release.tag_name || '').replace(/^v/, '');
  const setup = (release.assets || []).find((a) => /Setup.*\.exe$/i.test(a.name) && !/Omzetter/i.test(a.name));
  updateInfo = latest && setup && newerVersion(latest, app.getVersion())
    ? { version: latest, url: setup.browser_download_url, name: setup.name, size: setup.size, notes: String(release.body || '').trim().slice(0, 900) }
    : null;
  sendUpdateStatus({ available: Boolean(updateInfo), version: updateInfo && updateInfo.version });
  return updateInfo;
}
function sendUpdateStatus(status) {
  sendAll('update-status', { current: app.getVersion(), ...status });
}

async function checkForUpdates({ manual = false } = {}) {
  try {
    const info = await fetchLatest();
    if (!info) {
      if (manual) dialog.showMessageBox(win, { type: 'info', icon: ICON, title: 'Updates', message: 'Je hebt de nieuwste versie', detail: `Audio OnAir Turbo Database ${app.getVersion()}` });
      return;
    }
    if (!manual && readConfig().skipVersion === info.version) return;
    await installUpdate();
  } catch (err) {
    if (manual) dialog.showMessageBox(win, { type: 'warning', icon: ICON, title: 'Updates', message: 'Kon niet controleren op updates', detail: `${err.message}\n\nAlle versies staan op ${UPDATES_PAGE}` });
  }
}

// One click: download the new installer, install it silently, restart the app.
// After the restart the server is brought up to date too (see updateServers).
let installing = false;
async function installUpdate() {
  if (installing) return { ok: false, error: 'De update wordt al geïnstalleerd' };
  const info = updateInfo || (await fetchLatest());
  if (!info) { sendUpdateStatus({ available: false }); return { ok: true, latest: true }; }
  const { response } = await dialog.showMessageBox(win, {
    type: 'info', icon: ICON, title: 'Update beschikbaar',
    message: `Bijwerken naar versie ${info.version}? (je hebt ${app.getVersion()})`,
    detail: `${info.notes ? `${info.notes}\n\n` : ''}De app downloadt de update, installeert hem en start opnieuw; daarna wordt de server vanzelf bijgewerkt. `
      + 'De uitzending stopt daarbij 1 à 2 minuten. Instellingen, databases en muziek blijven staan.',
    buttons: ['Nu bijwerken', 'Later', 'Deze versie overslaan'], defaultId: 0, cancelId: 1,
  });
  if (response === 2) writeConfig({ skipVersion: info.version });
  if (response !== 0) return { ok: false, cancelled: true };
  installing = true;
  try {
    // A fresh name each time: an earlier download may still be locked by the virus scanner.
    const file = path.join(os.tmpdir(), info.name.replace(/\.exe$/i, `-${Date.now()}.exe`));
    const res = await net.fetch(info.url);
    if (!res.ok || !res.body) throw new Error(`downloaden mislukt (${res.status})`);
    const total = Number(res.headers.get('content-length')) || info.size || 0;
    const out = fs.createWriteStream(file);
    let done = 0; let lastPct = -1;
    const reader = res.body.getReader();
    for (;;) {
      const { done: end, value } = await reader.read();
      if (end) break;
      done += value.length;
      if (!out.write(Buffer.from(value))) await new Promise((r) => out.once('drain', r));
      const pct = total ? Math.floor((done / total) * 100) : 0;
      if (pct !== lastPct) { lastPct = pct; if (win && !win.isDestroyed()) win.setProgressBar(pct / 100); sendUpdateStatus({ available: true, version: info.version, downloading: pct }); }
    }
    await new Promise((resolve, reject) => out.end((err) => (err ? reject(err) : resolve())));
    if (total && done < total) throw new Error('download onvolledig');
    writeConfig({ updatedFrom: app.getVersion() });
    sendUpdateStatus({ available: true, version: info.version, installing: true });
    // /S = silent install in the same folder, --force-run = start the app again afterwards.
    // Right after downloading, Windows (the virus scanner) often still has the file open
    // and starting it fails with EBUSY: keep trying for up to a minute.
    await startInstaller(file);
    setTimeout(() => app.exit(0), 500);
    return { ok: true };
  } catch (err) {
    installing = false;
    if (win && !win.isDestroyed()) win.setProgressBar(-1);
    sendUpdateStatus({ available: true, version: info.version, error: err.message });
    dialog.showMessageBox(win, { type: 'warning', icon: ICON, title: 'Bijwerken mislukt', message: 'De update kon niet worden geïnstalleerd', detail: `${err.message}\n\nJe kunt hem ook zelf downloaden: ${UPDATES_PAGE}` });
    return { ok: false, error: err.message };
  }
}

function startInstaller(file) {
  const once = () => new Promise((resolve, reject) => {
    let child;
    try { child = require('child_process').spawn(file, ['/S', '--force-run'], { detached: true, stdio: 'ignore' }); } catch (err) { reject(err); return; }
    child.once('error', reject);
    child.once('spawn', () => { child.unref(); resolve(); });
  });
  return (async () => {
    for (let attempt = 1; ; attempt++) {
      try { return await once(); } catch (err) {
        if (!/EBUSY|EPERM|EACCES/.test(err.code || err.message) || attempt >= 30) throw err;
        sendUpdateStatus({ available: true, installing: true });
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
  })();
}

// After an app update: rebuild every local server that still runs the old version,
// showing the progress on the Server beheren screen, then go back to the studio.
async function updateServers() {
  const outdated = databases().map((db) => ({ db, m: managerFor(db) })).filter(({ m }) => m.outdated);
  if (!outdated.length) return;
  const active = outdated.find(({ db }) => db.id === activeDb().id);
  if (active) showServer();
  for (const { db, m } of outdated) {
    for (let attempt = 0; attempt < 20; attempt++) {
      try {
        const st = await m.status();
        if (st.docker !== 'running') { if (attempt === 0 && st.docker === 'stopped') await m.startDocker().catch(() => {}); throw new Error('Docker start nog op'); }
        const code = await m.run('start');
        if (code) throw new Error(`server bijwerken gaf code ${code}`);
        if (db === (active && active.db)) {
          serverOrigin = `http://localhost:${db.port}`;
          setTimeout(() => openStudio('/studio.html', { reload: true }), 3000);
        }
        break;
      } catch (err) {
        sendAll('server-log', `Server bijwerken: ${err.message} — opnieuw over 30 seconden…\n`);
        await new Promise((r) => setTimeout(r, 30000));
      }
    }
  }
}

ipcMain.handle('update', async (e, action) => {
  try {
    if (action === 'check') { await fetchLatest(); return { ok: true, current: app.getVersion(), available: Boolean(updateInfo), version: updateInfo && updateInfo.version }; }
    if (action === 'install') return await installUpdate();
    throw new Error('Onbekend verzoek');
  } catch (err) {
    return { ok: false, error: err.message, current: app.getVersion() };
  }
});

// Stay on the own server; everything else opens in the normal browser. A link from
// the studio to another page opens in the second window; a link back to the studio
// brings the studio window forward.
function guardNavigation(w) {
  const allowed = (url) => url.startsWith('file:') || (serverOrigin && new URL(url).origin === serverOrigin);
  const route = (url) => {
    if (!allowed(url)) { if (/^https?:/.test(url)) shell.openExternal(url); return true; }
    if (w === win && studioOpen() && !isStudioUrl(url)) { sideWindow().loadURL(url); return true; }
    if (w !== win && isStudioUrl(url)) { openStudio(); return true; }
    return false;
  };
  w.webContents.on('will-navigate', (e, url) => { if (route(url)) e.preventDefault(); });
  w.webContents.setWindowOpenHandler(({ url }) => {
    if (!route(url)) w.loadURL(url);
    return { action: 'deny' };
  });
}

function createWindow() {
  const cfg = readConfig();
  const bounds = cfg.bounds || { width: 1600, height: 950 };
  win = new BrowserWindow({
    ...bounds,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    title: 'Audio OnAir Turbo Database',
    icon: ICON,
    backgroundColor: '#000000',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });
  if (cfg.maximized) win.maximize();
  win.once('ready-to-show', () => win.show());

  guardNavigation(win);
  win.webContents.on('page-title-updated', (e, title) => {
    const db = !win.webContents.getURL().startsWith('file:') && databaseForOrigin(serverOrigin);
    if (db && databases().length > 1) { e.preventDefault(); win.setTitle(`${title} — ${db.name}`); }
  });
  win.webContents.on('did-fail-load', (e, code, description, url, isMainFrame) => {
    if (isMainFrame && !url.startsWith('file:')) showConnect(`Server niet bereikbaar (${description}). Staat de server-pc aan?`);
  });

  // Microphone permission is only used to show sound card names; only our server may ask.
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((wc, permission, cb, details) => {
    const origin = details.requestingUrl ? new URL(details.requestingUrl).origin : '';
    cb(['media', 'speaker-selection'].includes(permission) && origin === serverOrigin);
  });
  ses.setPermissionCheckHandler((wc, permission, origin) => ['media', 'speaker-selection'].includes(permission) && origin === serverOrigin);

  let confirmed = false;
  win.on('close', async (e) => {
    writeConfig({ bounds: win.getNormalBounds(), maximized: win.isMaximized() });
    if (confirmed) return;
    e.preventDefault();
    const onAir = await win.webContents
      .executeJavaScript('typeof S !== "undefined" && !!S.live && S.live.state === "playing"', true)
      .catch(() => false);
    if (!onAir && busyManager()) {
      const { response } = await dialog.showMessageBox(win, {
        type: 'warning', icon: ICON, buttons: ['Blijven', 'Toch afsluiten'], defaultId: 0, cancelId: 0,
        title: 'Nog bezig', message: `"${busyManager().busy}" is nog bezig.`, detail: 'Wacht tot het klaar is voordat je afsluit.',
      });
      if (response !== 1) return;
    }
    if (onAir) {
      const { response } = await dialog.showMessageBox(win, {
        type: 'warning', icon: ICON, buttons: ['Blijven', 'Toch afsluiten'], defaultId: 0, cancelId: 0,
        title: 'Er speelt nog iets', message: 'Er is nog iets on air.',
        detail: 'Als je nu afsluit, stopt de uitzending.',
      });
      if (response !== 1) return;
    }
    confirmed = true;
    win.close();
  });
  // Closing the studio window closes the app, second window included.
  win.on('closed', () => { if (side && !side.isDestroyed()) side.destroy(); });

  if (cfg.server) {
    serverOrigin = cfg.server;
    openStudio();
  } else {
    showConnect();
  }
}

ipcMain.handle('open-server', () => showServer());

// Everything the "Server beheren" screen can ask for.
ipcMain.handle('server', async (e, method, ...args) => {
  try {
    switch (method) {
      case 'status': return { ...(await current().status()), database: activeDb() };
      case 'saveSettings': {
        const res = current().saveSettings(args[0] || {});
        updateDatabase(activeDb().id, { dir: res.dir });
        if (activeDb().id === 'main') writeConfig({ serverDir: res.dir });
        return res;
      }
      case 'databases': return { list: databases(), active: activeDb().id };
      case 'switchDatabase': {
        const db = databases().find((d) => d.id === args[0]);
        if (!db) throw new Error('Database niet gevonden');
        writeConfig({ activeDb: db.id }); buildMenu();
        return { ok: true };
      }
      case 'createDatabase': return { ok: true, database: createDatabase(args[0]) };
      case 'renameDatabase': {
        const name = String(args[0] || '').trim().slice(0, 40);
        if (!name) throw new Error('Geef de database een naam');
        if (databases().some((d) => d.id !== activeDb().id && d.name.toLowerCase() === name.toLowerCase())) throw new Error('Er is al een database met deze naam');
        updateDatabase(activeDb().id, { name }); buildMenu();
        return { ok: true };
      }
      case 'removeDatabase': {
        const db = activeDb();
        if (db.id === 'main') throw new Error('De hoofddatabase blijft altijd in de lijst');
        const st = await current().status();
        if (st.containers.some((c) => c.state === 'running')) throw new Error('Stop eerst de server van deze database');
        writeConfig({ databases: databases().filter((d) => d.id !== db.id), activeDb: 'main' });
        managers.delete(db.id); buildMenu();
        return { ok: true, dir: db.dir };
      }
      case 'chooseFolder': {
        const r = await dialog.showOpenDialog(BrowserWindow.fromWebContents(e.sender) || win, {
          title: { music: 'Map met muziek kiezen', archive: 'Map voor het nieuwe FLAC-archief kiezen' }[args[0]] || 'Map voor de server kiezen',
          properties: ['openDirectory', 'createDirectory'],
        });
        return r.canceled ? null : r.filePaths[0];
      }
      case 'run': return { ok: true, code: await current().run(args[0], args[1]) };
      case 'listBackups': return await current().listBackups();
      case 'startDocker': return current().startDocker();
      case 'installDocker': return { ok: true, code: await current().installDocker() };
      case 'saveArchive': return current().saveArchiveSettings(args[0] || {});
      case 'freeSpace': return current().freeBytes(String(args[0] || ''));
      case 'getAutostart': return app.getLoginItemSettings().openAtLogin;
      case 'setAutostart': app.setLoginItemSettings({ openAtLogin: Boolean(args[0]) }); return app.getLoginItemSettings().openAtLogin;
      case 'openDockerDownload': return shell.openExternal('https://www.docker.com/products/docker-desktop/');
      case 'openStudio': {
        const { port } = await current().status();
        serverOrigin = `http://localhost:${port}`;
        writeConfig({ server: serverOrigin });
        openStudio();
        return { ok: true };
      }
      default: throw new Error('Onbekend verzoek');
    }
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle('config', () => ({ server: readConfig().server || '', version: app.getVersion() }));
ipcMain.handle('connect', async (e, input) => {
  try {
    const origin = normaliseUrl(input);
    await checkServer(origin);
    serverOrigin = origin;
    writeConfig({ server: origin });
    openStudio();
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message.includes('ERR_') ? 'Server niet bereikbaar. Klopt het adres en staat de server-pc aan?' : err.message };
  }
});

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.whenReady().then(() => {
    powerSaveBlocker.start('prevent-app-suspension');
    databases(); // migrate the single server of earlier versions into the list
    buildMenu();
    createWindow();
    setTimeout(() => updateServers().catch(() => {}), 2000);
    setTimeout(() => checkForUpdates(), 15 * 1000);
    setInterval(() => checkForUpdates(), 6 * 60 * 60 * 1000);
  });
  app.on('window-all-closed', () => app.quit());
}
