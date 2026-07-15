"""Persistent SQLite catalog for all phoxif pipeline state."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Mode = Literal["rescue", "inbox"]
FileStatus = Literal[
    "ingested",
    "unique",
    "enriched",
    "quarantined",
    "archived",
    "duplicate",
]

SCHEMA_VERSION = 5
DEFAULT_CATALOG_PATH = Path("~/.phoxif/catalog.db").expanduser()
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "ingested": {"unique", "duplicate"},
    "unique": {"enriched", "quarantined"},
    "enriched": {"archived", "quarantined"},
    "quarantined": {"enriched"},
    "archived": set(),
    "duplicate": set(),
}


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class Catalog:
    """Single-writer catalog with migration and state-transition guards."""

    def __init__(self, path: Path = DEFAULT_CATALOG_PATH) -> None:
        """Open and migrate a catalog.

        Args:
            path: SQLite database path. Parent directories are created.
        """
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def __enter__(self) -> Catalog:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the SQLite connection."""
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit a unit of work or roll it back completely."""
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _migrate(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Catalog schema {version} is newer than supported version {SCHEMA_VERSION}"
            )
        migration_dir = Path(__file__).with_name("migrations")
        for next_version in range(version + 1, SCHEMA_VERSION + 1):
            matches = sorted(migration_dir.glob(f"{next_version:04d}_*.sql"))
            if len(matches) != 1:
                raise RuntimeError(f"Expected one catalog migration for version {next_version}")
            self._apply_migration(matches[0].read_text(), next_version)

    def _apply_migration(self, script: str, version: int) -> None:
        """Apply schema SQL and its version marker in one transaction."""
        try:
            self.connection.executescript(
                f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
            )
        except Exception:
            self.connection.rollback()
            raise

    def register_source(
        self,
        source_id: str,
        label: str,
        kind: Mode,
        *,
        root_path: Path | None = None,
    ) -> None:
        """Create a source, refusing identity drift on later runs."""
        if not _SOURCE_ID.fullmatch(source_id):
            raise ValueError(
                "source_id must start with a lowercase letter or digit and contain only "
                "lowercase letters, digits, dot, underscore, or hyphen"
            )
        if kind not in {"rescue", "inbox"}:
            raise ValueError(f"Unsupported source kind: {kind}")
        normalized_root = str(Path(root_path).expanduser().resolve()) if root_path else None
        existing = self.connection.execute(
            "SELECT label, kind, root_path FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        if existing is not None:
            if existing["kind"] != kind:
                raise ValueError(f"Source {source_id} is already registered as {existing['kind']}")
            if (
                normalized_root is not None
                and existing["root_path"] is not None
                and str(existing["root_path"]) != normalized_root
            ):
                raise ValueError(f"Source {source_id} is already registered at another root")
            if existing["label"] != label or (
                normalized_root is not None and existing["root_path"] is None
            ):
                with self.transaction():
                    self.connection.execute(
                        "UPDATE sources SET label = ?, root_path = COALESCE(root_path, ?) "
                        "WHERE source_id = ?",
                        (label, normalized_root, source_id),
                    )
            return
        with self.transaction():
            self.connection.execute(
                "INSERT INTO sources(source_id, label, kind, root_path, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_id, label, kind, normalized_root, utc_now()),
            )

    def start_batch(self, source_id: str, mode: Mode) -> str:
        """Create a unique batch identifier and started record."""
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        prefix = f"{day}-{source_id}-"
        rows = self.connection.execute(
            "SELECT batch_id FROM batches WHERE batch_id LIKE ?", (f"{prefix}%",)
        ).fetchall()
        sequences = []
        for row in rows:
            try:
                sequences.append(int(str(row["batch_id"]).removeprefix(prefix)))
            except ValueError:
                continue
        batch_id = f"{prefix}{max(sequences, default=0) + 1}"
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO batches(batch_id, source_id, mode, started_at)
                VALUES (?, ?, ?, ?)
                """,
                (batch_id, source_id, mode, utc_now()),
            )
        return batch_id

    def finish_batch(self, batch_id: str, stats: dict[str, Any]) -> None:
        """Persist final batch statistics."""
        with self.transaction():
            cursor = self.connection.execute(
                """
                UPDATE batches SET finished_at = ?, stats_json = ? WHERE batch_id = ?
                """,
                (utc_now(), json.dumps(stats, sort_keys=True), batch_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown batch: {batch_id}")

    def fail_batch(self, batch_id: str, error: str) -> None:
        """Close a failed batch while preserving a recoverable audit record."""
        self.finish_batch(batch_id, {"status": "failed", "error": error})

    def file(self, sha256: str) -> sqlite3.Row | None:
        """Return one content identity."""
        return self.connection.execute("SELECT * FROM files WHERE sha256 = ?", (sha256,)).fetchone()

    def file_by_content_hash(self, sha256: str) -> sqlite3.Row | None:
        """Resolve either the immutable ingest identity or current working bytes."""
        return self.connection.execute(
            "SELECT * FROM files WHERE sha256 = ? OR current_sha256 = ? LIMIT 1",
            (sha256, sha256),
        ).fetchone()

    def upsert_file(
        self,
        *,
        sha256: str,
        size: int,
        ext: str,
        media_type: str,
        phash: str | None,
        width: int | None,
        height: int | None,
    ) -> tuple[sqlite3.Row, bool]:
        """Insert a content identity once and return ``(row, created)``."""
        existing = self.file_by_content_hash(sha256)
        if existing is not None:
            expected_size = (
                int(existing["size"])
                if existing["sha256"] == sha256
                else int(existing["current_size"])
            )
            if expected_size != size:
                raise RuntimeError(f"SHA-256 collision or catalog corruption for {sha256}")
            return existing, False
        now = utc_now()
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO files(
                  sha256, size, ext, media_type, phash, width, height,
                  current_sha256, current_size,
                  status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ingested', ?, ?)
                """,
                (
                    sha256,
                    size,
                    ext,
                    media_type,
                    phash,
                    width,
                    height,
                    sha256,
                    size,
                    now,
                    now,
                ),
            )
        created = self.file(sha256)
        assert created is not None
        return created, True

    def update_current_content(self, sha256: str, current_sha256: str, size: int) -> None:
        """Record verified post-metadata bytes without changing ingest identity."""
        with self.transaction():
            cursor = self.connection.execute(
                """
                UPDATE files SET current_sha256 = ?, current_size = ?, updated_at = ?
                WHERE sha256 = ?
                """,
                (current_sha256, size, utc_now(), sha256),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown file: {sha256}")

    def set_collection_class(
        self,
        sha256: str,
        collection_class: str,
        category: str | None,
    ) -> None:
        """Persist ingest-time photo versus non-photo classification."""
        if collection_class not in {"photo", "non-photo"}:
            raise ValueError(f"Unsupported collection class: {collection_class}")
        if collection_class == "photo":
            category = None
        existing = self.file(sha256)
        if existing is None:
            raise KeyError(f"Unknown file: {sha256}")
        # A later generic filename must not erase an earlier, stronger
        # screenshot/document classification for the same content identity.
        if existing["collection_class"] == "non-photo" and collection_class == "photo":
            return
        with self.transaction():
            cursor = self.connection.execute(
                """
                UPDATE files SET collection_class = ?, non_photo_category = ?, updated_at = ?
                WHERE sha256 = ?
                """,
                (collection_class, category, utc_now(), sha256),
            )
            assert cursor.rowcount == 1

    def set_live_partners(self, first_sha256: str, second_sha256: str) -> None:
        """Link the image and video identities of one verified Live Photo."""
        if first_sha256 == second_sha256:
            raise ValueError("A Live Photo partner must be a different file")
        with self.transaction():
            for sha256, partner_sha256 in (
                (first_sha256, second_sha256),
                (second_sha256, first_sha256),
            ):
                cursor = self.connection.execute(
                    """
                    UPDATE files SET live_partner_sha256 = ?, updated_at = ?
                    WHERE sha256 = ?
                      AND (live_partner_sha256 IS NULL OR live_partner_sha256 = ?)
                    """,
                    (partner_sha256, utc_now(), sha256, partner_sha256),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Live Photo identity conflicts with catalog state")

    def register_live_identity(self, sha256: str, content_id: str) -> None:
        """Persist a Live Photo ID and link its unique image/video across batches."""
        normalized = content_id.strip()
        if not normalized:
            return
        record = self.file(sha256)
        if record is None:
            raise KeyError(f"Unknown file: {sha256}")
        if record["live_content_id"] not in {None, normalized}:
            raise RuntimeError("Live Photo content identity changed for existing bytes")
        with self.transaction():
            self.connection.execute(
                "UPDATE files SET live_content_id = ?, updated_at = ? WHERE sha256 = ?",
                (normalized, utc_now(), sha256),
            )
        members = self.connection.execute(
            """
            SELECT sha256, media_type FROM files
            WHERE live_content_id = ? ORDER BY sha256
            """,
            (normalized,),
        ).fetchall()
        images = [str(member["sha256"]) for member in members if member["media_type"] == "image"]
        videos = [str(member["sha256"]) for member in members if member["media_type"] == "video"]
        if len(images) == 1 and len(videos) == 1:
            self.set_live_partners(images[0], videos[0])

    def record_sidecar(
        self,
        *,
        sha256: str,
        size: int,
        ext: str,
        owner_sha256: str | None,
        source_id: str,
        batch_id: str,
        original_path: Path,
        original_name: str,
        staging_path: Path,
    ) -> tuple[sqlite3.Row, bool, bool]:
        """Record one AAE sidecar and its batch-scoped source evidence."""
        owner_key = owner_sha256 or "orphan"
        sidecar_id = f"{owner_key}:{sha256}"
        existing = self.connection.execute(
            "SELECT * FROM sidecars WHERE sidecar_id = ?", (sidecar_id,)
        ).fetchone()
        created = existing is None
        now = utc_now()
        with self.transaction():
            if created:
                self.connection.execute(
                    """
                    INSERT INTO sidecars(
                      sidecar_id, sha256, current_sha256, size, ext, owner_sha256,
                      status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sidecar_id,
                        sha256,
                        sha256,
                        size,
                        ext,
                        owner_sha256,
                        "ready" if owner_sha256 else "orphan",
                        now,
                        now,
                    ),
                )
            else:
                assert existing is not None
                if int(existing["size"]) != size or str(existing["sha256"]) != sha256:
                    raise RuntimeError(f"Sidecar identity conflict for {sidecar_id}")
            self.connection.execute(
                """
                INSERT OR IGNORE INTO sidecar_sightings(
                  sidecar_id, source_id, original_path, original_name,
                  staging_path, seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sidecar_id,
                    source_id,
                    str(original_path),
                    original_name,
                    str(staging_path),
                    now,
                ),
            )
            sighting = self.connection.execute(
                """
                SELECT id FROM sidecar_sightings
                WHERE sidecar_id = ? AND source_id = ? AND original_path = ?
                """,
                (sidecar_id, source_id, str(original_path)),
            ).fetchone()
            assert sighting is not None
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO sidecar_batch_items(batch_id, sighting_id)
                VALUES (?, ?)
                """,
                (batch_id, int(sighting["id"])),
            )
        record = self.connection.execute(
            "SELECT * FROM sidecars WHERE sidecar_id = ?", (sidecar_id,)
        ).fetchone()
        assert record is not None
        return record, created, cursor.rowcount == 1

    def add_sighting(
        self,
        *,
        sha256: str,
        source_id: str,
        batch_id: str,
        original_path: Path,
        original_name: str,
        original_mtime: str | None,
        original_btime: str | None,
        staging_path: Path | None,
    ) -> bool:
        """Append immutable source evidence, returning whether it was new."""
        with self.transaction():
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO sightings(
                  sha256, source_id, batch_id, original_path, original_name,
                  original_mtime, original_btime, staging_path, seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sha256,
                    source_id,
                    batch_id,
                    str(original_path),
                    original_name,
                    original_mtime,
                    original_btime,
                    str(staging_path) if staging_path else None,
                    utc_now(),
                ),
            )
            sighting = self.connection.execute(
                """
                SELECT id FROM sightings
                WHERE sha256 = ? AND source_id = ? AND original_path = ?
                """,
                (sha256, source_id, str(original_path)),
            ).fetchone()
            assert sighting is not None
            self.connection.execute(
                """
                INSERT OR IGNORE INTO batch_items(batch_id, sighting_id)
                VALUES (?, ?)
                """,
                (batch_id, sighting["id"]),
            )
        return cursor.rowcount == 1

    def record_ingest(
        self,
        *,
        sha256: str,
        size: int,
        ext: str,
        media_type: str,
        phash: str | None,
        width: int | None,
        height: int | None,
        source_id: str,
        batch_id: str,
        original_path: Path,
        original_name: str,
        original_mtime: str | None,
        original_btime: str | None,
        staging_path: Path | None,
    ) -> tuple[sqlite3.Row, bool, bool]:
        """Atomically persist one content identity and its source evidence."""
        existing = self.file_by_content_hash(sha256)
        if existing is not None:
            expected_size = (
                int(existing["size"])
                if existing["sha256"] == sha256
                else int(existing["current_size"])
            )
            if expected_size != size:
                raise RuntimeError(f"SHA-256 collision or catalog corruption for {sha256}")

        created = existing is None
        identity_sha256 = sha256 if existing is None else str(existing["sha256"])
        now = utc_now()
        with self.transaction():
            if created:
                self.connection.execute(
                    """
                    INSERT INTO files(
                      sha256, size, ext, media_type, phash, width, height,
                      current_sha256, current_size,
                      status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ingested', ?, ?)
                    """,
                    (
                        sha256,
                        size,
                        ext,
                        media_type,
                        phash,
                        width,
                        height,
                        sha256,
                        size,
                        now,
                        now,
                    ),
                )
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO sightings(
                  sha256, source_id, batch_id, original_path, original_name,
                  original_mtime, original_btime, staging_path, seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity_sha256,
                    source_id,
                    batch_id,
                    str(original_path),
                    original_name,
                    original_mtime,
                    original_btime,
                    str(staging_path) if staging_path else None,
                    now,
                ),
            )
            sighting = self.connection.execute(
                """
                SELECT id FROM sightings
                WHERE sha256 = ? AND source_id = ? AND original_path = ?
                """,
                (identity_sha256, source_id, str(original_path)),
            ).fetchone()
            assert sighting is not None
            self.connection.execute(
                """
                INSERT OR IGNORE INTO batch_items(batch_id, sighting_id)
                VALUES (?, ?)
                """,
                (batch_id, sighting["id"]),
            )
        record = self.file(identity_sha256)
        assert record is not None
        return record, created, cursor.rowcount == 1

    def sighting_staging_path(
        self,
        sha256: str,
        source_id: str,
        original_path: Path,
    ) -> Path | None:
        """Return the working-copy pointer for exact source evidence."""
        row = self.connection.execute(
            """
            SELECT staging_path FROM sightings
            WHERE sha256 = ? AND source_id = ? AND original_path = ?
            """,
            (sha256, source_id, str(original_path)),
        ).fetchone()
        if row is None or row["staging_path"] is None:
            return None
        return Path(str(row["staging_path"]))

    def update_sighting_staging_path(
        self,
        sha256: str,
        source_id: str,
        original_path: Path,
        staging_path: Path,
    ) -> None:
        """Repair the mutable working-copy pointer without changing source evidence."""
        with self.transaction():
            cursor = self.connection.execute(
                """
                UPDATE sightings SET staging_path = ?
                WHERE sha256 = ? AND source_id = ? AND original_path = ?
                """,
                (str(staging_path), sha256, source_id, str(original_path)),
            )
            if cursor.rowcount != 1:
                raise KeyError("Cannot repair unknown sighting")

    def queue_archived_reunion(
        self,
        *,
        batch_id: str,
        sha256: str,
        source_path: Path,
    ) -> bool:
        """Queue an inbox copy of archived content for later user approval."""
        detail = json.dumps(
            {
                "status": "pending",
                "reason": "archived_reunion",
                "source_path": str(source_path),
            },
            sort_keys=True,
        )
        existing = self.connection.execute(
            """
            SELECT 1 FROM operations
            WHERE sha256 = ? AND op = 'trash' AND detail_json = ?
            """,
            (sha256, detail),
        ).fetchone()
        if existing is not None:
            return False
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
                VALUES (?, ?, 'trash', ?, ?)
                """,
                (batch_id, sha256, detail, utc_now()),
            )
        return True

    def mark_near_duplicate(
        self,
        batch_id: str,
        winner_sha256: str,
        loser_sha256: str,
        group_id: str,
        *,
        reason: str = "near_duplicate",
    ) -> None:
        """Persist one high-confidence near-duplicate decision without deleting files."""
        with self.transaction():
            winner = self.file(winner_sha256)
            loser = self.file(loser_sha256)
            if winner is None or loser is None:
                raise KeyError("Unknown near-duplicate file")
            if winner["status"] == "ingested":
                self.connection.execute(
                    "UPDATE files SET status = 'unique', dup_group_id = ?, updated_at = ? "
                    "WHERE sha256 = ?",
                    (group_id, utc_now(), winner_sha256),
                )
            if loser["status"] != "ingested":
                raise ValueError(f"Cannot replace dedupe status {loser['status']}")
            self.connection.execute(
                """
                UPDATE files SET status = 'duplicate', dup_group_id = ?, kept_sha256 = ?,
                                 updated_at = ? WHERE sha256 = ?
                """,
                (group_id, winner_sha256, utc_now(), loser_sha256),
            )
            paths = [
                str(row["candidate_path"])
                for row in self.connection.execute(
                    """
                    SELECT CASE
                             WHEN sources.kind = 'rescue' THEN sightings.staging_path
                             ELSE COALESCE(sightings.staging_path, sightings.original_path)
                           END AS candidate_path
                    FROM sightings JOIN sources USING(source_id)
                    WHERE sightings.sha256 = ? ORDER BY sightings.seen_at
                    """,
                    (loser_sha256,),
                ).fetchall()
                if row["candidate_path"] is not None
            ]
            detail = json.dumps(
                {
                    "status": "pending",
                    "reason": reason,
                    "kept_sha256": winner_sha256,
                    "paths": paths,
                },
                sort_keys=True,
            )
            existing = self.connection.execute(
                "SELECT 1 FROM operations WHERE sha256 = ? AND op = 'trash' AND detail_json = ?",
                (loser_sha256, detail),
            ).fetchone()
            if paths and existing is None:
                self.connection.execute(
                    """
                    INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
                    VALUES (?, ?, 'trash', ?, ?)
                    """,
                    (batch_id, loser_sha256, detail, utc_now()),
                )

    def mark_files_unique(self, sha256_values: set[str]) -> None:
        """Mark explicitly retained ingested files as unique."""
        with self.transaction():
            for sha256 in sha256_values:
                self.connection.execute(
                    """
                    UPDATE files SET status = 'unique', updated_at = ?
                    WHERE sha256 = ? AND status = 'ingested'
                    """,
                    (utc_now(), sha256),
                )

    def mark_batch_unique(self, batch_id: str, *, exclude: set[str]) -> None:
        """Advance unambiguous new files after dedupe analysis."""
        rows = self.connection.execute(
            """
            SELECT DISTINCT sightings.sha256
            FROM batch_items JOIN sightings ON sightings.id = batch_items.sighting_id
            WHERE batch_items.batch_id = ?
            """,
            (batch_id,),
        ).fetchall()
        with self.transaction():
            for row in rows:
                sha256 = str(row["sha256"])
                if sha256 in exclude:
                    continue
                self.connection.execute(
                    """
                    UPDATE files SET status = 'unique', updated_at = ?
                    WHERE sha256 = ? AND status = 'ingested'
                    """,
                    (utc_now(), sha256),
                )

    def rescue_staging_paths(self, sha256: str) -> list[Path]:
        """Return previously recorded rescue copies, newest first.

        The catalog only returns recorded evidence. Callers must still verify that
        the path exists and contains the expected bytes before reusing it.
        """
        rows = self.connection.execute(
            """
            SELECT sightings.staging_path
            FROM sightings
            JOIN sources ON sources.source_id = sightings.source_id
            WHERE sightings.sha256 = ?
              AND sources.kind = 'rescue'
              AND sightings.staging_path IS NOT NULL
            ORDER BY sightings.id DESC
            """,
            (sha256,),
        ).fetchall()
        return [Path(str(row["staging_path"])) for row in rows]

    def transition(self, sha256: str, target: FileStatus) -> None:
        """Apply the only legal file status transitions."""
        current = self.file(sha256)
        if current is None:
            raise KeyError(f"Unknown file: {sha256}")
        source = str(current["status"])
        if target not in _ALLOWED_TRANSITIONS[source]:
            raise ValueError(f"Illegal file transition: {source} -> {target}")
        with self.transaction():
            self.connection.execute(
                "UPDATE files SET status = ?, updated_at = ? WHERE sha256 = ?",
                (target, utc_now(), sha256),
            )

    def count(self, table: str) -> int:
        """Return a row count for a known catalog table (tests/reporting only)."""
        if table not in {
            "sources",
            "batches",
            "files",
            "sightings",
            "batch_items",
            "operations",
        }:
            raise ValueError(f"Unsupported table: {table}")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
