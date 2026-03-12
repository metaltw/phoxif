"""Execute operations — trash, rename, and other file actions."""

import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from send2trash import send2trash

from phoxif.api.logger import OperationLogger

_logger = logging.getLogger(__name__)

# File type sets for rotation strategy dispatch
_JPEG_EXTS = {".jpg", ".jpeg"}
_VIDEO_EXTS = {".mov", ".mp4", ".avi", ".mkv", ".m4v"}
_PILLOW_EXTS = {".heic", ".png", ".tiff", ".tif", ".webp", ".bmp"}

# jpegtran path (Homebrew on macOS)
_JPEGTRAN = "/opt/homebrew/bin/jpegtran"


def trash_files(
    file_paths: list[str],
    logger: OperationLogger,
) -> dict[str, Any]:
    """Send files to system Trash.

    Args:
        file_paths: List of absolute file paths to trash.
        logger: Operation logger for undo support.

    Returns:
        Dict with keys:
        - success: List of successfully trashed paths.
        - failed: List of {path, error} for failures.
        - count: Number of files trashed.
    """
    success: list[str] = []
    failed: list[dict[str, str]] = []

    for path_str in file_paths:
        path = Path(path_str)
        if not path.exists():
            failed.append({"path": path_str, "error": "File not found"})
            continue

        try:
            send2trash(str(path))
            logger.log_operation(
                op_type="TRASH",
                file=str(path),
                detail=f"Sent to trash: {path.name}",
            )
            success.append(path_str)
        except Exception as e:
            failed.append({"path": path_str, "error": str(e)})

    return {
        "success": success,
        "failed": failed,
        "count": len(success),
    }


def rename_file(
    old_path: str,
    new_path: str,
    logger: OperationLogger,
) -> dict[str, Any]:
    """Rename a single file.

    Args:
        old_path: Current absolute path.
        new_path: Desired absolute path.
        logger: Operation logger for undo support.

    Returns:
        Dict with keys: old, new, success, error (if any).
    """
    old = Path(old_path)
    new = Path(new_path)

    if not old.exists():
        return {
            "old": old_path,
            "new": new_path,
            "success": False,
            "error": "Source not found",
        }

    if new.exists():
        return {
            "old": old_path,
            "new": new_path,
            "success": False,
            "error": "Target already exists",
        }

    try:
        # Ensure target directory exists
        new.parent.mkdir(parents=True, exist_ok=True)
        os.rename(str(old), str(new))
        logger.log_operation(
            op_type="RENAME",
            file=old_path,
            old_value=old_path,
            new_value=new_path,
            detail=f"Renamed: {old.name} → {new.name}",
        )
        return {"old": old_path, "new": new_path, "success": True}
    except Exception as e:
        return {"old": old_path, "new": new_path, "success": False, "error": str(e)}


def rename_files(
    renames: list[dict[str, str]],
    logger: OperationLogger,
) -> dict[str, Any]:
    """Execute a batch of renames.

    Args:
        renames: List of {old, new} path pairs.
        logger: Operation logger for undo support.

    Returns:
        Dict with keys:
        - results: List of individual rename results.
        - success_count: Number of successful renames.
        - failed_count: Number of failures.
    """
    results: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0

    for r in renames:
        result = rename_file(r["old"], r["new"], logger)
        results.append(result)
        if result["success"]:
            success_count += 1
        else:
            failed_count += 1

    return {
        "results": results,
        "success_count": success_count,
        "failed_count": failed_count,
    }


def fix_orientation(
    file_paths: list[dict[str, Any]],
    logger: OperationLogger,
) -> dict[str, Any]:
    """Reset EXIF orientation to 1 (Normal) for specified files.

    Uses exiftool to overwrite the Orientation tag. Each operation is
    logged for undo support.

    Args:
        file_paths: List of dicts with keys:
            - path: Absolute file path.
            - orientation: Current orientation value (int).
        logger: Operation logger for undo support.

    Returns:
        Dict with keys:
        - success: List of successfully fixed paths.
        - failed: List of {path, error} for failures.
        - count: Number of files fixed.
    """
    success: list[str] = []
    failed: list[dict[str, str]] = []

    for item in file_paths:
        path_str = item["path"]
        old_orientation = item["orientation"]
        path = Path(path_str)

        if not path.exists():
            failed.append({"path": path_str, "error": "File not found"})
            continue

        try:
            result = subprocess.run(
                ["exiftool", "-Orientation=1", "-n", "-overwrite_original", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                failed.append({"path": path_str, "error": result.stderr.strip()})
                continue

            logger.log_operation(
                op_type="ORIENTATION",
                file=str(path),
                old_value=str(old_orientation),
                new_value="1",
                detail=f"Orientation fixed: {old_orientation} → 1 for {path.name}",
            )
            success.append(path_str)
        except subprocess.TimeoutExpired:
            failed.append({"path": path_str, "error": "exiftool timed out"})
        except FileNotFoundError:
            failed.append({"path": path_str, "error": "exiftool not found"})
        except Exception as e:
            failed.append({"path": path_str, "error": str(e)})

    return {
        "success": success,
        "failed": failed,
        "count": len(success),
    }


def _rotate_jpeg(path: Path, rotation: int) -> None:
    """Losslessly rotate a JPEG using jpegtran, then reset EXIF Orientation.

    Args:
        path: Path to the JPEG file.
        rotation: Degrees CW (90, 180, 270).

    Raises:
        RuntimeError: If jpegtran or exiftool fails.
    """
    with tempfile.NamedTemporaryFile(
        suffix=".jpg", dir=path.parent, delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                _JPEGTRAN,
                "-rotate",
                str(rotation),
                "-copy",
                "all",
                "-perfect",
                "-outfile",
                str(tmp_path),
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"jpegtran failed: {result.stderr.strip()}")

        # Replace original with rotated version
        tmp_path.replace(path)

        # Reset EXIF Orientation to 1 (Normal)
        result = subprocess.run(
            ["exiftool", "-Orientation=1", "-n", "-overwrite_original", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            _logger.warning(
                "Failed to reset EXIF orientation for %s: %s",
                path,
                result.stderr.strip(),
            )
    finally:
        tmp_path.unlink(missing_ok=True)


def _rotate_pillow(path: Path, rotation: int) -> None:
    """Rotate an image using Pillow, then reset EXIF Orientation if supported.

    Args:
        path: Path to the image file.
        rotation: Degrees CW (90, 180, 270).

    Raises:
        RuntimeError: If rotation or save fails.
    """
    from PIL import Image

    # PIL transpose constants: ROTATE_90 = 90° CCW, ROTATE_270 = 90° CW
    transpose_map = {
        90: Image.Transpose.ROTATE_270,  # 90° CW
        180: Image.Transpose.ROTATE_180,  # 180°
        270: Image.Transpose.ROTATE_90,  # 270° CW (= 90° CCW)
    }
    transpose_op = transpose_map.get(rotation)
    if transpose_op is None:
        raise RuntimeError(f"Invalid rotation for Pillow: {rotation}")

    ext = path.suffix.lower()

    with Image.open(path) as img:
        fmt = img.format
        rotated = img.transpose(transpose_op)
        rotated.save(path, format=fmt)

    # Reset EXIF Orientation for formats that support it
    if ext in {".heic", ".tiff", ".tif", ".webp"}:
        subprocess.run(
            ["exiftool", "-Orientation=1", "-n", "-overwrite_original", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )


def _rotate_video_metadata(path: Path, rotation: int) -> None:
    """Set rotation metadata on a video file without re-encoding.

    Args:
        path: Path to the video file.
        rotation: Degrees CW (90, 180, 270).

    Raises:
        RuntimeError: If exiftool fails.
    """
    result = subprocess.run(
        [
            "exiftool",
            f"-Rotation={rotation}",
            "-overwrite_original",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"exiftool failed: {result.stderr.strip()}")


def auto_rotate(
    file_items: list[dict[str, Any]],
    logger: OperationLogger,
) -> dict[str, Any]:
    """Auto-rotate images and videos based on AI-detected orientation.

    Strategy per file type:
    - JPEG: lossless rotation via jpegtran + EXIF reset
    - Other images (HEIC, PNG, TIFF, WebP, BMP): Pillow transpose + EXIF reset
    - Video: metadata-only rotation via exiftool (no re-encode)

    Args:
        file_items: List of dicts with keys:
            - path: Absolute file path.
            - rotation: Degrees to rotate CW (90, 180, 270).
        logger: Operation logger for undo support.

    Returns:
        Dict with keys:
        - success: List of successfully rotated paths.
        - failed: List of {path, error} for failures.
        - count: Number of files rotated.
    """
    success: list[str] = []
    failed: list[dict[str, str]] = []

    for item in file_items:
        path_str = item["path"]
        rotation = item["rotation"]
        path = Path(path_str)

        if not path.exists():
            failed.append({"path": path_str, "error": "File not found"})
            continue

        if rotation not in (90, 180, 270):
            failed.append({"path": path_str, "error": f"Invalid rotation: {rotation}"})
            continue

        ext = path.suffix.lower()

        try:
            # Preserve original modification time
            stat = path.stat()
            orig_mtime = stat.st_mtime

            if ext in _JPEG_EXTS:
                _rotate_jpeg(path, rotation)
            elif ext in _PILLOW_EXTS:
                _rotate_pillow(path, rotation)
            elif ext in _VIDEO_EXTS:
                _rotate_video_metadata(path, rotation)
            else:
                failed.append({"path": path_str, "error": f"Unsupported format: {ext}"})
                continue

            # Restore original modification time
            os.utime(path, (stat.st_atime, orig_mtime))

            logger.log_operation(
                op_type="ORIENTATION",
                file=str(path),
                old_value="1",
                new_value=f"rotated {rotation}°",
                detail=f"Auto-rotated {rotation}° CW: {path.name}",
            )
            success.append(path_str)
        except subprocess.TimeoutExpired:
            failed.append({"path": path_str, "error": "Command timed out"})
        except FileNotFoundError as e:
            failed.append({"path": path_str, "error": f"Tool not found: {e}"})
        except RuntimeError as e:
            failed.append({"path": path_str, "error": str(e)})
        except Exception as e:
            failed.append({"path": path_str, "error": str(e)})

    return {
        "success": success,
        "failed": failed,
        "count": len(success),
    }


def fix_file_dates(
    file_items: list[dict[str, Any]],
    logger: OperationLogger,
) -> dict[str, Any]:
    """Set file modification times to match EXIF or filename dates.

    Args:
        file_items: List of dicts with keys:
            - path: Absolute file path.
            - target_date: ISO format target date string.
        logger: Operation logger for undo support.

    Returns:
        Dict with keys:
        - success: List of successfully fixed paths.
        - failed: List of {path, error} for failures.
        - count: Number of files fixed.
    """
    success: list[str] = []
    failed: list[dict[str, str]] = []

    for item in file_items:
        path_str = item["path"]
        target_date_str = item["target_date"]
        path = Path(path_str)

        if not path.exists():
            failed.append({"path": path_str, "error": "File not found"})
            continue

        try:
            # Parse target date
            target_dt = datetime.fromisoformat(target_date_str)
            target_ts = target_dt.timestamp()

            # Get current times
            stat = path.stat()
            old_mtime = stat.st_mtime
            old_mtime_dt = datetime.fromtimestamp(old_mtime, tz=timezone.utc)

            # Set new mtime (preserve atime)
            os.utime(path, (stat.st_atime, target_ts))

            logger.log_operation(
                op_type="DATE_FIX",
                file=str(path),
                old_value=old_mtime_dt.isoformat(),
                new_value=target_date_str,
                detail=f"Date fixed: {old_mtime_dt.strftime('%Y-%m-%d %H:%M:%S')} "
                f"→ {target_dt.strftime('%Y-%m-%d %H:%M:%S')} for {path.name}",
            )
            success.append(path_str)
        except ValueError as e:
            failed.append({"path": path_str, "error": f"Invalid date: {e}"})
        except OSError as e:
            failed.append({"path": path_str, "error": str(e)})

    return {
        "success": success,
        "failed": failed,
        "count": len(success),
    }
