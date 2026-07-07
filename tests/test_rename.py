"""Tests for phoxif.api.rename — EXIF-date-based rename preview generation."""

from typing import Any

from phoxif.api.rename import generate_rename_preview


def _file(filename: str, date: Any, directory: str = "/tmp/phoxif-test") -> dict[str, Any]:
    """Build a minimal normalized file-info dict as generate_rename_preview expects."""
    return {
        "path": f"{directory}/{filename}",
        "filename": filename,
        "extension": f".{filename.rsplit('.', 1)[-1]}",
        "directory": directory,
        "date": date,
    }


def test_generates_date_based_name_from_exif_date():
    files = [_file("IMG_0001.jpg", "2026:03:05 14:30:22")]

    previews = generate_rename_preview(files)

    assert len(previews) == 1
    assert previews[0]["old_name"] == "IMG_0001.jpg"
    assert previews[0]["new_name"] == "20260305_143022.jpg"


def test_same_second_collision_gets_numbered_suffix():
    files = [
        _file("IMG_0001.jpg", "2026:03:05 14:30:22"),
        _file("IMG_0002.jpg", "2026:03:05 14:30:22"),
    ]

    previews = generate_rename_preview(files)
    new_names = sorted(p["new_name"] for p in previews)

    # rename.py:93-96 — every file in a colliding bucket gets a numeric
    # suffix (including the first), there is no un-suffixed "winner".
    assert new_names == ["20260305_143022_1.jpg", "20260305_143022_2.jpg"]


def test_numeric_timestamp_date_is_skipped():
    # rename.py:27-29 — numeric (fallback-stat) timestamps are considered
    # unreliable for renaming and are skipped entirely.
    files = [_file("IMG_0003.jpg", 1709650222.0)]

    previews = generate_rename_preview(files)

    assert previews == []


def test_already_date_named_file_is_skipped():
    files = [_file("20260305_143022.jpg", "2026:03:05 14:30:22")]

    previews = generate_rename_preview(files)

    assert previews == []
