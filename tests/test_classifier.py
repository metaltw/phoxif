"""Tests for phoxif.api.classifier — filename/metadata based non-photo classification."""

from typing import Any

from phoxif.api.classifier import (
    CATEGORY_MESSAGING,
    CATEGORY_SCREENSHOT,
    classify_non_photos,
)


def _file(filename: str, **overrides: Any) -> dict[str, Any]:
    """Build a minimal normalized file-info dict as classify_non_photos expects."""
    info: dict[str, Any] = {
        "path": f"/tmp/phoxif-test/{filename}",
        "filename": filename,
        "width": None,
        "height": None,
        "gps_lat": None,
    }
    info.update(overrides)
    return info


def _categories(files: list[dict[str, Any]]) -> dict[str, str]:
    """Map filename -> assigned category for easy lookup in assertions."""
    results = classify_non_photos(files)
    return {r["file"]["filename"]: r["category"] for r in results}


def test_wechat_mmexport_13_digit_is_messaging():
    files = [_file("mmexport1705312245678.jpg")]
    assert _categories(files)["mmexport1705312245678.jpg"] == CATEGORY_MESSAGING


def test_line_underscore_prefix_is_messaging():
    files = [_file("LINE_ALBUM_20240115_123456.jpg")]
    assert _categories(files)["LINE_ALBUM_20240115_123456.jpg"] == CATEGORY_MESSAGING


def test_line_hyphen_prefix_is_messaging():
    files = [_file("LINE-20240115-123456.jpg")]
    assert _categories(files)["LINE-20240115-123456.jpg"] == CATEGORY_MESSAGING


def test_android_screenshot_pattern():
    files = [_file("Screenshot_20240115-103045.png")]
    assert (
        _categories(files)["Screenshot_20240115-103045.png"] == CATEGORY_SCREENSHOT
    )


def test_macos_screenshot_pattern():
    files = [_file("Screenshot 2024-01-15 at 10.30.45.png")]
    assert (
        _categories(files)["Screenshot 2024-01-15 at 10.30.45.png"]
        == CATEGORY_SCREENSHOT
    )


def test_regular_camera_filename_not_classified_as_non_photo():
    files = [_file("IMG_1234.jpg")]
    assert "IMG_1234.jpg" not in _categories(files)


def test_date_named_filename_not_classified_as_non_photo():
    files = [_file("20230405_120000.jpg")]
    assert "20230405_120000.jpg" not in _categories(files)


def test_unsupported_extension_is_ignored_even_if_name_matches():
    # ".txt" is in neither _PHOTO_EXTS nor _VIDEO_EXTS, so classify_non_photos
    # skips it outright regardless of filename pattern (classifier.py:236-237).
    files = [_file("mmexport1705312245678.txt")]
    assert "mmexport1705312245678.txt" not in _categories(files)
