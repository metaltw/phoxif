"""Integration tests for approval-gated, idempotent archive copies."""

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path, PurePath

import pytest

from phoxif.pipeline.archive import (
    ARCHIVE_MARKER_CONTENT,
    ARCHIVE_MARKER_NAME,
    _catalog_snapshot_size,
    approval_fingerprint,
    approval_matches,
    execute_archive,
    plan_archive,
)
from phoxif.pipeline.catalog import Catalog


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_root(path: Path) -> Path:
    path.mkdir()
    (path / ARCHIVE_MARKER_NAME).write_text(ARCHIVE_MARKER_CONTENT)
    return path


def _add_enriched(
    catalog: Catalog,
    *,
    batch_id: str,
    source: Path,
    staging: Path,
    date_written: str,
) -> str:
    digest = _sha256(source)
    catalog.upsert_file(
        sha256=digest,
        size=source.stat().st_size,
        ext=source.suffix,
        media_type="image",
        phash=None,
        width=32,
        height=32,
    )
    catalog.add_sighting(
        sha256=digest,
        source_id="camera",
        batch_id=batch_id,
        original_path=source,
        original_name=source.name,
        original_mtime=None,
        original_btime=None,
        staging_path=staging,
    )
    catalog.transition(digest, "unique")
    catalog.transition(digest, "enriched")
    with catalog.transaction():
        catalog.connection.execute(
            "UPDATE files SET date_written = ?, date_source = 'native-exif', "
            "date_confidence = 1 WHERE sha256 = ?",
            (date_written, digest),
        )
    return digest


def test_archive_requires_approval_then_copies_verifies_and_is_idempotent(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("original name.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    archive_root = _archive_root(tmp_path / "library")
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        digest = _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )

    plan = plan_archive([batch_id], catalog_db=database)
    assert plan.items[0].relative_path == "2024/2024-01/20240102_030405.jpg"
    assert [path.name for path in archive_root.iterdir()] == [ARCHIVE_MARKER_NAME]
    with pytest.raises(PermissionError):
        execute_archive(
            plan,
            catalog_db=database,
            archive_root=archive_root,
            approved=False,
        )
    assert [path.name for path in archive_root.iterdir()] == [ARCHIVE_MARKER_NAME]

    result = execute_archive(
        plan,
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )
    destination = archive_root / "2024/2024-01/20240102_030405.jpg"

    assert result["archived"] == 1
    assert result["failed"] == 0
    assert result["snapshot_path"].startswith("_phoxif/catalog-")
    assert destination.read_bytes() == staging.read_bytes()
    assert source.exists() and staging.exists()
    assert destination.stat().st_mode & 0o222 == 0
    with Catalog(database) as catalog:
        record = catalog.file(digest)
        operation = catalog.connection.execute(
            "SELECT detail_json FROM operations WHERE op = 'archive_copy'"
        ).fetchone()
    assert record["status"] == "archived"
    assert record["archived_path"] == "2024/2024-01/20240102_030405.jpg"
    assert json.loads(operation["detail_json"])["status"] == "completed"

    repeated_plan = plan_archive([batch_id], catalog_db=database)
    assert repeated_plan.items[0].reason == "already-archived"
    repeated = execute_archive(
        repeated_plan,
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )
    assert repeated["archived"] == 0
    assert list((archive_root / "2024/2024-01").glob("*.jpg")) == [destination]


def test_archive_allocates_catalog_collisions_and_skips_quarantine(
    make_jpeg,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        digests = []
        for index in range(3):
            source = make_jpeg(
                f"photo-{index}.jpg",
                exif={"ImageDescription": f"distinct-{index}"},
                directory=tmp_path / "source",
            )
            staging = staging_dir / source.name
            shutil.copy2(source, staging)
            digests.append(
                _add_enriched(
                    catalog,
                    batch_id=batch_id,
                    source=source,
                    staging=staging,
                    date_written="2024:01:02 03:04:05",
                )
            )
        catalog.transition(digests[2], "quarantined")

    plan = plan_archive([batch_id], catalog_db=database)
    paths = [item.relative_path for item in plan.items if item.action == "archive"]

    assert paths == [
        "2024/2024-01/20240102_030405.jpg",
        "2024/2024-01/20240102_030405_1.jpg",
    ]
    assert any(item.reason == "date-quarantined" for item in plan.items)


def test_archive_rejects_changed_source_and_destination_collision(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    archive_root = _archive_root(tmp_path / "library")
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )
    plan = plan_archive([batch_id], catalog_db=database)
    staging.write_bytes(b"changed")

    changed = execute_archive(
        plan,
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )

    assert changed["failed"] == 1
    destination = archive_root / str(plan.items[0].relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"foreign")
    shutil.copy2(source, staging)
    collision = execute_archive(
        plan,
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )
    assert collision["failed"] == 1
    assert destination.read_bytes() == b"foreign"


def test_archive_restart_recovers_verified_destination(make_jpeg, tmp_path: Path) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    archive_root = _archive_root(tmp_path / "library")
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        digest = _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )
    plan = plan_archive([batch_id], catalog_db=database)
    item = plan.items[0]
    destination = archive_root / str(item.relative_path)
    destination.parent.mkdir(parents=True)
    shutil.copy2(staging, destination)
    detail = {
        "status": "executing",
        "source_path": str(staging),
        "relative_path": item.relative_path,
        "expected_current_sha256": item.current_sha256,
        "size": item.size,
    }
    with Catalog(database) as catalog:
        with catalog.transaction():
            catalog.connection.execute(
                """
                INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
                VALUES (?, ?, 'archive_copy', ?, 'interrupted')
                """,
                (batch_id, digest, json.dumps(detail, sort_keys=True)),
            )

    restarted_plan = plan_archive([batch_id], catalog_db=database)
    result = execute_archive(
        restarted_plan,
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )

    assert result["failed"] == 0
    assert result["results"][0]["copied"] is False
    assert destination.stat().st_mode & 0o222 == 0
    with Catalog(database) as catalog:
        assert catalog.file(digest)["status"] == "archived"


def test_archive_destination_is_not_writable_after_publish(make_jpeg, tmp_path: Path) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )
    plan = plan_archive([batch_id], catalog_db=database)
    archive_root = _archive_root(tmp_path / "library")
    execute_archive(
        plan,
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )
    destination = archive_root / str(plan.items[0].relative_path)

    assert os.access(destination, os.F_OK)
    assert destination.stat().st_mode & 0o222 == 0


def test_archive_refuses_unmounted_root_and_source_inside_library(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )
    plan = plan_archive([batch_id], catalog_db=database)

    with pytest.raises(NotADirectoryError, match="must already exist"):
        execute_archive(
            plan,
            catalog_db=database,
            archive_root=tmp_path / "not-mounted",
            approved=True,
        )

    library = _archive_root(tmp_path / "library")
    inside = library / "inbox.jpg"
    shutil.copy2(staging, inside)
    inside_plan = type(plan)(
        plan.batch_ids,
        [
            type(plan.items[0])(
                **{
                    **plan.items[0].to_dict(),
                    "source_path": str(inside),
                }
            )
        ],
    )
    with pytest.raises(ValueError, match="outside archive_root"):
        execute_archive(
            inside_plan,
            catalog_db=database,
            archive_root=library,
            approved=True,
        )


def test_archive_rejects_library_nested_inside_recorded_source_root(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source = make_jpeg("photo.jpg", directory=source_root)
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source(
            "camera",
            "Camera",
            "rescue",
            root_path=source_root,
        )
        batch_id = catalog.start_batch("camera", "rescue")
        _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )
    library = _archive_root(source_root / "library")

    with pytest.raises(ValueError, match="must not overlap"):
        execute_archive(
            plan_archive([batch_id], catalog_db=database),
            catalog_db=database,
            archive_root=library,
            approved=True,
        )

    assert list(library.iterdir()) == [library / ARCHIVE_MARKER_NAME]


def test_archive_rejects_overlap_even_when_selected_item_is_quarantined(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source = make_jpeg("photo.jpg", directory=source_root)
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue", root_path=source_root)
        batch_id = catalog.start_batch("camera", "rescue")
        digest = _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )
        catalog.transition(digest, "quarantined")
    library = _archive_root(source_root / "library")
    plan = plan_archive([batch_id], catalog_db=database)

    assert all(item.action == "skip" for item in plan.items)
    with pytest.raises(ValueError, match="must not overlap"):
        execute_archive(
            plan,
            catalog_db=database,
            archive_root=library,
            approved=True,
        )


def test_archive_rejects_symlink_in_root_ancestor(make_jpeg, tmp_path: Path) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    library = _archive_root(linked_parent / "library")

    with pytest.raises(RuntimeError, match="ancestors cannot be symlinks"):
        execute_archive(
            plan_archive([batch_id], catalog_db=database),
            catalog_db=database,
            archive_root=library,
            approved=True,
        )


def test_archive_approval_fingerprint_binds_root_paths_and_hashes(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )
    plan = plan_archive([batch_id], catalog_db=database)
    first_root = _archive_root(tmp_path / "library-a")
    second_root = _archive_root(tmp_path / "library-b")
    fingerprint = approval_fingerprint(plan, first_root)

    assert approval_matches(plan, first_root, fingerprint)
    assert not approval_matches(plan, second_root, fingerprint)
    changed_item = type(plan.items[0])(
        **{**plan.items[0].to_dict(), "relative_path": "changed/file.jpg"}
    )
    changed_plan = type(plan)(plan.batch_ids, [changed_item])
    assert not approval_matches(changed_plan, first_root, fingerprint)
    changed_source = type(plan.items[0])(
        **{**plan.items[0].to_dict(), "source_path": str(tmp_path / "other-copy.jpg")}
    )
    changed_source_plan = type(plan)(plan.batch_ids, [changed_source])
    assert not approval_matches(changed_source_plan, first_root, fingerprint)


def test_archive_rejects_symlink_ancestor_without_writing_outside(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )
    plan = plan_archive([batch_id], catalog_db=database)
    library = _archive_root(tmp_path / "library")
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "2024").symlink_to(outside, target_is_directory=True)

    result = execute_archive(
        plan,
        catalog_db=database,
        archive_root=library,
        approved=True,
    )

    assert result["failed"] == 1
    assert list(outside.rglob("*.jpg")) == []


def test_archive_routes_classified_screenshot_outside_photo_tree(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("Screenshot (123).jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        digest = _sha256(source)
        catalog.upsert_file(
            sha256=digest,
            size=source.stat().st_size,
            ext=source.suffix,
            media_type="image",
            phash=None,
            width=32,
            height=32,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id="camera",
            batch_id=batch_id,
            original_path=source,
            original_name=source.name,
            original_mtime=None,
            original_btime=None,
            staging_path=staging,
        )
        catalog.transition(digest, "unique")
        catalog.set_collection_class(digest, "non-photo", "screenshot")

    plan = plan_archive([batch_id], catalog_db=database)

    assert plan.items[0].action == "archive"
    assert plan.items[0].relative_path == f"_non_photos/screenshot/{digest[:16]}.jpg"


def _add_live_photo_with_sidecar(
    catalog: Catalog,
    *,
    batch_id: str,
    source_dir: Path,
    staging_dir: Path,
) -> tuple[str, str, str]:
    source_dir.mkdir()
    staging_dir.mkdir()
    image = source_dir / "IMG_2001.heic"
    video = source_dir / "IMG_2001.mov"
    sidecar = source_dir / "IMG_2001.AAE"
    image.write_bytes(b"live-image")
    video.write_bytes(b"live-video")
    sidecar.write_bytes(b"edit-recipe")
    digests: list[str] = []
    for source, media_type in ((image, "image"), (video, "video")):
        staging = staging_dir / source.name
        shutil.copy2(source, staging)
        digest = _sha256(source)
        digests.append(digest)
        catalog.upsert_file(
            sha256=digest,
            size=source.stat().st_size,
            ext=source.suffix,
            media_type=media_type,
            phash=None,
            width=None,
            height=None,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id="camera",
            batch_id=batch_id,
            original_path=source,
            original_name=source.name,
            original_mtime=None,
            original_btime=None,
            staging_path=staging,
        )
        catalog.transition(digest, "unique")
        catalog.transition(digest, "enriched")
        with catalog.transaction():
            catalog.connection.execute(
                "UPDATE files SET date_written = '2024:02:03 04:05:06' WHERE sha256 = ?",
                (digest,),
            )
    catalog.set_live_partners(digests[0], digests[1])
    staged_sidecar = staging_dir / sidecar.name
    shutil.copy2(sidecar, staged_sidecar)
    sidecar_digest = _sha256(sidecar)
    catalog.record_sidecar(
        sha256=sidecar_digest,
        size=sidecar.stat().st_size,
        ext=".aae",
        owner_sha256=digests[0],
        source_id="camera",
        batch_id=batch_id,
        original_path=sidecar,
        original_name=sidecar.name,
        staging_path=staged_sidecar,
    )
    return digests[0], digests[1], sidecar_digest


def test_archive_live_photo_and_aae_share_basename_and_commit_as_group(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    archive_root = _archive_root(tmp_path / "library")
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        image_sha, video_sha, sidecar_sha = _add_live_photo_with_sidecar(
            catalog,
            batch_id=batch_id,
            source_dir=tmp_path / "source",
            staging_dir=tmp_path / "staging",
        )

    plan = plan_archive([batch_id], catalog_db=database)
    archive_items = [item for item in plan.items if item.action == "archive"]
    assert len(archive_items) == 3
    assert len({item.group_id for item in archive_items}) == 1
    assert {PurePath(item.relative_path).stem for item in archive_items} == {"20240203_040506"}
    assert {PurePath(item.relative_path).suffix for item in archive_items} == {
        ".heic",
        ".mov",
        ".aae",
    }

    result = execute_archive(
        plan,
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )

    assert result["archived"] == 3
    assert result["failed"] == 0
    with Catalog(database) as catalog:
        assert catalog.file(image_sha)["status"] == "archived"
        assert catalog.file(video_sha)["status"] == "archived"
        sidecar = catalog.connection.execute(
            "SELECT status FROM sidecars WHERE sha256 = ?", (sidecar_sha,)
        ).fetchone()
    assert sidecar["status"] == "archived"


def test_archive_holds_unpaired_live_photo_component(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    staging_dir = tmp_path / "staging"
    source_dir.mkdir()
    staging_dir.mkdir()
    source = source_dir / "IMG_3001.heic"
    staging = staging_dir / source.name
    source.write_bytes(b"unpaired-live-image")
    shutil.copy2(source, staging)
    digest = _sha256(source)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        catalog.upsert_file(
            sha256=digest,
            size=source.stat().st_size,
            ext=source.suffix,
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id="camera",
            batch_id=batch_id,
            original_path=source,
            original_name=source.name,
            original_mtime=None,
            original_btime=None,
            staging_path=staging,
        )
        catalog.transition(digest, "unique")
        catalog.transition(digest, "enriched")
        with catalog.transaction():
            catalog.connection.execute(
                "UPDATE files SET date_written = '2024:02:03 04:05:06' WHERE sha256 = ?",
                (digest,),
            )
        catalog.register_live_identity(digest, "pending-live-group")

    plan = plan_archive([batch_id], catalog_db=database)

    assert plan.items[0].action == "skip"
    assert plan.items[0].reason == "live-partner-not-ready"


def test_archive_expands_selected_batch_to_cross_batch_live_partner(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    staging_dir = tmp_path / "staging"
    source_dir.mkdir()
    staging_dir.mkdir()
    image = source_dir / "IMG_3002.heic"
    video = source_dir / "IMG_3002.mov"
    image.write_bytes(b"cross-batch-live-image")
    video.write_bytes(b"cross-batch-live-video")
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        for source_id in ("image-source", "video-source"):
            catalog.register_source(source_id, source_id, "rescue")
        selected_batch = ""
        for source, source_id, media_type in (
            (image, "image-source", "image"),
            (video, "video-source", "video"),
        ):
            batch_id = catalog.start_batch(source_id, "rescue")
            selected_batch = batch_id
            staging = staging_dir / source.name
            shutil.copy2(source, staging)
            digest = _sha256(source)
            catalog.upsert_file(
                sha256=digest,
                size=source.stat().st_size,
                ext=source.suffix,
                media_type=media_type,
                phash=None,
                width=None,
                height=None,
            )
            catalog.add_sighting(
                sha256=digest,
                source_id=source_id,
                batch_id=batch_id,
                original_path=source,
                original_name=source.name,
                original_mtime=None,
                original_btime=None,
                staging_path=staging,
            )
            catalog.transition(digest, "unique")
            catalog.transition(digest, "enriched")
            with catalog.transaction():
                catalog.connection.execute(
                    "UPDATE files SET date_written = '2024:02:03 04:05:06' WHERE sha256 = ?",
                    (digest,),
                )
            catalog.register_live_identity(digest, "cross-batch-live-group")

    plan = plan_archive([selected_batch], catalog_db=database)
    archive_items = [item for item in plan.items if item.action == "archive"]

    assert len(archive_items) == 2
    assert len({item.group_id for item in archive_items}) == 1
    assert {PurePath(item.relative_path).suffix for item in archive_items} == {
        ".heic",
        ".mov",
    }


def test_archive_live_group_recovers_after_partial_publish(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from phoxif.pipeline import archive

    database = tmp_path / "catalog.db"
    archive_root = _archive_root(tmp_path / "library")
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        image_sha, video_sha, _sidecar_sha = _add_live_photo_with_sidecar(
            catalog,
            batch_id=batch_id,
            source_dir=tmp_path / "source",
            staging_dir=tmp_path / "staging",
        )
    plan = plan_archive([batch_id], catalog_db=database)
    original_publish = archive._publish_verified_copy
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected group interruption")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(archive, "_publish_verified_copy", fail_second)
    interrupted = execute_archive(
        plan,
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )
    assert interrupted["failed"] == 3
    with Catalog(database) as catalog:
        assert catalog.file(image_sha)["status"] == "enriched"
        assert catalog.file(video_sha)["status"] == "enriched"

    monkeypatch.setattr(archive, "_publish_verified_copy", original_publish)
    retried = execute_archive(
        plan_archive([batch_id], catalog_db=database),
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )
    assert retried["archived"] == 3
    assert retried["failed"] == 0


def test_archive_approved_snapshot_rotation_keeps_recent_eight(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    archive_root = _archive_root(tmp_path / "library")
    snapshot_dir = archive_root / "_phoxif"
    snapshot_dir.mkdir()
    for day in range(1, 10):
        (snapshot_dir / f"catalog-202001{day:02d}.db").write_bytes(b"old")
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        _add_enriched(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:02 03:04:05",
        )

    result = execute_archive(
        plan_archive([batch_id], catalog_db=database),
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )

    assert result["snapshot_error"] is None
    snapshots = sorted(snapshot_dir.glob("catalog-*.db"), reverse=True)
    assert len(snapshots) == 8
    assert snapshots[-1].name == "catalog-20200103.db"


def test_archive_creates_a_distinct_snapshot_for_each_success(
    make_jpeg,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    archive_root = _archive_root(tmp_path / "library")
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    batch_ids: list[str] = []
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        for index in range(2):
            batch_id = catalog.start_batch("camera", "rescue")
            batch_ids.append(batch_id)
            source = make_jpeg(
                f"photo-{index}.jpg",
                exif={"ImageDescription": f"distinct-{index}"},
                directory=tmp_path / "source",
            )
            staging = staging_dir / source.name
            shutil.copy2(source, staging)
            _add_enriched(
                catalog,
                batch_id=batch_id,
                source=source,
                staging=staging,
                date_written=f"2024:01:02 03:04:0{index}",
            )

    results = [
        execute_archive(
            plan_archive([batch_id], catalog_db=database),
            catalog_db=database,
            archive_root=archive_root,
            approved=True,
        )
        for batch_id in batch_ids
    ]

    assert all(result["snapshot_error"] is None for result in results)
    assert results[0]["snapshot_path"] != results[1]["snapshot_path"]
    assert len(list((archive_root / "_phoxif").glob("catalog-*.db"))) == 2


def test_archive_sqlite_item_failure_does_not_block_later_group(
    monkeypatch,
    make_jpeg,
    tmp_path: Path,
) -> None:
    from phoxif.pipeline import archive

    database = tmp_path / "catalog.db"
    archive_root = _archive_root(tmp_path / "library")
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        for index in range(2):
            source = make_jpeg(
                f"photo-{index}.jpg",
                exif={"ImageDescription": f"distinct-{index}"},
                directory=tmp_path / "source",
            )
            staging = staging_dir / source.name
            shutil.copy2(source, staging)
            _add_enriched(
                catalog,
                batch_id=batch_id,
                source=source,
                staging=staging,
                date_written=f"2024:01:02 03:04:0{index}",
            )
    original_execute_group = archive._execute_group
    calls = 0

    def fail_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("injected item database error")
        return original_execute_group(*args, **kwargs)

    monkeypatch.setattr(archive, "_execute_group", fail_first)
    result = execute_archive(
        plan_archive([batch_id], catalog_db=database),
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )

    assert result["failed"] == 1
    assert result["archived"] == 1


def test_archive_failure_marker_error_does_not_block_later_group(
    monkeypatch,
    make_jpeg,
    tmp_path: Path,
) -> None:
    from phoxif.pipeline import archive

    database = tmp_path / "catalog.db"
    archive_root = _archive_root(tmp_path / "library")
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        for index in range(2):
            source = make_jpeg(
                f"photo-{index}.jpg",
                exif={"ImageDescription": f"distinct-{index}"},
                directory=tmp_path / "source",
            )
            staging = staging_dir / source.name
            shutil.copy2(source, staging)
            _add_enriched(
                catalog,
                batch_id=batch_id,
                source=source,
                staging=staging,
                date_written=f"2024:01:02 03:04:0{index}",
            )
    original_execute_group = archive._execute_group
    execute_calls = 0

    def fail_first_group(*args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        if execute_calls == 1:
            raise RuntimeError("injected archive failure")
        return original_execute_group(*args, **kwargs)

    monkeypatch.setattr(archive, "_execute_group", fail_first_group)
    monkeypatch.setattr(
        archive,
        "_mark_archive_failed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("marker failure")),
    )

    result = execute_archive(
        plan_archive([batch_id], catalog_db=database),
        catalog_db=database,
        archive_root=archive_root,
        approved=True,
    )

    assert result["failed"] == 1
    assert result["archived"] == 1


def test_catalog_snapshot_size_includes_committed_wal_pages(tmp_path: Path) -> None:
    database = tmp_path / "catalog.db"
    with Catalog(database):
        pass
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE wal_payload(value BLOB)")
        writer.execute("INSERT INTO wal_payload(value) VALUES (?)", (b"x" * 2_000_000,))
        writer.commit()

        assert (database.parent / f"{database.name}-wal").stat().st_size > 1_000_000
        assert _catalog_snapshot_size(database) > database.stat().st_size
    finally:
        writer.close()
