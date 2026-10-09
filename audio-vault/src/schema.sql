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

-- What is on air right now, per person: every customer runs their own station.
-- Shown on /nu.html (the owner's) and /nu.html?station=<station_key> (anyone else's).
DROP TABLE IF EXISTS now_playing;
CREATE TABLE IF NOT EXISTS station_now_playing (
  user_id          INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  file_id          INTEGER REFERENCES audio_files(id) ON DELETE SET NULL,
  title            TEXT NOT NULL,
  artist           TEXT NOT NULL DEFAULT '',
  duration_seconds NUMERIC(10, 3),
  started_at       TIMESTAMPTZ NOT NULL DEFAULT now()
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
CREATE INDEX IF NOT EXISTS audio_files_title_idx ON audio_files (lower(title), lower(artist), id);
CREATE INDEX IF NOT EXISTS access_log_file_idx ON access_log (file_id);

-- Private key for someone's public "Nu op de radio" page (not guessable like an id).
ALTER TABLE users ADD COLUMN IF NOT EXISTS station_key TEXT UNIQUE;
-- Rotation and "eerder gedraaid" are per person.
CREATE INDEX IF NOT EXISTS access_log_user_onair_idx ON access_log (user_id, file_id, created_at) WHERE action = 'onair';

-- Loudness (EBU R128) per track so the studio can play everything at the same level.
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS loudness_lufs NUMERIC(6, 2);
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS true_peak_db NUMERIC(6, 2);
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS loudness_checked BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS audio_files_loudness_todo_idx ON audio_files (id) WHERE NOT loudness_checked;

-- Genre from the file's tags. NULL = not read yet (filled in the background), '' = none.
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS genre TEXT;
CREATE INDEX IF NOT EXISTS audio_files_genre_todo_idx ON audio_files (id) WHERE genre IS NULL;

-- Nonstop filter: what someone never wants in their automatically planned hours.
-- kind: file (one track), artist, genre or folder (text the artist/genre/path contains).
CREATE TABLE IF NOT EXISTS nonstop_blocks (
  id         SERIAL PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('file', 'artist', 'genre', 'folder')),
  value      TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, kind, value)
);

-- "Naadloos": a track that flows into the next on the recording (live album, mix,
-- medley). It plays from the very start to the very end and the next item starts
-- exactly where it ends: no silence skipped, no overlap, no fade.
ALTER TABLE audio_files ADD COLUMN IF NOT EXISTS segue BOOLEAN NOT NULL DEFAULT FALSE;

-- Packages (Basis / Standaard / Pro). A member with a plan also sees every collection
-- whose min_plan is at or below that plan, on top of the collections ticked by hand.
-- access_until ends a trial week or a paid period: after it the account stops working.
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS access_until TIMESTAMPTZ;
ALTER TABLE collections ADD COLUMN IF NOT EXISTS min_plan TEXT;
-- Track feedback: a listener/DJ/customer reports a track that is not right (bad
-- quality, wrong title, starts or ends wrong, …); the owner/admins fix it and close it.
CREATE TABLE IF NOT EXISTS track_reports (
  id          SERIAL PRIMARY KEY,
  file_id     INTEGER NOT NULL REFERENCES audio_files(id) ON DELETE CASCADE,
  user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  reason      TEXT NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ,
  resolved_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS track_reports_open_idx ON track_reports (created_at DESC) WHERE status = 'open';

-- Music wishes: tracks people miss in the database. The owner/admins add them (or not)
-- and the person sees what became of the wish.
CREATE TABLE IF NOT EXISTS music_wishes (
  id         SERIAL PRIMARY KEY,
  user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
  artist     TEXT NOT NULL DEFAULT '',
  title      TEXT NOT NULL DEFAULT '',
  note       TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'added', 'rejected')),
  reply      TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  handled_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS music_wishes_open_idx ON music_wishes (created_at DESC) WHERE status = 'open';
