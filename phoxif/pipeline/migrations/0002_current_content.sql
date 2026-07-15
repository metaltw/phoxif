ALTER TABLE files ADD COLUMN current_sha256 TEXT;
ALTER TABLE files ADD COLUMN current_size INTEGER;

UPDATE files SET current_sha256 = sha256, current_size = size;

CREATE UNIQUE INDEX idx_files_current_sha ON files(current_sha256);
