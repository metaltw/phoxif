CREATE TABLE batch_items (
  batch_id    TEXT NOT NULL REFERENCES batches(batch_id),
  sighting_id INTEGER NOT NULL REFERENCES sightings(id),
  PRIMARY KEY (batch_id, sighting_id)
);

INSERT INTO batch_items(batch_id, sighting_id)
SELECT batch_id, id FROM sightings;

CREATE INDEX idx_batch_items_sighting ON batch_items(sighting_id);
