"""Integration tests for catalog-backed GPS enrichment."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from phoxif.api.exif_writer import read_tags, write_tags
from phoxif.pipeline.catalog import Catalog, utc_now
from phoxif.pipeline.enrich_gps import execute_gps, plan_gps


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _add_photo(
    catalog: Catalog,
    *,
    batch_id: str,
    source: Path,
    staging: Path,
    date_written: str | None,
    date_source: str | None,
    date_confidence: int | None,
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
            """
            UPDATE files SET date_written = ?, date_source = ?, date_confidence = ?
            WHERE sha256 = ?
            """,
            (date_written, date_source, date_confidence, digest),
        )
    return digest


def test_temporal_gps_writes_only_rescue_staging_with_provenance(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source" / "roll"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    first = make_jpeg(
        "IMG_20240101_100000.jpg",
        exif={
            "DateTimeOriginal": "2024:01:01 10:00:00",
            "GPSLatitude": "20.0000",
            "GPSLongitude": "30.0000",
            "GPSLatitudeRef": "N",
            "GPSLongitudeRef": "E",
        },
        directory=source_dir,
    )
    target = make_jpeg("IMG_20240101_101000.jpg", directory=source_dir)
    second = make_jpeg(
        "IMG_20240101_102000.jpg",
        exif={
            "DateTimeOriginal": "2024:01:01 10:20:00",
            "GPSLatitude": "20.0020",
            "GPSLongitude": "30.0020",
            "GPSLatitudeRef": "N",
            "GPSLongitudeRef": "E",
        },
        directory=source_dir,
    )
    copies = {}
    for source in (first, target, second):
        copies[source] = staging_dir / source.name
        shutil.copy2(source, copies[source])
    target_original = target.read_bytes()
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        first_sha = _add_photo(
            catalog,
            batch_id=batch_id,
            source=first,
            staging=copies[first],
            date_written="2024:01:01 10:00:00",
            date_source="native-exif",
            date_confidence=1,
        )
        target_sha = _add_photo(
            catalog,
            batch_id=batch_id,
            source=target,
            staging=copies[target],
            date_written="2024:01:01 10:10:00",
            date_source="filename-date",
            date_confidence=2,
        )
        second_sha = _add_photo(
            catalog,
            batch_id=batch_id,
            source=second,
            staging=copies[second],
            date_written="2024:01:01 10:20:00",
            date_source="native-exif",
            date_confidence=1,
        )

    plan = plan_gps(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        mappings={},
    )
    target_item = next(item for item in plan.items if item.sha256 == target_sha)
    assert target_item.action == "write-neighbor"
    assert target_item.evidence is not None
    assert target_item.evidence.reference_sha256 == (first_sha, second_sha)

    result = execute_gps(plan, catalog_db=database)

    assert result["failed"] == 0
    assert target.read_bytes() == target_original
    tags = read_tags(
        copies[target],
        ["GPSLatitude", "GPSLongitude", "IPTC:Keywords", "XMP-dc:Subject"],
        numeric=True,
    )
    assert tags["GPSLatitude"] == 20.001
    assert tags["GPSLongitude"] == 30.001
    assert "phoxif:gps-estimated" in tags["IPTC:Keywords"]
    assert "phoxif:gps-src:temporal-neighbor" in tags["XMP-dc:Subject"]
    with Catalog(database) as catalog:
        record = catalog.file(target_sha)
        operation = catalog.connection.execute(
            "SELECT detail_json FROM operations WHERE op = 'write_gps'"
        ).fetchone()
    detail = json.loads(operation["detail_json"])
    assert record["gps_source"] == "temporal-neighbor"
    assert record["current_sha256"] == _sha256(copies[target])
    assert detail["reference_sha256"] == [first_sha, second_sha]


def test_folder_mapping_overrides_untrusted_message_date(make_jpeg, tmp_path: Path) -> None:
    source = make_jpeg("mmexport1705312245678.jpg", directory=tmp_path / "source" / "confirmed")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        digest = _add_photo(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:15 17:50:45",
            date_source="filename-epoch",
            date_confidence=3,
        )
        catalog.transition(digest, "quarantined")

    plan = plan_gps(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        mappings={"confirmed": (20.5, -30.25)},
    )

    assert plan.items[0].sha256 == digest
    assert plan.items[0].action == "write-mapped"
    assert plan.items[0].evidence is not None
    assert "phoxif:gps-user-confirmed" in plan.items[0].evidence.keywords


def test_message_epoch_never_uses_temporal_gps_neighbor(make_jpeg, tmp_path: Path) -> None:
    source_dir = tmp_path / "source" / "roll"
    anchor = make_jpeg(
        "IMG_20240101_100000.jpg",
        exif={
            "GPSLatitude": "20.0",
            "GPSLongitude": "30.0",
            "GPSLatitudeRef": "N",
            "GPSLongitudeRef": "E",
        },
        directory=source_dir,
    )
    target = make_jpeg("mmexport1704089100000.jpg", directory=source_dir)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        for source, date_source, confidence in (
            (anchor, "native-exif", 1),
            (target, "filename-epoch", 3),
        ):
            staging = staging_dir / source.name
            shutil.copy2(source, staging)
            _add_photo(
                catalog,
                batch_id=batch_id,
                source=source,
                staging=staging,
                date_written="2024:01:01 10:05:00",
                date_source=date_source,
                date_confidence=confidence,
            )

    plan = plan_gps(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        mappings={},
    )
    target_item = next(item for item in plan.items if item.name.startswith("mmexport"))

    assert target_item.action == "skip"
    assert target_item.reason == "date-not-trustworthy-for-gps"


def test_gps_execute_recovers_after_file_replace_without_losing_old_values(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source" / "confirmed")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        digest = _add_photo(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:01 10:00:00",
            date_source="native-exif",
            date_confidence=1,
        )
    plan = plan_gps(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        mappings={"confirmed": (20.5, -30.25)},
    )
    item = plan.items[0]
    assert item.evidence is not None
    tag_names = [
        "GPSLatitude",
        "GPSLongitude",
        "GPSLatitudeRef",
        "GPSLongitudeRef",
        "IPTC:Keywords",
        "XMP-dc:Subject",
    ]
    old_values = read_tags(staging, tag_names, numeric=True)
    detail = {
        "status": "executing",
        "expected_latitude": item.evidence.latitude,
        "expected_longitude": item.evidence.longitude,
        "source": item.evidence.source,
        "keywords": item.evidence.keywords,
        "reference_sha256": item.evidence.reference_sha256,
        "offset_seconds": item.evidence.offset_seconds,
        "folder_key": item.evidence.folder_key,
        "path": str(staging),
        "expected_current_sha256": digest,
        "old_values": old_values,
    }
    with Catalog(database) as catalog:
        with catalog.transaction():
            catalog.connection.execute(
                """
                INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
                VALUES (?, ?, 'write_gps', ?, ?)
                """,
                (batch_id, digest, json.dumps(detail, sort_keys=True), utc_now()),
            )
    write_tags(
        staging,
        {
            "GPSLatitude": 20.5,
            "GPSLongitude": -30.25,
            "GPSLatitudeRef": "N",
            "GPSLongitudeRef": "W",
            "IPTC:Keywords": item.evidence.keywords,
            "XMP-dc:Subject": item.evidence.keywords,
        },
        numeric=True,
    )

    result = execute_gps(plan, catalog_db=database)

    assert result["failed"] == 0
    with Catalog(database) as catalog:
        record = catalog.file(digest)
        operation = catalog.connection.execute(
            "SELECT detail_json FROM operations WHERE op = 'write_gps'"
        ).fetchone()
    completed = json.loads(operation["detail_json"])
    original = json.loads(record["gps_original_value"])
    assert completed["status"] == "completed"
    assert completed["old_values"]["GPSLatitude"] is None
    assert original["GPSLatitude"] is None


def test_gps_execute_recovers_after_restart_replans_backfilled_file(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("photo.jpg", directory=tmp_path / "source" / "confirmed")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        digest = _add_photo(
            catalog,
            batch_id=batch_id,
            source=source,
            staging=staging,
            date_written="2024:01:01 10:00:00",
            date_source="native-exif",
            date_confidence=1,
        )
    original_plan = plan_gps(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        mappings={"confirmed": (20.5, -30.25)},
    )
    item = original_plan.items[0]
    assert item.evidence is not None
    tag_names = [
        "GPSLatitude",
        "GPSLongitude",
        "GPSLatitudeRef",
        "GPSLongitudeRef",
        "IPTC:Keywords",
        "XMP-dc:Subject",
    ]
    old_values = read_tags(staging, tag_names, numeric=True)
    detail = {
        "status": "executing",
        "expected_latitude": item.evidence.latitude,
        "expected_longitude": item.evidence.longitude,
        "source": item.evidence.source,
        "keywords": item.evidence.keywords,
        "reference_sha256": item.evidence.reference_sha256,
        "offset_seconds": item.evidence.offset_seconds,
        "folder_key": item.evidence.folder_key,
        "path": str(staging),
        "expected_current_sha256": digest,
        "old_values": old_values,
    }
    with Catalog(database) as catalog:
        with catalog.transaction():
            catalog.connection.execute(
                """
                INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
                VALUES (?, ?, 'write_gps', ?, ?)
                """,
                (batch_id, digest, json.dumps(detail, sort_keys=True), utc_now()),
            )
    write_tags(
        staging,
        {
            "GPSLatitude": 20.5,
            "GPSLongitude": -30.25,
            "GPSLatitudeRef": "N",
            "GPSLongitudeRef": "W",
            "IPTC:Keywords": item.evidence.keywords,
            "XMP-dc:Subject": item.evidence.keywords,
        },
        numeric=True,
    )

    restarted_plan = plan_gps(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        mappings={"confirmed": (20.5, -30.25)},
    )
    assert restarted_plan.items[0].action == "keep-backfilled"

    with Catalog(database) as catalog:
        operation = catalog.connection.execute(
            "SELECT id, detail_json FROM operations WHERE op = 'write_gps'"
        ).fetchone()
        incomplete_detail = json.loads(operation["detail_json"])
        incomplete_detail["old_values"] = {}
        with catalog.transaction():
            catalog.connection.execute(
                "UPDATE operations SET detail_json = ? WHERE id = ?",
                (json.dumps(incomplete_detail, sort_keys=True), int(operation["id"])),
            )
    rejected = execute_gps(restarted_plan, catalog_db=database)
    assert rejected["failed"] == 1

    incomplete_keywords = [
        "phoxif:gps-backfilled",
        "phoxif:gps-src:folder-mapping",
    ]
    complete_detail = {**detail, "keywords": incomplete_keywords}
    with Catalog(database) as catalog:
        with catalog.transaction():
            catalog.connection.execute(
                "UPDATE operations SET detail_json = ? WHERE id = ?",
                (json.dumps(complete_detail, sort_keys=True), int(operation["id"])),
            )
    provenance_rejected = execute_gps(restarted_plan, catalog_db=database)
    assert provenance_rejected["failed"] == 1

    with Catalog(database) as catalog:
        with catalog.transaction():
            catalog.connection.execute(
                "UPDATE operations SET detail_json = ? WHERE id = ?",
                (json.dumps(detail, sort_keys=True), int(operation["id"])),
            )
    result = execute_gps(restarted_plan, catalog_db=database)

    assert result["failed"] == 0
    assert result["results"][0]["recovered"] is True
    with Catalog(database) as catalog:
        record = catalog.file(digest)
        operation = catalog.connection.execute(
            "SELECT detail_json FROM operations WHERE op = 'write_gps'"
        ).fetchone()
    completed = json.loads(operation["detail_json"])
    assert record["current_sha256"] == _sha256(staging)
    assert record["gps_source"] == "folder-mapping"
    assert completed["status"] == "completed"
    assert completed["old_values"]["GPSLatitude"] is None


def test_iptc_only_phoxif_gps_is_not_used_as_native_anchor(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source" / "roll"
    anchor = make_jpeg(
        "IMG_20240101_100000.jpg",
        exif={
            "GPSLatitude": "20.0",
            "GPSLongitude": "30.0",
            "GPSLatitudeRef": "N",
            "GPSLongitudeRef": "E",
            "IPTC:Keywords": "phoxif:gps-estimated",
        },
        directory=source_dir,
    )
    target = make_jpeg("IMG_20240101_100500.jpg", directory=source_dir)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        for source in (anchor, target):
            staging = staging_dir / source.name
            shutil.copy2(source, staging)
            _add_photo(
                catalog,
                batch_id=batch_id,
                source=source,
                staging=staging,
                date_written="2024:01:01 10:05:00",
                date_source="native-exif",
                date_confidence=1,
            )

    plan = plan_gps(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        mappings={},
    )
    actions = {item.name: item.action for item in plan.items}

    assert actions[anchor.name] == "keep-backfilled"
    assert actions[target.name] == "skip"


def test_gps_execute_continues_after_one_file_write_failure(
    make_jpeg,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source" / "confirmed"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    database = tmp_path / "catalog.db"
    sources = [
        make_jpeg(
            f"photo-{index}.jpg",
            exif={"ImageDescription": f"distinct-{index}"},
            directory=source_dir,
        )
        for index in range(2)
    ]
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        for source in sources:
            staging = staging_dir / source.name
            shutil.copy2(source, staging)
            _add_photo(
                catalog,
                batch_id=batch_id,
                source=source,
                staging=staging,
                date_written="2024:01:01 10:00:00",
                date_source="native-exif",
                date_confidence=1,
            )
    plan = plan_gps(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        mappings={"confirmed": (20.5, -30.25)},
    )
    real_write_tags = write_tags
    calls = 0

    def fail_first_write(path, tags, *, numeric=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic write failure")
        return real_write_tags(path, tags, numeric=numeric)

    monkeypatch.setattr("phoxif.pipeline.enrich_gps.write_tags", fail_first_write)

    result = execute_gps(plan, catalog_db=database)

    assert calls == 2
    assert result["failed"] == 1
    assert result["completed"] == 1
    assert [item["status"] for item in result["results"]] == ["failed", "enriched"]
    with Catalog(database) as catalog:
        failed_operation = catalog.connection.execute(
            "SELECT detail_json FROM operations ORDER BY id LIMIT 1"
        ).fetchone()
    assert json.loads(failed_operation["detail_json"])["status"] == "failed"
