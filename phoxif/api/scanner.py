"""Scan & detect module — reads metadata and finds duplicates."""

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_exiftool_metadata(
    base_dir: Path, extensions: set[str]
) -> list[dict[str, Any]]:
    """Batch-read metadata via exiftool -json -r.

    Args:
        base_dir: Root directory to scan.
        extensions: File extensions to include (e.g., {".jpg", ".mov"}).

    Returns:
        List of metadata dicts from exiftool.
    """
    ext_args: list[str] = []
    for ext in extensions:
        # exiftool uses -ext jpg (without dot)
        ext_args.extend(["-ext", ext.lstrip(".")])

    cmd = ["exiftool", "-json", "-r", "-n", *ext_args, str(base_dir)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0 and not result.stdout:
        return []

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def _fallback_file_stats(base_dir: Path, extensions: set[str]) -> list[dict[str, Any]]:
    """Read basic file stats without exiftool.

    Args:
        base_dir: Root directory to scan.
        extensions: File extensions to include.

    Returns:
        List of basic file info dicts.
    """
    results: list[dict[str, Any]] = []
    for file_path in base_dir.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in extensions:
            continue
        if file_path.name.startswith("."):
            continue

        try:
            stat = file_path.stat()
        except OSError:
            continue

        results.append(
            {
                "SourceFile": str(file_path),
                "FileName": file_path.name,
                "FileSize": stat.st_size,
                "FileModifyDate": stat.st_mtime,
                "Directory": str(file_path.parent),
            }
        )

    return results


def _normalize_file_info(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract a consistent file info dict from exiftool output.

    Args:
        raw: Single file metadata dict from exiftool or fallback.

    Returns:
        Normalized file info dict.
    """
    source = raw.get("SourceFile", "")
    filename = raw.get("FileName", Path(source).name if source else "")
    extension = Path(filename).suffix.lower() if filename else ""
    return {
        "path": source,
        "filename": filename,
        "extension": extension,
        "size": raw.get("FileSize", 0),
        "date": raw.get("DateTimeOriginal")
        or raw.get("CreateDate")
        or raw.get("FileModifyDate"),
        "gps_lat": raw.get("GPSLatitude"),
        "gps_lon": raw.get("GPSLongitude"),
        "orientation": raw.get("Orientation"),
        "width": raw.get("ImageWidth"),
        "height": raw.get("ImageHeight"),
        "codec": raw.get("CompressorID")
        or raw.get("VideoCodecID")
        or raw.get("CompressorName"),
        "duration": raw.get("Duration"),
        "directory": raw.get("Directory", ""),
        "mime_type": raw.get("MIMEType", ""),
    }


def scan_folder(
    base_dir: Path,
    extensions: set[str] | None = None,
) -> dict[str, Any]:
    """Scan a folder and return all files with metadata.

    Args:
        base_dir: Root directory to scan.
        extensions: File extensions to process. Defaults to common photo/video types.

    Returns:
        Dict with keys:
        - files: List of normalized file info dicts.
        - stats: Summary statistics.
        - exiftool_available: Whether exiftool was used.
    """
    if extensions is None:
        extensions = {".jpg", ".jpeg", ".heic", ".png", ".mov", ".mp4"}

    # Normalize extensions to lowercase with dot prefix
    extensions = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions
    }

    # Try exiftool first, fallback to basic stats
    exiftool_available = True
    try:
        raw_metadata = _read_exiftool_metadata(base_dir, extensions)
    except FileNotFoundError:
        # exiftool not installed
        exiftool_available = False
        raw_metadata = []

    if not raw_metadata:
        if exiftool_available:
            # exiftool ran but found nothing — might be empty folder
            # Still try fallback for basic stats
            pass
        exiftool_available = False
        raw_metadata = _fallback_file_stats(base_dir, extensions)

    files = [_normalize_file_info(r) for r in raw_metadata]

    # Compute stats
    total_size = sum(f["size"] for f in files)
    photo_exts = {".jpg", ".jpeg", ".heic", ".png", ".tiff", ".tif", ".webp"}
    video_exts = {".mov", ".mp4", ".avi", ".mkv", ".m4v"}

    photos = [f for f in files if Path(f["path"]).suffix.lower() in photo_exts]
    videos = [f for f in files if Path(f["path"]).suffix.lower() in video_exts]
    with_gps = [
        f for f in files if f["gps_lat"] is not None and f["gps_lon"] is not None
    ]

    stats = {
        "total_files": len(files),
        "total_size": total_size,
        "photo_count": len(photos),
        "video_count": len(videos),
        "with_gps": len(with_gps),
        "without_gps": len(files) - len(with_gps),
    }

    return {
        "files": files,
        "stats": stats,
        "exiftool_available": exiftool_available,
    }


def _compute_md5(file_path: Path, chunk_size: int = 8192) -> str:
    """Compute MD5 hash of a file using chunked reading.

    Args:
        file_path: Path to the file.
        chunk_size: Read chunk size in bytes (default 8KB).

    Returns:
        Hex digest string.
    """
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            md5.update(chunk)
    return md5.hexdigest()


def find_duplicates(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find groups of duplicate files by MD5 hash.

    Args:
        files: List of normalized file info dicts (from scan_folder).

    Returns:
        List of duplicate groups, each with:
        - hash: MD5 hex digest
        - files: List of file info dicts in this group
        - count: Number of files
        - wasted_size: Total size minus one copy
    """
    # First pass: group by size (only same-size files can be duplicates)
    size_groups: dict[int, list[dict[str, Any]]] = {}
    for f in files:
        size = f["size"]
        if size == 0:
            continue
        size_groups.setdefault(size, []).append(f)

    # Second pass: compute MD5 only for size-collision files
    hash_groups: dict[str, list[dict[str, Any]]] = {}
    for group in size_groups.values():
        if len(group) < 2:
            continue
        for f in group:
            try:
                file_hash = _compute_md5(Path(f["path"]))
            except OSError:
                continue
            hash_groups.setdefault(file_hash, []).append(f)

    # Filter to actual duplicates (2+ files with same hash)
    duplicates: list[dict[str, Any]] = []
    for file_hash, group in hash_groups.items():
        if len(group) < 2:
            continue
        file_size = group[0]["size"]
        duplicates.append(
            {
                "hash": file_hash,
                "files": group,
                "count": len(group),
                "wasted_size": file_size * (len(group) - 1),
            }
        )

    return duplicates


# Orientation value to human-readable label mapping
_ORIENTATION_LABELS: dict[int, str] = {
    1: "Normal",
    2: "Mirrored horizontal",
    3: "Rotated 180°",
    4: "Mirrored vertical",
    5: "Mirrored horizontal & rotated 270° CW",
    6: "Rotated 90° CW",
    7: "Mirrored horizontal & rotated 90° CW",
    8: "Rotated 90° CCW",
}


def find_exif_orientation_issues(
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find photo files with non-normal EXIF orientation.

    Quick pre-check based on EXIF tags only (no AI). Filters to photo
    files and returns those whose orientation tag is not 1 (Normal).

    Args:
        files: List of normalized file info dicts (from scan_folder).

    Returns:
        List of dicts, each with:
        - file: The original file info dict.
        - current_orientation: Integer orientation value.
        - label: Human-readable orientation description.
    """
    photo_exts = {".jpg", ".jpeg", ".heic", ".png", ".tiff", ".tif", ".webp"}
    issues: list[dict[str, Any]] = []

    for f in files:
        ext = Path(f["path"]).suffix.lower()
        if ext not in photo_exts:
            continue

        orientation = f.get("orientation")
        if orientation is None or orientation == 1:
            continue

        # Ensure orientation is an int for consistent handling
        try:
            orientation_int = int(orientation)
        except (TypeError, ValueError):
            continue

        issues.append(
            {
                "file": f,
                "current_orientation": orientation_int,
                "label": _ORIENTATION_LABELS.get(
                    orientation_int, f"Unknown ({orientation_int})"
                ),
            }
        )

    return issues


# Regex for extracting date from filenames like YYYYMMDD_HHMMSS or YYYY-MM-DD_HH-MM-SS
_FILENAME_DATE_RE = re.compile(
    r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})[-_](\d{2})[-_]?(\d{2})[-_]?(\d{2})"
)


def _parse_exif_date(date_val: str | float | int | None) -> float | None:
    """Parse an EXIF date value to a UNIX timestamp.

    Handles:
    - "YYYY:MM:DD HH:MM:SS" (standard EXIF format)
    - "YYYY-MM-DDTHH:MM:SS" (ISO format)
    - float/int (already a unix timestamp)
    - None

    Args:
        date_val: Raw date value from EXIF or fallback scanner.

    Returns:
        UNIX timestamp as float, or None if unparseable.
    """
    if date_val is None:
        return None

    if isinstance(date_val, (int, float)):
        return float(date_val)

    if not isinstance(date_val, str):
        return None

    # EXIF format: "YYYY:MM:DD HH:MM:SS"
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(date_val, fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue

    return None


def _parse_filename_date(filename: str) -> float | None:
    """Try to extract a date from a filename pattern.

    Matches patterns like: 20240101_120000, 2024-01-01_12-00-00, IMG_20240101_120000.

    Args:
        filename: The filename (not full path).

    Returns:
        UNIX timestamp as float, or None if no date pattern found.
    """
    m = _FILENAME_DATE_RE.search(filename)
    if not m:
        return None

    try:
        dt = datetime(
            year=int(m.group(1)),
            month=int(m.group(2)),
            day=int(m.group(3)),
            hour=int(m.group(4)),
            minute=int(m.group(5)),
            second=int(m.group(6)),
            tzinfo=timezone.utc,
        )
        return dt.timestamp()
    except ValueError:
        return None


def find_date_mismatches(
    files: list[dict[str, Any]], tolerance_sec: int = 2
) -> list[dict[str, Any]]:
    """Find files whose mtime doesn't match their EXIF or filename date.

    Args:
        files: List of normalized file info dicts (from scan_folder).
        tolerance_sec: Maximum allowed difference in seconds.

    Returns:
        List of mismatch dicts, each with:
        - file: The original file info dict.
        - exif_date: ISO string of the target date.
        - file_mtime: ISO string of the current file mtime.
        - source: "exif" or "filename".
    """
    mismatches: list[dict[str, Any]] = []

    for f in files:
        file_path = f.get("path", "")
        if not file_path:
            continue

        # Get file mtime
        try:
            file_mtime = os.path.getmtime(file_path)
        except OSError:
            continue

        # Try EXIF date first
        exif_ts = _parse_exif_date(f.get("date"))
        source = "exif"

        # If no EXIF date, try filename
        if exif_ts is None:
            exif_ts = _parse_filename_date(f.get("filename", ""))
            source = "filename"

        # Skip if no date available
        if exif_ts is None:
            continue

        # Check mismatch
        if abs(file_mtime - exif_ts) > tolerance_sec:
            target_dt = datetime.fromtimestamp(exif_ts, tz=timezone.utc)
            mtime_dt = datetime.fromtimestamp(file_mtime, tz=timezone.utc)
            mismatches.append(
                {
                    "file": f,
                    "exif_date": target_dt.isoformat(),
                    "file_mtime": mtime_dt.isoformat(),
                    "source": source,
                }
            )

    return mismatches
