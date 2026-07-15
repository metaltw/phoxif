"""Tests for catalog-backed date enrichment and provenance."""

import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from phoxif.api.exif_writer import read_tags, write_tags
from phoxif.pipeline.catalog import Catalog, utc_now
from phoxif.pipeline.enrich import execute_dates, plan_dates
from phoxif.pipeline.ingest import run as ingest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog_photo(
    database: Path,
    source: Path,
    staging: Path,
    *,
    source_id: str = "messages",
) -> tuple[str, str]:
    digest = _sha256(source)
    with Catalog(database) as catalog:
        catalog.register_source(source_id, "Messages", "rescue")
        batch_id = catalog.start_batch(source_id, "rescue")
        catalog.upsert_file(
            sha256=digest,
            size=source.stat().st_size,
            ext=source.suffix,
            media_type="image",
            phash="0000000000000000",
            width=32,
            height=32,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id=source_id,
            batch_id=batch_id,
            original_path=source,
            original_name=source.name,
            original_mtime="2024-01-15T10:00:00+08:00",
            original_btime=None,
            staging_path=staging,
        )
        catalog.transition(digest, "unique")
    return batch_id, digest


def test_mmexport_date_is_written_only_to_working_copy_with_provenance(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("mmexport1705312245678.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    source_bytes = source.read_bytes()
    database = tmp_path / "catalog.db"
    batch_id, digest = _catalog_photo(database, source, staging)
    zone = ZoneInfo("Asia/Taipei")
    plan = plan_dates(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        earliest=datetime(1995, 1, 1, tzinfo=zone),
        now=datetime(2026, 7, 15, tzinfo=zone),
    )

    assert plan.items[0].action == "write-estimated"
    assert plan.items[0].evidence is not None
    assert plan.items[0].evidence.source == "filename-epoch"
    result = execute_dates(plan, catalog_db=database)

    assert result["failed"] == 0
    assert source.read_bytes() == source_bytes
    payload = json.loads(
        subprocess.run(
            ["exiftool", "-j", "-DateTimeOriginal", "-Keywords", "-Subject", str(staging)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )[0]
    assert "phoxif:date-estimated" in payload["Keywords"]
    assert "phoxif:date-src:filename-epoch" in payload["Subject"]
    with Catalog(database) as catalog:
        record = catalog.file(digest)
        assert record["status"] == "enriched"
        assert record["date_source"] == "filename-epoch"
        assert record["current_sha256"] == _sha256(staging)
        assert record["current_sha256"] != digest
        operation = catalog.connection.execute(
            "SELECT detail_json FROM operations WHERE op = 'write_date'"
        ).fetchone()
        detail = json.loads(operation["detail_json"])
        assert detail["status"] == "completed"
        assert detail["old_values"]["DateTimeOriginal"] is None


def test_suspicious_native_date_is_quarantined_without_overwrite(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg(
        "IMG_20240101_120000.jpg",
        exif={"DateTimeOriginal": "2000:01:01 00:00:00"},
        directory=tmp_path / "source",
    )
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    original = staging.read_bytes()
    database = tmp_path / "catalog.db"
    batch_id, digest = _catalog_photo(database, source, staging)
    zone = ZoneInfo("Asia/Taipei")

    plan = plan_dates(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        earliest=datetime(1995, 1, 1, tzinfo=zone),
        now=datetime(2026, 7, 15, tzinfo=zone),
    )
    result = execute_dates(plan, catalog_db=database)

    assert plan.items[0].action == "quarantine"
    assert plan.items[0].reason == "suspicious-native-date"
    assert result["failed"] == 0
    assert staging.read_bytes() == original
    with Catalog(database) as catalog:
        assert catalog.file(digest)["status"] == "quarantined"
        assert catalog.count("operations") == 0


def test_date_execute_is_idempotent_after_success(make_jpeg, tmp_path: Path) -> None:
    source = make_jpeg("IMG_20240203_040506.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    batch_id, _digest = _catalog_photo(database, source, staging)
    zone = ZoneInfo("Asia/Taipei")
    plan = plan_dates(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        earliest=datetime(1995, 1, 1, tzinfo=zone),
        now=datetime(2026, 7, 15, tzinfo=zone),
    )

    first = execute_dates(plan, catalog_db=database)
    first_bytes = staging.read_bytes()
    second = execute_dates(plan, catalog_db=database)

    assert first["failed"] == 0
    assert second["failed"] == 0
    assert staging.read_bytes() == first_bytes
    with Catalog(database) as catalog:
        assert catalog.count("operations") == 1
        detail = json.loads(
            catalog.connection.execute("SELECT detail_json FROM operations").fetchone()[0]
        )
        assert detail["old_values"]["DateTimeOriginal"] is None


def test_rerun_batch_keeps_existing_file_in_date_plan(make_jpeg, tmp_path: Path) -> None:
    source_root = tmp_path / "messages"
    photo = make_jpeg("unknown-date.jpg", directory=source_root)
    database = tmp_path / "catalog.db"
    staging = tmp_path / "staging"
    first = ingest(
        "messages",
        source_root,
        "rescue",
        catalog_db=database,
        staging_root=staging,
    )
    second = ingest(
        "messages",
        source_root,
        "rescue",
        catalog_db=database,
        staging_root=staging,
    )
    digest = _sha256(photo)
    with Catalog(database) as catalog:
        catalog.transition(digest, "unique")

    zone = ZoneInfo("Asia/Taipei")
    plan = plan_dates(
        second.batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        earliest=datetime(1995, 1, 1, tzinfo=zone),
        now=datetime(2026, 7, 15, tzinfo=zone),
    )

    assert first.batch_id != second.batch_id
    assert [item.sha256 for item in plan.items] == [digest]
    assert plan.items[0].action == "quarantine"


def test_date_execute_recovers_after_file_replace_without_losing_old_values(
    make_jpeg,
    tmp_path: Path,
) -> None:
    source = make_jpeg("IMG_20240203_040506.jpg", directory=tmp_path / "source")
    staging = tmp_path / "staging" / source.name
    staging.parent.mkdir()
    shutil.copy2(source, staging)
    database = tmp_path / "catalog.db"
    batch_id, digest = _catalog_photo(database, source, staging)
    zone = ZoneInfo("Asia/Taipei")
    plan = plan_dates(
        batch_id,
        catalog_db=database,
        timezone_name="Asia/Taipei",
        earliest=datetime(1995, 1, 1, tzinfo=zone),
        now=datetime(2026, 7, 15, tzinfo=zone),
    )
    item = plan.items[0]
    assert item.evidence is not None
    old_values = read_tags(
        staging,
        ["DateTimeOriginal", "CreateDate", "IPTC:Keywords", "XMP-dc:Subject"],
    )
    detail = {
        "status": "executing",
        "expected_value": item.evidence.exif_value,
        "source": item.evidence.source,
        "confidence": item.evidence.confidence,
        "keywords": item.evidence.keywords,
        "path": str(staging),
        "expected_current_sha256": digest,
        "old_values": old_values,
    }
    with Catalog(database) as catalog:
        with catalog.transaction():
            catalog.connection.execute(
                """
                INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
                VALUES (?, ?, 'write_date', ?, ?)
                """,
                (batch_id, digest, json.dumps(detail, sort_keys=True), utc_now()),
            )
    write_tags(
        staging,
        {
            "DateTimeOriginal": item.evidence.exif_value,
            "CreateDate": item.evidence.exif_value,
            "IPTC:Keywords": item.evidence.keywords,
            "XMP-dc:Subject": item.evidence.keywords,
        },
    )

    result = execute_dates(plan, catalog_db=database)

    assert result["failed"] == 0
    with Catalog(database) as catalog:
        record = catalog.file(digest)
        operation = catalog.connection.execute(
            "SELECT detail_json FROM operations WHERE op = 'write_date'"
        ).fetchone()
    completed = json.loads(operation["detail_json"])
    original = json.loads(record["date_original_value"])
    assert completed["status"] == "completed"
    assert completed["old_values"]["DateTimeOriginal"] is None
    assert original["DateTimeOriginal"] is None
