"""Contract tests for the photo-inbox API routes."""

import asyncio
import hashlib
import io
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from phoxif.api import routes
from phoxif.pipeline.catalog import Catalog


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

    response = (
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
        "quarantined_staging": 0,
        "phash_failures": 0,
        "sidecars": 0,
        "staged_sidecars": 0,
        "total_bytes": 200,
    }


def test_intake_ingest_rejects_missing_source(tmp_path: Path) -> None:
    response = (
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
    response = (
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


def test_intake_dedupe_reports_results_and_batch_failures(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (tmp_path / "catalog.db", tmp_path / "staging"),
    )
    monkeypatch.setattr(routes, "_dedupe_thresholds", lambda: (4, 10))

    def fake_dedupe(batch_id: str, **_kwargs):
        if batch_id == "failed-batch":
            raise RuntimeError("dedupe failed")
        return SimpleNamespace(
            to_dict=lambda: {
                "batch_id": batch_id,
                "exact_groups": [],
                "auto_groups": [],
                "review_pairs": [],
                "burst_pairs": [],
                "protected_edits": [],
            }
        )

    monkeypatch.setattr(routes, "run_dedupe", fake_dedupe)
    response = (
        routes.api_intake_dedupe(
            routes.IntakeDedupeRequest(batch_ids=["good-batch", "good-batch", "failed-batch"])
        )
    )

    assert response.ok is True
    assert response.data["complete"] is False
    assert [result["batch_id"] for result in response.data["results"]] == ["good-batch"]
    assert response.data["failures"] == [{"batch_id": "failed-batch", "error": "dedupe failed"}]


def test_dedupe_resolve_forwards_explicit_decision(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (tmp_path / "catalog.db", tmp_path / "staging"),
    )
    monkeypatch.setattr(routes, "_dedupe_thresholds", lambda: (4, 10))
    captured: dict[str, object] = {}

    def fake_resolve(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"decision": "keep-both", "pair_id": "pair-1"}

    monkeypatch.setattr(routes, "resolve_review", fake_resolve)
    response = (
        routes.api_intake_dedupe_resolve(
            routes.DedupeResolveRequest(
                batch_id="batch-1",
                pair_id="pair-1",
                left_sha256="a" * 64,
                right_sha256="b" * 64,
            )
        )
    )

    assert response.ok is True
    assert response.data["decision"] == "keep-both"
    assert captured["args"] == ("batch-1", "pair-1", "a" * 64, "b" * 64, None)
    assert captured["kwargs"]["auto_threshold"] == 4
    assert captured["kwargs"]["review_threshold"] == 10


def test_pipeline_trash_routes_require_explicit_approval(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (tmp_path / "catalog.db", tmp_path / "staging"),
    )
    fake_item = SimpleNamespace(to_dict=lambda: {"operation_id": 7, "paths": ["photo.jpg"]})
    monkeypatch.setattr(routes, "pending_pipeline_trash", lambda *_args, **_kwargs: [fake_item])
    pending_response = (
        routes.api_pipeline_trash_pending(routes.PipelineTrashPendingRequest(batch_ids=["batch-1"]))
    )
    assert pending_response.ok is True
    assert pending_response.data["items"][0]["operation_id"] == 7

    monkeypatch.setattr(
        routes,
        "execute_pipeline_trash",
        lambda _db, _ids, *, approved: (
            {"completed": 1, "failed": 0, "results": []}
            if approved
            else (_ for _ in ()).throw(PermissionError("approval required"))
        ),
    )
    denied = (
        routes.api_pipeline_trash_execute(
            routes.PipelineTrashExecuteRequest(operation_ids=[7], approved=False)
        )
    )
    assert denied.ok is False
    approved = (
        routes.api_pipeline_trash_execute(
            routes.PipelineTrashExecuteRequest(operation_ids=[7], approved=True)
        )
    )
    assert approved.ok is True
    assert approved.data["completed"] == 1

    empty_pending = (
        routes.api_pipeline_trash_pending(routes.PipelineTrashPendingRequest(batch_ids=[]))
    )
    assert empty_pending.ok is False


def test_date_plan_and_execute_routes_use_fresh_server_side_plan(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_plan = SimpleNamespace(
        to_dict=lambda: {
            "batch_id": "batch-1",
            "items": [{"action": "write-estimated"}],
            "counts": {"write-estimated": 1},
        }
    )
    calls: list[str] = []
    monkeypatch.setattr(
        routes,
        "_plan_date_batches",
        lambda batch_ids: ([fake_plan], []) if batch_ids == ["batch-1"] else ([], []),
    )
    monkeypatch.setattr(
        routes,
        "_date_settings",
        lambda: (tmp_path / "catalog.db", "Asia/Taipei", object(), set()),
    )

    def fake_execute(plan, *, catalog_db):
        calls.append(f"{plan.to_dict()['batch_id']}:{catalog_db.name}")
        return {"batch_id": "batch-1", "failed": 0, "completed": 1, "results": []}

    monkeypatch.setattr(routes, "execute_dates", fake_execute)

    plan_response = (
        routes.api_intake_date_plan(routes.IntakeDateRequest(batch_ids=["batch-1"]))
    )
    execute_response = (
        routes.api_intake_date_execute(routes.IntakeDateRequest(batch_ids=["batch-1"]))
    )

    assert plan_response.ok is True
    assert plan_response.data["plans"][0]["counts"]["write-estimated"] == 1
    assert execute_response.ok is True
    assert execute_response.data["complete"] is True
    assert calls == ["batch-1:catalog.db"]


def test_gps_plan_and_execute_routes_use_fresh_server_side_plan(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_plan = SimpleNamespace(
        to_dict=lambda: {
            "batch_id": "batch-1",
            "items": [{"action": "write-neighbor"}],
            "counts": {"write-neighbor": 1},
        }
    )
    calls: list[str] = []
    monkeypatch.setattr(
        routes,
        "_plan_gps_batches",
        lambda batch_ids: ([fake_plan], []) if batch_ids == ["batch-1"] else ([], []),
    )
    monkeypatch.setattr(
        routes,
        "_gps_settings",
        lambda: (tmp_path / "catalog.db", "Asia/Taipei", {}, 30, False),
    )

    def fake_execute(plan, *, catalog_db, folder_name_as_tag):
        calls.append(f"{plan.to_dict()['batch_id']}:{catalog_db.name}:{folder_name_as_tag}")
        return {"batch_id": "batch-1", "failed": 0, "completed": 1, "results": []}

    monkeypatch.setattr(routes, "execute_gps", fake_execute)

    plan_response = (
        routes.api_intake_gps_plan(routes.IntakeGpsRequest(batch_ids=["batch-1"]))
    )
    execute_response = (
        routes.api_intake_gps_execute(routes.IntakeGpsRequest(batch_ids=["batch-1"]))
    )

    assert plan_response.ok is True
    assert plan_response.data["plans"][0]["counts"]["write-neighbor"] == 1
    assert execute_response.ok is True
    assert execute_response.data["complete"] is True
    assert calls == ["batch-1:catalog.db:False"]


def test_gps_settings_reject_invalid_config_instead_of_using_defaults(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (tmp_path / "catalog.db", tmp_path / "staging"),
    )
    monkeypatch.setattr(
        routes,
        "load_config",
        lambda: (_ for _ in ()).throw(ValueError("invalid GPS time window")),
    )

    with pytest.raises(ValueError, match="invalid GPS time window"):
        routes._gps_settings()


def test_archive_routes_recompute_plan_and_require_explicit_approval(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_plan = SimpleNamespace(
        to_dict=lambda: {
            "batch_ids": ["batch-1"],
            "items": [{"action": "archive", "relative_path": "2024/file.jpg"}],
            "counts": {"archive": 1},
            "total_bytes": 100,
        }
    )
    plan_calls: list[list[str]] = []
    execute_calls: list[bool] = []

    def fake_plan_archive(batch_ids, *, catalog_db):
        assert catalog_db == tmp_path / "catalog.db"
        plan_calls.append(batch_ids)
        return fake_plan

    def fake_execute_archive(plan, *, catalog_db, archive_root, approved):
        assert plan is fake_plan
        assert catalog_db == tmp_path / "catalog.db"
        assert archive_root == tmp_path / "library"
        execute_calls.append(approved)
        return {
            "batch_ids": ["batch-1"],
            "results": [],
            "archived": 1,
            "failed": 0,
            "skipped": 0,
            "snapshot_path": "_phoxif/catalog.db",
            "snapshot_error": None,
            "source_cleanup": "retained-pending-separate-approval",
        }

    monkeypatch.setattr(
        routes,
        "_archive_settings",
        lambda: (tmp_path / "catalog.db", tmp_path / "library"),
    )
    monkeypatch.setattr(routes, "plan_archive", fake_plan_archive)
    monkeypatch.setattr(routes, "execute_archive", fake_execute_archive)
    monkeypatch.setattr(routes, "approval_fingerprint", lambda plan, root: "reviewed-plan")
    monkeypatch.setattr(
        routes,
        "approval_matches",
        lambda plan, root, fingerprint: fingerprint == "reviewed-plan",
    )

    preview = (
        routes.api_intake_archive_plan(routes.IntakeArchiveRequest(batch_ids=["batch-1"]))
    )
    denied = (
        routes.api_intake_archive_execute(
            routes.IntakeArchiveExecuteRequest(
                batch_ids=["batch-1"], plan_fingerprint="reviewed-plan", approved=False
            )
        )
    )
    executed = (
        routes.api_intake_archive_execute(
            routes.IntakeArchiveExecuteRequest(
                batch_ids=["batch-1"],
                plan_fingerprint="reviewed-plan",
                approved=True,
            )
        )
    )
    changed = (
        routes.api_intake_archive_execute(
            routes.IntakeArchiveExecuteRequest(
                batch_ids=["batch-1"], plan_fingerprint="stale-plan", approved=True
            )
        )
    )

    assert preview.ok is True
    assert preview.data["archive_root"] == str(tmp_path / "library")
    assert denied.ok is False
    assert executed.ok is True
    assert executed.data["complete"] is True
    assert changed.ok is False
    assert "changed since preview" in changed.error
    assert plan_calls == [["batch-1"], ["batch-1"], ["batch-1"]]
    assert execute_calls == [True]


def test_thumbnail_allows_catalog_working_copy_but_not_arbitrary_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    working_copy = tmp_path / "staging" / "photo.jpg"
    working_copy.parent.mkdir()
    working_copy.write_bytes(b"jpeg-placeholder")
    digest = hashlib.sha256(working_copy.read_bytes()).hexdigest()
    arbitrary = tmp_path / "private.jpg"
    arbitrary.write_bytes(b"not-cataloged")
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        catalog.upsert_file(
            sha256=digest,
            size=working_copy.stat().st_size,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id="camera",
            batch_id=batch_id,
            original_path=tmp_path / "source" / "photo.jpg",
            original_name="photo.jpg",
            original_mtime=None,
            original_btime=None,
            staging_path=working_copy,
        )
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (database, tmp_path / "staging"),
    )
    routes._scan_cache.clear()

    allowed = (routes.api_thumbnail(str(working_copy)))
    denied = (routes.api_thumbnail(str(arbitrary)))

    assert allowed.status_code == 200
    assert denied.status_code == 403


def test_thumbnail_accepts_a_cataloged_path_before_symlink_resolution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    real_staging = tmp_path / "real-staging"
    real_staging.mkdir()
    working_copy = real_staging / "photo.jpg"
    working_copy.write_bytes(b"jpeg-placeholder")
    digest = hashlib.sha256(working_copy.read_bytes()).hexdigest()
    staging_alias = tmp_path / "staging-alias"
    staging_alias.symlink_to(real_staging, target_is_directory=True)
    catalog_path = staging_alias / working_copy.name
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        catalog.upsert_file(
            sha256=digest,
            size=working_copy.stat().st_size,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id="camera",
            batch_id=batch_id,
            original_path=tmp_path / "source" / "photo.jpg",
            original_name="photo.jpg",
            original_mtime=None,
            original_btime=None,
            staging_path=catalog_path,
        )
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (database, real_staging),
    )
    routes._scan_cache.clear()

    response = (routes.api_thumbnail(str(catalog_path)))

    assert response.status_code == 200


def test_thumbnail_rejects_a_cataloged_symlink_repointed_after_ingest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    original_dir = tmp_path / "original"
    original_dir.mkdir()
    original = original_dir / "photo.jpg"
    original.write_bytes(b"cataloged-photo")
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    alias = tmp_path / "staging-alias"
    alias.symlink_to(original_dir, target_is_directory=True)
    catalog_path = alias / original.name
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        catalog.upsert_file(
            sha256=digest,
            size=original.stat().st_size,
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id="camera",
            batch_id=batch_id,
            original_path=tmp_path / "source" / "photo.jpg",
            original_name="photo.jpg",
            original_mtime=None,
            original_btime=None,
            staging_path=catalog_path,
        )
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "photo.jpg").write_bytes(b"private-unrelated-content")
    replacement_alias = tmp_path / "replacement-alias"
    replacement_alias.symlink_to(other_dir, target_is_directory=True)
    replacement_alias.replace(alias)
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (database, original_dir),
    )
    routes._scan_cache.clear()

    response = (routes.api_thumbnail(str(catalog_path)))

    assert response.status_code == 403


def test_thumbnail_streams_the_verified_inode_if_path_is_replaced(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    working_copy = tmp_path / "photo.jpg"
    original_bytes = b"cataloged-photo"
    working_copy.write_bytes(original_bytes)
    digest = hashlib.sha256(original_bytes).hexdigest()
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        catalog.upsert_file(
            sha256=digest,
            size=len(original_bytes),
            ext=".jpg",
            media_type="image",
            phash=None,
            width=None,
            height=None,
        )
        catalog.add_sighting(
            sha256=digest,
            source_id="camera",
            batch_id=batch_id,
            original_path=tmp_path / "source" / "photo.jpg",
            original_name="photo.jpg",
            original_mtime=None,
            original_btime=None,
            staging_path=working_copy,
        )
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (database, tmp_path),
    )
    routes._scan_cache.clear()

    async def request_then_replace() -> tuple[int, bytes]:
        response = routes.api_thumbnail(str(working_copy))
        working_copy.replace(tmp_path / "verified-original.jpg")
        working_copy.write_bytes(b"private-replacement")
        chunks = [chunk async for chunk in response.body_iterator]
        return response.status_code, b"".join(chunks)

    status_code, body = asyncio.run(request_then_replace())

    assert status_code == 200
    assert body == original_bytes


def test_thumbnail_denies_unscanned_path_when_catalog_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    arbitrary = tmp_path / "private.jpg"
    arbitrary.write_bytes(b"private")
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (tmp_path / "missing.db", tmp_path / "staging"),
    )
    routes._scan_cache.clear()

    response = (routes.api_thumbnail(str(arbitrary)))

    assert response.status_code == 403


def test_thumbnail_converter_failure_never_publishes_partial_cache(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    cache_dir = tmp_path / "thumb-cache"
    cache_dir.mkdir()
    files = [tmp_path / "photo.heic", tmp_path / "video.mp4"]
    for index, media_path in enumerate(files):
        media_path.write_bytes(f"cataloged-media-{index}".encode())
    with Catalog(database) as catalog:
        catalog.register_source("camera", "Camera", "rescue")
        batch_id = catalog.start_batch("camera", "rescue")
        for media_path in files:
            digest = hashlib.sha256(media_path.read_bytes()).hexdigest()
            catalog.upsert_file(
                sha256=digest,
                size=media_path.stat().st_size,
                ext=media_path.suffix,
                media_type="video" if media_path.suffix == ".mp4" else "image",
                phash=None,
                width=None,
                height=None,
            )
            catalog.add_sighting(
                sha256=digest,
                source_id="camera",
                batch_id=batch_id,
                original_path=tmp_path / "source" / media_path.name,
                original_name=media_path.name,
                original_mtime=None,
                original_btime=None,
                staging_path=media_path,
            )
    monkeypatch.setattr(
        routes,
        "_pipeline_storage_paths",
        lambda: (database, tmp_path),
    )
    monkeypatch.setattr(routes, "_thumb_cache_dir", cache_dir)
    routes._scan_cache.clear()

    def fail_after_partial_output(command, **_kwargs):
        output_path = next(
            Path(argument)
            for argument in command
            if str(argument).startswith(str(cache_dir)) and str(argument).endswith(".jpg")
        )
        output_path.write_bytes(b"partial-thumbnail")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(routes.subprocess, "run", fail_after_partial_output)

    responses = [(routes.api_thumbnail(str(media_path))) for media_path in files]

    assert [response.status_code for response in responses] == [500, 500]
    assert list(cache_dir.iterdir()) == []


def test_thumbnail_cache_cleanup_error_closes_verified_handle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    media_path = tmp_path / "photo.heic"
    media_path.write_bytes(b"cataloged-media")
    verified_handle = io.BytesIO(media_path.read_bytes())
    monkeypatch.setattr(routes, "_thumb_cache_dir", tmp_path / "cache")
    routes._thumb_cache_dir.mkdir()
    monkeypatch.setattr(
        routes,
        "_open_catalog_thumbnail",
        lambda *_args: (verified_handle, "a" * 64),
    )
    monkeypatch.setattr(routes, "_thumbnail_cache_ready", lambda _path: False)
    routes._scan_cache.clear()

    def fail_unlink(_path: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise OSError("cache became unavailable")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    response = (routes.api_thumbnail(str(media_path)))

    assert response.status_code == 500
    assert verified_handle.closed is True


def test_legacy_trash_rejects_path_outside_current_scan(tmp_path: Path) -> None:
    scanned = tmp_path / "scanned"
    scanned.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"private")
    routes._scan_cache.clear()
    routes._scan_cache[str(scanned)] = {"files": [], "exiftool_available": False}

    response = (routes.api_trash_duplicates(routes.TrashRequest(files=[str(outside)])))

    assert response.ok is False
    assert response.error == "Every trash path must belong to the current scan"


def test_intake_scan_rejects_file_path(make_jpeg, tmp_path: Path) -> None:
    photo = make_jpeg("IMG_0004.jpg", directory=tmp_path / "src")

    response = (
        routes.api_intake_scan(
            routes.IntakeScanRequest(paths=[str(photo)], mode="rescue")
        )
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.startswith("Not a folder:")


def test_api_handlers_stay_sync_for_threadpool() -> None:
    """async-def handlers would block the event loop during long copies."""
    import inspect

    handlers = [
        obj
        for name, obj in vars(routes).items()
        if name.startswith("api_") and callable(obj)
    ]
    assert handlers
    assert not any(inspect.iscoroutinefunction(h) for h in handlers)
