"""Tests for the permanent SQLite catalog contract."""

import sqlite3
from pathlib import Path

import pytest

from phoxif.pipeline.catalog import Catalog


def test_catalog_migrates_and_enables_integrity_guards(tmp_path: Path):
    database = tmp_path / "catalog.db"

    with Catalog(database) as catalog:
        version = catalog.connection.execute("PRAGMA user_version").fetchone()[0]
        journal_mode = catalog.connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = catalog.connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert version == 5
    assert journal_mode == "wal"
    assert foreign_keys == 1


def test_catalog_rejects_source_identity_drift(tmp_path: Path):
    with Catalog(tmp_path / "catalog.db") as catalog:
        catalog.register_source("old-laptop", "Old laptop", "rescue")

        with pytest.raises(ValueError, match="already registered"):
            catalog.register_source("old-laptop", "Old laptop", "inbox")


def test_catalog_persists_source_root_and_rejects_root_drift(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    with Catalog(tmp_path / "catalog.db") as catalog:
        catalog.register_source(
            "old-laptop",
            "Old laptop",
            "rescue",
            root_path=first_root,
        )
        source = catalog.connection.execute(
            "SELECT root_path FROM sources WHERE source_id = 'old-laptop'"
        ).fetchone()

        assert source["root_path"] == str(first_root.resolve())
        with pytest.raises(ValueError, match="another root"):
            catalog.register_source(
                "old-laptop",
                "Old laptop",
                "rescue",
                root_path=second_root,
            )


def test_catalog_enforces_file_state_machine(tmp_path: Path):
    with Catalog(tmp_path / "catalog.db") as catalog:
        record, created = catalog.upsert_file(
            sha256="a" * 64,
            size=10,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=2,
            height=5,
        )
        assert created is True
        assert record["status"] == "ingested"

        catalog.transition("a" * 64, "unique")
        catalog.transition("a" * 64, "enriched")
        catalog.transition("a" * 64, "archived")

        with pytest.raises(ValueError, match="Illegal file transition"):
            catalog.transition("a" * 64, "unique")


def test_catalog_preserves_ingest_identity_while_tracking_current_bytes(tmp_path: Path):
    database = tmp_path / "catalog.db"
    ingest_sha = "1" * 64
    current_sha = "2" * 64
    with Catalog(database) as catalog:
        catalog.upsert_file(
            sha256=ingest_sha,
            size=10,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.update_current_content(ingest_sha, current_sha, 14)

        record, created = catalog.upsert_file(
            sha256=current_sha,
            size=14,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )

    assert created is False
    assert record["sha256"] == ingest_sha
    assert record["current_sha256"] == current_sha
    assert record["current_size"] == 14


def test_sighting_evidence_cannot_be_duplicated(tmp_path: Path):
    database = tmp_path / "catalog.db"
    source_file = tmp_path / "photo.jpg"
    source_file.write_bytes(b"photo")

    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        catalog.upsert_file(
            sha256="b" * 64,
            size=5,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        kwargs = {
            "sha256": "b" * 64,
            "source_id": "camera",
            "batch_id": batch_id,
            "original_path": source_file,
            "original_name": source_file.name,
            "original_mtime": None,
            "original_btime": None,
            "staging_path": None,
        }
        assert catalog.add_sighting(**kwargs) is True
        assert catalog.add_sighting(**kwargs) is False
        assert catalog.count("sightings") == 1
        assert catalog.count("batch_items") == 1


def test_database_rejects_sighting_without_file(tmp_path: Path):
    with Catalog(tmp_path / "catalog.db") as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        with pytest.raises(sqlite3.IntegrityError):
            catalog.add_sighting(
                sha256="c" * 64,
                source_id="camera",
                batch_id=batch_id,
                original_path=tmp_path / "missing.jpg",
                original_name="missing.jpg",
                original_mtime=None,
                original_btime=None,
                staging_path=None,
            )


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path) -> None:
    with Catalog(tmp_path / "catalog.db") as catalog:
        with pytest.raises(sqlite3.Error):
            catalog._apply_migration(
                "CREATE TABLE should_rollback(value TEXT); INVALID SQL;",
                6,
            )

        version = catalog.connection.execute("PRAGMA user_version").fetchone()[0]
        table = catalog.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'should_rollback'"
        ).fetchone()

    assert version == 5
    assert table is None


def test_non_photo_classification_is_not_erased_by_later_generic_sighting(
    tmp_path: Path,
) -> None:
    digest = "d" * 64
    with Catalog(tmp_path / "catalog.db") as catalog:
        catalog.upsert_file(
            sha256=digest,
            size=5,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.set_collection_class(digest, "non-photo", "screenshot")
        catalog.set_collection_class(digest, "photo", None)
        record = catalog.file(digest)

    assert record["collection_class"] == "non-photo"
    assert record["non_photo_category"] == "screenshot"
