"""Tests for explicit catalog-scoped duplicate trash approval."""

import hashlib
import json
from pathlib import Path

import pytest

from phoxif.pipeline.catalog import Catalog
from phoxif.pipeline.trash import execute, pending


def _queued_operation(database: Path, photo: Path) -> int:
    digest = hashlib.sha256(photo.read_bytes()).hexdigest()
    with Catalog(database) as catalog:
        catalog.register_source("inbox", "Inbox", "inbox")
        batch_id = catalog.start_batch("inbox", "inbox")
        catalog.upsert_file(
            sha256=digest,
            size=photo.stat().st_size,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id="inbox",
            batch_id=batch_id,
            original_path=photo,
            original_name=photo.name,
            original_mtime=None,
            original_btime=None,
            staging_path=photo,
        )
        catalog.queue_archived_reunion(
            batch_id=batch_id,
            sha256=digest,
            source_path=photo,
        )
        return int(catalog.connection.execute("SELECT id FROM operations").fetchone()[0])


def test_pending_trash_requires_approval_and_is_idempotent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    photo = tmp_path / "duplicate.jpg"
    photo.write_bytes(b"duplicate-content")
    database = tmp_path / "catalog.db"
    operation_id = _queued_operation(database, photo)
    trashed: list[str] = []

    def fake_trash(path: str) -> None:
        trashed.append(path)
        Path(path).rename(tmp_path / "system-trash.jpg")

    monkeypatch.setattr("phoxif.pipeline.trash.send2trash", fake_trash)

    items = pending(database)
    assert [item.operation_id for item in items] == [operation_id]
    assert items[0].names == [photo.name]
    with pytest.raises(PermissionError, match="explicit approval"):
        execute(database, [operation_id], approved=False)
    assert photo.exists()

    result = execute(database, [operation_id], approved=True)
    assert result["completed"] == 1
    assert result["failed"] == 0
    assert trashed == [str(photo)]
    assert pending(database) == []
    with Catalog(database) as catalog:
        row = catalog.connection.execute("SELECT staging_path FROM sightings").fetchone()
        assert row["staging_path"] is None

    repeated = execute(database, [operation_id], approved=True)
    assert repeated["completed"] == 1
    assert trashed == [str(photo)]


def test_trash_refuses_content_changed_after_review(monkeypatch, tmp_path: Path) -> None:
    photo = tmp_path / "duplicate.jpg"
    photo.write_bytes(b"reviewed-content")
    database = tmp_path / "catalog.db"
    operation_id = _queued_operation(database, photo)
    photo.write_bytes(b"changed-after-review")
    called: list[str] = []
    monkeypatch.setattr("phoxif.pipeline.trash.send2trash", called.append)

    result = execute(database, [operation_id], approved=True)

    assert result["failed"] == 1
    assert result["results"][0]["failures"][0]["error"] == "Content changed since review"
    assert called == []
    assert photo.exists()
    assert len(pending(database)) == 1


def test_trash_refuses_symlink_even_when_target_matches(monkeypatch, tmp_path: Path) -> None:
    photo = tmp_path / "duplicate.jpg"
    photo.write_bytes(b"reviewed-content")
    database = tmp_path / "catalog.db"
    operation_id = _queued_operation(database, photo)
    target = tmp_path / "target.jpg"
    photo.rename(target)
    photo.symlink_to(target)
    called: list[str] = []
    monkeypatch.setattr("phoxif.pipeline.trash.send2trash", called.append)

    result = execute(database, [operation_id], approved=True)

    assert result["failed"] == 1
    assert result["results"][0]["failures"][0]["error"] == "Refusing to trash a symlink"
    assert called == []
    assert photo.is_symlink()


def test_trash_never_accepts_an_unstaged_rescue_original(
    monkeypatch,
    tmp_path: Path,
) -> None:
    photo = tmp_path / "rescue-original.jpg"
    photo.write_bytes(b"only-copy")
    digest = hashlib.sha256(photo.read_bytes()).hexdigest()
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("rescue", "Rescue", "rescue")
        batch_id = catalog.start_batch("rescue", "rescue")
        catalog.upsert_file(
            sha256=digest,
            size=photo.stat().st_size,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id="rescue",
            batch_id=batch_id,
            original_path=photo,
            original_name=photo.name,
            original_mtime=None,
            original_btime=None,
            staging_path=None,
        )
        detail = json.dumps(
            {"status": "pending", "reason": "adversarial", "paths": [str(photo)]},
            sort_keys=True,
        )
        with catalog.transaction():
            catalog.connection.execute(
                """
                INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
                VALUES (?, ?, 'trash', ?, '2026-01-01T00:00:00+00:00')
                """,
                (batch_id, digest, detail),
            )
        operation_id = int(catalog.connection.execute("SELECT id FROM operations").fetchone()[0])
    called: list[str] = []
    monkeypatch.setattr("phoxif.pipeline.trash.send2trash", called.append)

    result = execute(database, [operation_id], approved=True)

    assert result["failed"] == 1
    assert result["results"][0]["failures"][0]["error"] == "Path is outside catalog evidence"
    assert called == []
    assert photo.read_bytes() == b"only-copy"
