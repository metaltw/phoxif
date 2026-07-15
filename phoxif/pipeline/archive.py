"""Catalog-backed, approval-gated archive copies for Immich external libraries."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from phoxif.pipeline.catalog import Catalog, utc_now

ARCHIVE_MARKER_NAME = ".phoxif-archive-root"
ARCHIVE_MARKER_CONTENT = "phoxif-archive-root-v1"


@dataclass(frozen=True)
class ArchivePlanItem:
    """One immutable archive decision derived from catalog state."""

    batch_id: str
    sha256: str
    current_sha256: str
    source_path: str | None
    name: str
    media_type: str
    size: int
    action: str
    relative_path: str | None
    reason: str
    source_root: str | None = None
    record_kind: str = "media"
    record_id: str | None = None
    group_id: str | None = None
    owner_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready archive decision."""
        return asdict(self)


@dataclass(frozen=True)
class ArchivePlan:
    """Server-built dry-run for one or more selected ingest batches."""

    batch_ids: tuple[str, ...]
    items: list[ArchivePlanItem]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready plan with review counters."""
        archive_items = [item for item in self.items if item.action == "archive"]
        return {
            "batch_ids": list(self.batch_ids),
            "items": [item.to_dict() for item in self.items],
            "counts": {
                "archive": len(archive_items),
                "already-archived": sum(item.reason == "already-archived" for item in self.items),
                "quarantined": sum(item.reason == "date-quarantined" for item in self.items),
                "skipped": sum(item.action == "skip" for item in self.items),
            },
            "total_bytes": sum(item.size for item in archive_items),
        }


def approval_fingerprint(plan: ArchivePlan, archive_root: Path) -> str:
    """Bind approval to the exact root, identities, hashes, paths, and sizes shown."""
    payload = {
        "archive_root": str(Path(archive_root).expanduser().resolve()),
        "batch_ids": plan.batch_ids,
        "items": [item.to_dict() for item in plan.items],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def approval_matches(
    plan: ArchivePlan,
    archive_root: Path,
    expected_fingerprint: str,
) -> bool:
    """Return whether a fresh server plan exactly matches the reviewed preview."""
    return hmac.compare_digest(approval_fingerprint(plan, archive_root), expected_fingerprint)


def validate_archive_root(archive_root: Path) -> Path:
    """Require an existing, non-symlink destination with its mounted-library sentinel."""
    configured_root = Path(archive_root).expanduser().absolute()
    if any(candidate.is_symlink() for candidate in (configured_root, *configured_root.parents)):
        raise RuntimeError("archive_root and its ancestors cannot be symlinks")
    if not configured_root.is_dir():
        raise NotADirectoryError(
            "archive_root must already exist; verify that the destination is mounted"
        )
    resolved = configured_root.resolve()
    marker = resolved / ARCHIVE_MARKER_NAME
    if (
        marker.is_symlink()
        or not marker.is_file()
        or marker.read_text().strip() != ARCHIVE_MARKER_CONTENT
    ):
        raise RuntimeError(
            f"archive_root marker missing; verify the mount and {ARCHIVE_MARKER_NAME}"
        )
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _working_path(rows: list[Any]) -> Path | None:
    """Choose only a catalog-recorded safe working file."""
    for row in rows:
        text = (
            row["staging_path"]
            if row["kind"] == "rescue"
            else (row["staging_path"] or row["original_path"])
        )
        if not text:
            continue
        path = Path(str(text))
        if path.is_file() and not path.is_symlink():
            return path
    return None


def _capture_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _relative_path(
    captured_at: datetime,
    extension: str,
    reserved: set[str],
) -> str:
    """Allocate the deterministic ASCII date-tree path from catalog reservations."""
    year = captured_at.strftime("%Y")
    month = captured_at.strftime("%Y-%m")
    stem = captured_at.strftime("%Y%m%d_%H%M%S")
    normalized_extension = extension.lower()
    if not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    sequence = 0
    while True:
        suffix = "" if sequence == 0 else f"_{sequence}"
        candidate = str(PurePosixPath(year, month, f"{stem}{suffix}{normalized_extension}"))
        if candidate not in reserved:
            reserved.add(candidate)
            return candidate
        sequence += 1


def _paired_relative_paths(
    captured_at: datetime,
    extensions: list[str],
    reserved: set[str],
) -> list[str]:
    """Allocate one collision-free basename for every member of a media group."""
    year = captured_at.strftime("%Y")
    month = captured_at.strftime("%Y-%m")
    stem = captured_at.strftime("%Y%m%d_%H%M%S")
    normalized = [ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions]
    sequence = 0
    while True:
        suffix = "" if sequence == 0 else f"_{sequence}"
        candidates = [
            str(PurePosixPath(year, month, f"{stem}{suffix}{extension}"))
            for extension in normalized
        ]
        if len(candidates) == len(set(candidates)) and not any(
            candidate in reserved for candidate in candidates
        ):
            reserved.update(candidates)
            return candidates
        sequence += 1


def _sidecar_relative_path(owner_path: str, reserved: set[str]) -> str:
    """Keep an AAE beside its owner with the same basename when possible."""
    owner = PurePosixPath(owner_path)
    sequence = 0
    while True:
        suffix = "" if sequence == 0 else f"_edit{sequence + 1}"
        candidate = str(owner.with_name(f"{owner.stem}{suffix}.aae"))
        if candidate not in reserved:
            reserved.add(candidate)
            return candidate
        sequence += 1


def _non_photo_relative_path(
    category: str,
    sha256: str,
    extension: str,
    reserved: set[str],
) -> str:
    normalized_category = (
        "".join(
            character if character.isascii() and character.isalnum() else "-"
            for character in category.lower()
        ).strip("-")
        or "other"
    )
    normalized_extension = extension.lower()
    if not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    candidate = str(
        PurePosixPath("_non_photos", normalized_category, f"{sha256[:16]}{normalized_extension}")
    )
    if candidate in reserved:
        raise RuntimeError(f"Non-photo catalog collision: {candidate}")
    reserved.add(candidate)
    return candidate


def plan_archive(batch_ids: list[str], *, catalog_db: Path) -> ArchivePlan:
    """Build a zero-write archive plan using catalog state, never NAS contents."""
    unique_batch_ids = tuple(dict.fromkeys(batch_ids))
    if not unique_batch_ids:
        raise ValueError("Choose at least one ingest batch")
    placeholders = ",".join("?" for _ in unique_batch_ids)
    with Catalog(catalog_db) as catalog:
        known = {
            str(row["batch_id"])
            for row in catalog.connection.execute(
                f"SELECT batch_id FROM batches WHERE batch_id IN ({placeholders})",
                unique_batch_ids,
            ).fetchall()
        }
        missing = [batch_id for batch_id in unique_batch_ids if batch_id not in known]
        if missing:
            raise KeyError(f"Unknown batch: {missing[0]}")
        rows = catalog.connection.execute(
            f"""
            SELECT files.*, sightings.source_id, sightings.original_path,
                   sightings.original_name, sightings.staging_path, sources.kind,
                   sources.root_path AS source_root,
                   batch_items.batch_id AS selected_batch_id, sightings.id AS sighting_id
            FROM batch_items
            JOIN sightings ON sightings.id = batch_items.sighting_id
            JOIN files ON files.sha256 = sightings.sha256
            JOIN sources ON sources.source_id = sightings.source_id
            WHERE batch_items.batch_id IN ({placeholders})
            ORDER BY files.sha256, sightings.id
            """,
            unique_batch_ids,
        ).fetchall()
        selected_media = {str(row["sha256"]) for row in rows}
        missing_partners = sorted(
            {
                str(row["live_partner_sha256"])
                for row in rows
                if row["live_partner_sha256"]
                and str(row["live_partner_sha256"]) not in selected_media
            }
        )
        if missing_partners:
            partner_placeholders = ",".join("?" for _ in missing_partners)
            rows.extend(
                catalog.connection.execute(
                    f"""
                    SELECT files.*, sightings.source_id, sightings.original_path,
                           sightings.original_name, sightings.staging_path, sources.kind,
                           sources.root_path AS source_root,
                           ? AS selected_batch_id, sightings.id AS sighting_id
                    FROM sightings
                    JOIN files ON files.sha256 = sightings.sha256
                    JOIN sources ON sources.source_id = sightings.source_id
                    WHERE files.sha256 IN ({partner_placeholders})
                    ORDER BY files.sha256, sightings.id
                    """,
                    (unique_batch_ids[0], *missing_partners),
                ).fetchall()
            )
        media_ids = sorted({str(row["sha256"]) for row in rows})
        media_placeholders = ",".join("?" for _ in media_ids)
        sidecar_rows = catalog.connection.execute(
            f"""
            SELECT sidecars.*, sidecar_sightings.original_path,
                   sidecar_sightings.original_name, sidecar_sightings.staging_path,
                   sidecar_batch_items.batch_id AS selected_batch_id,
                   sources.kind, sources.root_path AS source_root
            FROM sidecar_batch_items
            JOIN sidecar_sightings ON sidecar_sightings.id = sidecar_batch_items.sighting_id
            JOIN sidecars ON sidecars.sidecar_id = sidecar_sightings.sidecar_id
            JOIN sources ON sources.source_id = sidecar_sightings.source_id
            WHERE sidecar_batch_items.batch_id IN ({placeholders})
               OR sidecars.owner_sha256 IN ({media_placeholders})
            ORDER BY sidecars.sidecar_id, sidecar_sightings.id
            """,
            (*unique_batch_ids, *media_ids),
        ).fetchall()
        reserved = {
            str(row["archived_path"])
            for row in catalog.connection.execute(
                """
                SELECT archived_path FROM files WHERE archived_path IS NOT NULL
                UNION ALL
                SELECT archived_path FROM sidecars WHERE archived_path IS NOT NULL
                """
            ).fetchall()
        }

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(str(row["sha256"]), []).append(row)

    planned: list[ArchivePlanItem] = []
    eligible: dict[str, tuple[datetime, list[Any], Path]] = {}
    item_by_sha: dict[str, ArchivePlanItem] = {}
    for sha256, identity_rows in grouped.items():
        row = identity_rows[0]
        common = {
            "batch_id": str(row["selected_batch_id"]),
            "sha256": sha256,
            "current_sha256": str(row["current_sha256"]),
            "name": str(row["original_name"]),
            "media_type": str(row["media_type"]),
            "size": int(row["current_size"]),
            "record_id": sha256,
            "source_root": str(row["source_root"] or Path(str(row["original_path"])).parent),
        }
        status = str(row["status"])
        item: ArchivePlanItem | None = None
        if status == "archived":
            item = ArchivePlanItem(
                **common,
                source_path=None,
                action="skip",
                relative_path=str(row["archived_path"]),
                reason="already-archived",
                group_id=f"media:{sha256}",
            )
        elif row["collection_class"] == "non-photo" and status in {"unique", "enriched"}:
            source_path = _working_path(identity_rows)
            if source_path is None:
                item = ArchivePlanItem(
                    **common,
                    source_path=None,
                    action="skip",
                    relative_path=None,
                    reason="missing-safe-working-copy",
                    group_id=f"media:{sha256}",
                )
            else:
                item = ArchivePlanItem(
                    **common,
                    source_path=str(source_path),
                    action="archive",
                    relative_path=_non_photo_relative_path(
                        str(row["non_photo_category"] or "other"),
                        sha256,
                        str(row["ext"]),
                        reserved,
                    ),
                    reason="classified-non-photo",
                    group_id=f"media:{sha256}",
                )
        elif status == "quarantined":
            item = ArchivePlanItem(
                **common,
                source_path=None,
                action="skip",
                relative_path=None,
                reason="date-quarantined",
                group_id=f"media:{sha256}",
            )
        elif status != "enriched":
            item = ArchivePlanItem(
                **common,
                source_path=None,
                action="skip",
                relative_path=None,
                reason=f"status-{status}",
                group_id=f"media:{sha256}",
            )
        elif row["live_content_id"] and not row["live_partner_sha256"]:
            item = ArchivePlanItem(
                **common,
                source_path=None,
                action="skip",
                relative_path=None,
                reason="live-partner-not-ready",
                group_id=f"live-pending:{sha256}",
            )
        else:
            captured_at = _capture_time(row["date_written"])
            source_path = _working_path(identity_rows)
            if captured_at is None:
                item = ArchivePlanItem(
                    **common,
                    source_path=None,
                    action="skip",
                    relative_path=None,
                    reason="missing-trustworthy-date",
                    group_id=f"media:{sha256}",
                )
            elif source_path is None:
                item = ArchivePlanItem(
                    **common,
                    source_path=None,
                    action="skip",
                    relative_path=None,
                    reason="missing-safe-working-copy",
                    group_id=f"media:{sha256}",
                )
            else:
                eligible[sha256] = (captured_at, identity_rows, source_path)
        if item is not None:
            planned.append(item)
            item_by_sha[sha256] = item

    handled: set[str] = set()
    for sha256, (captured_at, identity_rows, source_path) in sorted(
        eligible.items(), key=lambda pair: (pair[1][0], pair[0])
    ):
        if sha256 in handled:
            continue
        row = identity_rows[0]
        partner_sha256 = str(row["live_partner_sha256"] or "")
        members = [sha256]
        group_id = f"media:{sha256}"
        if partner_sha256:
            if partner_sha256 not in eligible:
                item = ArchivePlanItem(
                    batch_id=str(row["selected_batch_id"]),
                    sha256=sha256,
                    current_sha256=str(row["current_sha256"]),
                    source_path=None,
                    name=str(row["original_name"]),
                    media_type=str(row["media_type"]),
                    size=int(row["current_size"]),
                    action="skip",
                    relative_path=None,
                    reason="live-partner-not-ready",
                    record_id=sha256,
                    group_id=f"live:{':'.join(sorted((sha256, partner_sha256)))}",
                    source_root=str(row["source_root"] or Path(str(row["original_path"])).parent),
                )
                planned.append(item)
                item_by_sha[sha256] = item
                handled.add(sha256)
                continue
            members.append(partner_sha256)
            members.sort()
            group_id = f"live:{':'.join(members)}"
        member_rows = [eligible[member][1][0] for member in members]
        paths = _paired_relative_paths(
            captured_at,
            [str(member_row["ext"]) for member_row in member_rows],
            reserved,
        )
        for member, member_row, relative_path in zip(members, member_rows, paths, strict=True):
            member_source = eligible[member][2]
            item = ArchivePlanItem(
                batch_id=str(member_row["selected_batch_id"]),
                sha256=member,
                current_sha256=str(member_row["current_sha256"]),
                source_path=str(member_source),
                name=str(member_row["original_name"]),
                media_type=str(member_row["media_type"]),
                size=int(member_row["current_size"]),
                action="archive",
                relative_path=relative_path,
                reason="live-photo-pair" if len(members) == 2 else "ready",
                record_id=member,
                group_id=group_id,
                source_root=str(
                    member_row["source_root"] or Path(str(member_row["original_path"])).parent
                ),
            )
            planned.append(item)
            item_by_sha[member] = item
            handled.add(member)

    grouped_sidecars: dict[str, list[Any]] = {}
    for row in sidecar_rows:
        grouped_sidecars.setdefault(str(row["sidecar_id"]), []).append(row)
    for sidecar_id, sightings in grouped_sidecars.items():
        row = sightings[0]
        owner_sha256 = str(row["owner_sha256"] or "") or None
        owner = item_by_sha.get(owner_sha256 or "")
        working = _working_path(sightings)
        common = {
            "batch_id": str(row["selected_batch_id"]),
            "sha256": str(row["sha256"]),
            "current_sha256": str(row["current_sha256"]),
            "name": str(row["original_name"]),
            "media_type": "sidecar",
            "size": int(row["size"]),
            "record_kind": "sidecar",
            "record_id": sidecar_id,
            "owner_sha256": owner_sha256,
            "group_id": owner.group_id if owner else f"sidecar:{sidecar_id}",
            "source_root": str(row["source_root"] or Path(str(row["original_path"])).parent),
        }
        if row["status"] == "archived":
            planned.append(
                ArchivePlanItem(
                    **common,
                    source_path=None,
                    action="skip",
                    relative_path=str(row["archived_path"]),
                    reason="already-archived",
                )
            )
        elif row["status"] == "orphan" or owner is None or owner.relative_path is None:
            planned.append(
                ArchivePlanItem(
                    **common,
                    source_path=None,
                    action="skip",
                    relative_path=None,
                    reason="orphan-sidecar"
                    if row["status"] == "orphan"
                    else "sidecar-owner-not-ready",
                )
            )
        elif owner.action not in {"archive", "skip"} or owner.reason not in {
            "ready",
            "live-photo-pair",
            "classified-non-photo",
            "already-archived",
        }:
            planned.append(
                ArchivePlanItem(
                    **common,
                    source_path=None,
                    action="skip",
                    relative_path=None,
                    reason="sidecar-owner-not-ready",
                )
            )
        elif working is None:
            planned.append(
                ArchivePlanItem(
                    **common,
                    source_path=None,
                    action="skip",
                    relative_path=None,
                    reason="missing-safe-working-copy",
                )
            )
        else:
            planned.append(
                ArchivePlanItem(
                    **common,
                    source_path=str(working),
                    action="archive",
                    relative_path=_sidecar_relative_path(owner.relative_path, reserved),
                    reason="aae-sidecar",
                )
            )
    return ArchivePlan(unique_batch_ids, planned)


def _archive_operation(
    catalog: Catalog,
    item: ArchivePlanItem,
    detail: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    existing = catalog.connection.execute(
        """
        SELECT id, detail_json FROM operations
        WHERE batch_id = ? AND sha256 = ? AND op = 'archive_copy'
          AND json_extract(detail_json, '$.relative_path') = ?
        ORDER BY id DESC LIMIT 1
        """,
        (item.batch_id, item.sha256, item.relative_path),
    ).fetchone()
    if existing is not None:
        return int(existing["id"]), json.loads(existing["detail_json"])
    with catalog.transaction():
        cursor = catalog.connection.execute(
            """
            INSERT INTO operations(batch_id, sha256, op, detail_json, executed_at)
            VALUES (?, ?, 'archive_copy', ?, ?)
            """,
            (item.batch_id, item.sha256, json.dumps(detail, sort_keys=True), utc_now()),
        )
    return int(cursor.lastrowid), detail


def _publish_verified_copy(
    source: Path,
    destination: Path,
    expected_sha256: str,
    archive_root: Path,
) -> bool:
    """Publish a read-only verified copy without ever replacing an existing path."""
    if destination.is_symlink():
        raise RuntimeError(f"Archive destination cannot be a symlink: {destination}")
    if destination.exists():
        if not destination.is_file() or _sha256(destination) != expected_sha256:
            raise RuntimeError(f"Archive destination collision: {destination}")
        return False
    if not destination.is_relative_to(archive_root):
        raise RuntimeError(f"Archive destination escaped its root: {destination}")
    current = archive_root
    for part in destination.relative_to(archive_root).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f"Archive path cannot traverse a symlink: {current}")
        current.mkdir(exist_ok=True)
        if not current.is_dir() or not current.resolve().is_relative_to(archive_root):
            raise RuntimeError(f"Archive path escaped its root: {current}")
    with tempfile.TemporaryDirectory(prefix=".phoxif-archive-", dir=destination.parent) as work:
        temporary = Path(work) / destination.name
        shutil.copy2(source, temporary)
        if _sha256(temporary) != expected_sha256:
            raise RuntimeError(f"Archive verification failed for {source}")
        with temporary.open("rb") as file_handle:
            os.fsync(file_handle.fileno())
        temporary.chmod(temporary.stat().st_mode & ~0o222)
        try:
            destination.hardlink_to(temporary)
        except FileExistsError:
            if destination.is_symlink() or _sha256(destination) != expected_sha256:
                raise RuntimeError(f"Archive destination race: {destination}") from None
            return False
    return True


def _execute_group(
    items: list[ArchivePlanItem],
    *,
    catalog_db: Path,
    archive_root: Path,
) -> list[dict[str, Any]]:
    """Publish a Live Photo, its AAE files, or one standalone item as one unit."""
    prepared: list[tuple[ArchivePlanItem, Path, Path]] = []
    for item in items:
        assert item.action == "archive"
        assert item.source_path is not None and item.relative_path is not None
        source = Path(item.source_path)
        destination = archive_root.joinpath(*PurePosixPath(item.relative_path).parts)
        if not source.is_file() or source.is_symlink() or _sha256(source) != item.current_sha256:
            raise RuntimeError("Working copy changed since archive plan")
        prepared.append((item, source, destination))

    with Catalog(catalog_db) as catalog:
        operations: list[tuple[ArchivePlanItem, int, dict[str, Any]]] = []
        for item, source, _destination in prepared:
            if item.record_kind == "sidecar":
                record = catalog.connection.execute(
                    "SELECT * FROM sidecars WHERE sidecar_id = ?", (item.record_id,)
                ).fetchone()
                valid = (
                    record is not None
                    and record["status"] == "ready"
                    and record["current_sha256"] == item.current_sha256
                )
            else:
                record = catalog.file(item.sha256)
                valid = (
                    record is not None
                    and record["status"] in {"unique", "enriched"}
                    and record["current_sha256"] == item.current_sha256
                )
            if not valid:
                raise RuntimeError("Catalog state changed since archive plan")
            detail = {
                "status": "executing",
                "record_kind": item.record_kind,
                "record_id": item.record_id,
                "group_id": item.group_id,
                "source_path": str(source),
                "relative_path": item.relative_path,
                "expected_current_sha256": item.current_sha256,
                "size": item.size,
            }
            operation_id, operation_detail = _archive_operation(catalog, item, detail)
            if (
                operation_detail.get("relative_path") != item.relative_path
                or operation_detail.get("expected_current_sha256") != item.current_sha256
                or operation_detail.get("record_id") not in {None, item.record_id}
            ):
                raise RuntimeError("Archive recovery evidence does not match")
            operations.append((item, operation_id, operation_detail))

        copied_by_id: dict[str, bool] = {}
        for item, source, destination in prepared:
            copied_by_id[item.record_id or item.sha256] = _publish_verified_copy(
                source,
                destination,
                item.current_sha256,
                archive_root,
            )
            destination.chmod(destination.stat().st_mode & ~0o222)

        archived_at = utc_now()
        with catalog.transaction():
            for item, operation_id, detail in operations:
                if item.record_kind == "sidecar":
                    cursor = catalog.connection.execute(
                        """
                        UPDATE sidecars
                        SET status = 'archived', archived_path = ?, archived_at = ?, updated_at = ?
                        WHERE sidecar_id = ? AND status = 'ready' AND current_sha256 = ?
                        """,
                        (
                            item.relative_path,
                            archived_at,
                            archived_at,
                            item.record_id,
                            item.current_sha256,
                        ),
                    )
                else:
                    cursor = catalog.connection.execute(
                        """
                        UPDATE files
                        SET status = 'archived', archived_path = ?, archived_at = ?, updated_at = ?
                        WHERE sha256 = ? AND status IN ('unique','enriched')
                          AND current_sha256 = ?
                        """,
                        (
                            item.relative_path,
                            archived_at,
                            archived_at,
                            item.sha256,
                            item.current_sha256,
                        ),
                    )
                if cursor.rowcount != 1:
                    raise RuntimeError("Catalog state changed during grouped archive")
                detail.update({"status": "completed", "archived_at": archived_at})
                detail.pop("error", None)
                catalog.connection.execute(
                    "UPDATE operations SET detail_json = ?, executed_at = ? WHERE id = ?",
                    (json.dumps(detail, sort_keys=True), archived_at, operation_id),
                )

    return [
        {
            "sha256": item.sha256,
            "record_kind": item.record_kind,
            "status": "archived",
            "copied": copied_by_id[item.record_id or item.sha256],
        }
        for item in items
    ]


def _mark_archive_failed(catalog_db: Path, item: ArchivePlanItem, error: str) -> None:
    with Catalog(catalog_db) as catalog:
        operation = catalog.connection.execute(
            """
            SELECT id, detail_json FROM operations
            WHERE batch_id = ? AND sha256 = ? AND op = 'archive_copy'
            ORDER BY id DESC LIMIT 1
            """,
            (item.batch_id, item.sha256),
        ).fetchone()
        if operation is None:
            return
        detail = json.loads(operation["detail_json"])
        detail.update({"status": "failed", "error": error})
        with catalog.transaction():
            catalog.connection.execute(
                "UPDATE operations SET detail_json = ?, executed_at = ? WHERE id = ?",
                (json.dumps(detail, sort_keys=True), utc_now(), int(operation["id"])),
            )


def _snapshot_catalog(catalog_db: Path, archive_root: Path) -> str:
    snapshot_dir = archive_root / "_phoxif"
    if snapshot_dir.is_symlink():
        raise RuntimeError("Catalog snapshot directory cannot be a symlink")
    snapshot_dir.mkdir(exist_ok=True)
    destination = snapshot_dir / f"catalog-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.db"
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".catalog-snapshot-", suffix=".db", dir=snapshot_dir
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        source_connection = sqlite3.connect(catalog_db)
        destination_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()
        with temporary.open("rb") as file_handle:
            os.fsync(file_handle.fileno())
        temporary.chmod(temporary.stat().st_mode & ~0o222)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    snapshots = sorted(snapshot_dir.glob("catalog-*.db"), reverse=True)
    for expired in snapshots[8:]:
        if expired.is_symlink():
            raise RuntimeError("Catalog snapshot retention encountered a symlink")
        expired.unlink(missing_ok=False)
    return str(PurePosixPath("_phoxif", destination.name))


def _catalog_snapshot_size(catalog_db: Path) -> int:
    """Estimate SQLite backup bytes from committed pages, including WAL state."""
    with sqlite3.connect(catalog_db) as connection:
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    return page_count * page_size


def execute_archive(
    plan: ArchivePlan,
    *,
    catalog_db: Path,
    archive_root: Path,
    approved: bool,
) -> dict[str, Any]:
    """Execute a fresh server plan only after explicit destination-write approval."""
    if not approved:
        raise PermissionError("Archive destination write requires explicit approval")
    archive_root = validate_archive_root(archive_root)
    archive_items = [item for item in plan.items if item.action == "archive"]
    for item in plan.items:
        if item.source_root is None:
            raise ValueError("Archive item is missing its recorded source root")
        source_root = Path(item.source_root).expanduser().resolve()
        if archive_root.is_relative_to(source_root) or source_root.is_relative_to(archive_root):
            raise ValueError("Archive root and photo source must not overlap")
    for item in archive_items:
        assert item.source_path is not None
        if Path(item.source_path).resolve().is_relative_to(archive_root):
            raise ValueError("Archive source must be outside archive_root")
    required_bytes = sum(item.size for item in archive_items) + _catalog_snapshot_size(catalog_db)
    available_bytes = shutil.disk_usage(archive_root).free
    if available_bytes < int(required_bytes * 1.05):
        raise OSError(
            f"Archive requires {int(required_bytes * 1.05)} bytes including safety margin; "
            f"only {available_bytes} bytes are free"
        )
    results: list[dict[str, Any]] = [
        {
            "sha256": item.sha256,
            "record_kind": item.record_kind,
            "status": "skipped",
            "reason": item.reason,
        }
        for item in plan.items
        if item.action == "skip"
    ]
    groups: dict[str, list[ArchivePlanItem]] = {}
    for item in archive_items:
        groups.setdefault(item.group_id or f"media:{item.sha256}", []).append(item)
    for group_items in groups.values():
        try:
            results.extend(
                _execute_group(
                    group_items,
                    catalog_db=catalog_db,
                    archive_root=archive_root,
                )
            )
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            message = str(error)
            for item in group_items:
                try:
                    _mark_archive_failed(catalog_db, item, message)
                except (OSError, RuntimeError, ValueError, sqlite3.Error):
                    pass
                results.append(
                    {
                        "sha256": item.sha256,
                        "record_kind": item.record_kind,
                        "status": "failed",
                        "error": message,
                    }
                )
    archived = sum(result["status"] == "archived" for result in results)
    snapshot_path = None
    snapshot_error = None
    if archived or any(item.reason == "already-archived" for item in plan.items):
        try:
            snapshot_path = _snapshot_catalog(catalog_db, archive_root)
        except (OSError, RuntimeError, sqlite3.Error) as error:
            snapshot_error = str(error)
    return {
        "batch_ids": list(plan.batch_ids),
        "results": results,
        "archived": archived,
        "failed": sum(result["status"] == "failed" for result in results),
        "skipped": sum(result["status"] == "skipped" for result in results),
        "snapshot_path": snapshot_path,
        "snapshot_error": snapshot_error,
        "source_cleanup": "retained-pending-separate-approval",
    }
