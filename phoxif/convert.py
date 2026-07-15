"""Convert video files to HEVC .mp4 with metadata preservation.

Uses ffmpeg with hardware-accelerated HEVC encoding (VideoToolbox on macOS)
and preserves all EXIF/metadata from the original file.

Usage:
    python -m phoxif.convert --config config.yaml [--dry-run] [--recompress]
"""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from send2trash import send2trash

from phoxif.config import load_config

VIDEO_EXTS = {".mov", ".mp4"}
HEVC_CODEC = "hvc1"


def get_codec(filepath: Path) -> str:
    """Get video compressor ID using exiftool.

    Args:
        filepath: Path to the video file.

    Returns:
        Compressor ID string (e.g., "hvc1", "avc1", "ap4h").
    """
    result = subprocess.run(
        ["exiftool", "-s3", "-CompressorID", str(filepath)],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _parse_date_from_filename(name: str) -> str | None:
    """Extract EXIF-format date from YYYYMMDD_HHMMSS filename.

    Returns:
        Date string like "2024:02:21 04:52:03", or None if unparseable.
    """
    stem = Path(name).stem
    if (
        len(stem) >= 15
        and stem[8] == "_"
        and stem[:8].isdigit()
        and stem[9:15].isdigit()
    ):
        return f"{stem[0:4]}:{stem[4:6]}:{stem[6:8]} {stem[9:11]}:{stem[11:13]}:{stem[13:15]}"
    return None


def _run_checked(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    """Run a conversion subprocess and fail on missing tools or non-zero status."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"{label} not found") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{label} timed out") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{label} failed: {detail[-500:]}")
    return result


def _run_conversion(
    src: Path,
    dst: Path,
    quality: int,
    audio_bitrate: str,
) -> bool:
    """Create a converted output without changing or removing the source."""
    try:
        _run_checked(
            [
                "ffmpeg",
                "-n",
                "-i",
                str(src),
                "-c:v",
                "hevc_videotoolbox",
                "-q:v",
                str(quality),
                "-tag:v",
                "hvc1",
                "-c:a",
                "aac",
                "-b:a",
                audio_bitrate,
                "-movflags",
                "+faststart",
                "-map_metadata",
                "0",
                str(dst),
            ],
            "ffmpeg",
        )

        _run_checked(
            [
                "exiftool",
                "-overwrite_original",
                "-TagsFromFile",
                str(src),
                "-All:All",
                str(dst),
            ],
            "exiftool metadata copy",
        )

        create_date = _run_checked(
            ["exiftool", "-s3", "-CreateDate", str(dst)],
            "exiftool CreateDate read",
        ).stdout.strip()

        if create_date and create_date != "0000:00:00 00:00:00":
            _run_checked(
                [
                    "exiftool",
                    "-overwrite_original",
                    "-FileModifyDate<CreateDate",
                    str(dst),
                ],
                "exiftool mtime update",
            )
        else:
            date_from_name = _parse_date_from_filename(dst.name)
            if date_from_name:
                _run_checked(
                    [
                        "exiftool",
                        "-overwrite_original",
                        f"-CreateDate={date_from_name}",
                        f"-FileModifyDate={date_from_name}",
                        str(dst),
                    ],
                    "exiftool filename date update",
                )
                print(f"    INFO: CreateDate empty, set from filename: {date_from_name}")
            else:
                print(f"    WARNING: No CreateDate and cannot parse filename: {dst.name}")
    except RuntimeError as error:
        print(f"    CONVERSION ERROR: {error}")
        return False
    return True


def _probe_media(path: Path) -> dict[str, Any]:
    """Read duration and stream counts using ffprobe."""
    result = _run_checked(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        "ffprobe",
    )
    try:
        payload = json.loads(result.stdout)
        duration = float(payload["format"]["duration"])
        streams = payload.get("streams", [])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"ffprobe returned incomplete data for {path.name}") from error
    return {
        "duration": duration,
        "video_streams": sum(stream.get("codec_type") == "video" for stream in streams),
        "audio_streams": sum(stream.get("codec_type") == "audio" for stream in streams),
    }


def _read_preserved_metadata(path: Path) -> dict[str, Any]:
    """Read metadata fields that must survive conversion."""
    result = _run_checked(
        [
            "exiftool",
            "-j",
            "-n",
            "-CreateDate",
            "-GPSLatitude",
            "-GPSLongitude",
            str(path),
        ],
        "exiftool validation read",
    )
    try:
        return json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError(f"exiftool returned invalid metadata for {path.name}") from error


def _decode_media(path: Path) -> None:
    """Decode every audio/video packet so container-only corruption cannot pass."""
    _run_checked(
        [
            "ffmpeg",
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
        "ffmpeg full decode validation",
    )


def validate_conversion(src: Path, dst: Path) -> tuple[bool, str]:
    """Prove converted output parity before the source may enter Trash."""
    try:
        if not dst.is_file() or dst.stat().st_size == 0:
            return False, "converted output is missing or empty"

        source_probe = _probe_media(src)
        output_probe = _probe_media(dst)
        duration_delta = abs(source_probe["duration"] - output_probe["duration"])
        if duration_delta >= 1.0:
            return False, f"duration mismatch ({duration_delta:.3f}s)"
        for stream_type in ("video_streams", "audio_streams"):
            if source_probe[stream_type] != output_probe[stream_type]:
                return False, f"{stream_type.replace('_', ' ')} mismatch"

        _decode_media(dst)

        source_metadata = _read_preserved_metadata(src)
        output_metadata = _read_preserved_metadata(dst)
        source_date = source_metadata.get("CreateDate")
        if source_date and output_metadata.get("CreateDate") != source_date:
            return False, "CreateDate was not preserved"
        for gps_tag in ("GPSLatitude", "GPSLongitude"):
            source_gps = source_metadata.get(gps_tag)
            output_gps = output_metadata.get(gps_tag)
            if source_gps is not None and (
                output_gps is None or abs(float(source_gps) - float(output_gps)) > 1e-6
            ):
                return False, f"{gps_tag} was not preserved"
    except (OSError, RuntimeError, ValueError) as error:
        return False, str(error)
    return True, "full decode, duration, streams, CreateDate, and GPS verified"


def convert_file(src: Path, quality: int = 55, audio_bitrate: str = "128k") -> bool:
    """Convert a single video to HEVC and trash the source only after verification.

    Args:
        src: Source video file path.
        quality: VideoToolbox quality parameter (lower = better quality).
        audio_bitrate: Audio bitrate for AAC encoding.

    Returns:
        True on successful conversion.
    """
    if not src.is_file():
        print(f"    ERROR: Source not found: {src}")
        return False

    final_path = src if src.suffix.lower() == ".mp4" else src.with_suffix(".mp4")
    if final_path == src:
        print(
            "    ERROR: In-place MP4 recompression is disabled because an atomic "
            "compare-and-swap cannot be guaranteed"
        )
        return False
    if final_path != src and final_path.exists():
        print(
            f"    ERROR: Destination already exists, refusing to overwrite: {final_path.name}"
        )
        return False

    with tempfile.TemporaryDirectory(prefix=".phoxif-convert-", dir=src.parent) as work_dir:
        temporary = Path(work_dir) / "converted.mp4"
        try:
            if not _run_conversion(src, temporary, quality, audio_bitrate):
                return False

            is_valid, validation_detail = validate_conversion(src, temporary)
            if not is_valid:
                print(f"    VALIDATION FAILED: {validation_detail}; original preserved")
                return False

            try:
                # Hard-link publication is atomic and fails instead of overwriting a race winner.
                os.link(temporary, final_path)
                try:
                    send2trash(str(src))
                except Exception:
                    # Remove only the inode this process published, never a path race winner.
                    if final_path.exists() and os.path.samestat(
                        final_path.stat(), temporary.stat()
                    ):
                        final_path.unlink()
                    raise
            except FileExistsError:
                print(
                    "    FINALIZE ERROR: Destination appeared during conversion: "
                    f"{final_path.name}"
                )
                return False
        except (OSError, RuntimeError) as error:
            print(f"    FINALIZE ERROR: {error}; original preserved")
            return False

    print(f"    VERIFIED: {validation_detail}")

    return True


def main(argv: list[str] | None = None) -> None:
    """Entry point for HEVC video conversion."""
    parser = argparse.ArgumentParser(
        description="Convert video files to HEVC .mp4 with metadata preservation."
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--base-dir", default=None, help="Override base_dir from config"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List files without converting"
    )
    parser.add_argument(
        "--recompress",
        action="store_true",
        help="Also re-encode existing HEVC .mov to .mp4 (saves ~50%%)",
    )
    args = parser.parse_args(argv)

    cfg: dict[str, Any] = load_config(args.config, base_dir_override=args.base_dir)
    base_dir: Path = cfg["base_dir"]
    skip_dirs: set[str] = cfg["skip_dirs"]
    hevc_cfg = cfg["hevc"]

    quality: int = hevc_cfg["quality"]
    audio_bitrate: str = hevc_cfg["audio_bitrate"]
    min_h264_size = hevc_cfg["min_h264_size_mb"] * 1024 * 1024
    recompress = args.recompress or hevc_cfg["recompress_mov"]

    # Collect target video files
    targets: list[tuple[Path, str, int]] = []
    for folder in sorted(base_dir.iterdir()):
        if (
            not folder.is_dir()
            or folder.name in skip_dirs
            or folder.name.startswith(".")
        ):
            continue
        for f in sorted(folder.iterdir()):
            if (
                not f.is_file()
                or f.suffix.lower() not in VIDEO_EXTS
                or f.stat().st_size == 0
            ):
                continue
            codec = get_codec(f)
            if codec != HEVC_CODEC:
                # Non-HEVC: always convert (skip small H.264 below threshold)
                if codec == "avc1" and f.stat().st_size < min_h264_size:
                    continue
                targets.append((f, codec, f.stat().st_size))
            elif recompress and f.suffix.lower() == ".mov":
                # HEVC .mov: recompress to .mp4
                targets.append((f, codec, f.stat().st_size))

    total_size = sum(s for _, _, s in targets)
    print(
        f"Found {len(targets)} non-HEVC videos, total {total_size / (1024**3):.1f} GB"
    )
    print()

    if args.dry_run:
        for f, codec, size in targets:
            print(f"  {size / (1024**2):.0f}MB  {codec:6s}  {f.relative_to(base_dir)}")
        print("\nDry run complete. Use without --dry-run to convert.")
        return

    converted = 0
    saved_bytes = 0
    for i, (f, codec, orig_size) in enumerate(targets):
        print(
            f"[{i + 1}/{len(targets)}] {f.relative_to(base_dir)} ({orig_size / (1024**2):.0f}MB, {codec})"
        )
        if convert_file(f, quality=quality, audio_bitrate=audio_bitrate):
            new_path = f.with_suffix(".mp4")
            if new_path.exists():
                new_size = new_path.stat().st_size
                saved = orig_size - new_size
                saved_bytes += saved
                print(
                    f"    -> {new_size / (1024**2):.0f}MB (saved {saved / (1024**2):.0f}MB)"
                )
            converted += 1
        else:
            print("    FAILED")

    print(
        f"\nDone! {converted}/{len(targets)} converted, saved {saved_bytes / (1024**3):.1f} GB"
    )


if __name__ == "__main__":
    main()
