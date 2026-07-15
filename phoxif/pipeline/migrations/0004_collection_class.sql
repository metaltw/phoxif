ALTER TABLE files ADD COLUMN collection_class TEXT NOT NULL DEFAULT 'photo'
  CHECK (collection_class IN ('photo','non-photo'));
ALTER TABLE files ADD COLUMN non_photo_category TEXT;
ALTER TABLE files ADD COLUMN live_content_id TEXT;

CREATE INDEX idx_files_collection_class ON files(collection_class);
CREATE INDEX idx_files_live_content_id ON files(live_content_id);

CREATE TABLE sidecars (
  sidecar_id       TEXT PRIMARY KEY,
  sha256           TEXT NOT NULL,
  current_sha256   TEXT NOT NULL,
  size             INTEGER NOT NULL,
  ext              TEXT NOT NULL,
  owner_sha256     TEXT REFERENCES files(sha256),
  status           TEXT NOT NULL CHECK (status IN ('ready','orphan','archived')),
  archived_path    TEXT UNIQUE,
  archived_at      TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE sidecar_sightings (
  id               INTEGER PRIMARY KEY,
  sidecar_id       TEXT NOT NULL REFERENCES sidecars(sidecar_id),
  source_id        TEXT NOT NULL REFERENCES sources(source_id),
  original_path    TEXT NOT NULL,
  original_name    TEXT NOT NULL,
  staging_path     TEXT,
  seen_at          TEXT NOT NULL,
  UNIQUE (sidecar_id, source_id, original_path)
);

CREATE TABLE sidecar_batch_items (
  batch_id         TEXT NOT NULL REFERENCES batches(batch_id),
  sighting_id      INTEGER NOT NULL REFERENCES sidecar_sightings(id),
  PRIMARY KEY (batch_id, sighting_id)
);

CREATE INDEX idx_sidecars_owner ON sidecars(owner_sha256);
CREATE INDEX idx_sidecars_status ON sidecars(status);
CREATE INDEX idx_sidecar_sightings_sidecar ON sidecar_sightings(sidecar_id);
