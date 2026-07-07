"""Shared pytest fixtures for phoxif tests.

All fixtures generate data on the fly under tmp_path — no binary fixture
files are committed to this public repo.
"""

import subprocess
from pathlib import Path
from typing import Callable

import pytest
from PIL import Image

MakeJpeg = Callable[..., Path]


@pytest.fixture
def make_jpeg(tmp_path: Path) -> MakeJpeg:
    """Factory fixture that creates a small JPEG, optionally with real EXIF tags.

    Returns:
        A callable ``make_jpeg(name, exif=None, directory=None) -> Path`` where:
        - name: filename to create (e.g. "photo.jpg").
        - exif: optional dict of {ExifToolTagName: value} written via the
          real `exiftool` CLI (e.g. {"DateTimeOriginal": "2024:01:15 10:30:45"}).
        - directory: parent directory (defaults to the test's tmp_path).
    """

    def _make(
        name: str,
        exif: dict[str, str] | None = None,
        directory: Path | None = None,
    ) -> Path:
        target_dir = directory if directory is not None else tmp_path
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / name

        image = Image.new("RGB", (32, 32), color=(120, 200, 80))
        image.save(path, "JPEG")

        if exif:
            cmd = ["exiftool", "-overwrite_original", "-q", "-q"]
            cmd.extend(f"-{tag}={value}" for tag, value in exif.items())
            cmd.append(str(path))
            subprocess.run(cmd, capture_output=True, text=True, check=True)

        return path

    return _make
