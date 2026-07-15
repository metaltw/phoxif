"""Tests that destructive GUI actions remain reversible or disabled."""

from pathlib import Path

from phoxif.api.actions import auto_rotate
from phoxif.api.logger import OperationLogger


def test_pixel_rotation_is_disabled_until_reversible(make_jpeg, tmp_path: Path):
    photo = make_jpeg("landscape.jpg")
    before = photo.read_bytes()
    logger = OperationLogger(tmp_path)

    result = auto_rotate([{"path": str(photo), "rotation": 90}], logger)

    assert result["count"] == 0
    assert result["success"] == []
    assert result["failed"][0]["error"].startswith("Temporarily unavailable")
    assert photo.read_bytes() == before
    assert logger.get_sessions() == []
