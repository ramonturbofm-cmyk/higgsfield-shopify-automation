// Audio OnAir Turbo Omzetter: converts a WAV archive to FLAC in a new folder,
// with the same bit-exact check as the server. ffmpeg is built in.
const { app, BrowserWindow, Menu, dialog, ipcMain, powerSaveBlocker, shell } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { Worker } = require('worker_threads');

const unpacked = (p) => p.replace(`app.asar${path.sep}`, `app.asar.unpacked${path.sep}`);
const FFMPEG = unpacked(require('ffmpeg-static'));
const FFPROBE = unpacked(require('ffprobe-static').path);
const WORKER = unpacked(path.join(__dirname, 'engine-worker.js'));
const ICON = path.join(__dirname, 'build', 'icon.png');
const configFile = () => path.join(app.getPath('userData'), 'config.json');
const readConfig = () => { try { return JSON.parse(fs.readFileSync(configFile(), 'utf8')); } catch { return {}; } };
const writeConfig = (patch) => {
  fs.mkdirSync(path.dirname(configFile()), { recursive: true });
  fs.writeFileSync(configFile(), JSON.stringify({ ...readConfig(), ...patch }, null, 2));
};

// Same settings folder as before the rename, so the last chosen folders are remembered.
app.setPath('userData', path.join(app.getPath('appData'), 'Audio OnAir Turbo Omzetter'));

let win = null;
let job = null; // running conversion worker
let blocker = null;

function freeBytes(dir) {
  let p = dir;
  while (p && !fs.existsSync(p)) { const up = path.dirname(p); if (up === p) break; p = up; }
  try { const st = fs.statfsSync(p); return st.bavail * st.bsize; } catch { return null; }
}

function runWorker(data, onMessage) {
  const worker = new Worker(WORKER, { workerData: { ...data, ffmpeg: FFMPEG, ffprobe: FFPROBE } });
  worker.on('message', onMessage);
  worker.on('error', (err) => onMessage({ type: 'error', message: err.message }));
  return worker;
}

ipcMain.handle('config', () => ({ ...readConfig(), version: app.getVersion(), cores: os.cpus().length }));

ipcMain.handle('choose', async (e, kind) => {
  const cfg = readConfig();
  const r = await dialog.showOpenDialog(win, {
    title: kind === 'source' ? 'Kies je map met WAV-bestanden' : 'Kies (of maak) een nieuwe map voor de FLAC-bestanden',
    defaultPath: kind === 'source' ? cfg.source : cfg.target,
    properties: ['openDirectory', 'createDirectory'],
  });
  if (r.canceled) return null;
  writeConfig({ [kind]: r.filePaths[0] });
  return r.filePaths[0];
});

ipcMain.handle('scan', (e, source, target) => new Promise((resolve) => {
  const worker = runWorker({ mode: 'scan', source, target }, (msg) => {
    worker.terminate();
    if (msg.type === 'scan') resolve({ ok: true, ...msg.result, freeBytes: target ? freeBytes(target) : null });
    else resolve({ ok: false, error: msg.message });
  });
}));

ipcMain.handle('start', (e, source, target) => {
  if (job) return { ok: false, error: 'Er loopt al een omzetting' };
  const jobs = Math.min(6, Math.max(2, Math.floor(os.cpus().length / 2)));
  blocker = powerSaveBlocker.start('prevent-app-suspension'); // the PC must not fall asleep halfway
  job = runWorker({ mode: 'convert', source, target, jobs }, (msg) => {
    if (win && !win.isDestroyed()) win.webContents.send('event', msg);
    if (msg.type === 'done' || msg.type === 'error') {
      if (job) job.terminate();
      job = null;
      if (blocker !== null) { powerSaveBlocker.stop(blocker); blocker = null; }
      if (win && !win.isDestroyed()) win.setProgressBar(-1);
    } else if (msg.type === 'file' && win && !win.isDestroyed()) {
      win.setProgressBar(msg.done / msg.total); // progress on the taskbar icon too
    }
  });
  return { ok: true, jobs };
});

ipcMain.handle('pause', () => { if (job) job.postMessage('stop'); return { ok: true }; });
ipcMain.handle('open', (e, p) => shell.openPath(p));

function createWindow() {
  win = new BrowserWindow({
    width: 940, height: 780, minWidth: 760, minHeight: 640, show: false, title: 'Audio OnAir Turbo Database Omzetter',
    icon: ICON, backgroundColor: '#000000', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true },
  });
  win.once('ready-to-show', () => win.show());
  win.loadFile(path.join(__dirname, 'index.html'));
  win.on('close', async (e) => {
    if (!job) return;
    e.preventDefault();
    const { response } = await dialog.showMessageBox(win, {
      type: 'question', icon: ICON, buttons: ['Doorgaan met omzetten', 'Pauzeren en afsluiten'], defaultId: 0, cancelId: 0,
      title: 'Omzetten is bezig', message: 'Het omzetten is nog bezig.',
      detail: 'Als je afsluit, wordt het gepauzeerd. Later ga je verder waar je was.',
    });
    if (response === 1) {
      job.postMessage('stop');
      const stopping = job;
      stopping.once('exit', () => { job = null; win.destroy(); });
      setTimeout(() => { if (!win.isDestroyed()) win.destroy(); }, 15000);
    }
  });
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.whenReady().then(() => { Menu.setApplicationMenu(null); createWindow(); });
  app.on('window-all-closed', () => app.quit());
}
