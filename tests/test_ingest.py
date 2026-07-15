"""Tests for idempotent, source-preserving pipeline ingest."""

import hashlib
import shutil
from pathlib import Path

import pytest

from phoxif.pipeline.catalog import Catalog
from phoxif.pipeline.ingest import run


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rescue_ingest_stages_verified_copy_and_preserves_source(make_jpeg, tmp_path: Path):
    source_root = tmp_path / "old-laptop"
    photo = make_jpeg("IMG_0001.jpg", directory=source_root)
    before = (photo.read_bytes(), photo.stat().st_mtime_ns)
    database = tmp_path / "state" / "catalog.db"
    staging = tmp_path / "staging"

    result = run(
        "old-laptop",
        source_root,
        "rescue",
        catalog_db=database,
        staging_root=staging,
    )

    assert result.scanned == 1
    assert result.new_files == 1
    assert result.new_sightings == 1
    assert result.staged_files == 1
    assert (photo.read_bytes(), photo.stat().st_mtime_ns) == before
    staged = list(staging.rglob("*.jpg"))
    assert len(staged) == 1
    assert _digest(staged[0]) == _digest(photo)
    assert staged[0].stat().st_mtime_ns == photo.stat().st_mtime_ns

    with Catalog(database) as catalog:
        assert catalog.count("sources") == 1
        assert catalog.count("batches") == 1
        assert catalog.count("files") == 1
        assert catalog.count("sightings") == 1


def test_ingest_rerun_is_idempotent(make_jpeg, tmp_path: Path):
    source_root = tmp_path / "camera"
    make_jpeg("IMG_0002.jpg", directory=source_root)
    database = tmp_path / "catalog.db"
    staging = tmp_path / "staging"

    first = run(
        "camera",
        source_root,
        "rescue",
        catalog_db=database,
        staging_root=staging,
    )
    second = run(
        "camera",
        source_root,
        "rescue",
        catalog_db=database,
        staging_root=staging,
    )

    assert first.new_sightings == 1
    assert second.new_files == 0
    assert second.new_sightings == 0
    assert second.staged_files == 0
    assert second.verified_staging == 1
    with Catalog(database) as catalog:
        assert catalog.count("files") == 1
        assert catalog.count("sightings") == 1
        assert catalog.count("batches") == 2
        assert catalog.count("batch_items") == 2
        memberships = catalog.connection.execute(
            "SELECT batch_id FROM batch_items ORDER BY batch_id"
        ).fetchall()

    assert [row["batch_id"] for row in memberships] == [first.batch_id, second.batch_id]


def test_duplicate_status_is_not_restaged_on_rescue_rerun(make_jpeg, tmp_path: Path) -> None:
    source_root = tmp_path / "camera"
    photo = make_jpeg("IMG_0012.jpg", directory=source_root)
    database = tmp_path / "catalog.db"
    staging = tmp_path / "staging"
    run("camera", source_root, "rescue", catalog_db=database, staging_root=staging)
    digest = _digest(photo)
    staged = next(staging.rglob("*.jpg"))
    with Catalog(database) as catalog:
        winner = "9" * 64
        catalog.upsert_file(
            sha256=winner,
            size=1,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.mark_near_duplicate("manual-batch", winner, digest, "group-1")
    staged.unlink()

    repeated = run(
        "camera",
        source_root,
        "rescue",
        catalog_db=database,
        staging_root=staging,
    )

    assert repeated.staged_files == 0
    assert repeated.verified_staging == 0
    assert list(staging.rglob("*.jpg")) == []


def test_cross_source_same_content_is_one_file_two_sightings(make_jpeg, tmp_path: Path):
    first_root = tmp_path / "laptop"
    second_root = tmp_path / "phone"
    original = make_jpeg("IMG_0003.jpg", directory=first_root)
    second_root.mkdir()
    shutil.copy2(original, second_root / "copy.jpg")
    database = tmp_path / "catalog.db"
    staging = tmp_path / "staging"

    first = run("laptop", first_root, "rescue", catalog_db=database, staging_root=staging)
    second = run("phone", second_root, "rescue", catalog_db=database, staging_root=staging)

    assert first.staged_files == 1
    assert second.new_files == 0
    assert second.already_known == 1
    assert second.new_sightings == 1
    assert second.staged_files == 0
    assert len(list(staging.rglob("*.jpg"))) == 1
    with Catalog(database) as catalog:
        assert catalog.count("files") == 1
        assert catalog.count("sightings") == 2
        rows = catalog.connection.execute(
            "SELECT staging_path FROM sightings ORDER BY id"
        ).fetchall()
        assert rows[0]["staging_path"] == rows[1]["staging_path"]


@pytest.mark.parametrize("damage", ["missing", "corrupt", "symlink"])
def test_rerun_repairs_stale_working_copy(
    damage: str,
    make_jpeg,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "camera"
    photo = make_jpeg("IMG_0007.jpg", directory=source_root)
    database = tmp_path / "catalog.db"
    staging = tmp_path / "staging"
    run("camera", source_root, "rescue", catalog_db=database, staging_root=staging)
    staged = next(staging.rglob("*.jpg"))
    if damage == "missing":
        staged.unlink()
    elif damage == "symlink":
        staged.unlink()
        staged.symlink_to(photo)
    else:
        staged.write_bytes(b"corrupt")

    repaired = run(
        "camera",
        source_root,
        "rescue",
        catalog_db=database,
        staging_root=staging,
    )

    assert repaired.staged_files == 1
    assert repaired.verified_staging == 1
    with Catalog(database) as catalog:
        repaired_path = catalog.sighting_staging_path(
            _digest(photo),
            "camera",
            photo.resolve(),
        )
    assert repaired_path is not None
    assert repaired_path.is_file()
    assert not repaired_path.is_symlink()
    assert _digest(repaired_path) == _digest(photo)
    assert repaired_path.stat().st_ino != photo.stat().st_ino
    if damage in {"corrupt", "symlink"}:
        assert len(list((staging / ".corrupt").glob("*.corrupt"))) == 1


def test_failed_catalog_record_is_recoverable_without_duplicate_copy(
    monkeypatch,
    make_jpeg,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "camera"
    make_jpeg("IMG_0008.jpg", directory=source_root)
    database = tmp_path / "catalog.db"
    staging = tmp_path / "staging"
    original_record_ingest = Catalog.record_ingest

    def fail_record(*_args, **_kwargs):
        raise RuntimeError("injected record failure")

    monkeypatch.setattr(Catalog, "record_ingest", fail_record)
    with pytest.raises(RuntimeError, match="injected record failure"):
        run("camera", source_root, "rescue", catalog_db=database, staging_root=staging)

    with Catalog(database) as catalog:
        assert catalog.count("files") == 0
        assert catalog.count("sightings") == 0
        failed = catalog.connection.execute(
            "SELECT finished_at, stats_json FROM batches"
        ).fetchone()
        assert failed["finished_at"] is not None
        assert '"status": "failed"' in failed["stats_json"]

    monkeypatch.setattr(Catalog, "record_ingest", original_record_ingest)
    retry = run("camera", source_root, "rescue", catalog_db=database, staging_root=staging)
    assert retry.new_files == 1
    assert retry.new_sightings == 1
    assert retry.verified_staging == 1
    assert len(list(staging.rglob("*.jpg"))) == 1


def test_source_change_during_copy_fails_without_catalog_identity(
    monkeypatch,
    make_jpeg,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "camera"
    make_jpeg("IMG_0011.jpg", directory=source_root)
    database = tmp_path / "catalog.db"
    staging = tmp_path / "staging"
    from phoxif.pipeline import ingest

    original_stage_copy = ingest._stage_copy

    def stage_then_change(source: Path, destination: Path, sha256: str) -> bool:
        copied = original_stage_copy(source, destination, sha256)
        source.write_bytes(source.read_bytes() + b"changed")
        return copied

    monkeypatch.setattr(ingest, "_stage_copy", stage_then_change)
    with pytest.raises(RuntimeError, match="Source changed during ingest"):
        run("camera", source_root, "rescue", catalog_db=database, staging_root=staging)

    with Catalog(database) as catalog:
        assert catalog.count("files") == 0
        assert catalog.count("sightings") == 0
        batch = catalog.connection.execute(
            "SELECT finished_at, stats_json FROM batches"
        ).fetchone()
        assert batch["finished_at"] is not None
        assert '"status": "failed"' in batch["stats_json"]


def test_inbox_ingest_uses_intake_file_without_copy(make_jpeg, tmp_path: Path):
    inbox = tmp_path / "wechat"
    photo = make_jpeg("mmexport1705312245678.jpg", directory=inbox)
    database = tmp_path / "catalog.db"
    staging = tmp_path / "staging"

    result = run(
        "wechat",
        inbox,
        "inbox",
        catalog_db=database,
        staging_root=staging,
    )

    assert result.staged_files == 0
    assert not staging.exists()
    with Catalog(database) as catalog:
        row = catalog.connection.execute("SELECT staging_path FROM sightings").fetchone()
        assert Path(row["staging_path"]) == photo.resolve()


def test_archived_reunion_queues_one_pending_inbox_trash(
    make_jpeg,
    tmp_path: Path,
) -> None:
    rescue_root = tmp_path / "old-laptop"
    inbox_root = tmp_path / "messages"
    original = make_jpeg("IMG_0010.jpg", directory=rescue_root)
    inbox_root.mkdir()
    shutil.copy2(original, inbox_root / "received.jpg")
    database = tmp_path / "catalog.db"
    staging = tmp_path / "staging"
    digest = _digest(original)
    run("old-laptop", rescue_root, "rescue", catalog_db=database, staging_root=staging)
    with Catalog(database) as catalog:
        catalog.transition(digest, "unique")
        catalog.transition(digest, "enriched")
        catalog.transition(digest, "archived")

    reunion = run(
        "messages",
        inbox_root,
        "inbox",
        catalog_db=database,
        staging_root=staging,
    )
    repeated = run(
        "messages",
        inbox_root,
        "inbox",
        catalog_db=database,
        staging_root=staging,
    )

    assert reunion.archived_reunions == 1
    assert repeated.archived_reunions == 1
    with Catalog(database) as catalog:
        assert catalog.count("operations") == 1
        operation = catalog.connection.execute(
            "SELECT op, detail_json FROM operations"
        ).fetchone()
        assert operation["op"] == "trash"
        assert '"reason": "archived_reunion"' in operation["detail_json"]
        assert '"status": "pending"' in operation["detail_json"]


def test_rescue_preflight_rejects_insufficient_space_before_catalog(
    monkeypatch,
    make_jpeg,
    tmp_path: Path,
):
    source_root = tmp_path / "large-source"
    make_jpeg("IMG_0004.jpg", directory=source_root)
    database = tmp_path / "catalog.db"

    monkeypatch.setattr(
        "phoxif.pipeline.ingest.shutil.disk_usage",
        lambda _path: shutil._ntuple_diskusage(total=100, used=100, free=0),
    )

    with pytest.raises(OSError, match="Staging requires"):
        run(
            "large-source",
            source_root,
            "rescue",
            catalog_db=database,
            staging_root=tmp_path / "staging",
        )

    assert not database.exists()


@pytest.mark.parametrize("staging_name", ["source/staging", "source"])
def test_rescue_rejects_staging_that_overlaps_source(
    staging_name: str,
    make_jpeg,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    make_jpeg("IMG_0005.jpg", directory=source_root)
    if staging_name == "source":
        source_root = source_root / "nested"
        make_jpeg("IMG_0006.jpg", directory=source_root)
        staging = tmp_path / "source"
    else:
        staging = tmp_path / staging_name

    with pytest.raises(ValueError, match="must not overlap"):
        run(
            "overlap",
            source_root,
            "rescue",
            catalog_db=tmp_path / "catalog.db",
            staging_root=staging,
        )

    assert not (tmp_path / "catalog.db").exists()


@pytest.mark.parametrize("mode", ["rescue", "inbox"])
def test_ingest_rejects_catalog_inside_source_before_writing(
    mode: str,
    make_jpeg,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    make_jpeg("IMG_0009.jpg", directory=source_root)
    before = sorted(source_root.iterdir())

    with pytest.raises(ValueError, match="Catalog database must be outside"):
        run(
            "unsafe-catalog",
            source_root,
            mode,
            catalog_db=source_root / "catalog.db",
            staging_root=tmp_path / "staging",
        )

    assert sorted(source_root.iterdir()) == before
