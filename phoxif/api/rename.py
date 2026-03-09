"""Batch rename-by-date — generate preview for date-based file renaming."""

import re
from collections import defaultdict
from pathlib import Path
from typing import Any


# Pattern matching YYYYMMDD_HHMMSS with optional suffix
_DATE_NAME_RE = re.compile(r"^\d{8}_\d{6}(_\d+)?$")


def _parse_date_string(date_val: str | float | None) -> str | None:
    """Parse a date value into YYYYMMDD_HHMMSS format.

    Handles exiftool formats like "2026:03:05 14:30:22" and numeric timestamps.

    Args:
        date_val: Date from normalized file info (string or float).

    Returns:
        Formatted date string "YYYYMMDD_HHMMSS" or None if unparseable.
    """
    if date_val is None:
        return None

    if isinstance(date_val, (int, float)):
        # Numeric timestamp (from fallback file stats) — skip, not reliable
        return None

    if not isinstance(date_val, str):
        return None

    # exiftool format: "2026:03:05 14:30:22" or "2026:03:05 14:30:22+08:00"
    # Strip timezone suffix if present
    clean_str = date_val.split("+")[0]
    # exiftool uses colons: "2026:03:05 14:30:22"
    match = re.match(r"(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})", clean_str)
    if not match:
        return None

    y, mo, d, h, mi, s = match.groups()
    return f"{y}{mo}{d}_{h}{mi}{s}"


def generate_rename_preview(
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate a rename preview for date-based renaming.

    For each file with a DateTimeOriginal (or fallback date), generates a new
    filename in YYYYMMDD_HHMMSS format. Handles collisions by appending _1, _2, etc.
    Skips files that already match the date-name pattern.

    Args:
        files: Normalized file info list from scanner (scan_folder result).

    Returns:
        List of rename preview dicts, each with:
        - file: Original file info dict.
        - old_name: Current filename.
        - new_name: Proposed new filename (with extension).
        - new_path: Full absolute path for the new name.
    """
    # First pass: compute desired base names and detect collisions
    # key = (directory, base_name_without_suffix), value = list of file info dicts
    name_buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    file_to_base: dict[str, str] = {}  # path -> base date string

    for f in files:
        date_str = _parse_date_string(f.get("date"))
        if date_str is None:
            continue

        stem = Path(f["filename"]).stem
        # Skip files already named in date format
        if _DATE_NAME_RE.match(stem):
            continue

        directory = f.get("directory", str(Path(f["path"]).parent))
        name_buckets[(directory, date_str)].append(f)
        file_to_base[f["path"]] = date_str

    # Second pass: build preview with collision suffixes
    previews: list[dict[str, Any]] = []

    for (directory, base_name), bucket in name_buckets.items():
        for idx, f in enumerate(bucket):
            ext = f.get("extension", Path(f["filename"]).suffix.lower())
            if not ext.startswith("."):
                ext = f".{ext}"

            if len(bucket) > 1:
                suffix = f"_{idx + 1}"
            else:
                suffix = ""

            new_name = f"{base_name}{suffix}{ext}"
            new_path = str(Path(directory) / new_name)

            previews.append(
                {
                    "file": f,
                    "old_name": f["filename"],
                    "new_name": new_name,
                    "new_path": new_path,
                }
            )

    return previews
