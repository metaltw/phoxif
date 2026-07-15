"""Regression tests for safe video conversion finalization."""

import shutil
import subprocess
from pathlib import Path

import pytest

from phoxif import convert


def _make_corrupt_mp4(path: Path) -> None:
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


def test_validation_failure_never_trashes_source(monkeypatch, tmp_path: Path):
    source = tmp_path / "clip.mov"
    source.write_bytes(b"original-video")
    trashed: list[str] = []

    monkeypatch.setattr(convert, "_run_conversion", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        convert,
        "validate_conversion",
        lambda _source, _output: (False, "duration mismatch"),
    )
    monkeypatch.setattr(convert, "send2trash", lambda path: trashed.append(str(path)))

    assert convert.convert_file(source) is False
    assert source.read_bytes() == b"original-video"
    assert trashed == []


def test_existing_destination_is_never_overwritten(monkeypatch, tmp_path: Path):
    source = tmp_path / "clip.mov"
    destination = tmp_path / "clip.mp4"
    source.write_bytes(b"original-video")
    destination.write_bytes(b"existing-library-file")
    conversion_called = False

    def fake_conversion(*_args, **_kwargs):
        nonlocal conversion_called
        conversion_called = True
        return True

    monkeypatch.setattr(convert, "_run_conversion", fake_conversion)

    assert convert.convert_file(source) is False
    assert conversion_called is False
    assert source.read_bytes() == b"original-video"
    assert destination.read_bytes() == b"existing-library-file"


def test_racing_destination_is_not_deleted(monkeypatch, tmp_path: Path):
    source = tmp_path / "clip.mov"
    destination = tmp_path / "clip.mp4"
    source.write_bytes(b"original-video")

    def fake_conversion(_source: Path, _temporary: Path, *_args) -> bool:
        _temporary.write_bytes(b"converted-video")
        destination.write_bytes(b"created-by-another-process")
        return True

    monkeypatch.setattr(convert, "_run_conversion", fake_conversion)
    monkeypatch.setattr(
        convert,
        "validate_conversion",
        lambda _source, _output: (True, "verified"),
    )

    assert convert.convert_file(source) is False
    assert source.read_bytes() == b"original-video"
    assert destination.read_bytes() == b"created-by-another-process"


def test_conversion_validation_rejects_undecodable_payload(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    _make_corrupt_mp4(source)
    shutil.copy2(source, output)

    monkeypatch.setattr(
        convert,
        "_probe_media",
        lambda _path: {"duration": 0.5, "video_streams": 1, "audio_streams": 0},
    )

    valid, detail = convert.validate_conversion(source, output)

    assert valid is False
    assert "full decode" in detail


def test_conversion_uses_private_owned_work_directory(monkeypatch, tmp_path: Path):
    source = tmp_path / "clip.mov"
    source.write_bytes(b"original-video")
    observed: dict[str, Path] = {}

    def fake_conversion(_source: Path, temporary: Path, *_args) -> bool:
        observed["temporary"] = temporary
        assert temporary.parent.parent == tmp_path
        assert temporary.parent.name.startswith(".phoxif-convert-")
        assert not temporary.exists()
        temporary.write_bytes(b"converted-video")
        return True

    monkeypatch.setattr(convert, "_run_conversion", fake_conversion)
    monkeypatch.setattr(convert, "validate_conversion", lambda *_args: (True, "verified"))
    monkeypatch.setattr(convert, "send2trash", lambda _path: None)

    assert convert.convert_file(source) is True
    assert (tmp_path / "clip.mp4").read_bytes() == b"converted-video"
    assert not observed["temporary"].parent.exists()


def test_in_place_mp4_recompression_is_refused_before_conversion(monkeypatch, tmp_path: Path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original-video")
    conversion_called = False

    def fake_conversion(*_args) -> bool:
        nonlocal conversion_called
        conversion_called = True
        return True

    monkeypatch.setattr(convert, "_run_conversion", fake_conversion)

    assert convert.convert_file(source) is False
    assert conversion_called is False
    assert source.read_bytes() == b"original-video"
