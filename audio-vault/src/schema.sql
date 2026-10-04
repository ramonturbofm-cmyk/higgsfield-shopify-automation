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

-- Playout: cue points (seconds). cue_in = start of audio, mix_out = where the next
-- item starts, cue_out = end of audio. Filled automatically by the studio's analysis.
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS cue_in  NUMERIC(10, 3);
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS mix_out NUMERIC(10, 3);
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS cue_out NUMERIC(10, 3);

-- Per-user studio settings: theme, playlist, jingle panel, crossfade, ...
CREATE TABLE IF NOT EXISTS user_settings (
  user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  data       JSONB NOT NULL DEFAULT '{}',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- What is on air right now (single row), shown in the studio and on /nu.html.
CREATE TABLE IF NOT EXISTS now_playing (
  id               INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  file_id          INTEGER REFERENCES audio_files(id) ON DELETE SET NULL,
  title            TEXT NOT NULL,
  artist           TEXT NOT NULL DEFAULT '',
  duration_seconds NUMERIC(10, 3),
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_by       INTEGER REFERENCES users(id) ON DELETE SET NULL
);

-- Members may only play inside the studio unless they get this right (download,
-- M3U links, WebDAV drive, own software such as mAirList). Owners/admins always may.
ALTER TABLE users ADD COLUMN IF NOT EXISTS can_download BOOLEAN NOT NULL DEFAULT FALSE;

-- Where a bulk-imported file came from, so re-running the import skips it.
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS source_path TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS audio_files_source_path_idx ON audio_files (source_path) WHERE source_path IS NOT NULL;

-- Hour clocks ("uurklokken"): an ordered list of slots that the planner fills from the
-- database. A slot is { type: 'muziek' | 'jingle' | 'vast', collection_id?, file_id?, color? }.
CREATE TABLE IF NOT EXISTS clocks (
  id         SERIAL PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  color      TEXT NOT NULL DEFAULT '#2f7bff',
  slots      JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Which clock runs in which hour of the week. day uses JavaScript numbering (0 = Sunday).
CREATE TABLE IF NOT EXISTS clock_schedule (
  day      SMALLINT NOT NULL CHECK (day BETWEEN 0 AND 6),
  hour     SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
  clock_id INTEGER NOT NULL REFERENCES clocks(id) ON DELETE CASCADE,
  PRIMARY KEY (day, hour)
);

-- Indexes for large libraries (hundreds of thousands of files).
CREATE INDEX IF NOT EXISTS audio_files_name_idx ON audio_files (lower(artist), lower(title), id);
CREATE INDEX IF NOT EXISTS audio_files_created_idx ON audio_files (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS access_log_onair_idx ON access_log (file_id, created_at) WHERE action = 'onair';
