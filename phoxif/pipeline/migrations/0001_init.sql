CREATE TABLE sources (
  source_id   TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('rescue','inbox')),
  created_at  TEXT NOT NULL
);

CREATE TABLE batches (
  batch_id    TEXT PRIMARY KEY,
  source_id   TEXT NOT NULL REFERENCES sources(source_id),
  mode        TEXT NOT NULL CHECK (mode IN ('rescue','inbox')),
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  stats_json  TEXT
);

CREATE TABLE files (
  sha256      TEXT PRIMARY KEY,
  size        INTEGER NOT NULL,
  ext         TEXT NOT NULL,
  media_type  TEXT NOT NULL CHECK (media_type IN ('image','video')),
  phash       TEXT,
  width       INTEGER,
  height      INTEGER,
  status      TEXT NOT NULL DEFAULT 'ingested' CHECK (status IN
    ('ingested','unique','enriched','quarantined','archived','duplicate')),
  dup_group_id TEXT,
  kept_sha256 TEXT REFERENCES files(sha256),
  live_partner_sha256 TEXT REFERENCES files(sha256),
  date_written TEXT,
  date_source TEXT,
  date_confidence INTEGER,
  date_original_value TEXT,
  gps_written TEXT,
  gps_source TEXT,
  gps_original_value TEXT,
  archived_path TEXT UNIQUE,
  archived_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE sightings (
  id             INTEGER PRIMARY KEY,
  sha256         TEXT NOT NULL REFERENCES files(sha256),
  source_id      TEXT NOT NULL REFERENCES sources(source_id),
  batch_id       TEXT NOT NULL REFERENCES batches(batch_id),
  original_path  TEXT NOT NULL,
  original_name  TEXT NOT NULL,
  original_mtime TEXT,
  original_btime TEXT,
  staging_path   TEXT,
  seen_at        TEXT NOT NULL,
  UNIQUE (sha256, source_id, original_path)
);

CREATE TABLE operations (
  id          INTEGER PRIMARY KEY,
  batch_id    TEXT NOT NULL,
  sha256      TEXT NOT NULL,
  op          TEXT NOT NULL CHECK (op IN
    ('trash','write_date','write_gps','archive_copy','restore')),
  detail_json TEXT NOT NULL,
  executed_at TEXT NOT NULL
);

CREATE INDEX idx_files_status ON files(status);
CREATE INDEX idx_files_phash ON files(phash);
CREATE INDEX idx_sightings_sha ON sightings(sha256);
