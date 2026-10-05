'use strict';
const $ = (id) => document.getElementById(id);
const api = window.omzetter;
const S = { source: '', target: '', scan: null, running: false, reportPath: '' };

const size = (b) => (b == null ? '–' : b >= 1e12 ? `${(b / 1e12).toFixed(2)} TB` : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.round(b / 1e6)} MB`);
const num = (n) => n.toLocaleString('nl-NL');
const eta = (min) => (min < 1 ? 'minder dan een minuut' : min < 90 ? `${Math.ceil(min)} min` : `${(min / 60).toFixed(1).replace('.', ',')} uur`);

function setPath(kind, value) {
  S[kind] = value;
  const el = $(kind);
  el.textContent = value;
  el.classList.add('set');
  $(kind === 'source' ? 'n1' : 'n2').classList.add('ok');
}

async function scan() {
  if (!S.source) return;
  $('overview-card').classList.remove('hidden');
  $('o-note').className = 'note';
  $('o-note').textContent = 'Bestanden tellen… (bij een grote map op het netwerk kan dit even duren)';
  $('start').disabled = true;
  const r = await api.scan(S.source, S.target);
  if (!r.ok) { $('o-note').className = 'note bad'; $('o-note').textContent = r.error; return; }
  S.scan = r;
  $('o-wav').textContent = num(r.wavFiles);
  $('o-size').textContent = size(r.wavBytes);
  $('o-need').textContent = size(r.neededBytes);
  $('o-free').textContent = r.freeBytes == null ? '–' : size(r.freeBytes);
  const note = $('o-note');
  if (!r.wavFiles) { note.className = 'note warn'; note.textContent = 'In deze map staan geen WAV- of AIFF-bestanden.'; }
  else if (!S.target) { note.textContent = `Gevonden: ${num(r.wavFiles)} WAV-bestanden en ${num(r.otherFiles)} andere bestanden (die worden meegekopieerd). Kies nu de nieuwe map.`; }
  else if (r.freeBytes != null && r.freeBytes < r.neededBytes) { note.className = 'note bad'; note.textContent = `Niet genoeg ruimte: er is ± ${size(r.neededBytes)} nodig, maar er is ${size(r.freeBytes)} vrij. Kies een andere schijf of maak ruimte.`; }
  else if (r.already && r.todo === 0) { note.className = 'note good'; note.textContent = `✓ Alles in deze map is omgezet (${num(r.already)} bestanden). Het rapport staat in de doelmap: _omzetrapport.txt`; }
  else if (r.already) { note.className = 'note good'; note.textContent = `${num(r.already)} bestanden zijn al eerder klaargezet; er blijven er ${num(r.todo)} over. Klik op VERDERGAAN.`; }
  else { note.className = 'note good'; note.textContent = 'Alles klaar. Je WAV-map wordt alleen gelezen en blijft gewoon staan.'; }
  const ok = r.wavFiles && S.target && !(r.freeBytes != null && r.freeBytes < r.neededBytes) && r.todo > 0;
  $('start').disabled = !ok;
  $('start').textContent = r.already && r.todo ? 'VERDERGAAN' : r.todo === 0 && S.target ? 'AL KLAAR' : 'START';
}

$('pick-source').addEventListener('click', async () => { const d = await api.chooseFolder('source'); if (d) { setPath('source', d); scan(); } });
$('pick-target').addEventListener('click', async () => { const d = await api.chooseFolder('target'); if (d) { setPath('target', d); scan(); } });

$('start').addEventListener('click', async () => {
  if (S.running) { $('start').disabled = true; $('start').textContent = 'PAUZEREN…'; await api.pause(); return; }
  const r = await api.start(S.source, S.target);
  if (!r.ok) { alert(r.error); return; }
  S.running = true;
  $('pick-source').disabled = true; $('pick-target').disabled = true;
  $('result-card').classList.add('hidden');
  $('progress-card').classList.remove('hidden');
  $('p-log').textContent = '';
  $('start').textContent = 'PAUZEREN'; $('start').classList.add('pause');
});

function logLine(text, cls) {
  const div = document.createElement('div');
  div.textContent = text;
  if (cls) div.className = cls;
  $('p-log').prepend(div);
  while ($('p-log').childNodes.length > 200) $('p-log').lastChild.remove();
}

api.onEvent((e) => {
  if (e.type === 'start') {
    $('p-count').textContent = `0 / ${num(e.todo)}`;
    $('p-eta').textContent = e.already ? `${num(e.already)} al klaar van een eerdere keer` : 'Bezig…';
  } else if (e.type === 'file') {
    const pct = Math.floor((e.done / e.total) * 100);
    $('p-pct').textContent = `${pct}%`;
    $('p-fill').style.width = `${(e.done / e.total) * 100}%`;
    $('p-count').textContent = `${num(e.done)} / ${num(e.total)}`;
    $('p-eta').textContent = e.done < e.total ? `Nog ± ${eta(e.etaMin)}` : 'Afronden…';
    $('p-saved').textContent = e.savedBytes ? `${size(e.savedBytes)} bespaard` : '';
    $('p-current').textContent = e.rel;
    if (e.kind === 'failed' || e.kind === 'kept') logLine(`${e.rel} — ${e.text}`, e.kind);
  } else if (e.type === 'done' || e.type === 'error') {
    S.running = false;
    $('pick-source').disabled = false; $('pick-target').disabled = false;
    $('start').classList.remove('pause');
    $('result-card').classList.remove('hidden');
    if (e.type === 'error') {
      $('r-title').textContent = 'Er ging iets mis';
      $('r-text').textContent = e.message;
    } else {
      S.reportPath = e.reportPath;
      const failed = e.stats.failed.length;
      $('r-title').textContent = e.aborted ? '⏸ Gepauzeerd — later verdergaan kan altijd' : failed ? `Klaar, maar ${failed} bestand(en) mislukt` : '✓ Klaar! Alles is omgezet';
      $('r-text').textContent = e.report.split('\n').filter((l) => /^(Omgezet|Gekopieerd|Origineel|Al klaar|Mislukt|Nog te doen|Ruimte)/.test(l)).join('\n');
      if (!e.aborted) { $('p-pct').textContent = '100%'; $('p-fill').style.width = '100%'; $('p-eta').textContent = 'Klaar'; }
    }
    scan();
  }
});

$('open-report').addEventListener('click', () => S.reportPath && api.open(S.reportPath));
$('open-target').addEventListener('click', () => S.target && api.open(S.target));

api.config().then((cfg) => {
  $('footer').textContent = `VERSIE ${cfg.version} · ${cfg.cores} PROCESSORKERNEN · DE WAV-MAP WORDT ALLEEN GELEZEN`;
  if (cfg.source) setPath('source', cfg.source);
  if (cfg.target) setPath('target', cfg.target);
  if (cfg.source) scan();
});
