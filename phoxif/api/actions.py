"""Execute operations — trash, rename, and other file actions."""

import os
import subprocess
from pathlib import Path
from typing import Any

from send2trash import send2trash

from phoxif.api.logger import OperationLogger


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
