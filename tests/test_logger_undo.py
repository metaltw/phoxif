"""Tests for phoxif.api.logger — operation log persistence and undo."""

import json
from pathlib import Path
import subprocess

import pytest

from phoxif.api.logger import OperationLogger
from phoxif.api.actions import fix_orientation


def test_rename_undo_restores_original_filename(tmp_path: Path):
    old_path = tmp_path / "IMG_0001.jpg"
    old_path.write_bytes(b"fake-jpeg-bytes")
    new_path = tmp_path / "20260305_143022.jpg"
    old_path.rename(new_path)

    logger = OperationLogger(tmp_path)
    logger.start_session()
    logger.log_operation(
        "RENAME",
        str(new_path),
        old_value=str(old_path),
        new_value=str(new_path),
    )
    logger.save()

    assert logger.log_path.exists()

    results = logger.undo_session(0)

    assert results[0]["success"] is True
    assert old_path.exists() is True
    assert new_path.exists() is False


def test_undo_session_twice_raises_value_error(tmp_path: Path):
    old_path = tmp_path / "a.jpg"
    old_path.write_bytes(b"data")
    new_path = tmp_path / "b.jpg"
    old_path.rename(new_path)

    logger = OperationLogger(tmp_path)
    logger.start_session()
    logger.log_operation(
        "RENAME", str(new_path), old_value=str(old_path), new_value=str(new_path)
    )
    logger.save()
    logger.undo_session(0)

    with pytest.raises(ValueError):
        logger.undo_session(0)


def test_undo_session_out_of_range_raises_index_error(tmp_path: Path):
    logger = OperationLogger(tmp_path)

    with pytest.raises(IndexError):
        logger.undo_session(0)


def test_orientation_undo_uses_safe_writer(make_jpeg, tmp_path: Path):
    photo = make_jpeg("portrait.jpg", exif={"Orientation#": "6"})
    logger = OperationLogger(tmp_path)
    logger.start_session()

    result = fix_orientation(
        [{"path": str(photo), "orientation": 6}],
        logger,
    )
    logger.save()

    assert result["count"] == 1
    assert subprocess.run(
        ["exiftool", "-s3", "-n", "-Orientation", str(photo)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip() == "1"

    undo = logger.undo_session(0)

    assert undo[0]["success"] is True
    assert subprocess.run(
        ["exiftool", "-s3", "-n", "-Orientation", str(photo)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip() == "6"


def test_gps_undo_restores_decimal_coordinates(make_jpeg, tmp_path: Path):
    photo = make_jpeg("located.jpg")
    logger = OperationLogger(tmp_path)
    logger.start_session()
    logger.log_operation(
        "GPS",
        str(photo),
        old_value="25.033,121.565",
        new_value="0,0",
    )
    logger.save()

    result = logger.undo_session(0)
    metadata = subprocess.run(
        [
            "exiftool",
            "-j",
            "-n",
            "-GPSLatitude",
            "-GPSLongitude",
            str(photo),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert result[0]["success"] is True
    assert '"GPSLatitude": 25.033' in metadata
    assert '"GPSLongitude": 121.565' in metadata


def test_gps_undo_restores_southwest_coordinates(make_jpeg, tmp_path: Path):
    photo = make_jpeg("southwest.jpg")
    logger = OperationLogger(tmp_path)
    logger.start_session()
    logger.log_operation(
        "GPS",
        str(photo),
        old_value="-25.033,-121.565",
        new_value="0,0",
    )
    logger.save()

    result = logger.undo_session(0)
    metadata = json.loads(
        subprocess.run(
            [
                "exiftool",
                "-j",
                "-n",
                "-GPSLatitude",
                "-GPSLongitude",
                str(photo),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )[0]

    assert result[0]["success"] is True
    assert metadata["GPSLatitude"] == pytest.approx(-25.033)
    assert metadata["GPSLongitude"] == pytest.approx(-121.565)


def test_completion_log_failure_keeps_applied_edit_pending(make_jpeg, tmp_path: Path):
    photo = make_jpeg("pending.jpg", exif={"Orientation#": "6"})
    logger = OperationLogger(tmp_path)
    real_save = logger.save
    save_calls = 0

    def fail_second_save() -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise OSError("simulated completion persistence failure")
        real_save()

    logger.save = fail_second_save  # type: ignore[method-assign]

    result = fix_orientation([{"path": str(photo), "orientation": 6}], logger)

    assert result["count"] == 1
    assert result["failed"] == []
    assert len(result["warnings"]) == 1
    persisted = OperationLogger(tmp_path)
    assert persisted.get_sessions()[0]["operations"][0]["status"] == "pending"
    assert persisted.undo_session(0)[0]["success"] is True


def test_partial_undo_can_retry_without_reapplying_success(tmp_path: Path):
    first_old = tmp_path / "first-old.jpg"
    first_new = tmp_path / "first-new.jpg"
    second_old = tmp_path / "second-old.jpg"
    second_new = tmp_path / "second-new.jpg"
    first_new.write_bytes(b"first")

    logger = OperationLogger(tmp_path)
    logger.start_session()
    logger.log_operation(
        "RENAME", str(first_new), old_value=str(first_old), new_value=str(first_new)
    )
    logger.log_operation(
        "RENAME", str(second_new), old_value=str(second_old), new_value=str(second_new)
    )
    logger.save()

    first_attempt = logger.undo_session(0)
    assert [result["success"] for result in first_attempt] == [False, True]
    assert logger.get_sessions()[0]["undone"] is False
    assert first_old.read_bytes() == b"first"

    second_new.write_bytes(b"second")
    second_attempt = logger.undo_session(0)

    assert [result["success"] for result in second_attempt] == [True, True]
    assert logger.get_sessions()[0]["undone"] is True
    assert first_old.read_bytes() == b"first"
    assert second_old.read_bytes() == b"second"


def test_convert_undo_retry_accepts_already_absent_output(monkeypatch, tmp_path: Path):
    converted = tmp_path / "converted.mp4"
    converted.write_bytes(b"converted")
    logger = OperationLogger(tmp_path)
    logger.start_session()
    logger.log_operation(
        "CONVERT",
        str(tmp_path / "original.mov"),
        old_value=str(tmp_path / "original.mov"),
        new_value=str(converted),
    )
    logger.save()
    real_save = logger.save
    save_calls = 0

    def fail_completed_checkpoint() -> None:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise OSError("simulated checkpoint failure")
        real_save()

    def fake_trash(path: str) -> None:
        Path(path).unlink()

    logger.save = fail_completed_checkpoint  # type: ignore[method-assign]
    monkeypatch.setattr("send2trash.send2trash", fake_trash)

    with pytest.raises(OSError, match="checkpoint failure"):
        logger.undo_session(0)
    assert not converted.exists()

    restarted = OperationLogger(tmp_path)
    retry = restarted.undo_session(0)

    assert retry[0]["success"] is True
    assert restarted.get_sessions()[0]["undone"] is True
