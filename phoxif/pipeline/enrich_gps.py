"""Catalog-backed conservative GPS enrichment with reversible provenance."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from phoxif.api.exif_writer import read_tags, write_tags
from phoxif.pipeline.catalog import Catalog, utc_now
from phoxif.pipeline.gps import GpsAnchor, GpsEvidence, infer_temporal_neighbor, mapped_evidence


@dataclass(frozen=True)
class GpsPlanItem:
    """One explainable GPS decision for a catalog identity."""

    batch_id: str
    sha256: str
    path: str | None
    name: str
    media_type: str
    action: str
    evidence: GpsEvidence | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.evidence is not None:
            payload["evidence"] = {
                **asdict(self.evidence),
                "keywords": self.evidence.keywords,
            }
        return payload


@dataclass(frozen=True)
class GpsPlan:
    """Dry-run GPS decisions for one ingest batch."""

    batch_id: str
    timezone_name: str
    items: list[GpsPlanItem]

    def to_dict(self) -> dict[str, Any]:
        actions = ("keep-native", "keep-backfilled", "write-mapped", "write-neighbor", "skip")
        return {
            "batch_id": self.batch_id,
            "timezone_name": self.timezone_name,
            "items": [item.to_dict() for item in self.items],
            "counts": {action: sum(item.action == action for item in self.items) for action in actions},
        }


@dataclass(frozen=True)
class _Context:
    row: Any
    path: Path | None
    tags: dict[str, Any]
    coordinates: tuple[float, float] | None
    keywords: list[str]
    captured_at: datetime | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _keywords(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _provenance_keywords(tags: dict[str, Any]) -> list[str]:
    """Return de-duplicated phoxif provenance from every supported tag family."""
    return list(
        dict.fromkeys(
            [
                *_keywords(tags.get("IPTC:Keywords")),
                *_keywords(tags.get("XMP-dc:Subject")),
            ]
        )
    )


def _working_path(row: Any) -> Path | None:
    text = row["staging_path"] if row["kind"] == "rescue" else (
        row["staging_path"] or row["original_path"]
    )
    if not text:
        return None
    path = Path(str(text))
    if not path.is_file() or path.is_symlink():
        return None
    return path


def _parse_video_coordinates(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, str):
        return None
    numbers = re.findall(r"[+-]?\d+(?:\.\d+)?", value)
    if len(numbers) < 2:
        return None
    latitude, longitude = float(numbers[0]), float(numbers[1])
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def _gps_tags(path: Path, media_type: str) -> tuple[tuple[float, float] | None, dict[str, Any]]:
    if media_type == "video":
        tags = read_tags(path, ["Keys:GPSCoordinates", "XMP-dc:Subject"], numeric=True)
        return _parse_video_coordinates(tags["Keys:GPSCoordinates"]), tags
    tags = read_tags(
        path,
        [
            "GPSLatitude",
            "GPSLongitude",
            "GPSLatitudeRef",
            "GPSLongitudeRef",
            "IPTC:Keywords",
            "XMP-dc:Subject",
        ],
        numeric=True,
    )
    latitude = tags["GPSLatitude"]
    longitude = tags["GPSLongitude"]
    if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
        return (float(latitude), float(longitude)), tags
    return None, tags


def _capture_time(row: Any, timezone_name: str) -> datetime | None:
    if row["date_source"] not in {"native-exif", "filename-date"}:
        return None
    if row["date_confidence"] is None or int(row["date_confidence"]) > 2:
        return None
    if not row["date_written"]:
        return None
    try:
        return datetime.strptime(str(row["date_written"]), "%Y:%m:%d %H:%M:%S").replace(
            tzinfo=ZoneInfo(timezone_name)
        )
    except ValueError:
        return None


def _folder_mapping(
    original_path: Path,
    mappings: dict[str, tuple[float, float]],
) -> GpsEvidence | None:
    parent_parts = original_path.parent.parts
    matches: list[tuple[int, str, tuple[float, float]]] = []
    for key, coordinates in mappings.items():
        key_parts = Path(key).parts
        if key_parts and len(key_parts) <= len(parent_parts):
            for start in range(len(parent_parts) - len(key_parts), -1, -1):
                if parent_parts[start : start + len(key_parts)] == key_parts:
                    matches.append((len(key_parts), key, coordinates))
                    break
    if not matches:
        return None
    _length, key, (latitude, longitude) = max(matches, key=lambda item: item[0])
    return mapped_evidence(key, float(latitude), float(longitude))


def plan_gps(
    batch_id: str,
    *,
    catalog_db: Path,
    timezone_name: str,
    mappings: dict[str, tuple[float, float]],
    max_minutes: int = 30,
) -> GpsPlan:
    """Build a zero-write GPS plan using only ADR-0005-approved evidence."""
    with Catalog(catalog_db) as catalog:
        batch = catalog.connection.execute(
            "SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise KeyError(f"Unknown batch: {batch_id}")
        rows = catalog.connection.execute(
            """
            SELECT files.*, sightings.source_id, sightings.original_path,
                   sightings.original_name, sightings.staging_path, sources.kind
            FROM batch_items
            JOIN sightings ON sightings.id = batch_items.sighting_id
            JOIN files ON files.sha256 = sightings.sha256
            JOIN sources ON sources.source_id = sightings.source_id
            WHERE batch_items.batch_id = ?
            ORDER BY sightings.id
            """,
            (batch_id,),
        ).fetchall()

    contexts: dict[str, _Context] = {}
    for row in rows:
        sha256 = str(row["sha256"])
        if sha256 in contexts:
            continue
        path = _working_path(row)
        tags: dict[str, Any] = {}
        coordinates = None
        if path is not None:
            coordinates, tags = _gps_tags(path, str(row["media_type"]))
        contexts[sha256] = _Context(
            row,
            path,
            tags,
            coordinates,
            _provenance_keywords(tags),
            _capture_time(row, timezone_name),
        )

    anchors_by_group: dict[tuple[str, str], list[GpsAnchor]] = {}
    for sha256, context in contexts.items():
        if (
            context.coordinates is None
            or context.captured_at is None
            or any(keyword.startswith("phoxif:gps-") for keyword in context.keywords)
        ):
            continue
        key = (
            str(context.row["source_id"]),
            str(Path(str(context.row["original_path"])).parent),
        )
        anchors_by_group.setdefault(key, []).append(
            GpsAnchor(sha256, context.captured_at, *context.coordinates)
        )

    items: list[GpsPlanItem] = []
    for sha256, context in contexts.items():
        row = context.row
        name = str(row["original_name"])
        media_type = str(row["media_type"])
        if row["status"] not in {"enriched", "quarantined"}:
            items.append(
                GpsPlanItem(batch_id, sha256, None, name, media_type, "skip", None, f"status-{row['status']}")
            )
            continue
        if context.path is None:
            items.append(
                GpsPlanItem(batch_id, sha256, None, name, media_type, "skip", None, "missing-safe-working-copy")
            )
            continue
        if context.coordinates is not None:
            action = "keep-backfilled" if any(
                keyword.startswith("phoxif:gps-") for keyword in context.keywords
            ) else "keep-native"
            items.append(
                GpsPlanItem(batch_id, sha256, str(context.path), name, media_type, action, None, "gps-present")
            )
            continue
        evidence = _folder_mapping(Path(str(row["original_path"])), mappings)
        action = "write-mapped"
        reason = "user-confirmed-folder-mapping"
        if evidence is None and row["status"] == "quarantined":
            action = "skip"
            reason = "date-quarantined"
        elif evidence is None and context.captured_at is not None:
            key = (str(row["source_id"]), str(Path(str(row["original_path"])).parent))
            evidence = infer_temporal_neighbor(
                context.captured_at,
                anchors_by_group.get(key, []),
                max_minutes=max_minutes,
            )
            action = "write-neighbor"
            reason = "native-gps-temporal-neighbor"
        if evidence is None:
            action = "skip"
            reason = (
                "date-not-trustworthy-for-gps"
                if context.captured_at is None
                else "no-consistent-native-gps-neighbor"
            )
        items.append(
            GpsPlanItem(
                batch_id,
                sha256,
                str(context.path),
                name,
                media_type,
                action,
                evidence,
                reason,
            )
        )
    return GpsPlan(batch_id, timezone_name, items)


def _write_operation(
    catalog: Catalog,
    item: GpsPlanItem,
    detail: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    existing = catalog.connection.execute(
        """
        SELECT id, detail_json FROM operations
        WHERE batch_id = ? AND sha256 = ? AND op = 'write_gps'
          AND json_extract(detail_json, '$.expected_latitude') = ?
          AND json_extract(detail_json, '$.expected_longitude') = ?
        ORDER BY id DESC LIMIT 1
        """,
        (
            item.batch_id,
            item.sha256,
            detail["expected_latitude"],
            detail["expected_longitude"],
        ),
    ).fetchone()
    if existing is not None:
        return int(existing["id"]), json.loads(existing["detail_json"])
    with catalog.transaction():
        cursor = catalog.connection.execute(
            """
            INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
            VALUES (?, ?, 'write_gps', ?, ?)
            """,
            (item.batch_id, item.sha256, json.dumps(detail, sort_keys=True), utc_now()),
        )
    return int(cursor.lastrowid), detail


def _coordinates_match(
    actual: tuple[float, float] | None,
    expected: GpsEvidence,
) -> bool:
    return actual is not None and abs(actual[0] - expected.latitude) < 1e-6 and abs(
        actual[1] - expected.longitude
    ) < 1e-6


def _recover_backfilled_gps(
    catalog: Catalog,
    item: GpsPlanItem,
    path: Path,
    record: Any,
    coordinates: tuple[float, float],
    tags: dict[str, Any],
    current_hash: str,
) -> bool:
    """Finish catalog commit after media replacement survived an interrupted run."""
    operation = catalog.connection.execute(
        """
        SELECT id, detail_json FROM operations
        WHERE batch_id = ? AND sha256 = ? AND op = 'write_gps'
        ORDER BY id DESC LIMIT 1
        """,
        (item.batch_id, item.sha256),
    ).fetchone()
    if operation is None:
        return False
    detail = json.loads(operation["detail_json"])
    try:
        expected = (
            float(detail["expected_latitude"]),
            float(detail["expected_longitude"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    source = str(detail.get("source", ""))
    source_marker = {
        "folder-mapping": "phoxif:gps-user-confirmed",
        "temporal-neighbor": "phoxif:gps-estimated",
    }.get(source)
    if source_marker is None:
        return False
    required_keywords = {
        "phoxif:gps-backfilled",
        f"phoxif:gps-src:{source}",
        source_marker,
    }
    ledger_keywords = {str(value) for value in detail.get("keywords", [])}
    old_values = detail.get("old_values")
    required_old_keys = (
        {"Keys:GPSCoordinates", "XMP-dc:Subject"}
        if item.media_type == "video"
        else {
            "GPSLatitude",
            "GPSLongitude",
            "GPSLatitudeRef",
            "GPSLongitudeRef",
            "IPTC:Keywords",
            "XMP-dc:Subject",
        }
    )
    if (
        detail.get("expected_current_sha256") != record["current_sha256"]
        or abs(coordinates[0] - expected[0]) >= 1e-6
        or abs(coordinates[1] - expected[1]) >= 1e-6
        or not required_keywords.issubset(ledger_keywords)
        or not required_keywords.issubset(_provenance_keywords(tags))
        or not isinstance(old_values, dict)
        or not required_old_keys.issubset(old_values)
    ):
        return False
    detail["status"] = "completed"
    detail["current_sha256"] = current_hash
    detail.pop("error", None)
    with catalog.transaction():
        catalog.connection.execute(
            """
            UPDATE files SET gps_written = ?, gps_source = ?, gps_original_value = ?,
                             current_sha256 = ?, current_size = ?, updated_at = ?
            WHERE sha256 = ?
            """,
            (
                json.dumps(
                    {"latitude": coordinates[0], "longitude": coordinates[1]},
                    sort_keys=True,
                ),
                source,
                json.dumps(old_values, sort_keys=True),
                current_hash,
                path.stat().st_size,
                utc_now(),
                item.sha256,
            ),
        )
        catalog.connection.execute(
            "UPDATE operations SET detail_json = ?, executed_at = ? WHERE id = ?",
            (json.dumps(detail, sort_keys=True), utc_now(), int(operation["id"])),
        )
    return True


def _execute_gps_unisolated(
    plan: GpsPlan,
    *,
    catalog_db: Path,
    folder_name_as_tag: bool = False,
) -> dict[str, Any]:
    """Apply a server-built GPS plan to safe working files with an audit ledger."""
    results: list[dict[str, Any]] = []
    for item in plan.items:
        if item.action == "skip":
            results.append({"sha256": item.sha256, "status": "skipped", "reason": item.reason})
            continue
        with Catalog(catalog_db) as catalog:
            record = catalog.file(item.sha256)
            if record is None:
                results.append({"sha256": item.sha256, "status": "failed", "error": "Unknown file"})
                continue
            if item.action in {"keep-native", "keep-backfilled"}:
                if item.path is None:
                    results.append(
                        {"sha256": item.sha256, "status": "failed", "error": "Missing working copy"}
                    )
                    continue
                path = Path(item.path)
                coordinates, tags = _gps_tags(path, item.media_type)
                current_hash = _sha256(path)
                recovered = (
                    item.action == "keep-backfilled"
                    and coordinates is not None
                    and current_hash != record["current_sha256"]
                    and _recover_backfilled_gps(
                        catalog,
                        item,
                        path,
                        record,
                        coordinates,
                        tags,
                        current_hash,
                    )
                )
                if coordinates is None or (
                    current_hash != record["current_sha256"] and not recovered
                ):
                    results.append(
                        {
                            "sha256": item.sha256,
                            "status": "failed",
                            "error": "Working copy changed since GPS plan",
                        }
                    )
                    continue
                if item.action == "keep-native":
                    with catalog.transaction():
                        catalog.connection.execute(
                            """
                            UPDATE files SET gps_written = ?, gps_source = 'native-gps',
                                             updated_at = ? WHERE sha256 = ?
                            """,
                            (
                                json.dumps(
                                    {
                                        "latitude": coordinates[0],
                                        "longitude": coordinates[1],
                                    },
                                    sort_keys=True,
                                ),
                                utc_now(),
                                item.sha256,
                            ),
                        )
                results.append(
                    {
                        "sha256": item.sha256,
                        "status": "enriched" if recovered else "kept",
                        "written": False,
                        "recovered": recovered,
                    }
                )
                continue
            assert item.evidence is not None and item.path is not None
            path = Path(item.path)
            actual, existing_tags = _gps_tags(path, item.media_type)
            current_hash = _sha256(path)
            expected_keywords = item.evidence.keywords
            existing_subject = _keywords(existing_tags.get("XMP-dc:Subject"))
            already_written = _coordinates_match(actual, item.evidence) and set(
                expected_keywords
            ).issubset(existing_subject)
            detail = {
                "status": "executing",
                "expected_latitude": item.evidence.latitude,
                "expected_longitude": item.evidence.longitude,
                "source": item.evidence.source,
                "keywords": expected_keywords,
                "reference_sha256": item.evidence.reference_sha256,
                "offset_seconds": item.evidence.offset_seconds,
                "folder_key": item.evidence.folder_key,
                "path": str(path),
                "expected_current_sha256": record["current_sha256"],
                "old_values": existing_tags,
            }
            operation_id, operation_detail = _write_operation(catalog, item, detail)
            if (
                operation_detail.get("status") == "completed"
                and operation_detail.get("current_sha256") == current_hash
                and already_written
            ):
                results.append({"sha256": item.sha256, "status": "enriched", "written": False})
                continue
            if current_hash != record["current_sha256"] and not already_written:
                detail["status"] = "failed"
                detail["error"] = "Working copy changed since GPS plan"
                with catalog.transaction():
                    catalog.connection.execute(
                        "UPDATE operations SET detail_json = ?, executed_at = ? WHERE id = ?",
                        (json.dumps(detail, sort_keys=True), utc_now(), operation_id),
                    )
                results.append({"sha256": item.sha256, "status": "failed", "error": detail["error"]})
                continue

            old_values: dict[str, Any] = operation_detail.get("old_values", existing_tags)
            if not already_written:
                merged_subject = list(dict.fromkeys([*existing_subject, *expected_keywords]))
                if folder_name_as_tag and item.evidence.folder_key:
                    merged_subject.append(item.evidence.folder_key)
                if item.media_type == "video":
                    coordinate_text = (
                        f"{item.evidence.latitude:+.8f}{item.evidence.longitude:+.8f}/"
                    )
                    old_values = write_tags(
                        path,
                        {
                            "Keys:GPSCoordinates": coordinate_text,
                            "XMP-dc:Subject": list(dict.fromkeys(merged_subject)),
                        },
                        numeric=True,
                    )
                else:
                    existing_iptc = _keywords(existing_tags.get("IPTC:Keywords"))
                    merged_iptc = list(dict.fromkeys([*existing_iptc, *expected_keywords]))
                    if folder_name_as_tag and item.evidence.folder_key:
                        merged_iptc.append(item.evidence.folder_key)
                    old_values = write_tags(
                        path,
                        {
                            "GPSLatitude": item.evidence.latitude,
                            "GPSLongitude": item.evidence.longitude,
                            "GPSLatitudeRef": "N" if item.evidence.latitude >= 0 else "S",
                            "GPSLongitudeRef": "E" if item.evidence.longitude >= 0 else "W",
                            "IPTC:Keywords": list(dict.fromkeys(merged_iptc)),
                            "XMP-dc:Subject": list(dict.fromkeys(merged_subject)),
                        },
                        numeric=True,
                    )
            new_coordinates, _new_tags = _gps_tags(path, item.media_type)
            if not _coordinates_match(new_coordinates, item.evidence):
                raise RuntimeError("GPS read-back did not match approved coordinates")
            new_hash = _sha256(path)
            detail.update(
                {
                    "status": "completed",
                    "old_values": old_values,
                    "current_sha256": new_hash,
                }
            )
            with catalog.transaction():
                catalog.connection.execute(
                    """
                    UPDATE files SET gps_written = ?, gps_source = ?, gps_original_value = ?,
                                     current_sha256 = ?, current_size = ?, updated_at = ?
                    WHERE sha256 = ?
                    """,
                    (
                        json.dumps(
                            {
                                "latitude": item.evidence.latitude,
                                "longitude": item.evidence.longitude,
                            },
                            sort_keys=True,
                        ),
                        item.evidence.source,
                        json.dumps(old_values, sort_keys=True),
                        new_hash,
                        path.stat().st_size,
                        utc_now(),
                        item.sha256,
                    ),
                )
                catalog.connection.execute(
                    "UPDATE operations SET detail_json = ?, executed_at = ? WHERE id = ?",
                    (json.dumps(detail, sort_keys=True), utc_now(), operation_id),
                )
            results.append({"sha256": item.sha256, "status": "enriched", "written": True})
    return {
        "batch_id": plan.batch_id,
        "results": results,
        "completed": sum(result["status"] in {"enriched", "kept", "skipped"} for result in results),
        "failed": sum(result["status"] == "failed" for result in results),
    }


def _mark_gps_operation_failed(
    catalog_db: Path,
    item: GpsPlanItem,
    error: str,
) -> None:
    """Preserve an interrupted operation's old values while recording its failure."""
    with Catalog(catalog_db) as catalog:
        operation = catalog.connection.execute(
            """
            SELECT id, detail_json FROM operations
            WHERE batch_id = ? AND sha256 = ? AND op = 'write_gps'
            ORDER BY id DESC LIMIT 1
            """,
            (item.batch_id, item.sha256),
        ).fetchone()
        if operation is None:
            return
        detail = json.loads(operation["detail_json"])
        detail["status"] = "failed"
        detail["error"] = error
        with catalog.transaction():
            catalog.connection.execute(
                "UPDATE operations SET detail_json = ?, executed_at = ? WHERE id = ?",
                (json.dumps(detail, sort_keys=True), utc_now(), int(operation["id"])),
            )


def execute_gps(
    plan: GpsPlan,
    *,
    catalog_db: Path,
    folder_name_as_tag: bool = False,
) -> dict[str, Any]:
    """Apply GPS decisions independently so one broken file cannot abort a batch."""
    results: list[dict[str, Any]] = []
    for item in plan.items:
        item_plan = GpsPlan(plan.batch_id, plan.timezone_name, [item])
        try:
            item_result = _execute_gps_unisolated(
                item_plan,
                catalog_db=catalog_db,
                folder_name_as_tag=folder_name_as_tag,
            )
        except (OSError, RuntimeError, ValueError) as error:
            message = str(error)
            _mark_gps_operation_failed(catalog_db, item, message)
            results.append(
                {"sha256": item.sha256, "status": "failed", "error": message}
            )
            continue
        results.extend(item_result["results"])
    return {
        "batch_id": plan.batch_id,
        "results": results,
        "completed": sum(
            result["status"] in {"enriched", "kept", "skipped"}
            for result in results
        ),
        "failed": sum(result["status"] == "failed" for result in results),
    }
