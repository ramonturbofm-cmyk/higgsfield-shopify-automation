const fs = require('fs');
const path = require('path');
const { Pool } = require('pg');

function createPool(connectionString) {
  if (!connectionString) throw new Error('DATABASE_URL is not set');
  // Hosted Postgres (Render, Neon, Supabase, ...) needs TLS; a local server usually doesn't.
  const local = /@(localhost|127\.0\.0\.1)(:|\/)|host=\/|^postgres(ql)?:\/\/\/?[^@]*$/.test(connectionString);
  const ssl = process.env.DATABASE_SSL
    ? process.env.DATABASE_SSL === 'true' && { rejectUnauthorized: false }
    : !local && { rejectUnauthorized: false };
  return new Pool({ connectionString, ssl: ssl || undefined });
}

async function migrate(pool) {
  const sql = fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8');
  await pool.query(sql);
}

module.exports = { createPool, migrate };
