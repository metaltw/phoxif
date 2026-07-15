"""Contract tests for the photo-inbox API routes."""

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from phoxif.api import routes


def test_intake_ingest_aggregates_unique_sources(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "camera"
    second = tmp_path / "messages"
    first.mkdir()
    second.mkdir()
    calls: list[tuple[str, Path, str]] = []

    def fake_ingest(source_id: str, root: Path, mode: str, **_kwargs):
        calls.append((source_id, root, mode))
        return SimpleNamespace(
            to_dict=lambda: {
                "batch_id": f"batch-{len(calls)}",
                "source_id": source_id,
                "mode": mode,
                "scanned": 2,
                "new_files": 1,
                "new_sightings": 2,
                "already_known": 1,
                "archived_reunions": 0,
                "staged_files": 1,
                "verified_staging": 2,
                "phash_failures": 0,
                "total_bytes": 100,
            }
        )

    monkeypatch.setattr(routes, "run_ingest", fake_ingest)
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (tmp_path / "catalog.db", tmp_path / "staging"),
    )

    response = asyncio.run(
        routes.api_intake_ingest(
            routes.IntakeIngestRequest(
                paths=[str(first), str(first), str(second)],
                mode="rescue",
            )
        )
    )

    assert response.ok is True
    assert len(calls) == 2
    assert response.data["complete"] is True
    assert response.data["failures"] == []
    assert response.data["totals"] == {
        "scanned": 4,
        "new_files": 2,
        "new_sightings": 4,
        "already_known": 2,
        "archived_reunions": 0,
        "staged_files": 2,
        "verified_staging": 4,
        "phash_failures": 0,
        "total_bytes": 200,
    }


def test_intake_ingest_rejects_missing_source(tmp_path: Path) -> None:
    response = asyncio.run(
        routes.api_intake_ingest(
            routes.IntakeIngestRequest(paths=[str(tmp_path / "missing")], mode="inbox")
        )
    )

    assert response.ok is False
    assert response.error == f"Path not found: {tmp_path / 'missing'}"


def test_intake_ingest_reports_partial_success(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "camera"
    second = tmp_path / "broken-disk"
    first.mkdir()
    second.mkdir()

    def fake_ingest(_source_id: str, root: Path, mode: str, **_kwargs):
        if root == second:
            raise sqlite3.OperationalError("catalog became unavailable")
        return SimpleNamespace(
            to_dict=lambda: {
                "batch_id": "batch-1",
                "source_id": "camera-id",
                "mode": mode,
                "scanned": 1,
                "new_files": 1,
                "new_sightings": 1,
                "already_known": 0,
                "archived_reunions": 0,
                "staged_files": 1,
                "verified_staging": 1,
                "phash_failures": 0,
                "total_bytes": 50,
            }
        )

    monkeypatch.setattr(routes, "run_ingest", fake_ingest)
    response = asyncio.run(
        routes.api_intake_ingest(
            routes.IntakeIngestRequest(paths=[str(first), str(second)], mode="rescue")
        )
    )

    assert response.ok is True
    assert response.data["complete"] is False
    assert len(response.data["batches"]) == 1
    assert response.data["totals"]["verified_staging"] == 1
    assert response.data["failures"] == [
        {
            "source_path": str(second),
            "label": second.name,
            "error": "catalog became unavailable",
        }
    ]
