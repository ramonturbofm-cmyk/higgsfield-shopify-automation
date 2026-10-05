// Adds new music automatically: every few minutes the server looks in the music folder
// (the Synology share or a folder on this PC) and imports what is new. Files that are
// still being copied wait for the next round; files that failed are only tried again
// when they change. The result is written to autoimport.json for "Server beheren".
const fs = require('fs');
const { importFolder } = require('./import');

const MIN_AGE_MS = 2 * 60 * 1000;

function startAutoImport({ pool, filesDir, statusFile, dir, minutes, perFolder = true, collection = 'Muziek', jobs = 2 }) {
  const status = { enabled: Boolean(minutes && dir), intervalMinutes: minutes || 0, addedTotal: 0 };
  const save = () => { try { fs.writeFileSync(statusFile, JSON.stringify(status, null, 2)); } catch { /* not important */ } };
  if (!status.enabled) { save(); return { stop() {} }; }

  const failed = new Map(); // file -> mtime when it failed
  let running = false;
  async function round() {
    if (running) return;
    running = true; status.running = true; save();
    try {
      if (!fs.existsSync(dir) || !fs.readdirSync(dir).length) throw new Error('Muziekmap is leeg of niet bereikbaar');
      const r = await importFolder({
        pool, filesDir, dir, perFolder, collection, jobs, minAgeMs: MIN_AGE_MS,
        skip: (file, mtime) => failed.get(file) === mtime,
        onFailed: (file, mtime) => failed.set(file, mtime),
        log: (line) => { if (!/ 0 nieuw\b|Klaar: 0 geïmporteerd, 0 mislukt/.test(line)) console.log(`[automatisch bijwerken] ${line}`); },
      });
      Object.assign(status, {
        lastRun: new Date().toISOString(), lastAdded: r.added, lastFailed: r.failed, waiting: r.waiting,
        skippedBusy: r.busy, failedFiles: failed.size, lastError: null,
      });
      status.addedTotal += r.added;
      if (r.added) status.lastAddedAt = status.lastRun;
    } catch (err) {
      Object.assign(status, { lastRun: new Date().toISOString(), lastError: err.message });
    } finally {
      running = false; status.running = false;
      status.nextRun = new Date(Date.now() + minutes * 60000).toISOString();
      save();
    }
  }
  const first = setTimeout(round, 30 * 1000);
  const timer = setInterval(round, minutes * 60 * 1000);
  status.nextRun = new Date(Date.now() + 30 * 1000).toISOString();
  save();
  return { round, stop() { clearTimeout(first); clearInterval(timer); } };
}

module.exports = { startAutoImport };
