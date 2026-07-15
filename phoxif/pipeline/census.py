"""Read-only census for one or more photo sources."""

from pathlib import Path
from typing import Any, Literal

from phoxif.api.classifier import CATEGORY_MESSAGING, classify_non_photos
from phoxif.api.rename import generate_rename_preview
from phoxif.api.scanner import (
    find_date_mismatches,
    find_duplicates,
    find_exif_orientation_issues,
    scan_folder,
)
from phoxif.api.similar import find_similar_groups

IntakeMode = Literal["rescue", "inbox"]


def scan_sources(
    roots: list[Path],
    *,
    mode: IntakeMode,
    extensions: set[str] | None = None,
) -> dict[str, Any]:
    """Inspect sources without writing, moving, renaming, or deleting any file.

    Args:
        roots: Unique source directories to inspect.
        mode: ``rescue`` for historical collections or ``inbox`` for chat imports.
        extensions: Optional media extensions accepted by the scanner.

    Returns:
        Combined source census and review queues for the GUI.

    Raises:
        ValueError: If no roots are supplied or the mode is invalid.
        NotADirectoryError: If a root is not an existing directory.
    """
    if not roots:
        raise ValueError("At least one photo source is required")
    if mode not in {"rescue", "inbox"}:
        raise ValueError(f"Unsupported intake mode: {mode}")

    unique_roots: list[Path] = []
    seen_roots: set[Path] = set()
    for raw_root in roots:
        root = raw_root.expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)
        if root not in seen_roots:
            seen_roots.add(root)
            unique_roots.append(root)

    all_files: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    exiftool_available = True
    for root in unique_roots:
        result = scan_folder(root, extensions)
        source_files = [
            {**file_info, "source_root": str(root), "source_label": root.name}
            for file_info in result["files"]
        ]
        all_files.extend(source_files)
        exiftool_available = exiftool_available and result["exiftool_available"]
        source_summaries.append(
            {
                "path": str(root),
                "label": root.name,
                **result["stats"],
            }
        )

    duplicates = find_duplicates(all_files)
    similar_groups = find_similar_groups(all_files)
    classified = classify_non_photos(all_files)
    messaging_files = [
        item for item in classified if item["category"] == CATEGORY_MESSAGING
    ]
    # Messaging photos are core collection assets, never "non-photo" move candidates.
    non_photos = [
        item for item in classified if item["category"] != CATEGORY_MESSAGING
    ]
    rename_preview = generate_rename_preview(all_files)
    orientation_issues = find_exif_orientation_issues(all_files)
    date_mismatches = find_date_mismatches(all_files)
    missing_dates = [
        file_info
        for file_info in all_files
        if file_info.get("date") is None
        or isinstance(file_info.get("date"), (int, float))
    ]

    total_size = sum(file_info["size"] for file_info in all_files)
    photo_extensions = {".jpg", ".jpeg", ".heic", ".png", ".tiff", ".tif", ".webp"}
    video_extensions = {".mov", ".mp4", ".avi", ".mkv", ".m4v"}
    duplicate_copies = sum(group["count"] - 1 for group in duplicates)
    stats = {
        "total_files": len(all_files),
        "total_size": total_size,
        "photo_count": sum(file_info["extension"] in photo_extensions for file_info in all_files),
        "video_count": sum(file_info["extension"] in video_extensions for file_info in all_files),
        "with_gps": sum(
            file_info["gps_lat"] is not None and file_info["gps_lon"] is not None
            for file_info in all_files
        ),
        "without_gps": sum(
            file_info["gps_lat"] is None or file_info["gps_lon"] is None
            for file_info in all_files
        ),
        "ready_to_collect": len(all_files) - duplicate_copies,
        "missing_dates": len(missing_dates),
        "messaging_files": len(messaging_files),
    }

    return {
        "mode": mode,
        "base_dir": str(unique_roots[0]),
        "base_dirs": [str(root) for root in unique_roots],
        "sources": source_summaries,
        "files": all_files,
        "stats": stats,
        "duplicates": duplicates,
        "duplicate_stats": {
            "groups": len(duplicates),
            "total_duplicates": duplicate_copies,
            "wasted_size": sum(group["wasted_size"] for group in duplicates),
        },
        "similar_groups": similar_groups,
        "similar_stats": {
            "groups": len(similar_groups),
            "total_similar": sum(group["count"] for group in similar_groups),
            "reclaimable_size": sum(group["reclaimable_size"] for group in similar_groups),
        },
        "rename_preview": rename_preview,
        "rename_stats": {"renameable": len(rename_preview)},
        "exif_orientation_issues": orientation_issues,
        "exif_orientation_stats": {"issues_count": len(orientation_issues)},
        "date_mismatches": date_mismatches,
        "date_stats": {
            "mismatches": len(date_mismatches),
            "missing": len(missing_dates),
            "total_checked": len(all_files),
        },
        "missing_date_files": missing_dates,
        "messaging_files": messaging_files,
        "non_photos": non_photos,
        "non_photo_stats": {
            "total": len(non_photos),
            "by_category": {
                category: sum(item["category"] == category for item in non_photos)
                for category in {item["category"] for item in non_photos}
            },
        },
        "exiftool_available": exiftool_available,
    }
