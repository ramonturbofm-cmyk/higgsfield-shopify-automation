'use strict';
const $ = (id) => document.getElementById(id);
const api = window.onair.server;
let last = null;

const gb = (bytes) => (bytes >= 1e12 ? `${(bytes / 1e12).toFixed(1)} TB` : `${Math.round(bytes / 1e9)} GB`);

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
  document.body.classList.toggle('setup', !s.installed || wizard.running || wizard.finished);
  if (document.body.classList.contains('setup')) renderWizard(s);
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
  $('txt-dir').textContent = s.freeBytes != null ? `${s.dir} (nog ${gb(s.freeBytes)} vrij)` : s.dir;

  if (s.docker === 'missing') banner('Docker Desktop is nodig om de server te draaien.', 'Docker Desktop installeren', () => api.installDocker().then(refresh));
  else if (s.docker === 'stopped') banner('Docker Desktop is niet gestart.', 'Docker Desktop starten', async () => { await api.startDocker(); log('Docker Desktop wordt gestart, dit duurt ongeveer een minuut…\n'); });
  else if (s.outdated) banner('De app is bijgewerkt. Klik op "Herstarten / bijwerken" om ook de server bij te werken.');
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

// ---------- first-run wizard ----------

const wizard = { step: 1, music: 'nas', backup: 'nas', values: {}, running: false, finished: false };
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else if (k === 'class') n.className = v; else if (v !== false && v != null) n.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) n.append(c.nodeType ? c : String(c));
  return n;
};
function field(key, label, attrs = {}) {
  const input = el('input', { ...attrs, value: wizard.values[key] || '' });
  input.addEventListener('input', () => { wizard.values[key] = input.value.trim(); });
  return el('div', {}, el('label', {}, label), input);
}
const go = (step) => { wizard.step = step; renderWizard(last); };

function renderWizard(s) {
  const root = $('wizard');
  const names = ['Docker', 'Muziek', 'Back-up', 'Starten'];
  const steps = el('div', { class: 'steps' }, names.map((n, i) => el('div', { class: i + 1 === wizard.step ? 'on' : i + 1 < wizard.step ? 'done' : '' }, `${i + 1}. ${n}`)));
  let body;

  if (wizard.step === 1) {
    const ok = s.docker === 'running';
    body = [
      el('h2', {}, 'Welkom! We zetten je server in een paar stappen klaar.'),
      el('p', { class: 'lead' }, 'De server draait op deze pc met Docker Desktop (gratis). Dat installeer je één keer.'),
      el('div', { class: 'check-line' }, el('span', { class: `dot ${ok ? 'ok' : s.docker === 'stopped' ? 'warn' : 'bad'}` }),
        ok ? 'Docker Desktop draait — top!' : s.docker === 'stopped' ? 'Docker Desktop is geïnstalleerd maar nog niet gestart.' : 'Docker Desktop is nog niet geïnstalleerd.'),
      s.docker === 'missing' ? el('div', {},
        el('button', { class: 'primary', onclick: () => api.installDocker().then(refresh) }, 'Docker Desktop installeren'), ' ',
        el('button', { onclick: () => api.openDockerDownload() }, 'Zelf downloaden'),
        el('p', { class: 'hint' }, 'Windows vraagt om toestemming. Herstart daarna de pc en open deze app opnieuw; je komt dan hier terug.')) : null,
      s.docker === 'stopped' ? el('button', { class: 'primary', onclick: async () => { await api.startDocker(); log('Docker Desktop start op, dit duurt ongeveer een minuut…\n'); } }, 'Docker Desktop starten') : null,
      el('div', { class: 'nav' }, el('span'), el('button', { class: 'primary big', disabled: !ok, onclick: () => go(2) }, 'Volgende →')),
    ];
  } else if (wizard.step === 2) {
    const pick = (v) => () => { wizard.music = v; renderWizard(last); };
    body = [
      el('h2', {}, 'Waar staat je muziek?'),
      el('p', { class: 'lead' }, 'Je muziek wordt alleen gelezen en als FLAC in de database gezet. Op de originele plek verandert niets.'),
      el('div', { class: 'choices' },
        el('button', { class: `choice${wizard.music === 'nas' ? ' sel' : ''}`, onclick: pick('nas') }, el('b', {}, 'Op mijn Synology'), el('span', {}, 'Via het netwerk')),
        el('button', { class: `choice${wizard.music === 'folder' ? ' sel' : ''}`, onclick: pick('folder') }, el('b', {}, 'In een map op deze pc'), el('span', {}, 'Bijv. Muziek of een externe schijf')),
        el('button', { class: `choice${wizard.music === 'none' ? ' sel' : ''}`, onclick: pick('none') }, el('b', {}, 'Later'), el('span', {}, 'Uploaden kan ook via de website'))),
      wizard.music === 'nas' ? el('div', {},
        el('div', { class: 'grid2' }, field('nasHost', 'IP-adres van de Synology', { placeholder: '192.168.1.20' }), field('nasShare', 'Gedeelde map met muziek', { placeholder: 'music' }),
          field('nasUser', 'Gebruiker', { placeholder: 'onair-lezen' }), field('nasPassword', 'Wachtwoord', { type: 'password', autocomplete: 'new-password' })),
        el('div', { class: 'howto' }, 'Tip: maak in DSM (Configuratiescherm → Gebruiker → Maken) een gebruiker "onair-lezen" die alleen mag lezen in je muziekmap. ',
          'Het IP-adres vind je in DSM bij Configuratiescherm → Netwerk → Netwerkinterface.')) : null,
      wizard.music === 'folder' ? el('div', { class: 'pick' }, el('input', { readonly: true, value: wizard.values.musicDir || '', placeholder: 'geen map gekozen' }),
        el('button', { class: 'small', onclick: async () => { const d = await api.chooseFolder('music'); if (d) { wizard.values.musicDir = d; renderWizard(last); } } }, 'Map kiezen…')) : null,
      el('div', { class: 'error', id: 'wiz-error' }),
      el('div', { class: 'nav' }, el('button', { onclick: () => go(1) }, '← Terug'), el('button', { class: 'primary big', onclick: () => {
        const v = wizard.values;
        const missing = wizard.music === 'nas' ? !(v.nasHost && v.nasShare && v.nasUser && v.nasPassword) : wizard.music === 'folder' ? !v.musicDir : false;
        if (missing) { $('wiz-error').textContent = 'Vul alles in (of kies "Later").'; return; }
        go(3);
      } }, 'Volgende →')),
    ];
  } else if (wizard.step === 3) {
    const pick = (v) => () => { wizard.backup = v; renderWizard(last); };
    body = [
      el('h2', {}, 'Waar komt de dagelijkse back-up?'),
      el('p', { class: 'lead' }, 'Elke dag worden de database en alle muziek gekopieerd. Gaat de pc stuk, dan ben je niets kwijt.'),
      el('div', { class: 'choices', style: 'grid-template-columns:1fr 1fr' },
        el('button', { class: `choice${wizard.backup === 'nas' ? ' sel' : ''}`, onclick: pick('nas') }, el('b', {}, 'Naar mijn Synology'), el('span', {}, 'Aanbevolen: veilig op een ander apparaat')),
        el('button', { class: `choice${wizard.backup === 'local' ? ' sel' : ''}`, onclick: pick('local') }, el('b', {}, 'Op deze pc'), el('span', {}, 'In de servermap; minder veilig'))),
      wizard.backup === 'nas' ? el('div', {},
        el('div', { class: 'grid2' },
          wizard.music === 'nas' ? null : field('nasHost', 'IP-adres van de Synology', { placeholder: '192.168.1.20' }),
          field('backupShare', 'Gedeelde map voor back-ups', { placeholder: 'backup' }),
          field('backupUser', 'Gebruiker', { placeholder: 'onair-backup' }), field('backupPassword', 'Wachtwoord', { type: 'password', autocomplete: 'new-password' })),
        el('div', { class: 'howto' }, 'Tip: maak in DSM een gedeelde map "backup" en een gebruiker "onair-backup" die alleen in die map mag schrijven.')) : null,
      el('div', { class: 'error', id: 'wiz-error' }),
      el('div', { class: 'nav' }, el('button', { onclick: () => go(2) }, '← Terug'), el('button', { class: 'primary big', onclick: () => {
        const v = wizard.values;
        if (wizard.backup === 'nas' && !(v.nasHost && v.backupShare && v.backupUser && v.backupPassword)) { $('wiz-error').textContent = 'Vul alles in (of kies "Op deze pc").'; return; }
        api.freeSpace(wizard.values.dir || last.dir).then((f) => { wizard.free = f; renderWizard(last); });
        go(4);
      } }, 'Volgende →')),
    ];
  } else {
    const v = wizard.values;
    const musicText = wizard.music === 'nas' ? `Synology ${v.nasHost} → map "${v.nasShare}"` : wizard.music === 'folder' ? v.musicDir : 'later (uploaden via de website)';
    body = [
      el('h2', {}, wizard.finished ? 'Klaar! Je station staat klaar.' : 'Alles klaar om te installeren'),
      wizard.finished
        ? el('div', {},
          el('p', { class: 'lead' }, 'Open de studio. De eerste keer maak je daar je eigenaarsaccount aan (naam, e-mail, wachtwoord).'),
          el('button', { class: 'primary big', onclick: () => api.openStudio() }, '▶ Open de studio'), ' ',
          el('button', { class: 'big', onclick: () => { wizard.finished = false; refresh(); } }, 'Naar Server beheren'))
        : el('div', {},
          el('div', { class: 'row' }, el('span', { class: 'label' }, 'Map op deze pc'),
            el('span', { style: 'flex:1' }, wizard.values.dir || s.dir, el('br'),
              el('span', { class: 'hint' }, wizard.free != null ? `Nog ${gb(wizard.free)} vrij op deze schijf — hier komen de database en al je muziek (FLAC).` : '')),
            el('button', { class: 'small', disabled: wizard.running, onclick: async () => {
              const d = await api.chooseFolder('server');
              if (d) { wizard.values.dir = /audioonair$/i.test(d) ? d : `${d.replace(/[\\/]+$/, '')}${d.includes('\\') ? '\\' : '/'}AudioOnAir`; wizard.free = await api.freeSpace(wizard.values.dir); renderWizard(last); }
            } }, 'Andere schijf…')),
          el('div', { class: 'row' }, el('span', { class: 'label' }, 'Muziek'), el('span', {}, musicText)),
          el('div', { class: 'row' }, el('span', { class: 'label' }, 'Back-up'), el('span', {}, wizard.backup === 'nas' ? `Synology → map "${v.backupShare}"` : 'op deze pc')),
          el('p', { class: 'hint' }, 'De eerste keer duurt het een paar minuten; het inlezen van een grote muziekbibliotheek kan langer duren. Je kunt meekijken in het logboek hieronder.'),
          el('div', { class: 'nav' }, el('button', { disabled: wizard.running, onclick: () => go(3) }, '← Terug'),
            el('button', { class: 'primary big', disabled: wizard.running, onclick: install }, wizard.running ? 'Bezig…' : 'Installeren en starten'))),
    ];
  }
  root.replaceChildren(steps, ...body.flat().filter(Boolean));
}

async function waitFor(check, timeoutMs) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const s = await api.status();
    if (check(s)) return s;
    await new Promise((r) => setTimeout(r, 2000));
  }
  return null;
}

async function install() {
  const v = wizard.values;
  wizard.running = true; renderWizard(last);
  try {
    const settings = {
      musicDir: wizard.music === 'folder' ? v.musicDir : '',
      nasHost: wizard.music === 'nas' || wizard.backup === 'nas' ? v.nasHost : '',
      nasShare: wizard.music === 'nas' ? v.nasShare : '', nasUser: wizard.music === 'nas' ? v.nasUser : '', nasPassword: wizard.music === 'nas' ? v.nasPassword : '',
      backupShare: wizard.backup === 'nas' ? v.backupShare : '', backupUser: wizard.backup === 'nas' ? v.backupUser : '', backupPassword: wizard.backup === 'nas' ? v.backupPassword : '',
    };
    if (v.dir) settings.dir = v.dir;
    const saved = await api.saveSettings(settings);
    if (!saved.ok) throw new Error(saved.error);
    log('Instellingen opgeslagen.\n');
    const started = await api.run('start');
    if (!started.ok || started.code) throw new Error('Starten is mislukt — zie het logboek. Klopt het NAS-adres, de map, gebruiker en het wachtwoord?');
    log('Wachten tot de server bereikbaar is…\n');
    if (!(await waitFor((s) => s.web, 180000))) throw new Error('De server reageert niet. Kijk in het logboek of probeer opnieuw.');
    if (wizard.music !== 'none') {
      const imported = await api.run('import');
      if (!imported.ok || imported.code) log('Let op: het inlezen van de muziek is niet helemaal gelukt. Je kunt het later opnieuw proberen met "Muziek importeren".\n');
    }
    wizard.finished = true;
  } catch (e) {
    log(`✗ ${e.message}\n`);
    alert(e.message);
  } finally {
    wizard.running = false;
    refresh();
  }
}

api.getAutostart().then((on) => { $('autostart').checked = Boolean(on); });
$('autostart').addEventListener('change', (e) => api.setAutostart(e.target.checked));
