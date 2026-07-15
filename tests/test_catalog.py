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

    assert version == 1
    assert journal_mode == "wal"
    assert foreign_keys == 1


def test_catalog_rejects_source_identity_drift(tmp_path: Path):
    with Catalog(tmp_path / "catalog.db") as catalog:
        catalog.register_source("old-laptop", "Old laptop", "rescue")

        with pytest.raises(ValueError, match="already registered"):
            catalog.register_source("old-laptop", "Old laptop", "inbox")


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
                2,
            )

        version = catalog.connection.execute("PRAGMA user_version").fetchone()[0]
        table = catalog.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'should_rollback'"
        ).fetchone()

    assert version == 1
    assert table is None
