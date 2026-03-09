"""Convert video files to HEVC .mp4 with metadata preservation.

Uses ffmpeg with hardware-accelerated HEVC encoding (VideoToolbox on macOS)
and preserves all EXIF/metadata from the original file.

Usage:
    python -m phoxif.convert --config config.yaml [--dry-run] [--recompress]
"""

import argparse
import subprocess
from pathlib import Path
from typing import Any

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


def convert_file(src: Path, quality: int = 55, audio_bitrate: str = "128k") -> bool:
    """Convert a single video file to HEVC .mp4.

    Args:
        src: Source video file path.
        quality: VideoToolbox quality parameter (lower = better quality).
        audio_bitrate: Audio bitrate for AAC encoding.

    Returns:
        True on successful conversion.
    """
    dst = src.with_suffix(".mp4")

    # If source is already .mp4, use temp name
    if src.suffix.lower() == ".mp4":
        dst = src.parent / f"{src.stem}_hevc.mp4"

    # Encode with VideoToolbox hardware acceleration
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
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
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"    FFMPEG ERROR: {result.stderr[-200:]}")
        if dst.exists():
            dst.unlink()
        return False

    # Copy all metadata from original
    subprocess.run(
        [
            "exiftool",
            "-overwrite_original",
            "-TagsFromFile",
            str(src),
            "-All:All",
            str(dst),
        ],
        capture_output=True,
        text=True,
    )

    # Fix FileModifyDate — try CreateDate first, fallback to filename date
    create_date = subprocess.run(
        ["exiftool", "-s3", "-CreateDate", str(dst)],
        capture_output=True,
        text=True,
    ).stdout.strip()

    if create_date and create_date != "0000:00:00 00:00:00":
        subprocess.run(
            ["exiftool", "-overwrite_original", "-FileModifyDate<CreateDate", str(dst)],
            capture_output=True,
            text=True,
        )
    else:
        # Extract date from filename (YYYYMMDD_HHMMSS.ext)
        date_from_name = _parse_date_from_filename(dst.name)
        if date_from_name:
            subprocess.run(
                [
                    "exiftool",
                    "-overwrite_original",
                    f"-CreateDate={date_from_name}",
                    f"-FileModifyDate={date_from_name}",
                    str(dst),
                ],
                capture_output=True,
                text=True,
            )
            print(f"    INFO: CreateDate empty, set from filename: {date_from_name}")
        else:
            print(f"    WARNING: No CreateDate and cannot parse filename: {dst.name}")

    # Verify GPS preserved
    orig_gps = subprocess.run(
        ["exiftool", "-s3", "-GPSLatitude", str(src)],
        capture_output=True,
        text=True,
    ).stdout.strip()

    if orig_gps:
        new_gps = subprocess.run(
            ["exiftool", "-s3", "-GPSLatitude", str(dst)],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not new_gps:
            print(f"    WARNING: GPS lost for {src.name}")

    # Remove original, rename if needed
    src.unlink()
    if src.suffix.lower() == ".mp4":
        final = src.parent / f"{src.stem}.mp4"
        dst.rename(final)

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
