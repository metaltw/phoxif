"""Tests for phoxif.api.scanner — filename date pattern and metadata date priority."""

from pathlib import Path

from phoxif.api.scanner import _FILENAME_DATE_RE, _normalize_file_info, scan_folder


def test_filename_date_re_matches_underscore_format():
    m = _FILENAME_DATE_RE.search("IMG_20240115_103045.jpg")
    assert m is not None
    assert m.groups() == ("2024", "01", "15", "10", "30", "45")


def test_filename_date_re_matches_hyphen_format():
    m = _FILENAME_DATE_RE.search("Screenshot_20240115-103045.png")
    assert m is not None
    assert m.groups() == ("2024", "01", "15", "10", "30", "45")


def test_filename_date_re_does_not_match_mmexport_timestamp():
    # "mmexport1705312245678" is one continuous 13-digit run with no
    # date/time separator, so it must not be mistaken for a date-named file.
    assert _FILENAME_DATE_RE.search("mmexport1705312245678.jpg") is None


def test_normalize_file_info_prefers_datetimeoriginal_over_others():
    raw = {
        "SourceFile": "/tmp/x/a.jpg",
        "FileName": "a.jpg",
        "DateTimeOriginal": "2024:01:15 10:30:45",
        "CreateDate": "2024:01:16 00:00:00",
        "FileModifyDate": "2024:01:17 00:00:00",
    }
    assert _normalize_file_info(raw)["date"] == "2024:01:15 10:30:45"


def test_normalize_file_info_falls_back_to_createdate():
    raw = {
        "SourceFile": "/tmp/x/a.jpg",
        "FileName": "a.jpg",
        "CreateDate": "2024:01:16 00:00:00",
        "FileModifyDate": "2024:01:17 00:00:00",
    }
    assert _normalize_file_info(raw)["date"] == "2024:01:16 00:00:00"


def test_normalize_file_info_falls_back_to_filemodifydate():
    raw = {
        "SourceFile": "/tmp/x/a.jpg",
        "FileName": "a.jpg",
        "FileModifyDate": "2024:01:17 00:00:00",
    }
    assert _normalize_file_info(raw)["date"] == "2024:01:17 00:00:00"


def test_scan_folder_date_priority_via_real_exiftool(make_jpeg, tmp_path: Path):
    """Integration: scan_folder must surface DateTimeOriginal over CreateDate."""
    make_jpeg(
        "photo.jpg",
        exif={
            "DateTimeOriginal": "2024:03:05 14:30:22",
            "CreateDate": "2024:03:06 00:00:00",
        },
    )

    result = scan_folder(tmp_path, extensions={".jpg"})

    assert result["exiftool_available"] is True
    assert len(result["files"]) == 1
    assert result["files"][0]["date"] == "2024:03:05 14:30:22"
