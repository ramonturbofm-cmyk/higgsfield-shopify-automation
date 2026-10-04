// Audio OnAir Turbo desktop app: the studio from your own server in a dedicated,
// branded window. Keeps playing in the background, keeps the PC awake while it runs,
// and asks before closing while something is on air.
const { app, BrowserWindow, Menu, dialog, ipcMain, net, powerSaveBlocker, session, shell } = require('electron');
const fs = require('fs');
const { createServerManager } = require('./server-manager');
const path = require('path');

const ICON = path.join(__dirname, 'build', 'icon.png');
const configFile = () => path.join(app.getPath('userData'), 'config.json');

// Playout must start sound without a click and must never be throttled.
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
app.commandLine.appendSwitch('disable-background-timer-throttling');
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.setAppUserModelId('fm.turbo.audio-onair');

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
let manager = null;

function showServer() {
  win.loadFile(path.join(__dirname, 'server.html'));
}

function showConnect(error) {
  win.loadFile(path.join(__dirname, 'connect.html'), { query: error ? { error } : {} });
}

function openStudio(page = '/studio.html') {
  win.loadURL(serverOrigin + page);
}

async function checkServer(origin) {
  const res = await net.fetch(`${origin}/health`, { cache: 'no-store' });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.ok) throw new Error('Dit adres is geen Audio OnAir Turbo-server');
}

function buildMenu() {
  const go = (page) => () => serverOrigin && openStudio(page);
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'Audio OnAir Turbo',
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
        label: 'Over Audio OnAir Turbo',
        click: () => dialog.showMessageBox(win, {
          type: 'info', icon: ICON, title: 'Over Audio OnAir Turbo',
          message: `Audio OnAir Turbo ${app.getVersion()}`,
          detail: `Radio playout studio\nVerbonden met: ${serverOrigin || '—'}\n\n© 2026 Turbo FM`,
        }),
      }],
    },
  ]));
}

function createWindow() {
  const cfg = readConfig();
  const bounds = cfg.bounds || { width: 1600, height: 950 };
  win = new BrowserWindow({
    ...bounds,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    title: 'Audio OnAir Turbo',
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

  // Stay on the own server; everything else opens in the normal browser.
  const allowed = (url) => url.startsWith('file:') || (serverOrigin && new URL(url).origin === serverOrigin);
  win.webContents.on('will-navigate', (e, url) => { if (!allowed(url)) { e.preventDefault(); shell.openExternal(url); } });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (allowed(url)) { win.loadURL(url); } else if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: 'deny' };
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
    if (!onAir && manager && manager.busy) {
      const { response } = await dialog.showMessageBox(win, {
        type: 'warning', icon: ICON, buttons: ['Blijven', 'Toch afsluiten'], defaultId: 0, cancelId: 0,
        title: 'Nog bezig', message: `"${manager.busy}" is nog bezig.`, detail: 'Wacht tot het klaar is voordat je afsluit.',
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
      case 'status': return await manager.status();
      case 'saveSettings': {
        const res = manager.saveSettings(args[0] || {});
        writeConfig({ serverDir: res.dir });
        return res;
      }
      case 'chooseFolder': {
        const r = await dialog.showOpenDialog(win, {
          title: args[0] === 'music' ? 'Map met muziek kiezen' : 'Map voor de server kiezen',
          properties: ['openDirectory', 'createDirectory'],
        });
        return r.canceled ? null : r.filePaths[0];
      }
      case 'run': return { ok: true, code: await manager.run(args[0], args[1]) };
      case 'listBackups': return await manager.listBackups();
      case 'startDocker': return manager.startDocker();
      case 'openDockerDownload': return shell.openExternal('https://www.docker.com/products/docker-desktop/');
      case 'openStudio': {
        const { port } = await manager.status();
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
    manager = createServerManager({
      sourceDir: app.isPackaged ? path.join(process.resourcesPath, 'server') : path.join(__dirname, '..'),
      version: app.getVersion(),
      dir: readConfig().serverDir,
      onLog: (text) => { if (win && !win.isDestroyed()) win.webContents.send('server-log', text); },
    });
    buildMenu();
    createWindow();
  });
  app.on('window-all-closed', () => app.quit());
}
