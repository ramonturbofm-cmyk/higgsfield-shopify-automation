require('dotenv').config();
const crypto = require('crypto');
const path = require('path');
const { createPool, migrate } = require('./db');
const { createApp } = require('./app');
const { startAutoImport } = require('./autoimport');
const { startLoudnessWorker } = require('./loudness');
const { startGenreWorker } = require('./nonstop');

async function main() {
  const sessionSecret = process.env.SESSION_SECRET;
  if (!sessionSecret) {
    console.warn('SESSION_SECRET is not set; using a random one (everyone is logged out on every restart).');
  }
  const pool = createPool(process.env.DATABASE_URL);
  await migrate(pool);

  const app = createApp({
    pool,
    storageDir: path.resolve(process.env.STORAGE_DIR || './storage'),
    sessionSecret: sessionSecret || crypto.randomBytes(32).toString('hex'),
    publicUrl: process.env.PUBLIC_URL,
    maxUploadMb: Number(process.env.MAX_UPLOAD_MB || 500),
    // Disk space for "zuinige modus" MP3 versions (least recently used are removed).
    cacheMaxBytes: Number(process.env.CACHE_MAX_GB || 20) * 1e9,
  });
  // New music in the music folder is added automatically (Docker: /import).
  const storageDir = path.resolve(process.env.STORAGE_DIR || './storage');
  startAutoImport({
    pool, filesDir: path.join(storageDir, 'files'), statusFile: path.join(storageDir, 'autoimport.json'),
    dir: process.env.IMPORT_DIR, minutes: Number(process.env.AUTO_IMPORT_MINUTES || 0),
    jobs: Math.min(6, Math.max(2, Math.floor(require('os').cpus().length / 2))),
  });
  // Measure the loudness of new (and not yet measured) tracks in the background.
  startLoudnessWorker({ pool, filesDir: path.join(storageDir, 'files'), log: (l) => console.log(`[volume] ${l}`) });
  startGenreWorker({ pool, filesDir: path.join(storageDir, 'files'), log: (l) => console.log(`[genres] ${l}`) });
  const port = Number(process.env.PORT || 3000);
  app.listen(port, () => console.log(`Audio Vault draait op http://localhost:${port}`));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
