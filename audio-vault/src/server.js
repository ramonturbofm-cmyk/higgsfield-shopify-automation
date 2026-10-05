require('dotenv').config();
const crypto = require('crypto');
const path = require('path');
const { createPool, migrate } = require('./db');
const { createApp } = require('./app');

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
  const port = Number(process.env.PORT || 3000);
  app.listen(port, () => console.log(`Audio Vault draait op http://localhost:${port}`));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
