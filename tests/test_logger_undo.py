"""Tests for phoxif.api.logger — operation log persistence and undo."""

from pathlib import Path

import pytest

from phoxif.api.logger import OperationLogger


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
