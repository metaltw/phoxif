"""Catalog-backed metadata enrichment with reversible provenance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from phoxif.api.exif_writer import read_tags, write_tags
from phoxif.pipeline.catalog import Catalog, utc_now
from phoxif.pipeline.dates import (
    DateEvidence,
    interpolate,
    parse_filename,
    parse_folder,
    parse_mtime,
    parse_native,
)


@dataclass(frozen=True)
class DatePlanItem:
    """One explainable date decision for a catalog identity."""

    batch_id: str
    sha256: str
    path: str | None
    name: str
    media_type: str
    action: str
    evidence: DateEvidence | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.evidence is not None:
            payload["evidence"] = {
                **asdict(self.evidence),
                "value": self.evidence.value.isoformat(),
                "exif_value": self.evidence.exif_value,
                "keywords": self.evidence.keywords,
            }
        return payload


@dataclass(frozen=True)
class DatePlan:
    """Dry-run date decisions for one ingest batch."""

    batch_id: str
    timezone_name: str
    items: list[DatePlanItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "timezone_name": self.timezone_name,
            "items": [item.to_dict() for item in self.items],
            "counts": {
                action: sum(item.action == action for item in self.items)
                for action in ("keep-native", "write-estimated", "quarantine", "skip")
            },
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _as_keywords(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _date_tags(
    path: Path,
    media_type: str,
    timezone_name: str,
) -> tuple[str | None, dict[str, Any]]:
    if media_type == "video":
        tags = read_tags(
            path,
            ["QuickTime:CreateDate", "XMP-dc:Subject"],
            quicktime_utc=True,
            timezone_name=timezone_name,
        )
        return tags["QuickTime:CreateDate"], tags
    tags = read_tags(
        path,
        ["DateTimeOriginal", "CreateDate", "IPTC:Keywords", "XMP-dc:Subject"],
    )
    return tags["DateTimeOriginal"] or tags["CreateDate"], tags


def _mtime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def plan_dates(
    batch_id: str,
    *,
    catalog_db: Path,
    timezone_name: str,
    earliest: datetime,
    now: datetime,
    allow_mtime: bool = False,
) -> DatePlan:
    """Build a zero-write date plan in confidence-ladder order."""
    zone = ZoneInfo(timezone_name)
    earliest = earliest.astimezone(zone)
    now = now.astimezone(zone)
    with Catalog(catalog_db) as catalog:
        batch = catalog.connection.execute(
            "SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise KeyError(f"Unknown batch: {batch_id}")
        rows = catalog.connection.execute(
            """
            SELECT files.*, sightings.source_id, sightings.original_path,
                   sightings.original_name, sightings.original_mtime,
                   sightings.staging_path, sources.kind
            FROM batch_items
            JOIN sightings ON sightings.id = batch_items.sighting_id
            JOIN files ON files.sha256 = sightings.sha256
            JOIN sources ON sources.source_id = sightings.source_id
            WHERE batch_items.batch_id = ?
            ORDER BY sightings.id
            """,
            (batch_id,),
        ).fetchall()

    preliminary: dict[str, DatePlanItem] = {}
    row_by_sha = {str(row["sha256"]): row for row in rows}
    anchor_groups: dict[tuple[str, str], list[tuple[datetime, DateEvidence]]] = {}
    for row in rows:
        sha256 = str(row["sha256"])
        if row["status"] not in {"unique", "quarantined"}:
            preliminary[sha256] = DatePlanItem(
                batch_id,
                sha256,
                None,
                str(row["original_name"]),
                str(row["media_type"]),
                "skip",
                None,
                f"status-{row['status']}",
            )
            continue
        working_text = (
            row["staging_path"]
            if row["kind"] == "rescue"
            else (row["staging_path"] or row["original_path"])
        )
        working_path = Path(str(working_text)) if working_text else None
        if working_path is None or not working_path.is_file() or working_path.is_symlink():
            preliminary[sha256] = DatePlanItem(
                batch_id,
                sha256,
                str(working_path) if working_path else None,
                str(row["original_name"]),
                str(row["media_type"]),
                "quarantine",
                None,
                "missing-safe-working-copy",
            )
            continue
        native_value, _tags = _date_tags(
            working_path,
            str(row["media_type"]),
            timezone_name,
        )
        native = parse_native(
            native_value,
            timezone_name=timezone_name,
            earliest=earliest,
            now=now,
        )
        if native_value and native is None:
            preliminary[sha256] = DatePlanItem(
                batch_id,
                sha256,
                str(working_path),
                str(row["original_name"]),
                str(row["media_type"]),
                "quarantine",
                None,
                "suspicious-native-date",
            )
            continue
        evidence = native or parse_filename(
            str(row["original_name"]),
            timezone_name=timezone_name,
            earliest=earliest,
            now=now,
        )
        if evidence is not None:
            action = "keep-native" if not evidence.estimated else "write-estimated"
            preliminary[sha256] = DatePlanItem(
                batch_id,
                sha256,
                str(working_path),
                str(row["original_name"]),
                str(row["media_type"]),
                action,
                evidence,
                f"confidence-{evidence.confidence}",
            )
            mtime = _mtime(row["original_mtime"])
            if mtime is not None and evidence.confidence <= 2:
                key = (str(row["source_id"]), str(Path(row["original_path"]).parent))
                anchor_groups.setdefault(key, []).append((mtime, evidence))

    for sha256, row in row_by_sha.items():
        if sha256 in preliminary:
            continue
        working_text = row["staging_path"] or row["original_path"]
        working_path = Path(str(working_text))
        target_mtime = _mtime(row["original_mtime"])
        evidence = None
        if target_mtime is not None:
            key = (str(row["source_id"]), str(Path(row["original_path"]).parent))
            anchors = sorted(anchor_groups.get(key, []), key=lambda item: item[0])
            before = [anchor for anchor in anchors if anchor[0] <= target_mtime]
            after = [anchor for anchor in anchors if anchor[0] >= target_mtime]
            if before and after and before[-1] != after[0]:
                evidence = interpolate(before[-1], target_mtime, after[0])
        evidence = evidence or parse_folder(
            Path(str(row["original_path"])),
            timezone_name=timezone_name,
            earliest=earliest,
            now=now,
        )
        if evidence is None and allow_mtime:
            evidence = parse_mtime(
                row["original_mtime"],
                timezone_name=timezone_name,
                earliest=earliest,
                now=now,
            )
        preliminary[sha256] = DatePlanItem(
            batch_id,
            sha256,
            str(working_path),
            str(row["original_name"]),
            str(row["media_type"]),
            "write-estimated" if evidence is not None else "quarantine",
            evidence,
            f"confidence-{evidence.confidence}" if evidence else "date-evidence-exhausted",
        )

    return DatePlan(
        batch_id,
        timezone_name,
        [preliminary[str(row["sha256"])] for row in rows],
    )


def _write_operation(
    catalog: Catalog,
    item: DatePlanItem,
    detail: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    existing = catalog.connection.execute(
        """
        SELECT id, detail_json FROM operations
        WHERE batch_id = ? AND sha256 = ? AND op = 'write_date'
          AND json_extract(detail_json, '$.expected_value') = ?
        ORDER BY id DESC LIMIT 1
        """,
        (item.batch_id, item.sha256, detail["expected_value"]),
    ).fetchone()
    if existing is not None:
        return int(existing["id"]), json.loads(existing["detail_json"])
    with catalog.transaction():
        cursor = catalog.connection.execute(
            """
            INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
            VALUES (?, ?, 'write_date', ?, ?)
            """,
            (item.batch_id, item.sha256, json.dumps(detail, sort_keys=True), utc_now()),
        )
    return int(cursor.lastrowid), detail


def execute_dates(plan: DatePlan, *, catalog_db: Path) -> dict[str, Any]:
    """Apply one dry-run plan to safe working files and persist provenance."""
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
            if item.action == "quarantine":
                with catalog.transaction():
                    catalog.connection.execute(
                        "UPDATE files SET status = 'quarantined', updated_at = ? WHERE sha256 = ?",
                        (utc_now(), item.sha256),
                    )
                results.append(
                    {"sha256": item.sha256, "status": "quarantined", "reason": item.reason}
                )
                continue
            assert item.evidence is not None and item.path is not None
            if item.action == "keep-native":
                with catalog.transaction():
                    catalog.connection.execute(
                        """
                        UPDATE files SET status = 'enriched', date_written = ?, date_source = ?,
                                         date_confidence = ?, updated_at = ? WHERE sha256 = ?
                        """,
                        (
                            item.evidence.exif_value,
                            item.evidence.source,
                            item.evidence.confidence,
                            utc_now(),
                            item.sha256,
                        ),
                    )
                results.append({"sha256": item.sha256, "status": "enriched", "written": False})
                continue

            path = Path(item.path)
            native_value, existing_tags = _date_tags(
                path,
                item.media_type,
                plan.timezone_name,
            )
            current_hash = _sha256(path)
            detail = {
                "status": "executing",
                "expected_value": item.evidence.exif_value,
                "source": item.evidence.source,
                "confidence": item.evidence.confidence,
                "keywords": item.evidence.keywords,
                "path": str(path),
                "expected_current_sha256": record["current_sha256"],
                "old_values": existing_tags,
            }
            operation_id, operation_detail = _write_operation(catalog, item, detail)
            expected_keywords = item.evidence.keywords
            existing_keywords = _as_keywords(existing_tags.get("XMP-dc:Subject"))
            already_written = native_value == item.evidence.exif_value and set(
                expected_keywords
            ).issubset(existing_keywords)
            if (
                operation_detail.get("status") == "completed"
                and operation_detail.get("current_sha256") == current_hash
                and already_written
            ):
                results.append({"sha256": item.sha256, "status": "enriched", "written": False})
                continue
            if current_hash != record["current_sha256"] and not already_written:
                detail["status"] = "failed"
                detail["error"] = "Working copy changed since date plan"
                with catalog.transaction():
                    catalog.connection.execute(
                        "UPDATE operations SET detail_json = ?, executed_at = ? WHERE id = ?",
                        (json.dumps(detail, sort_keys=True), utc_now(), operation_id),
                    )
                results.append(
                    {"sha256": item.sha256, "status": "failed", "error": detail["error"]}
                )
                continue

            old_values: dict[str, Any] = operation_detail.get("old_values", existing_tags)
            if not already_written:
                merged_subject = list(dict.fromkeys([*existing_keywords, *expected_keywords]))
                if item.media_type == "video":
                    tags = {
                        "QuickTime:CreateDate": item.evidence.exif_value,
                        "XMP-dc:Subject": merged_subject,
                    }
                    old_values = write_tags(
                        path,
                        tags,
                        quicktime_utc=True,
                        timezone_name=plan.timezone_name,
                    )
                else:
                    existing_iptc = _as_keywords(existing_tags.get("IPTC:Keywords"))
                    merged_iptc = list(dict.fromkeys([*existing_iptc, *expected_keywords]))
                    tags = {
                        "DateTimeOriginal": item.evidence.exif_value,
                        "CreateDate": item.evidence.exif_value,
                        "IPTC:Keywords": merged_iptc,
                        "XMP-dc:Subject": merged_subject,
                    }
                    old_values = write_tags(path, tags)

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
                    UPDATE files SET status = 'enriched', date_written = ?, date_source = ?,
                                     date_confidence = ?, date_original_value = ?,
                                     current_sha256 = ?, current_size = ?, updated_at = ?
                    WHERE sha256 = ?
                    """,
                    (
                        item.evidence.exif_value,
                        item.evidence.source,
                        item.evidence.confidence,
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
        "completed": sum(
            result["status"] in {"enriched", "quarantined", "skipped"} for result in results
        ),
        "failed": sum(result["status"] == "failed" for result in results),
    }
