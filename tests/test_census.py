"""Tests for the read-only multi-source photo census."""

import shutil
from pathlib import Path

import pytest

from phoxif.pipeline.census import scan_sources


def test_multi_source_census_finds_cross_source_duplicate(make_jpeg, tmp_path: Path):
    old_laptop = tmp_path / "old-laptop"
    phone_export = tmp_path / "phone-export"
    original = make_jpeg("IMG_0001.jpg", directory=old_laptop)
    phone_export.mkdir()
    shutil.copy2(original, phone_export / "copy.jpg")

    result = scan_sources([old_laptop, phone_export], mode="rescue")

    assert result["stats"]["total_files"] == 2
    assert result["stats"]["ready_to_collect"] == 1
    assert len(result["sources"]) == 2
    assert result["duplicate_stats"]["groups"] == 1
    assert result["duplicate_stats"]["total_duplicates"] == 1
    assert {file["source_label"] for file in result["duplicates"][0]["files"]} == {
        "old-laptop",
        "phone-export",
    }


def test_messaging_photo_is_collection_asset_not_non_photo(make_jpeg, tmp_path: Path):
    inbox = tmp_path / "wechat-inbox"
    make_jpeg("mmexport1705312245678.jpg", directory=inbox)

    result = scan_sources([inbox], mode="inbox")

    assert result["stats"]["messaging_files"] == 1
    assert len(result["messaging_files"]) == 1
    assert result["non_photos"] == []


def test_census_does_not_modify_source_files(make_jpeg, tmp_path: Path):
    source = tmp_path / "source"
    photo = make_jpeg("LINE_ALBUM_20240115_123456.jpg", directory=source)
    before = (photo.read_bytes(), photo.stat().st_mtime_ns)

    scan_sources([source], mode="rescue")

    assert (photo.read_bytes(), photo.stat().st_mtime_ns) == before


def test_scan_sources_rejects_unreadable_root(make_jpeg, tmp_path: Path):
    locked = tmp_path / "locked"
    make_jpeg("IMG_0002.jpg", directory=locked)
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError, match="Permission denied"):
            scan_sources([locked], mode="rescue")
    finally:
        locked.chmod(0o755)


def test_scan_sources_rejects_nested_sources(make_jpeg, tmp_path: Path):
    parent = tmp_path / "library"
    child = parent / "trip"
    make_jpeg("IMG_0003.jpg", directory=child)

    with pytest.raises(ValueError, match="Sources overlap"):
        scan_sources([parent, child], mode="rescue")
