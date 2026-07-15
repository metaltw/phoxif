"""Execute operations — trash, rename, and other file actions."""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from send2trash import send2trash

from phoxif.api.logger import OperationLogger
from phoxif.api.exif_writer import SafeEditError, write_tags


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
    warnings: list[dict[str, str]] = []

    for item in file_paths:
        path_str = item["path"]
        old_orientation = item["orientation"]
        path = Path(path_str)

        if not path.exists():
            failed.append({"path": path_str, "error": "File not found"})
            continue

        operation: dict[str, Any] | None = None
        edit_applied = False
        try:
            operation = logger.log_operation(
                op_type="ORIENTATION",
                file=str(path),
                old_value=str(old_orientation),
                new_value="1",
                detail=f"Pending orientation fix for {path.name}",
                status="pending",
            )
            logger.save()
            old_values = write_tags(path, {"Orientation": 1}, numeric=True)
            edit_applied = True
            operation["old_value"] = str(old_values.get("Orientation") or old_orientation)
            try:
                logger.mark_operation(
                    operation,
                    "completed",
                    detail=f"Orientation fixed: {old_orientation} → 1 for {path.name}",
                )
            except OSError as log_error:
                warnings.append(
                    {
                        "path": path_str,
                        "warning": (
                            "Edit applied; completion log remains pending: "
                            f"{log_error}"
                        ),
                    }
                )
            success.append(path_str)
        except (FileNotFoundError, SafeEditError, OSError) as e:
            if operation is not None and not edit_applied:
                try:
                    logger.mark_operation(
                        operation,
                        "failed",
                        detail=f"Orientation fix failed: {e}",
                    )
                except OSError:
                    pass
            failed.append({"path": path_str, "error": str(e)})

    return {
        "success": success,
        "failed": failed,
        "warnings": warnings,
        "count": len(success),
    }


def auto_rotate(
    file_items: list[dict[str, Any]],
    logger: OperationLogger,
) -> dict[str, Any]:
    """Refuse pixel rotation until a byte-safe undo design is available.

    Args:
        file_items: List of dicts with keys:
            - path: Absolute file path.
            - rotation: Degrees to rotate CW (90, 180, 270).
        logger: Reserved for the future reversible implementation.

    Returns:
        Dict with keys:
        - success: List of successfully rotated paths.
        - failed: List of {path, error} for failures.
        - count: Number of files rotated.
    """
    del logger
    error = (
        "Temporarily unavailable: pixel rotation is disabled until its undo "
        "can restore the original file safely"
    )
    return {
        "success": [],
        "failed": [{"path": item.get("path", ""), "error": error} for item in file_items],
        "count": 0,
    }


def move_non_photos(
    file_items: list[dict[str, Any]],
    base_dir: str,
    logger: OperationLogger,
) -> dict[str, Any]:
    """Move non-photo files to category subfolders.

    Moves files to `_non_photos/{category}/` under the base directory.
    Creates directories as needed. Preserves file modification times.

    Args:
        file_items: List of dicts with keys:
            - path: Absolute file path.
            - category: Category string (screenshot, messaging, etc.).
        base_dir: Root scan directory for creating subfolders.
        logger: Operation logger for undo support.

    Returns:
        Dict with keys:
        - success: List of successfully moved paths.
        - failed: List of {path, error} for failures.
        - count: Number of files moved.
    """
    success: list[str] = []
    failed: list[dict[str, str]] = []
    non_photos_dir = Path(base_dir) / "_non_photos"

    for item in file_items:
        path_str = item["path"]
        category = item["category"]
        path = Path(path_str)

        if not path.exists():
            failed.append({"path": path_str, "error": "File not found"})
            continue

        target_dir = non_photos_dir / category
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name

        # Handle name collisions (cap at 9999 to avoid infinite loop)
        if target.exists():
            stem = path.stem
            ext = path.suffix
            for counter in range(1, 10000):
                target = target_dir / f"{stem}_{counter}{ext}"
                if not target.exists():
                    break
            else:
                failed.append({"path": path_str, "error": "Too many name collisions"})
                continue

        try:
            stat = path.stat()
            shutil.move(str(path), str(target))

            # Preserve modification time (best effort)
            try:
                os.utime(target, (stat.st_atime, stat.st_mtime))
            except OSError:
                pass

            logger.log_operation(
                op_type="MOVE",
                file=path_str,
                old_value=path_str,
                new_value=str(target),
                detail=f"Moved to _non_photos/{category}/: {path.name}",
            )
            success.append(path_str)
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
