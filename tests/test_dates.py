"""Tests for the table-driven date confidence ladder."""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from phoxif.pipeline.dates import (
    DateEvidence,
    interpolate,
    parse_filename,
    parse_folder,
    parse_mtime,
)

ZONE = "Asia/Taipei"
EARLIEST = datetime(1995, 1, 1, tzinfo=ZoneInfo(ZONE))
NOW = datetime(2026, 7, 15, tzinfo=ZoneInfo(ZONE))


def test_camera_filename_has_level_two_provenance() -> None:
    evidence = parse_filename(
        "IMG_20230405_120000.jpg",
        timezone_name=ZONE,
        earliest=EARLIEST,
        now=NOW,
    )

    assert evidence is not None
    assert evidence.exif_value == "2023:04:05 12:00:00"
    assert evidence.confidence == 2
    assert evidence.keywords == [
        "phoxif:date-estimated",
        "phoxif:date-src:filename-date",
    ]


def test_mmexport_epoch_uses_configured_timezone() -> None:
    evidence = parse_filename(
        "mmexport1705312245678.jpg",
        timezone_name=ZONE,
        earliest=EARLIEST,
        now=NOW,
    )

    assert evidence is not None
    expected = datetime.fromtimestamp(1705312245.678, tz=timezone.utc).astimezone(ZoneInfo(ZONE))
    assert evidence.value == expected
    assert evidence.source == "filename-epoch"
    assert evidence.confidence == 3


def test_whatsapp_day_precision_is_explicit() -> None:
    evidence = parse_filename(
        "IMG-20240203-WA0012.jpg",
        timezone_name=ZONE,
        earliest=EARLIEST,
        now=NOW,
    )

    assert evidence is not None
    assert evidence.exif_value == "2024:02:03 12:00:00"
    assert "phoxif:date-precision:day" in evidence.keywords


def test_future_and_pre_wechat_epoch_are_rejected() -> None:
    assert (
        parse_filename(
            "mmexport1262304000000.jpg",
            timezone_name=ZONE,
            earliest=EARLIEST,
            now=NOW,
        )
        is None
    )
    assert (
        parse_filename(
            "IMG_20300101_120000.jpg",
            timezone_name=ZONE,
            earliest=EARLIEST,
            now=NOW,
        )
        is None
    )


def test_folder_prefers_specific_day_and_marks_precision() -> None:
    evidence = parse_folder(
        Path("/fixture/2024/2024-02-03 trip/photo.jpg"),
        timezone_name=ZONE,
        earliest=EARLIEST,
        now=NOW,
    )

    assert evidence is not None
    assert evidence.exif_value == "2024:02:03 12:00:00"
    assert evidence.precision == "day"


def test_mtime_is_lowest_confidence_and_timezone_normalized() -> None:
    evidence = parse_mtime(
        "2024-01-02T03:04:05+00:00",
        timezone_name=ZONE,
        earliest=EARLIEST,
        now=NOW,
    )

    assert evidence is not None
    assert evidence.exif_value == "2024:01:02 11:04:05"
    assert evidence.confidence == 6


def test_neighbor_interpolation_requires_a_tight_bracket() -> None:
    before_mtime = datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    after_mtime = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
    before = DateEvidence(
        datetime(2024, 2, 1, 10, tzinfo=timezone.utc),
        "native-exif",
        1,
        False,
    )
    after = DateEvidence(
        datetime(2024, 2, 1, 12, tzinfo=timezone.utc),
        "filename-date",
        2,
        True,
    )

    result = interpolate(
        (before_mtime, before),
        datetime(2024, 1, 1, 11, tzinfo=timezone.utc),
        (after_mtime, after),
    )

    assert result is not None
    assert result.value == datetime(2024, 2, 1, 11, tzinfo=timezone.utc)
    assert result.source == "batch-interp"
