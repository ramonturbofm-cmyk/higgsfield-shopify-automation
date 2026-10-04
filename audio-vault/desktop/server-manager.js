// Runs the Audio OnAir Turbo server on this PC through Docker Desktop: installs the
// server files into a folder, writes its .env, and starts/stops/imports/backs up
// with `docker compose`. Everything the .bat files did, behind buttons.
const { spawn, execFile } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const PROJECT = 'audioonair';
const SERVER_FILES = [
  'src', 'public', 'backup', 'package.json', 'package-lock.json', 'Dockerfile', '.dockerignore',
  'docker-compose.yml', 'docker-compose.nas.yml', 'docker-compose.nas-backup.yml', 'docker-entrypoint.sh', 'docker.env.example',
];
const DEFAULT_DIR = process.env.AOT_SERVER_DIR || (process.platform === 'win32' ? 'C:\\AudioOnAir' : path.join(os.homedir(), 'AudioOnAir'));
const DOCKER_WIN = 'C:\\Program Files\\Docker\\Docker';
const DOCKERIGNORE = ['data', 'import', 'storage', 'backup', 'desktop', 'test', 'node_modules', '.env', '.env.*', '*.bat', '*.cmd', ''].join('\n');

function createServerManager({ sourceDir, version, onLog, dir }) {
  let serverDir = dir || DEFAULT_DIR;
  let busy = null;

  // ---------- .env ----------

  const envPath = () => path.join(serverDir, '.env');
  function readEnv() {
    const env = {};
    try {
      for (const line of fs.readFileSync(envPath(), 'utf8').split(/\r?\n/)) {
        const m = /^([A-Z_]+)=(.*)$/.exec(line.trim());
        if (m) env[m[1]] = m[2];
      }
    } catch { /* not installed yet */ }
    return env;
  }
  function writeEnv(env) {
    const lines = ['# Gemaakt door Audio OnAir Turbo — wijzig liever via "Server beheren" in de app.'];
    for (const [k, v] of Object.entries(env)) if (v !== undefined && v !== null) lines.push(`${k}=${String(v).replace(/[\r\n]/g, '')}`);
    fs.writeFileSync(envPath(), lines.join('\n') + '\n');
  }

  const installed = () => fs.existsSync(envPath()) && fs.existsSync(path.join(serverDir, 'docker-compose.yml'));

  // Copy the bundled server files; never touches .env or data/.
  function installFiles() {
    fs.mkdirSync(serverDir, { recursive: true });
    for (const name of SERVER_FILES) {
      const from = path.join(sourceDir, name);
      if (fs.existsSync(from)) fs.cpSync(from, path.join(serverDir, name), { recursive: true, force: true });
    }
    // Hidden files are not always bundled, and this one keeps data/ (database, audio)
    // out of every image build, so always write it.
    fs.writeFileSync(path.join(serverDir, '.dockerignore'), DOCKERIGNORE);
    fs.writeFileSync(path.join(serverDir, '.version'), version);
  }
  function filesOutdated() {
    try { return fs.readFileSync(path.join(serverDir, '.version'), 'utf8').trim() !== version; } catch { return true; }
  }

  function composeFiles(env = readEnv()) {
    const files = ['-f', 'docker-compose.yml'];
    if (env.NAS_PASSWORD) files.push('-f', 'docker-compose.nas.yml');
    if (env.NAS_BACKUP_PASSWORD) files.push('-f', 'docker-compose.nas-backup.yml');
    if (process.env.AOT_COMPOSE_EXTRA) files.push('-f', process.env.AOT_COMPOSE_EXTRA); // tests only
    return files;
  }

  // ---------- docker ----------

  function dockerBin() {
    if (process.platform === 'win32') {
      const full = path.join(DOCKER_WIN, 'resources', 'bin', 'docker.exe');
      if (fs.existsSync(full)) return full;
    }
    return 'docker';
  }

  function capture(args, { timeout = 30000 } = {}) {
    return new Promise((resolve) => {
      execFile(dockerBin(), args, { cwd: fs.existsSync(serverDir) ? serverDir : undefined, timeout, windowsHide: true, maxBuffer: 10 * 1024 * 1024 },
        (err, stdout, stderr) => resolve({ code: err ? (typeof err.code === 'number' ? err.code : 1) : 0, stdout: String(stdout), stderr: String(stderr), missing: err && err.code === 'ENOENT' }));
    });
  }

  // Streams output to the log panel; resolves with the exit code.
  function stream(args, label) {
    return new Promise((resolve) => {
      onLog(`\n▶ ${label}\n`);
      const child = spawn(dockerBin(), args, { cwd: serverDir, windowsHide: true });
      child.stdout.on('data', (d) => onLog(String(d)));
      child.stderr.on('data', (d) => onLog(String(d)));
      child.on('error', (err) => { onLog(`Fout: ${err.message}\n`); resolve(1); });
      child.on('close', (code) => { onLog(code === 0 ? '✓ Klaar\n' : `✗ Mislukt (code ${code})\n`); resolve(code); });
    });
  }
  const compose = (args, label) => stream(['compose', '-p', PROJECT, ...composeFiles(), ...args], label);

  async function dockerState() {
    const r = await capture(['version', '--format', '{{.Server.Version}}'], { timeout: 15000 });
    if (r.missing) return 'missing';
    return r.code === 0 && r.stdout.trim() ? 'running' : 'stopped';
  }

  // ---------- status ----------

  async function status() {
    const docker = await dockerState();
    const env = readEnv();
    const result = {
      dir: serverDir, installed: installed(), outdated: installed() && filesOutdated(), docker, containers: [], web: false, lastBackup: null, busy,
      port: Number(env.PORT || 3000),
      settings: {
        musicDir: env.MUSIC_DIR && env.MUSIC_DIR !== './import' ? env.MUSIC_DIR : '',
        nasHost: env.NAS_HOST || '', nasShare: env.NAS_SHARE || '', nasUser: env.NAS_USER || '', hasNasPassword: Boolean(env.NAS_PASSWORD),
        backupShare: env.NAS_BACKUP_SHARE || '', backupUser: env.NAS_BACKUP_USER || '', hasBackupPassword: Boolean(env.NAS_BACKUP_PASSWORD),
        publicUrl: env.PUBLIC_URL || '',
      },
    };
    if (docker !== 'running' || !result.installed) return result;
    const ps = await capture(['compose', '-p', PROJECT, ...composeFiles(env), 'ps', '-a', '--format', 'json']);
    for (const line of ps.stdout.split('\n')) {
      const t = line.trim();
      if (!t) continue;
      try {
        for (const c of [].concat(JSON.parse(t))) result.containers.push({ service: c.Service, state: c.State, health: c.Health || '' });
      } catch { /* ignore partial output */ }
    }
    try {
      const res = await fetch(`http://localhost:${result.port}/health`, { signal: AbortSignal.timeout(2000) });
      result.web = res.ok;
    } catch { /* not reachable */ }
    if (result.containers.some((c) => c.service === 'backup' && c.state === 'running')) {
      const log = await capture(['compose', '-p', PROJECT, ...composeFiles(env), 'exec', '-T', 'backup', 'sh', '-c', 'grep -E "Backup klaar|MISLUKT" /backup/backup.log | tail -n 1']);
      result.lastBackup = log.stdout.trim() || null;
    }
    return result;
  }

  // ---------- actions ----------

  function saveSettings(input) {
    if (input.dir && !installed()) serverDir = input.dir;
    const env = readEnv();
    const next = {
      DB_PASSWORD: env.DB_PASSWORD || crypto.randomBytes(24).toString('hex'),
      SESSION_SECRET: env.SESSION_SECRET || crypto.randomBytes(32).toString('hex'),
      PORT: env.PORT || '3000',
      PUBLIC_URL: input.publicUrl ?? env.PUBLIC_URL ?? '',
      MAX_UPLOAD_MB: env.MAX_UPLOAD_MB || '1000',
      MUSIC_DIR: input.musicDir ? input.musicDir.replace(/\\/g, '/') : './import',
      BACKUP_DIR: env.BACKUP_DIR || './data/backup',
      BACKUP_KEEP_DAYS: env.BACKUP_KEEP_DAYS || '30',
      NAS_HOST: input.nasHost || '',
      NAS_SHARE: input.nasShare || '',
      NAS_USER: input.nasUser || '',
      // An empty password field means "keep the current one". Music comes from the
      // NAS only when a share is filled in; the NAS may also be used for backups only.
      NAS_PASSWORD: input.nasHost && input.nasShare ? (input.nasPassword || env.NAS_PASSWORD || '') : '',
      NAS_BACKUP_SHARE: input.backupShare || '',
      NAS_BACKUP_USER: input.backupUser || '',
      NAS_BACKUP_PASSWORD: input.backupShare ? (input.backupPassword || env.NAS_BACKUP_PASSWORD || '') : '',
    };
    if ((next.NAS_SHARE || next.NAS_USER) && (!next.NAS_HOST || !next.NAS_SHARE || !next.NAS_USER || !next.NAS_PASSWORD)) {
      throw new Error('Vul voor muziek op de NAS het IP-adres, de map, gebruiker en wachtwoord in');
    }
    if (next.NAS_HOST && !next.NAS_SHARE && !next.NAS_BACKUP_SHARE) throw new Error('Vul bij de NAS ook de gedeelde map in');
    if (next.NAS_BACKUP_SHARE && (!next.NAS_HOST || !next.NAS_BACKUP_USER || !next.NAS_BACKUP_PASSWORD)) {
      throw new Error('Vul voor de back-up naar de NAS het NAS-adres, de map, gebruiker en wachtwoord in');
    }
    installFiles();
    fs.mkdirSync(path.join(serverDir, 'import'), { recursive: true });
    writeEnv(next);
    return { ok: true, dir: serverDir };
  }

  async function exclusive(name, fn) {
    if (busy) throw new Error(`Even geduld: "${busy}" is nog bezig`);
    busy = name;
    try { return await fn(); } finally { busy = null; }
  }

  // Windows 11 has winget: install Docker Desktop without hunting for a download.
  function installDocker() {
    return exclusive('Docker Desktop installeren', () => new Promise((resolve) => {
      if (process.platform !== 'win32') { onLog('Automatisch installeren kan alleen op Windows.\n'); resolve(1); return; }
      onLog('\n▶ Docker Desktop installeren (Windows vraagt om toestemming)…\n');
      const child = spawn('winget', ['install', '-e', '--id', 'Docker.DockerDesktop', '--accept-package-agreements', '--accept-source-agreements'], { windowsHide: true });
      child.stdout.on('data', (d) => onLog(String(d)));
      child.stderr.on('data', (d) => onLog(String(d)));
      child.on('error', () => { onLog('winget niet gevonden. Download Docker Desktop via de knop "Downloaden".\n'); resolve(1); });
      child.on('close', (code) => {
        onLog(code === 0 ? '✓ Docker Desktop is geïnstalleerd. Herstart de pc en open daarna deze app opnieuw.\n' : `✗ Installeren mislukt (code ${code}). Probeer de knop "Downloaden".\n`);
        resolve(code);
      });
    }));
  }

  async function startDocker() {
    if (process.platform === 'win32') {
      const exe = path.join(DOCKER_WIN, 'Docker Desktop.exe');
      if (!fs.existsSync(exe)) throw new Error('Docker Desktop is niet geïnstalleerd');
      spawn(exe, [], { detached: true, stdio: 'ignore' }).unref();
      return { ok: true };
    }
    throw new Error('Start Docker zelf op deze computer');
  }

  const actions = {
    start: () => exclusive('Server starten', async () => {
      if (!installed()) throw new Error('Vul eerst de instellingen in en sla ze op');
      if (filesOutdated()) installFiles(); // app update → new server version
      onLog('De eerste keer bouwen kan een paar minuten duren…\n');
      return compose(['up', '-d', '--build', '--remove-orphans'], 'Server starten');
    }),
    stop: () => exclusive('Server stoppen', () => compose(['stop'], 'Server stoppen')),
    import: () => exclusive('Muziek importeren', () => compose(
      ['exec', '-T', '-u', 'node', 'app', 'node', 'src/import.js', '/import', '--per-folder', '--collection', 'Muziek'], 'Muziek importeren')),
    backup: () => exclusive('Back-up maken', () => compose(['exec', '-T', 'backup', 'sh', '/backup.sh', 'now'], 'Back-up maken')),
    restore: (dump) => exclusive('Back-up terugzetten', async () => {
      if (!/^onair-[\d_-]+\.dump$/.test(dump || '')) throw new Error('Kies een back-up uit de lijst');
      let code = await compose(['stop', 'app'], 'Server even stoppen');
      if (code) return code;
      code = await compose(['run', '--rm', '--entrypoint', 'sh', 'backup', '-c',
        `pg_restore -h db -U onair -d onair --clean --if-exists --no-owner /backup/database/${dump}`], `Database terugzetten (${dump})`);
      if (code) return code;
      code = await compose(['run', '--rm', '--entrypoint', 'sh', '-v', `${path.join(serverDir, 'data', 'audio')}:/restore`, 'backup', '-c',
        'mkdir -p /restore/files && cp -n /backup/audio/* /restore/files/ 2>/dev/null; true'], 'Ontbrekende muziek terugzetten');
      if (code) return code;
      return compose(['start', 'app'], 'Server weer starten');
    }),
  };

  async function listBackups() {
    const r = await capture(['compose', '-p', PROJECT, ...composeFiles(), 'run', '--rm', '--no-deps', '--entrypoint', 'sh', 'backup', '-c',
      'ls -1 /backup/database 2>/dev/null | grep "\\.dump$" || true'], { timeout: 60000 });
    return r.stdout.split('\n').map((s) => s.trim()).filter(Boolean).sort().reverse();
  }

  return {
    status, saveSettings, listBackups, startDocker, installDocker,
    get outdated() { return installed() && filesOutdated(); },
    run: (name, arg) => { if (!actions[name]) throw new Error('Onbekende actie'); return actions[name](arg); },
    get dir() { return serverDir; },
    get busy() { return busy; },
    set dir(value) { if (!installed()) serverDir = value; },
  };
}

module.exports = { createServerManager };
