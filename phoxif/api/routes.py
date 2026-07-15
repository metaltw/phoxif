"""API routes for phoxif backend."""

import hashlib
import platform
import re
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from phoxif.api.actions import (
    auto_rotate,
    fix_file_dates,
    fix_orientation,
    move_non_photos,
    rename_files,
    trash_files,
)
from phoxif.api.logger import OperationLogger
from phoxif.api.scanner import scan_folder
from phoxif.config import load_config
from phoxif.pipeline.catalog import DEFAULT_CATALOG_PATH, Catalog
from phoxif.pipeline.census import IntakeMode, scan_sources
from phoxif.pipeline.dedupe import resolve_review, run as run_dedupe
from phoxif.pipeline.enrich import execute_dates, plan_dates
from phoxif.pipeline.ingest import DEFAULT_STAGING_ROOT, run as run_ingest
from phoxif.pipeline.trash import execute as execute_pipeline_trash
from phoxif.pipeline.trash import pending as pending_pipeline_trash

router = APIRouter(prefix="/api")

# Thumbnail cache directory
_thumb_cache_dir = Path(tempfile.gettempdir()) / "phoxif_thumbs"
_thumb_cache_dir.mkdir(exist_ok=True)

# --- Request / Response models ---


class ScanRequest(BaseModel):
    """Request body for /api/scan."""

    path: str
    extensions: list[str] | None = None


class IntakeScanRequest(BaseModel):
    """Request body for a read-only multi-source photo census."""

    paths: list[str]
    mode: IntakeMode
    extensions: list[str] | None = None


class IntakeIngestRequest(BaseModel):
    """Request body for a source-preserving catalog ingest."""

    paths: list[str]
    mode: IntakeMode


class IntakeDedupeRequest(BaseModel):
    """Request body for conservative catalog duplicate analysis."""

    batch_ids: list[str]


class DedupeResolveRequest(BaseModel):
    """One explicit human near-duplicate decision."""

    batch_id: str
    pair_id: str
    left_sha256: str
    right_sha256: str
    keep_sha256: str | None = None


class PipelineTrashPendingRequest(BaseModel):
    """Request pending duplicate disposal for selected batches."""

    batch_ids: list[str]


class PipelineTrashExecuteRequest(BaseModel):
    """Explicit approval for selected catalog trash operations."""

    operation_ids: list[int]
    approved: bool = False


class IntakeDateRequest(BaseModel):
    """Selected ingest batches for date planning or execution."""

    batch_ids: list[str]


class TrashRequest(BaseModel):
    """Request body for /api/duplicates/trash."""

    files: list[str]


class RenameItem(BaseModel):
    """Single rename pair."""

    old: str
    new: str


class RenameRequest(BaseModel):
    """Request body for /api/rename/execute."""

    renames: list[RenameItem]


class UndoRequest(BaseModel):
    """Request body for /api/history/undo."""

    session_index: int


class OrientationFixItem(BaseModel):
    """Single file orientation fix entry."""

    path: str
    orientation: int


class OrientationFixRequest(BaseModel):
    """Request body for /api/orientation/fix."""

    files: list[OrientationFixItem]


class OrientationDetectRequest(BaseModel):
    """Request body for /api/orientation/detect."""

    path: str
    google_api_key: str | None = None
    model: str = "gemini-2.5-flash"
    confidence_threshold: float = 0.7


class AutoRotateItem(BaseModel):
    """Single file auto-rotate entry."""

    path: str
    rotation: int


class AutoRotateRequest(BaseModel):
    """Request body for /api/orientation/auto-rotate."""

    files: list[AutoRotateItem]


class DateFixItem(BaseModel):
    """Single file date fix entry."""

    path: str
    target_date: str  # ISO format target date


class DateFixRequest(BaseModel):
    """Request body for /api/dates/fix."""

    files: list[DateFixItem]


class MoveNonPhotoItem(BaseModel):
    """Single non-photo move entry."""

    path: str
    category: str


class MoveNonPhotosRequest(BaseModel):
    """Request body for /api/non-photos/move."""

    files: list[MoveNonPhotoItem]
    base_dir: str


class ApiResponse(BaseModel):
    """Consistent API response wrapper."""

    ok: bool
    data: Any = None
    error: str | None = None


# --- In-memory state ---
# Stores the last scan result so other endpoints can reference it
_scan_cache: dict[str, Any] = {}
_loggers: dict[str, OperationLogger] = {}


def _get_logger(base_dir: str) -> OperationLogger:
    """Get or create a logger for a base directory.

    Args:
        base_dir: Absolute path to the base directory.

    Returns:
        OperationLogger instance.
    """
    if base_dir not in _loggers:
        _loggers[base_dir] = OperationLogger(Path(base_dir))
    return _loggers[base_dir]


# Common search roots for folder name resolution
_SEARCH_ROOTS = [
    Path.home(),
    Path.home() / "Documents",
    Path.home() / "Pictures",
    Path.home() / "Photos",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path("/Volumes"),
]


def _resolve_folder_path(raw_path: str) -> Path | None:
    """Try to resolve a user-provided path to an actual directory.

    Handles: absolute paths, ~ paths, and bare folder names (searches common locations).

    Args:
        raw_path: User-provided path string.

    Returns:
        Resolved Path if found, None otherwise.
    """
    # Try as-is (absolute or ~ path)
    candidate = Path(raw_path).expanduser().resolve()
    if candidate.is_dir():
        return candidate

    # If it looks like a bare folder name (no / separator), search common locations
    if "/" not in raw_path and "\\" not in raw_path:
        for root in _SEARCH_ROOTS:
            candidate = root / raw_path
            if candidate.is_dir():
                return candidate.resolve()
        # Deep search (2 levels into each root)
        for root in _SEARCH_ROOTS:
            if not root.is_dir():
                continue
            try:
                for lvl1 in root.iterdir():
                    if not lvl1.is_dir():
                        continue
                    candidate = lvl1 / raw_path
                    if candidate.is_dir():
                        return candidate.resolve()
                    # One more level
                    try:
                        for lvl2 in lvl1.iterdir():
                            if not lvl2.is_dir():
                                continue
                            candidate = lvl2 / raw_path
                            if candidate.is_dir():
                                return candidate.resolve()
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue

    return None


# --- Routes ---


def _normalize_extensions(extensions: list[str] | None) -> set[str] | None:
    """Normalize optional API extensions to scanner format."""
    if not extensions:
        return None
    return {extension if extension.startswith(".") else f".{extension}" for extension in extensions}


def _cache_census(scan_data: dict[str, Any]) -> None:
    """Cache each source independently for legacy review/action routes."""
    for base_dir in scan_data["base_dirs"]:
        _scan_cache[base_dir] = {
            "files": [
                file_info
                for file_info in scan_data["files"]
                if file_info.get("source_root") == base_dir
            ],
            "exiftool_available": scan_data["exiftool_available"],
        }


@router.post("/scan", response_model=ApiResponse)
async def api_scan(req: ScanRequest) -> ApiResponse:
    """Scan a folder and return file metadata + duplicate groups.

    Args:
        req: Scan request with path and optional extensions.

    Returns:
        ApiResponse with scan results.
    """
    resolved = _resolve_folder_path(req.path)
    if resolved is None:
        return ApiResponse(ok=False, error=f"Path not found: {req.path}")
    try:
        scan_data = scan_sources(
            [resolved],
            mode="rescue",
            extensions=_normalize_extensions(req.extensions),
        )
        _cache_census(scan_data)
        return ApiResponse(ok=True, data=scan_data)
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))


@router.post("/intake/scan", response_model=ApiResponse)
async def api_intake_scan(req: IntakeScanRequest) -> ApiResponse:
    """Inspect historical or messaging sources without modifying them."""
    if not req.paths:
        return ApiResponse(ok=False, error="Choose at least one photo source")

    resolved_paths: list[Path] = []
    for raw_path in req.paths:
        resolved = _resolve_folder_path(raw_path)
        if resolved is None:
            return ApiResponse(ok=False, error=f"Path not found: {raw_path}")
        if resolved not in resolved_paths:
            resolved_paths.append(resolved)

    try:
        scan_data = scan_sources(
            resolved_paths,
            mode=req.mode,
            extensions=_normalize_extensions(req.extensions),
        )
        _cache_census(scan_data)
        return ApiResponse(ok=True, data=scan_data)
    except (OSError, RuntimeError, ValueError) as error:
        return ApiResponse(ok=False, error=str(error))


def _pipeline_storage_paths() -> tuple[Path, Path]:
    """Load private catalog/staging paths without making config mandatory."""
    try:
        config = load_config()
    except (FileNotFoundError, ValueError):
        return DEFAULT_CATALOG_PATH, DEFAULT_STAGING_ROOT
    return config["catalog_db"], config["staging_dir"]


def _source_id(path: Path) -> str:
    """Derive a stable, non-secret source slug from a local path."""
    readable = re.sub(r"[^a-z0-9]+", "-", path.name.lower()).strip("-") or "source"
    fingerprint = hashlib.sha256(str(path).encode()).hexdigest()[:8]
    return f"{readable[:32]}-{fingerprint}"


def _dedupe_thresholds() -> tuple[int, int]:
    """Load conservative near-duplicate thresholds."""
    try:
        config = load_config()
    except (FileNotFoundError, ValueError):
        return 4, 10
    return config["dedupe_auto_threshold"], config["dedupe_review_threshold"]


def _date_settings() -> tuple[Path, str, datetime, set[str]]:
    """Load date policy with safe defaults when private config is absent."""
    catalog_db, _staging_root = _pipeline_storage_paths()
    try:
        config = load_config()
        timezone_name = str(config["default_timezone"])
        earliest_text = str(config["date_earliest"])
        mtime_sources = set(config["date_mtime_source_ids"])
    except (FileNotFoundError, ValueError):
        timezone_name = "Asia/Taipei"
        earliest_text = "1995-01-01"
        mtime_sources = set()
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown default_timezone: {timezone_name}") from error
    try:
        earliest = datetime.fromisoformat(earliest_text).replace(tzinfo=zone)
    except ValueError as error:
        raise ValueError(f"Invalid date_earliest: {earliest_text}") from error
    return catalog_db, timezone_name, earliest, mtime_sources


@router.post("/intake/ingest", response_model=ApiResponse)
async def api_intake_ingest(req: IntakeIngestRequest) -> ApiResponse:
    """Create verified working copies and permanent catalog evidence."""
    if not req.paths:
        return ApiResponse(ok=False, error="Choose at least one photo source")

    resolved_paths: list[Path] = []
    for raw_path in req.paths:
        resolved = _resolve_folder_path(raw_path)
        if resolved is None:
            return ApiResponse(ok=False, error=f"Path not found: {raw_path}")
        if resolved not in resolved_paths:
            resolved_paths.append(resolved)

    catalog_db, staging_root = _pipeline_storage_paths()
    batches: list[dict[str, str | int]] = []
    failures: list[dict[str, str]] = []
    for source in resolved_paths:
        try:
            result = run_ingest(
                _source_id(source),
                source,
                req.mode,
                label=source.name,
                catalog_db=catalog_db,
                staging_root=staging_root,
            )
            batches.append(result.to_dict())
        except Exception as error:
            failures.append({"source_path": str(source), "label": source.name, "error": str(error)})

    totals = {
        key: sum(int(batch[key]) for batch in batches)
        for key in (
            "scanned",
            "new_files",
            "new_sightings",
            "already_known",
            "archived_reunions",
            "staged_files",
            "verified_staging",
            "phash_failures",
            "total_bytes",
        )
    }
    return ApiResponse(
        ok=True,
        data={
            "mode": req.mode,
            "complete": not failures,
            "batches": batches,
            "failures": failures,
            "totals": totals,
        },
    )


@router.post("/intake/dedupe", response_model=ApiResponse)
async def api_intake_dedupe(req: IntakeDedupeRequest) -> ApiResponse:
    """Analyze ingested batches without deleting any user file."""
    batch_ids = list(dict.fromkeys(req.batch_ids))
    if not batch_ids:
        return ApiResponse(ok=False, error="Choose at least one ingest batch")
    catalog_db, _staging_root = _pipeline_storage_paths()
    auto_threshold, review_threshold = _dedupe_thresholds()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    seen_groups: dict[str, set[str]] = {
        "exact_groups": set(),
        "auto_groups": set(),
        "review_pairs": set(),
        "burst_pairs": set(),
        "protected_edits": set(),
    }
    for batch_id in batch_ids:
        try:
            result = run_dedupe(
                batch_id,
                catalog_db=catalog_db,
                auto_threshold=auto_threshold,
                review_threshold=review_threshold,
            ).to_dict()
            for key, seen in seen_groups.items():
                identity_key = "sha256" if key == "exact_groups" else "id"
                unique_groups = []
                for group in result[key]:
                    identity = str(group[identity_key])
                    if identity in seen:
                        continue
                    seen.add(identity)
                    unique_groups.append(group)
                result[key] = unique_groups
            results.append(result)
        except Exception as error:
            failures.append({"batch_id": batch_id, "error": str(error)})
    return ApiResponse(
        ok=True,
        data={"complete": not failures, "results": results, "failures": failures},
    )


@router.post("/intake/dedupe/resolve", response_model=ApiResponse)
async def api_intake_dedupe_resolve(req: DedupeResolveRequest) -> ApiResponse:
    """Resolve one review pair after recomputing its safety constraints."""
    catalog_db, _staging_root = _pipeline_storage_paths()
    auto_threshold, review_threshold = _dedupe_thresholds()
    try:
        decision = resolve_review(
            req.batch_id,
            req.pair_id,
            req.left_sha256,
            req.right_sha256,
            req.keep_sha256,
            catalog_db=catalog_db,
            auto_threshold=auto_threshold,
            review_threshold=review_threshold,
        )
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        return ApiResponse(ok=False, error=str(error))
    return ApiResponse(ok=True, data=decision)


@router.post("/intake/trash/pending", response_model=ApiResponse)
async def api_pipeline_trash_pending(req: PipelineTrashPendingRequest) -> ApiResponse:
    """List duplicate files that still require explicit disposal approval."""
    if not req.batch_ids:
        return ApiResponse(ok=False, error="Choose at least one ingest batch")
    catalog_db, _staging_root = _pipeline_storage_paths()
    try:
        items = [
            item.to_dict() for item in pending_pipeline_trash(catalog_db, batch_ids=req.batch_ids)
        ]
    except (OSError, RuntimeError, ValueError) as error:
        return ApiResponse(ok=False, error=str(error))
    return ApiResponse(ok=True, data={"items": items})


@router.post("/intake/trash/execute", response_model=ApiResponse)
async def api_pipeline_trash_execute(req: PipelineTrashExecuteRequest) -> ApiResponse:
    """Execute only explicitly approved, catalog-scoped trash operations."""
    catalog_db, _staging_root = _pipeline_storage_paths()
    try:
        result = execute_pipeline_trash(
            catalog_db,
            req.operation_ids,
            approved=req.approved,
        )
    except (OSError, PermissionError, RuntimeError, ValueError) as error:
        return ApiResponse(ok=False, error=str(error))
    return ApiResponse(ok=True, data=result)


def _plan_date_batches(batch_ids: list[str]) -> tuple[list[Any], list[dict[str, str]]]:
    catalog_db, timezone_name, earliest, mtime_sources = _date_settings()
    plans = []
    failures: list[dict[str, str]] = []
    seen_sha256: set[str] = set()
    now = datetime.now(ZoneInfo(timezone_name))
    with Catalog(catalog_db) as catalog:
        source_by_batch = (
            {
                str(row["batch_id"]): str(row["source_id"])
                for row in catalog.connection.execute(
                    "SELECT batch_id, source_id FROM batches WHERE batch_id IN ({})".format(
                        ",".join("?" for _ in batch_ids)
                    ),
                    batch_ids,
                ).fetchall()
            }
            if batch_ids
            else {}
        )
    for batch_id in list(dict.fromkeys(batch_ids)):
        try:
            plan = plan_dates(
                batch_id,
                catalog_db=catalog_db,
                timezone_name=timezone_name,
                earliest=earliest,
                now=now,
                allow_mtime=source_by_batch.get(batch_id) in mtime_sources,
            )
            unique_items = []
            for item in plan.items:
                if item.sha256 in seen_sha256:
                    continue
                seen_sha256.add(item.sha256)
                unique_items.append(item)
            plan.items[:] = unique_items
            plans.append(plan)
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            failures.append({"batch_id": batch_id, "error": str(error)})
    return plans, failures


@router.post("/intake/enrich/dates/plan", response_model=ApiResponse)
async def api_intake_date_plan(req: IntakeDateRequest) -> ApiResponse:
    """Return explainable date decisions without modifying media."""
    if not req.batch_ids:
        return ApiResponse(ok=False, error="Choose at least one ingest batch")
    try:
        plans, failures = _plan_date_batches(req.batch_ids)
    except (OSError, RuntimeError, ValueError) as error:
        return ApiResponse(ok=False, error=str(error))
    return ApiResponse(
        ok=True,
        data={
            "complete": not failures,
            "plans": [plan.to_dict() for plan in plans],
            "failures": failures,
        },
    )


@router.post("/intake/enrich/dates/execute", response_model=ApiResponse)
async def api_intake_date_execute(req: IntakeDateRequest) -> ApiResponse:
    """Execute freshly recomputed date plans on catalog-scoped working files."""
    if not req.batch_ids:
        return ApiResponse(ok=False, error="Choose at least one ingest batch")
    try:
        catalog_db, _timezone_name, _earliest, _mtime_sources = _date_settings()
        plans, failures = _plan_date_batches(req.batch_ids)
        results = [execute_dates(plan, catalog_db=catalog_db) for plan in plans]
    except (OSError, RuntimeError, ValueError) as error:
        return ApiResponse(ok=False, error=str(error))
    return ApiResponse(
        ok=True,
        data={
            "complete": not failures and all(not result["failed"] for result in results),
            "results": results,
            "failures": failures,
        },
    )


@router.get("/scan/status", response_model=ApiResponse)
async def api_scan_status() -> ApiResponse:
    """Return scan progress (placeholder for future WebSocket).

    Returns:
        ApiResponse with cached scan paths.
    """
    return ApiResponse(
        ok=True,
        data={
            "scanned_paths": list(_scan_cache.keys()),
            "status": "idle" if not _scan_cache else "complete",
        },
    )


@router.post("/duplicates/trash", response_model=ApiResponse)
async def api_trash_duplicates(req: TrashRequest) -> ApiResponse:
    """Trash selected duplicate files.

    Args:
        req: Request with list of file paths to trash.

    Returns:
        ApiResponse with trash results.
    """
    if not req.files:
        return ApiResponse(ok=False, error="No files specified")

    resolved_files = [Path(path).expanduser().resolve() for path in req.files]
    if any(
        not any(file_path.is_relative_to(Path(cached_path)) for cached_path in _scan_cache)
        for file_path in resolved_files
    ):
        return ApiResponse(ok=False, error="Every trash path must belong to the current scan")

    # Determine base_dir from first file
    first_file = Path(req.files[0]).resolve()
    base_dir = str(first_file.parent)

    # Find the scan cache base_dir that contains this file
    for cached_path in _scan_cache:
        try:
            first_file.relative_to(cached_path)
            base_dir = cached_path
            break
        except ValueError:
            continue

    logger = _get_logger(base_dir)
    logger.start_session()

    try:
        result = trash_files(req.files, logger)
        logger.save()
        return ApiResponse(ok=True, data=result)
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))


@router.post("/rename/execute", response_model=ApiResponse)
async def api_rename(req: RenameRequest) -> ApiResponse:
    """Execute batch renames.

    Args:
        req: Request with list of old/new path pairs.

    Returns:
        ApiResponse with rename results.
    """
    if not req.renames:
        return ApiResponse(ok=False, error="No renames specified")

    # Determine base_dir from first old path
    first_old = Path(req.renames[0].old).resolve()
    base_dir = str(first_old.parent)

    for cached_path in _scan_cache:
        try:
            first_old.relative_to(cached_path)
            base_dir = cached_path
            break
        except ValueError:
            continue

    logger = _get_logger(base_dir)
    logger.start_session()

    try:
        renames = [{"old": r.old, "new": r.new} for r in req.renames]
        result = rename_files(renames, logger)
        logger.save()
        return ApiResponse(ok=True, data=result)
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))


@router.post("/orientation/fix", response_model=ApiResponse)
async def api_fix_orientation(req: OrientationFixRequest) -> ApiResponse:
    """Fix EXIF orientation for selected files (reset to Normal).

    Args:
        req: Request with list of files and their current orientations.

    Returns:
        ApiResponse with fix results.
    """
    if not req.files:
        return ApiResponse(ok=False, error="No files specified")

    # Determine base_dir from first file
    first_file = Path(req.files[0].path).resolve()
    base_dir = str(first_file.parent)

    for cached_path in _scan_cache:
        try:
            first_file.relative_to(cached_path)
            base_dir = cached_path
            break
        except ValueError:
            continue

    logger = _get_logger(base_dir)
    logger.start_session()

    try:
        file_items = [{"path": f.path, "orientation": f.orientation} for f in req.files]
        result = fix_orientation(file_items, logger)
        logger.save()
        return ApiResponse(ok=True, data=result)
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))


@router.get("/history", response_model=ApiResponse)
async def api_history() -> ApiResponse:
    """Return all operation sessions from all loggers.

    Returns:
        ApiResponse with session list.
    """
    all_sessions: list[dict[str, Any]] = []
    for base_dir, logger in _loggers.items():
        for session in logger.get_sessions():
            session_copy = dict(session)
            session_copy["base_dir"] = base_dir
            all_sessions.append(session_copy)

    # Sort by timestamp descending
    all_sessions.sort(key=lambda s: s.get("timestamp", ""), reverse=True)
    return ApiResponse(ok=True, data=all_sessions)


@router.post("/history/undo", response_model=ApiResponse)
async def api_undo(req: UndoRequest) -> ApiResponse:
    """Undo a session by index.

    Args:
        req: Request with session_index.

    Returns:
        ApiResponse with undo results.
    """
    if req.session_index < 0:
        return ApiResponse(ok=False, error="Invalid session index")

    # Build global session list matching /api/history order
    all_sessions: list[tuple[OperationLogger, int]] = []
    for logger in _loggers.values():
        for local_idx, session in enumerate(logger.get_sessions()):
            all_sessions.append((logger, local_idx))

    # Sort by timestamp descending (same order as history endpoint)
    all_sessions.sort(
        key=lambda pair: pair[0].get_sessions()[pair[1]].get("timestamp", ""),
        reverse=True,
    )

    if req.session_index >= len(all_sessions):
        return ApiResponse(ok=False, error=f"Session {req.session_index} not found")

    target_logger, local_index = all_sessions[req.session_index]
    try:
        results = target_logger.undo_session(local_index)
        return ApiResponse(ok=True, data=results)
    except (IndexError, ValueError) as e:
        return ApiResponse(ok=False, error=str(e))


class TestKeyRequest(BaseModel):
    """Request body for /api/orientation/test-key."""

    google_api_key: str


@router.post("/orientation/test-key", response_model=ApiResponse)
def api_test_key(req: TestKeyRequest) -> ApiResponse:
    """Test if a Google Gemini API key is valid.

    Makes a minimal API call to verify the key works.

    Args:
        req: Request with google_api_key.

    Returns:
        ApiResponse with ok=True if key is valid.
    """
    try:
        from google import genai

        client = genai.Client(api_key=req.google_api_key)
        # Minimal call: list models to verify key
        models = client.models.list()
        # Consume at least one result to confirm auth works
        next(iter(models))
        return ApiResponse(ok=True)
    except ImportError:
        return ApiResponse(
            ok=False,
            error="google-generativeai package not installed. Run: uv add google-genai",
        )
    except StopIteration:
        return ApiResponse(ok=True)
    except Exception as e:
        msg = str(e).lower()
        if "400" in msg or "401" in msg or "403" in msg or "api key" in msg or "invalid" in msg:
            return ApiResponse(ok=False, error="Invalid API key")
        if "network" in msg or "connect" in msg or "timeout" in msg:
            return ApiResponse(ok=False, error="Network error — check internet connection")
        return ApiResponse(ok=False, error="Key test failed — please try again")


@router.post("/orientation/detect")
def api_detect_orientation(req: OrientationDetectRequest) -> Response:
    """Detect visually incorrect orientation using Gemini Vision AI.

    Returns a Server-Sent Events stream with progress updates and final results.
    Events:
    - progress: {current, total, filename}
    - result: {issues, issues_count, scanned_count}
    - error: {message}

    Args:
        req: Request with path, Google API key, and optional model/threshold.

    Returns:
        SSE stream response.
    """
    import json as _json

    resolved = _resolve_folder_path(req.path)
    if resolved is None:
        err = _json.dumps({"message": f"Path not found: {req.path}"})
        return Response(
            content=f"event: error\ndata: {err}\n\n",
            media_type="text/event-stream",
        )

    base_dir_str = str(resolved)

    # Scan if not cached yet
    if base_dir_str not in _scan_cache:
        try:
            scan_result = scan_folder(resolved)
            _scan_cache[base_dir_str] = {
                "files": scan_result["files"],
                "stats": scan_result["stats"],
                "exiftool_available": scan_result["exiftool_available"],
            }
        except Exception as e:
            err = _json.dumps({"message": f"Scan failed: {e}"})
            return Response(
                content=f"event: error\ndata: {err}\n\n",
                media_type="text/event-stream",
            )

    files = _scan_cache[base_dir_str]["files"]

    from phoxif.api.orientation_ai import (
        _ALL_SUPPORTED_EXTS,
        detect_orientation_batch,
    )

    scanned_count = len([f for f in files if Path(f["path"]).suffix.lower() in _ALL_SUPPORTED_EXTS])

    import queue
    import threading

    from starlette.responses import StreamingResponse

    event_queue: queue.Queue[str | None] = queue.Queue()

    def on_progress(current: int, total: int, filename: str) -> None:
        """Push progress event to queue."""
        evt = _json.dumps({"current": current, "total": total, "filename": filename})
        event_queue.put(f"event: progress\ndata: {evt}\n\n")

    def run_detection() -> None:
        """Run batch detection in a thread, push results to queue."""
        try:
            issues = detect_orientation_batch(
                files,
                confidence_threshold=req.confidence_threshold,
                progress_callback=on_progress,
                api_key=req.google_api_key,
                model=req.model,
            )
            result_data = _json.dumps(
                {
                    "issues": issues,
                    "issues_count": len(issues),
                    "scanned_count": scanned_count,
                }
            )
            event_queue.put(f"event: result\ndata: {result_data}\n\n")
        except Exception as e:
            event_queue.put(f"event: error\ndata: {_json.dumps({'message': str(e)})}\n\n")
        finally:
            event_queue.put(None)  # Signal end

    def generate():  # type: ignore[no-untyped-def]
        thread = threading.Thread(target=run_detection, daemon=True)
        thread.start()
        while True:
            evt = event_queue.get()
            if evt is None:
                break
            yield evt

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/orientation/auto-rotate", response_model=ApiResponse)
async def api_auto_rotate(req: AutoRotateRequest) -> ApiResponse:
    """Auto-rotate images that are visually wrong.

    Sets EXIF Orientation tag then applies lossless auto-rotation
    via exiftool.

    Args:
        req: Request with list of files and their required rotations.

    Returns:
        ApiResponse with rotation results.
    """
    if not req.files:
        return ApiResponse(ok=False, error="No files specified")

    first_file = Path(req.files[0].path).resolve()
    base_dir = str(first_file.parent)

    for cached_path in _scan_cache:
        try:
            first_file.relative_to(cached_path)
            base_dir = cached_path
            break
        except ValueError:
            continue

    logger = _get_logger(base_dir)
    logger.start_session()

    try:
        file_items = [{"path": f.path, "rotation": f.rotation} for f in req.files]
        result = auto_rotate(file_items, logger)
        logger.save()
        return ApiResponse(ok=True, data=result)
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))


@router.post("/dates/fix", response_model=ApiResponse)
async def api_fix_dates(req: DateFixRequest) -> ApiResponse:
    """Fix file modification dates to match EXIF or filename dates.

    Args:
        req: Request with list of files and their target dates.

    Returns:
        ApiResponse with fix results.
    """
    if not req.files:
        return ApiResponse(ok=False, error="No files specified")

    first_file = Path(req.files[0].path).resolve()
    base_dir = str(first_file.parent)

    for cached_path in _scan_cache:
        try:
            first_file.relative_to(cached_path)
            base_dir = cached_path
            break
        except ValueError:
            continue

    logger = _get_logger(base_dir)
    logger.start_session()

    try:
        file_items = [{"path": f.path, "target_date": f.target_date} for f in req.files]
        result = fix_file_dates(file_items, logger)
        logger.save()
        return ApiResponse(ok=True, data=result)
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))


@router.post("/non-photos/move", response_model=ApiResponse)
async def api_move_non_photos(req: MoveNonPhotosRequest) -> ApiResponse:
    """Move non-photo files to category subfolders.

    Moves files to `_non_photos/{category}/` under the base directory.

    Args:
        req: Request with list of files, categories, and base directory.

    Returns:
        ApiResponse with move results.
    """
    if not req.files:
        return ApiResponse(ok=False, error="No files specified")

    # Validate base_dir is a scanned directory
    if req.base_dir not in _scan_cache:
        return ApiResponse(ok=False, error="Base directory not in scan cache")

    # Validate all categories are in the allowed set
    from phoxif.api.classifier import ALL_CATEGORIES

    for f in req.files:
        if f.category not in ALL_CATEGORIES:
            return ApiResponse(ok=False, error=f"Invalid category: {f.category}")

    # Validate all file paths are within the scanned directory
    base = Path(req.base_dir)
    for f in req.files:
        try:
            Path(f.path).resolve().relative_to(base)
        except ValueError:
            return ApiResponse(ok=False, error=f"File not in scan directory: {f.path}")

    logger = _get_logger(req.base_dir)
    logger.start_session()

    try:
        file_items = [{"path": f.path, "category": f.category} for f in req.files]
        result = move_non_photos(file_items, req.base_dir, logger)
        logger.save()
        return ApiResponse(ok=True, data=result)
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))


# --- Finder reveal ---


@router.get("/reveal")
async def api_reveal(
    path: str = Query(..., description="File or folder path"),
) -> ApiResponse:
    """Reveal a file or folder in the system file manager.

    Supports macOS (Finder), Windows (Explorer), and Linux (xdg-open).

    Args:
        path: Absolute path to reveal.

    Returns:
        ApiResponse with success status.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return ApiResponse(ok=False, error="Path not found")

    # Security: only allow paths within scanned directories
    allowed = False
    for cached_path in _scan_cache:
        try:
            target.relative_to(cached_path)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        return ApiResponse(ok=False, error="Access denied")

    try:
        system = platform.system()
        if system == "Darwin":
            if target.is_file():
                subprocess.Popen(["open", "-R", str(target)])
            else:
                subprocess.Popen(["open", str(target)])
        elif system == "Windows":
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                subprocess.Popen(["explorer", str(target)])
        else:
            # Linux: xdg-open opens the containing folder
            folder = str(target.parent) if target.is_file() else str(target)
            subprocess.Popen(["xdg-open", folder])
        return ApiResponse(ok=True)
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))


# --- Thumbnail endpoint ---

# Extensions that browsers can display directly
_BROWSER_VIEWABLE = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}

# MIME type mapping
_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".heic": "image/jpeg",  # converted via sips
    ".heif": "image/jpeg",
}


def _open_catalog_thumbnail(
    requested_path: Path,
    resolved_path: Path,
) -> tuple[BinaryIO, str] | None:
    """Open catalog evidence and retain the verified inode for later serving."""
    catalog_db, _staging_root = _pipeline_storage_paths()
    if not catalog_db.exists():
        return None
    try:
        with Catalog(catalog_db) as catalog:
            rows = catalog.connection.execute(
                """
                SELECT DISTINCT sha256 FROM sightings
                WHERE original_path IN (?, ?) OR staging_path IN (?, ?)
                """,
                (
                    str(requested_path),
                    str(resolved_path),
                    str(requested_path),
                    str(resolved_path),
                ),
            ).fetchall()
    except (OSError, RuntimeError, sqlite3.Error):
        return None
    if not rows or not resolved_path.is_file():
        return None
    digest = hashlib.sha256()
    file_handle: BinaryIO | None = None
    try:
        file_handle = resolved_path.open("rb")
        while chunk := file_handle.read(1024 * 1024):
            digest.update(chunk)
    except OSError:
        if file_handle is not None:
            file_handle.close()
        return None
    assert file_handle is not None
    digest_text = digest.hexdigest()
    if digest_text not in {str(row["sha256"]) for row in rows}:
        file_handle.close()
        return None
    file_handle.seek(0)
    return file_handle, digest_text


def _stream_verified_file(file_handle: BinaryIO) -> Iterator[bytes]:
    """Stream and close the same inode that passed catalog hash verification."""
    try:
        while chunk := file_handle.read(1024 * 1024):
            yield chunk
    finally:
        file_handle.close()


def _thumbnail_cache_ready(path: Path) -> bool:
    """Treat cache races and unreadable entries as a miss."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


@contextmanager
def _verified_conversion_source(
    file_handle: BinaryIO,
    *,
    suffix: str,
) -> Iterator[Path]:
    """Materialize verified bytes for a converter that only accepts paths."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".phoxif-thumb-source-",
            suffix=suffix,
            dir=_thumb_cache_dir,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := file_handle.read(1024 * 1024):
                temporary.write(chunk)
        yield temporary_path
    finally:
        file_handle.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@router.get("/thumbnail")
async def api_thumbnail(path: str = Query(..., description="File path")) -> Response:
    """Serve a thumbnail for a file.

    For browser-viewable images (JPG/PNG), serves the file directly.
    For HEIC files, converts to JPEG via macOS sips.

    Args:
        path: Absolute path to the image file.

    Returns:
        Image file response.
    """
    requested_path = Path(path).expanduser()
    file_path = requested_path.resolve()
    if not file_path.exists():
        return Response(status_code=404, content="File not found")

    # Validate path is within a scanned directory
    allowed = False
    for cached_path in _scan_cache:
        try:
            file_path.relative_to(cached_path)
            allowed = True
            break
        except ValueError:
            continue
    catalog_media: tuple[BinaryIO, str] | None = None
    if not allowed:
        catalog_media = _open_catalog_thumbnail(requested_path, file_path)
        if catalog_media is None:
            return Response(status_code=403, content="Access denied: path not in scanned directory")

    ext = file_path.suffix.lower()

    # Browser-viewable: serve directly
    if ext in _BROWSER_VIEWABLE:
        if catalog_media is not None:
            return StreamingResponse(
                _stream_verified_file(catalog_media[0]),
                media_type=_MIME_TYPES.get(ext, "image/jpeg"),
            )
        return FileResponse(
            str(file_path),
            media_type=_MIME_TYPES.get(ext, "image/jpeg"),
        )

    # HEIC: convert via sips to cached JPEG thumbnail
    if ext in {".heic", ".heif"}:
        cache_key = (
            catalog_media[1]
            if catalog_media is not None
            else hashlib.md5(str(file_path).encode()).hexdigest()
        )
        cached_thumb = _thumb_cache_dir / f"{cache_key}.jpg"

        if _thumbnail_cache_ready(cached_thumb):
            if catalog_media is not None:
                catalog_media[0].close()
        else:
            try:
                cached_thumb.unlink(missing_ok=True)
            except OSError:
                if catalog_media is not None:
                    catalog_media[0].close()
                return Response(status_code=500, content="Thumbnail cache is unavailable")
            temporary_output: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=".phoxif-thumb-output-",
                    suffix=".jpg",
                    dir=_thumb_cache_dir,
                    delete=False,
                ) as temporary:
                    temporary_output = Path(temporary.name)
                source_context = (
                    _verified_conversion_source(catalog_media[0], suffix=ext)
                    if catalog_media is not None
                    else nullcontext(file_path)
                )
                with source_context as source_path:
                    subprocess.run(
                        [
                            "sips",
                            "-s",
                            "format",
                            "jpeg",
                            "-s",
                            "formatOptions",
                            "60",
                            "-Z",
                            "400",
                            str(source_path),
                            "--out",
                            str(temporary_output),
                        ],
                        capture_output=True,
                        timeout=10,
                        check=True,
                    )
                if temporary_output.stat().st_size == 0:
                    raise OSError("Thumbnail converter produced an empty file")
                temporary_output.replace(cached_thumb)
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                FileNotFoundError,
                OSError,
            ):
                return Response(status_code=500, content="Thumbnail generation failed")
            finally:
                if catalog_media is not None and not catalog_media[0].closed:
                    catalog_media[0].close()
                if temporary_output is not None:
                    temporary_output.unlink(missing_ok=True)

        return FileResponse(str(cached_thumb), media_type="image/jpeg")

    # Video: try to extract a frame via ffmpeg
    if ext in {".mov", ".mp4", ".avi", ".mkv"}:
        cache_key = (
            catalog_media[1]
            if catalog_media is not None
            else hashlib.md5(str(file_path).encode()).hexdigest()
        )
        cached_thumb = _thumb_cache_dir / f"{cache_key}.jpg"

        if _thumbnail_cache_ready(cached_thumb):
            if catalog_media is not None:
                catalog_media[0].close()
        else:
            try:
                cached_thumb.unlink(missing_ok=True)
            except OSError:
                if catalog_media is not None:
                    catalog_media[0].close()
                return Response(status_code=500, content="Thumbnail cache is unavailable")
            temporary_output = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=".phoxif-thumb-output-",
                    suffix=".jpg",
                    dir=_thumb_cache_dir,
                    delete=False,
                ) as temporary:
                    temporary_output = Path(temporary.name)
                source_context = (
                    _verified_conversion_source(catalog_media[0], suffix=ext)
                    if catalog_media is not None
                    else nullcontext(file_path)
                )
                with source_context as source_path:
                    subprocess.run(
                        [
                            "ffmpeg",
                            "-i",
                            str(source_path),
                            "-vframes",
                            "1",
                            "-vf",
                            "scale=400:-1",
                            "-q:v",
                            "5",
                            str(temporary_output),
                            "-y",
                        ],
                        capture_output=True,
                        timeout=10,
                        check=True,
                    )
                if temporary_output.stat().st_size == 0:
                    raise OSError("Thumbnail converter produced an empty file")
                temporary_output.replace(cached_thumb)
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                FileNotFoundError,
                OSError,
            ):
                return Response(status_code=500, content="Thumbnail generation failed")
            finally:
                if catalog_media is not None and not catalog_media[0].closed:
                    catalog_media[0].close()
                if temporary_output is not None:
                    temporary_output.unlink(missing_ok=True)

        return FileResponse(str(cached_thumb), media_type="image/jpeg")

    if catalog_media is not None:
        catalog_media[0].close()
    return Response(status_code=415, content="Unsupported format")


# --- Folder picker ---


@router.get("/pick-folder", response_model=ApiResponse)
async def api_pick_folder() -> ApiResponse:
    """Open a native folder picker dialog and return the selected path.

    Uses osascript on macOS, PowerShell on Windows, zenity/kdialog on Linux.

    Returns:
        ApiResponse with selected folder path.
    """
    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'set f to POSIX path of (choose folder with prompt "Select photo folder")',
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                return ApiResponse(ok=False, error="Cancelled")
            folder = result.stdout.strip().rstrip("/")
        elif system == "Windows":
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$d.Description = 'Select photo folder'; "
                "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath } else { exit 1 }"
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                return ApiResponse(ok=False, error="Cancelled")
            folder = result.stdout.strip()
        else:
            # Linux: try zenity, then kdialog
            for cmd in [
                [
                    "zenity",
                    "--file-selection",
                    "--directory",
                    "--title=Select photo folder",
                ],
                ["kdialog", "--getexistingdirectory", "."],
            ]:
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=True,
                    )
                    folder = result.stdout.strip()
                    break
                except (FileNotFoundError, subprocess.CalledProcessError):
                    continue
            else:
                return ApiResponse(ok=False, error="No folder picker available")

        if not folder or not Path(folder).is_dir():
            return ApiResponse(ok=False, error="Invalid folder")

        return ApiResponse(ok=True, data={"path": folder})
    except subprocess.TimeoutExpired:
        return ApiResponse(ok=False, error="Picker timed out")
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))
