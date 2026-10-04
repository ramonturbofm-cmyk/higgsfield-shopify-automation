-- Audio Vault schema. Runs on every start; every statement is idempotent.

CREATE TABLE IF NOT EXISTS users (
  id                 SERIAL PRIMARY KEY,
  email              TEXT NOT NULL UNIQUE,
  name               TEXT NOT NULL,
  role               TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
  password_hash      TEXT,
  invite_token_hash  TEXT UNIQUE,
  invite_expires_at  TIMESTAMPTZ,
  api_token_hash     TEXT UNIQUE,
  disabled           BOOLEAN NOT NULL DEFAULT FALSE,
  session_version    INTEGER NOT NULL DEFAULT 1,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS collections (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audio_files (
  id               SERIAL PRIMARY KEY,
  collection_id    INTEGER NOT NULL REFERENCES collections(id) ON DELETE RESTRICT,
  title            TEXT NOT NULL,
  artist           TEXT NOT NULL DEFAULT '',
  original_name    TEXT NOT NULL,
  storage_key      TEXT NOT NULL UNIQUE,
  mime_type        TEXT NOT NULL,
  size_bytes       BIGINT NOT NULL,
  duration_seconds NUMERIC(10, 3),
  tags             TEXT[] NOT NULL DEFAULT '{}',
  uploaded_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audio_files_collection_idx ON audio_files (collection_id);

-- Which member may use which collection. Owners/admins always see everything.
CREATE TABLE IF NOT EXISTS collection_access (
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  can_upload    BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (user_id, collection_id)
);

-- Who used which file, and through which route (web, m3u, stream, webdav).
CREATE TABLE IF NOT EXISTS access_log (
  id         BIGSERIAL PRIMARY KEY,
  user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  file_id    INTEGER REFERENCES audio_files(id) ON DELETE SET NULL,
  action     TEXT NOT NULL,
  client     TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS access_log_created_idx ON access_log (created_at DESC);
