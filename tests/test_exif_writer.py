"""Safety tests for the single EXIF/file mutation choke point."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from phoxif.api.exif_writer import (
    SafeEditError,
    _validate_file,
    edit_file,
    read_tags,
    write_tags,
)


def _make_corrupt_mp4(path: Path) -> None:
    """Create an MP4 with a valid container but an undecodable mdat payload."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.5",
            "-c:v",
            "mpeg4",
            "-y",
            str(path),
        ],
        check=True,
    )
    payload = bytearray(path.read_bytes())
    marker = payload.find(b"mdat")
    assert marker > 4
    atom_size = int.from_bytes(payload[marker - 4 : marker], "big")
    payload[marker + 4 : marker - 4 + atom_size] = b"\0" * (atom_size - 8)
    path.write_bytes(payload)


def _orientation(path: Path) -> str:
    result = subprocess.run(
        ["exiftool", "-s3", "-n", "-Orientation", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_write_tags_verifies_and_preserves_mtime(make_jpeg, tmp_path: Path):
    photo = make_jpeg("rotated.jpg", exif={"Orientation#": "6"})
    original_mtime = 1_700_000_000.0
    photo.touch()
    photo.chmod(0o640)
    import os

    os.utime(photo, (original_mtime, original_mtime))

    old_values = write_tags(photo, {"Orientation": 1}, numeric=True)

    assert old_values["Orientation"] == 6
    assert _orientation(photo) == "1"
    assert photo.stat().st_mtime == pytest.approx(original_mtime)
    assert photo.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob(".phoxif-edit-*")) == []


def test_failed_edit_leaves_original_byte_identical(make_jpeg, tmp_path: Path):
    photo = make_jpeg("original.jpg")
    original = photo.read_bytes()

    def corrupt_then_fail(temp_path: Path) -> None:
        temp_path.write_bytes(b"corrupt")
        raise RuntimeError("simulated editor failure")

    with pytest.raises(RuntimeError, match="simulated editor failure"):
        edit_file(photo, corrupt_then_fail)

    assert photo.read_bytes() == original
    assert list(tmp_path.glob(".phoxif-edit-*")) == []


def test_write_tags_preserves_multiple_provenance_keywords(make_jpeg):
    photo = make_jpeg("estimated.jpg")
    keywords = [
        "phoxif:date-estimated",
        "phoxif:date-src:filename-epoch",
    ]

    write_tags(
        photo,
        {
            "DateTimeOriginal": "2024:01:15 17:50:45",
            "CreateDate": "2024:01:15 17:50:45",
            "IPTC:Keywords": keywords,
            "XMP-dc:Subject": keywords,
        },
    )

    result = subprocess.run(
        ["exiftool", "-j", "-DateTimeOriginal", "-Keywords", "-Subject", str(photo)],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)[0]
    assert payload["DateTimeOriginal"] == "2024:01:15 17:50:45"
    assert payload["Keywords"] == keywords
    assert payload["Subject"] == keywords


def test_corrupt_mp4_is_rejected_even_when_container_is_recognized(tmp_path: Path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")

    with pytest.raises(SafeEditError, match="decode validation"):
        _validate_file(broken)


def test_container_valid_but_payload_corrupt_mp4_is_rejected(tmp_path: Path):
    broken = tmp_path / "payload-corrupt.mp4"
    _make_corrupt_mp4(broken)

    with pytest.raises(SafeEditError, match="decode validation"):
        _validate_file(broken)


def test_heic_validation_requires_real_decode(monkeypatch, tmp_path: Path):
    from phoxif.api import exif_writer

    heic = tmp_path / "truncated.heic"
    heic.write_bytes(b"recognized-container")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs):
        commands.append(command)
        if command[0] == "exiftool":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps([{"FileType": "HEIC"}]),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 13, stdout="", stderr="decode failed")

    monkeypatch.setattr(exif_writer.subprocess, "run", fake_run)
    monkeypatch.setattr(exif_writer.shutil, "which", lambda _tool: "/usr/bin/sips")

    with pytest.raises(SafeEditError, match="decode validation"):
        _validate_file(heic)

    assert any(command[1:4] == ["-s", "format", "png"] for command in commands)


def test_quicktime_date_uses_explicit_timezone_and_keeps_xmp_provenance(
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required")
    video = tmp_path / "clip.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:d=0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ],
        check=True,
    )
    keywords = ["phoxif:date-estimated", "phoxif:date-src:filename-date"]

    write_tags(
        video,
        {
            "QuickTime:CreateDate": "2024:01:15 10:00:00",
            "XMP-dc:Subject": keywords,
        },
        quicktime_utc=True,
        timezone_name="Pacific/Honolulu",
    )

    local = read_tags(
        video,
        ["QuickTime:CreateDate", "XMP-dc:Subject"],
        quicktime_utc=True,
        timezone_name="Pacific/Honolulu",
    )
    utc = read_tags(
        video,
        ["QuickTime:CreateDate"],
        quicktime_utc=True,
        timezone_name="UTC",
    )
    assert local["QuickTime:CreateDate"].startswith("2024:01:15 10:00:00")
    assert local["XMP-dc:Subject"] == keywords
    assert utc["QuickTime:CreateDate"].startswith("2024:01:15 20:00:00")
