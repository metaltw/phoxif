"""Safe mutation choke point for user-owned media files.

Every metadata or pixel edit in the GUI must pass through this module. The
original path is replaced only after a same-directory temporary copy has been
edited and verified successfully.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from PIL import Image

Editor = Callable[[Path], None]

_PIL_VERIFY_EXTENSIONS = {
    ".bmp",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
_VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4"}


class SafeEditError(RuntimeError):
    """Raised when a temporary media edit cannot be proven safe."""


def read_tags(path: Path, tags: list[str], *, numeric: bool = False) -> dict[str, Any]:
    """Read selected tags with ExifTool.

    Args:
        path: Media file to inspect.
        tags: ExifTool tag names without leading dashes.
        numeric: Request raw numeric values from ExifTool.

    Returns:
        Mapping from requested tag name to value or ``None``.
    """
    command = ["exiftool", "-j"]
    if numeric:
        command.append("-n")
    command.extend(f"-{tag}" for tag in tags)
    command.append(str(path))
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as error:
        raise SafeEditError("exiftool not found") from error
    except subprocess.TimeoutExpired as error:
        raise SafeEditError("exiftool timed out") from error
    if result.returncode != 0:
        raise SafeEditError(f"exiftool read failed: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)[0]
    except (IndexError, KeyError, json.JSONDecodeError) as error:
        raise SafeEditError("exiftool returned invalid JSON") from error
    return {tag: payload.get(tag) for tag in tags}


def _values_match(actual: Any, expected: Any) -> bool:
    """Compare ExifTool values without hiding meaningful differences."""
    if actual == expected:
        return True
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) < 1e-9
    return str(actual) == str(expected)


def _validate_file(path: Path) -> None:
    """Verify that an edited file remains non-empty and readable."""
    if not path.is_file() or path.stat().st_size == 0:
        raise SafeEditError("edited file is missing or empty")

    # ExifTool provides a format-independent structural read for all media.
    try:
        result = subprocess.run(
            ["exiftool", "-j", "-FileType", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as error:
        raise SafeEditError("exiftool not found") from error
    except subprocess.TimeoutExpired as error:
        raise SafeEditError("exiftool timed out") from error
    if result.returncode != 0 or not result.stdout.strip():
        raise SafeEditError(f"edited file failed ExifTool validation: {result.stderr.strip()}")
    try:
        file_type = json.loads(result.stdout)[0].get("FileType")
    except (IndexError, json.JSONDecodeError) as error:
        raise SafeEditError("edited file failed ExifTool structural validation") from error
    if not file_type:
        raise SafeEditError("edited file has no recognized media type")

    extension = path.suffix.lower()
    if extension in _PIL_VERIFY_EXTENSIONS:
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            raise SafeEditError(f"edited image failed decode validation: {error}") from error
    elif extension in _VIDEO_EXTENSIONS:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise SafeEditError("ffmpeg is required to validate edited video files")
        try:
            decoded = subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(path),
                    "-map",
                    "0:v?",
                    "-map",
                    "0:a?",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=3600,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise SafeEditError("ffmpeg timed out while validating edited video") from error
        if decoded.returncode != 0:
            detail = decoded.stderr.strip() or "media payload could not be fully decoded"
            raise SafeEditError(f"edited video failed decode validation: {detail}")
    elif extension == ".heic":
        sips = shutil.which("sips")
        if sips is None:
            raise SafeEditError("sips is required to validate edited HEIC files")
        with tempfile.TemporaryDirectory(
            prefix=".phoxif-heic-validate-",
            dir=path.parent,
        ) as decode_dir:
            decoded_path = Path(decode_dir) / "decoded.png"
            try:
                decoded = subprocess.run(
                    [
                        sips,
                        "-s",
                        "format",
                        "png",
                        str(path),
                        "--out",
                        str(decoded_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise SafeEditError("sips timed out while decoding edited HEIC") from error
            if decoded.returncode != 0 or not decoded_path.is_file():
                detail = decoded.stderr.strip() or "HEIC payload could not be decoded"
                raise SafeEditError(f"edited HEIC failed decode validation: {detail}")
            try:
                with Image.open(decoded_path) as image:
                    image.verify()
            except Exception as error:
                raise SafeEditError(f"edited HEIC failed decode validation: {error}") from error


def edit_file(
    path: Path,
    editor: Editor,
    *,
    tags: Mapping[str, Any] | None = None,
    numeric: bool = False,
) -> None:
    """Edit a temporary copy, verify it, then atomically replace the original.

    Args:
        path: User-owned file to edit.
        editor: Callback that mutates only the supplied temporary path.
        tags: Optional tags to write to the temporary copy and read back.
        numeric: Read expected tags using ExifTool numeric mode.

    Raises:
        FileNotFoundError: If the original path does not exist.
        SafeEditError: If validation or tag read-back fails.
        Exception: Any editor error, after the temporary file is cleaned up.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    original_stat = path.stat()
    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=".phoxif-edit-",
        suffix=path.suffix,
        dir=path.parent,
    )
    os.close(file_descriptor)
    temp_path = Path(temp_name)

    try:
        shutil.copy2(path, temp_path)
        editor(temp_path)
        if tags:
            _apply_tags(temp_path, tags, numeric=numeric)
        _validate_file(temp_path)

        if tags:
            expected_tags = {
                tag: None if expected == "" else expected
                for tag, expected in tags.items()
            }
            actual_tags = read_tags(temp_path, list(tags), numeric=numeric)
            for tag, expected in expected_tags.items():
                if not _values_match(actual_tags.get(tag), expected):
                    raise SafeEditError(
                        f"tag verification failed for {tag}: "
                        f"expected {expected!r}, got {actual_tags.get(tag)!r}"
                    )

        os.chmod(temp_path, original_stat.st_mode)
        os.utime(
            temp_path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _apply_tags(path: Path, tags: Mapping[str, Any], *, numeric: bool) -> None:
    """Write tags in-place to a temporary file owned by this module."""
    command = ["exiftool", "-overwrite_original"]
    if numeric:
        command.append("-n")
    command.extend(f"-{tag}={value}" for tag, value in tags.items())
    command.append(str(path))
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as error:
        raise SafeEditError("exiftool not found") from error
    except subprocess.TimeoutExpired as error:
        raise SafeEditError("exiftool timed out") from error
    if result.returncode != 0:
        raise SafeEditError(f"exiftool write failed: {result.stderr.strip()}")


def write_tags(
    path: Path,
    tags: Mapping[str, Any],
    *,
    numeric: bool = False,
) -> dict[str, Any]:
    """Safely write and read back metadata tags.

    Args:
        path: User-owned media file.
        tags: ExifTool tag/value mapping.
        numeric: Use numeric values for write and verification.

    Returns:
        Original values for each requested tag, suitable for operation logging.
    """
    if not tags:
        return {}
    old_values = read_tags(path, list(tags), numeric=numeric)

    edit_file(path, lambda _temp_path: None, tags=tags, numeric=numeric)
    return old_values
