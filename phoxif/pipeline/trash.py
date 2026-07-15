"""Explicitly approved, catalog-scoped duplicate disposal."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from send2trash import send2trash

from phoxif.pipeline.catalog import Catalog, utc_now


@dataclass(frozen=True)
class PendingTrashItem:
    """One catalog operation awaiting explicit user approval."""

    operation_id: int
    batch_id: str
    sha256: str
    reason: str
    paths: list[str]
    names: list[str]
    kept_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _detail_paths(detail: dict[str, Any]) -> list[str]:
    values = detail.get("paths")
    if isinstance(values, list):
        return list(dict.fromkeys(str(value) for value in values))
    source_path = detail.get("source_path")
    return [str(source_path)] if source_path else []


def pending(catalog_db: Path, *, batch_ids: list[str] | None = None) -> list[PendingTrashItem]:
    """Return pending trash operations, optionally limited to selected batches."""
    with Catalog(catalog_db) as catalog:
        rows = catalog.connection.execute(
            "SELECT id, batch_id, sha256, detail_json FROM operations WHERE op = 'trash' "
            "ORDER BY id"
        ).fetchall()
        items: list[PendingTrashItem] = []
        allowed_batches = set(batch_ids or [])
        for row in rows:
            if allowed_batches and row["batch_id"] not in allowed_batches:
                continue
            detail = json.loads(row["detail_json"])
            if detail.get("status") not in {"pending", "failed", "executing"}:
                continue
            names = [
                str(name_row["original_name"])
                for name_row in catalog.connection.execute(
                    """
                    SELECT DISTINCT original_name FROM sightings
                    WHERE sha256 = ? ORDER BY original_name
                    """,
                    (row["sha256"],),
                ).fetchall()
            ]
            items.append(
                PendingTrashItem(
                    operation_id=int(row["id"]),
                    batch_id=str(row["batch_id"]),
                    sha256=str(row["sha256"]),
                    reason=str(detail.get("reason", "duplicate")),
                    paths=_detail_paths(detail),
                    names=names,
                    kept_sha256=detail.get("kept_sha256"),
                )
            )
        return items


def _catalog_paths(catalog: Catalog, sha256: str) -> set[str]:
    rows = catalog.connection.execute(
        """
        SELECT sources.kind, sightings.original_path, sightings.staging_path
        FROM sightings JOIN sources USING(source_id)
        WHERE sightings.sha256 = ?
        """,
        (sha256,),
    ).fetchall()
    return {
        str(path)
        for row in rows
        for path in (
            (row["staging_path"],)
            if row["kind"] == "rescue"
            else (row["original_path"], row["staging_path"])
        )
        if path is not None
    }


def execute(
    catalog_db: Path,
    operation_ids: list[int],
    *,
    approved: bool,
) -> dict[str, Any]:
    """Send catalog-validated duplicate paths to system Trash after approval."""
    if not approved:
        raise PermissionError("Trash execution requires explicit approval")
    unique_ids = list(dict.fromkeys(operation_ids))
    if not unique_ids:
        raise ValueError("Choose at least one pending trash operation")

    results: list[dict[str, Any]] = []
    with Catalog(catalog_db) as catalog:
        for operation_id in unique_ids:
            row = catalog.connection.execute(
                "SELECT * FROM operations WHERE id = ? AND op = 'trash'",
                (operation_id,),
            ).fetchone()
            if row is None:
                results.append(
                    {"operation_id": operation_id, "status": "failed", "error": "Unknown operation"}
                )
                continue
            detail = json.loads(row["detail_json"])
            if detail.get("status") == "completed":
                results.append({"operation_id": operation_id, "status": "completed", "paths": []})
                continue
            paths = _detail_paths(detail)
            allowed_paths = _catalog_paths(catalog, str(row["sha256"]))
            file_record = catalog.file(str(row["sha256"]))
            expected_hashes = {str(row["sha256"])}
            if file_record is not None and file_record["current_sha256"]:
                expected_hashes.add(str(file_record["current_sha256"]))
            detail["status"] = "executing"
            detail["approved_at"] = utc_now()
            with catalog.transaction():
                catalog.connection.execute(
                    "UPDATE operations SET detail_json = ?, executed_at = ? WHERE id = ?",
                    (json.dumps(detail, sort_keys=True), utc_now(), operation_id),
                )

            trashed: list[str] = []
            failures: list[dict[str, str]] = []
            previously_trashed = set(detail.get("trashed_paths", []))
            for path_text in paths:
                path = Path(path_text)
                if path_text in previously_trashed and not path.exists():
                    trashed.append(path_text)
                    continue
                if path_text not in allowed_paths:
                    failures.append({"path": path_text, "error": "Path is outside catalog evidence"})
                    continue
                if path.is_symlink():
                    failures.append({"path": path_text, "error": "Refusing to trash a symlink"})
                    continue
                if not path.exists():
                    trashed.append(path_text)
                    continue
                try:
                    if _sha256(path) not in expected_hashes:
                        failures.append({"path": path_text, "error": "Content changed since review"})
                        continue
                    send2trash(str(path))
                    trashed.append(path_text)
                except Exception as error:
                    failures.append({"path": path_text, "error": str(error)})

            detail["status"] = "failed" if failures else "completed"
            detail["trashed_paths"] = trashed
            detail["failures"] = failures
            with catalog.transaction():
                for path_text in trashed:
                    catalog.connection.execute(
                        """
                        UPDATE sightings SET staging_path = NULL
                        WHERE sha256 = ? AND staging_path = ?
                        """,
                        (row["sha256"], path_text),
                    )
                catalog.connection.execute(
                    "UPDATE operations SET detail_json = ?, executed_at = ? WHERE id = ?",
                    (json.dumps(detail, sort_keys=True), utc_now(), operation_id),
                )
            results.append(
                {
                    "operation_id": operation_id,
                    "status": detail["status"],
                    "paths": trashed,
                    "failures": failures,
                }
            )

    return {
        "results": results,
        "completed": sum(result["status"] == "completed" for result in results),
        "failed": sum(result["status"] == "failed" for result in results),
    }
