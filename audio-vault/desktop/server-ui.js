'use strict';
const $ = (id) => document.getElementById(id);
const api = window.onair.server;
let last = null;

function setDot(id, state) { $(id).className = `dot ${state}`; }

function log(text) {
  const el = $('log');
  if (el.textContent === 'Klaar.') el.textContent = '';
  // Docker progress output uses carriage returns; keep it readable.
  el.textContent += text.replace(/\r(?!\n)/g, '\n');
  if (el.textContent.length > 200000) el.textContent = el.textContent.slice(-150000);
  el.scrollTop = el.scrollHeight;
}
window.onair.onServerLog(log);

function banner(text, button, action) {
  $('banner').classList.toggle('show', Boolean(text));
  $('banner-text').textContent = text || '';
  $('banner-btn').textContent = button || '';
  $('banner-btn').onclick = action || null;
  $('banner-btn').style.display = button ? '' : 'none';
}

async function refresh() {
  const s = await api.status();
  last = s;
  const c = (name) => s.containers.find((x) => x.service === name);
  const docker = { running: ['ok', 'Draait'], stopped: ['warn', 'Geïnstalleerd, maar niet gestart'], missing: ['bad', 'Niet geïnstalleerd'] }[s.docker];
  setDot('dot-docker', docker[0]); $('txt-docker').textContent = docker[1];
  const db = c('db');
  setDot('dot-db', db && db.state === 'running' ? 'ok' : db ? 'bad' : '');
  $('txt-db').textContent = db ? (db.state === 'running' ? `Draait${db.health ? ` (${db.health === 'healthy' ? 'gezond' : db.health})` : ''}` : 'Gestopt') : 'Niet gestart';
  setDot('dot-app', s.web ? 'ok' : c('app') ? 'warn' : '');
  $('txt-app').textContent = s.web ? `Draait op http://localhost:${s.port}` : c('app') && c('app').state === 'running' ? 'Start op…' : 'Gestopt';
  const b = c('backup');
  setDot('dot-backup', s.lastBackup && /MISLUKT/.test(s.lastBackup) ? 'bad' : s.lastBackup ? 'ok' : b && b.state === 'running' ? 'warn' : '');
  $('txt-backup').textContent = s.lastBackup ? s.lastBackup.replace(/^(\S+ \S+) /, '$1 · ') : b && b.state === 'running' ? 'Nog geen back-up' : 'Niet actief';
  $('txt-dir').textContent = s.dir;

  if (s.docker === 'missing') banner('Docker Desktop is nodig om de server te draaien. Installeer het eenmalig (gratis) en herstart daarna de pc.', 'Docker Desktop downloaden', () => api.openDockerDownload());
  else if (s.docker === 'stopped') banner('Docker Desktop is niet gestart.', 'Docker Desktop starten', async () => { await api.startDocker(); log('Docker Desktop wordt gestart, dit duurt ongeveer een minuut…\n'); });
  else if (!s.installed) banner('Nog niet ingesteld: vul rechts de instellingen in en klik op Opslaan. Daarna "Server starten".');
  else banner('');

  const ready = s.docker === 'running' && s.installed && !s.busy;
  document.querySelector('[data-run="start"]').textContent = s.web ? 'Herstarten / bijwerken' : 'Server starten';
  document.querySelectorAll('[data-run]').forEach((btn) => { btn.disabled = !ready; });
  $('btn-restore').disabled = !ready;
  $('btn-studio').disabled = !s.web;
  $('dir-wrap').style.display = s.installed ? 'none' : '';

  if (!refresh.filled) {
    refresh.filled = true;
    const st = s.settings;
    $('dir').value = s.dir;
    for (const k of ['nasHost', 'nasShare', 'nasUser', 'musicDir', 'backupShare', 'backupUser', 'publicUrl']) $(k).value = st[k] || '';
    $('nasPassword').placeholder = st.hasNasPassword ? '•••••••• (ongewijzigd)' : '';
    $('backupPassword').placeholder = st.hasBackupPassword ? '•••••••• (ongewijzigd)' : '';
  }
}

document.querySelectorAll('[data-run]').forEach((btn) => btn.addEventListener('click', async () => {
  document.querySelectorAll('[data-run]').forEach((b) => { b.disabled = true; });
  try { await api.run(btn.dataset.run); } catch (e) { log(`✗ ${e.message}\n`); }
  refresh();
}));

$('settings').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('error').textContent = '';
  const values = Object.fromEntries(['dir', 'nasHost', 'nasShare', 'nasUser', 'nasPassword', 'musicDir', 'backupShare', 'backupUser', 'backupPassword', 'publicUrl']
    .map((k) => [k, $(k).value.trim()]));
  const res = await api.saveSettings(values);
  if (!res.ok) { $('error').textContent = res.error; return; }
  $('nasPassword').value = ''; $('backupPassword').value = '';
  $('saved').textContent = last && last.web ? 'Opgeslagen — klik "Server starten" om de wijziging toe te passen' : 'Opgeslagen';
  refresh.filled = false;
  refresh();
});

$('pick-dir').addEventListener('click', async () => { const d = await api.chooseFolder('server'); if (d) $('dir').value = d; });
$('pick-music').addEventListener('click', async () => { const d = await api.chooseFolder('music'); if (d) $('musicDir').value = d; });
$('btn-refresh').addEventListener('click', refresh);
$('btn-studio').addEventListener('click', () => api.openStudio());

$('btn-restore').addEventListener('click', async () => {
  log('\n▶ Back-ups ophalen…\n');
  const list = await api.listBackups();
  if (!list.length) { log('Er zijn nog geen back-ups.\n'); return; }
  $('restore-list').replaceChildren(...list.map((name) => {
    const o = document.createElement('option');
    const m = /onair-(\d{4})-(\d\d)-(\d\d)_(\d\d)(\d\d)/.exec(name);
    o.value = name; o.textContent = m ? `${m[3]}-${m[2]}-${m[1]} om ${m[4]}:${m[5]}` : name;
    return o;
  }));
  $('restore-confirm').value = '';
  $('restore-dialog').showModal();
});
$('restore-cancel').addEventListener('click', () => $('restore-dialog').close());
$('restore-go').addEventListener('click', async () => {
  if ($('restore-confirm').value.trim().toUpperCase() !== 'JA') { $('restore-confirm').focus(); return; }
  $('restore-dialog').close();
  try { await api.run('restore', $('restore-list').value); } catch (e) { log(`✗ ${e.message}\n`); }
  refresh();
});

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 5000);
