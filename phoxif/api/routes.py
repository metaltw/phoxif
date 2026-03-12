"""API routes for phoxif backend."""

import hashlib
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from phoxif.api.actions import (
    auto_rotate,
    fix_file_dates,
    fix_orientation,
    move_non_photos,
    rename_files,
    trash_files,
)
from phoxif.api.classifier import classify_non_photos
from phoxif.api.logger import OperationLogger
from phoxif.api.rename import generate_rename_preview
from phoxif.api.scanner import (
    find_date_mismatches,
    find_duplicates,
    find_exif_orientation_issues,
    scan_folder,
)
from phoxif.api.similar import find_similar_groups

router = APIRouter(prefix="/api")

# Thumbnail cache directory
_thumb_cache_dir = Path(tempfile.gettempdir()) / "phoxif_thumbs"
_thumb_cache_dir.mkdir(exist_ok=True)

# --- Request / Response models ---


class ScanRequest(BaseModel):
    """Request body for /api/scan."""

    path: str
    extensions: list[str] | None = None


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
    base_dir = resolved

    extensions: set[str] | None = None
    if req.extensions:
        extensions = {
            ext if ext.startswith(".") else f".{ext}" for ext in req.extensions
        }

    try:
        result = scan_folder(base_dir, extensions)
        duplicates = find_duplicates(result["files"])

        rename_preview = generate_rename_preview(result["files"])
        # Files without parseable dates can't be renamed; don't count them
        files_without_date = len(
            [
                f
                for f in result["files"]
                if f.get("date") is None or isinstance(f.get("date"), (int, float))
            ]
        )
        already_named = len(result["files"]) - len(rename_preview) - files_without_date
        orientation_issues = find_exif_orientation_issues(result["files"])
        similar_groups = find_similar_groups(result["files"])
        date_mismatches = find_date_mismatches(result["files"])
        non_photos = classify_non_photos(result["files"])

        scan_data = {
            "base_dir": str(base_dir),
            "files": result["files"],
            "stats": result["stats"],
            "duplicates": duplicates,
            "exiftool_available": result["exiftool_available"],
            "duplicate_stats": {
                "groups": len(duplicates),
                "total_duplicates": sum(d["count"] for d in duplicates),
                "wasted_size": sum(d["wasted_size"] for d in duplicates),
            },
            "rename_preview": rename_preview,
            "rename_stats": {
                "renameable": len(rename_preview),
                "already_named": already_named,
            },
            "exif_orientation_issues": orientation_issues,
            "exif_orientation_stats": {
                "issues_count": len(orientation_issues),
            },
            "similar_groups": similar_groups,
            "similar_stats": {
                "groups": len(similar_groups),
                "total_similar": sum(g["count"] for g in similar_groups),
                "reclaimable_size": sum(g["reclaimable_size"] for g in similar_groups),
            },
            "date_mismatches": date_mismatches,
            "date_stats": {
                "mismatches": len(date_mismatches),
                "total_checked": len(result["files"]),
            },
            "non_photos": non_photos,
            "non_photo_stats": {
                "total": len(non_photos),
                "by_category": {
                    cat: len([n for n in non_photos if n["category"] == cat])
                    for cat in {n["category"] for n in non_photos}
                },
            },
        }

        # Cache for other endpoints (use resolved path for consistent matching)
        _scan_cache[str(base_dir)] = scan_data

        return ApiResponse(ok=True, data=scan_data)
    except Exception as e:
        return ApiResponse(ok=False, error=str(e))


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
        if (
            "400" in msg
            or "401" in msg
            or "403" in msg
            or "api key" in msg
            or "invalid" in msg
        ):
            return ApiResponse(ok=False, error="Invalid API key")
        if "network" in msg or "connect" in msg or "timeout" in msg:
            return ApiResponse(
                ok=False, error="Network error — check internet connection"
            )
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

    scanned_count = len(
        [f for f in files if Path(f["path"]).suffix.lower() in _ALL_SUPPORTED_EXTS]
    )

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
            event_queue.put(
                f"event: error\ndata: {_json.dumps({'message': str(e)})}\n\n"
            )
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
    file_path = Path(path).expanduser().resolve()
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
    if not allowed:
        return Response(
            status_code=403, content="Access denied: path not in scanned directory"
        )

    ext = file_path.suffix.lower()

    # Browser-viewable: serve directly
    if ext in _BROWSER_VIEWABLE:
        return FileResponse(
            str(file_path),
            media_type=_MIME_TYPES.get(ext, "image/jpeg"),
        )

    # HEIC: convert via sips to cached JPEG thumbnail
    if ext in {".heic", ".heif"}:
        cache_key = hashlib.md5(str(file_path).encode()).hexdigest()
        cached_thumb = _thumb_cache_dir / f"{cache_key}.jpg"

        if not cached_thumb.exists():
            try:
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
                        str(file_path),
                        "--out",
                        str(cached_thumb),
                    ],
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                FileNotFoundError,
            ):
                return Response(status_code=500, content="Thumbnail generation failed")

        return FileResponse(str(cached_thumb), media_type="image/jpeg")

    # Video: try to extract a frame via ffmpeg
    if ext in {".mov", ".mp4", ".avi", ".mkv"}:
        cache_key = hashlib.md5(str(file_path).encode()).hexdigest()
        cached_thumb = _thumb_cache_dir / f"{cache_key}.jpg"

        if not cached_thumb.exists():
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-i",
                        str(file_path),
                        "-vframes",
                        "1",
                        "-vf",
                        "scale=400:-1",
                        "-q:v",
                        "5",
                        str(cached_thumb),
                        "-y",
                    ],
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                FileNotFoundError,
            ):
                return Response(status_code=500, content="Thumbnail generation failed")

        return FileResponse(str(cached_thumb), media_type="image/jpeg")

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
