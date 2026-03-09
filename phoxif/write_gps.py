"""Batch write GPS coordinates into files that lack GPS metadata.

Reads folder-to-GPS mappings from config.yaml and writes coordinates
to media files that don't already have GPS data.

Usage:
    python -m phoxif.write_gps --config config.yaml [--dry-run]
"""

import argparse
import subprocess
from pathlib import Path
from typing import Any

from phoxif.config import load_config


def get_files_without_gps(folder: Path, extensions: set[str]) -> list[Path]:
    """Return media files in folder that have no GPS metadata.

    Args:
        folder: Directory to scan.
        extensions: Set of file extensions to include.

    Returns:
        List of file paths missing GPS data.
    """
    media_files = [
        f
        for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in extensions and f.stat().st_size > 0
    ]
    if not media_files:
        return []

    # Use exiftool to check which files already have GPS
    result = subprocess.run(
        ["exiftool", "-if", "$GPSLatitude", "-p", "$FileName", str(folder)],
        capture_output=True,
        text=True,
    )
    has_gps = set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()

    return [f for f in media_files if f.name not in has_gps]


def write_gps(files: list[Path], lat: float, lon: float) -> int:
    """Write GPS coordinates to files using exiftool.

    Args:
        files: List of file paths to write GPS data to.
        lat: Latitude (positive=North, negative=South).
        lon: Longitude (positive=East, negative=West).

    Returns:
        Count of files successfully updated.
    """
    if not files:
        return 0

    lat_ref = "N" if lat >= 0 else "S"
    lon_ref = "E" if lon >= 0 else "W"

    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-GPSLatitude={abs(lat)}",
        f"-GPSLatitudeRef={lat_ref}",
        f"-GPSLongitude={abs(lon)}",
        f"-GPSLongitudeRef={lon_ref}",
    ] + [str(f) for f in files]

    result = subprocess.run(cmd, capture_output=True, text=True)
    for line in result.stdout.strip().split("\n"):
        if "image files updated" in line:
            return int(line.split()[0])
    return 0


def main(argv: list[str] | None = None) -> None:
    """Entry point for GPS batch writer."""
    parser = argparse.ArgumentParser(
        description="Batch write GPS coordinates to files missing GPS metadata."
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--base-dir", default=None, help="Override base_dir from config"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show plan without writing GPS"
    )
    args = parser.parse_args(argv)

    cfg: dict[str, Any] = load_config(args.config, base_dir_override=args.base_dir)
    base_dir: Path = cfg["base_dir"]
    extensions: set[str] = cfg["extensions"]
    gps_locations: dict[str, tuple[float, float]] = cfg["gps_locations"]

    if not gps_locations:
        print("No GPS locations defined in config. Nothing to do.")
        return

    total_written = 0
    total_skipped = 0
    folders_processed = 0

    for folder_name, (lat, lon) in sorted(gps_locations.items()):
        folder = base_dir / folder_name
        if not folder.exists() or not folder.is_dir():
            print(f"  SKIP (not found): {folder_name}")
            continue

        no_gps_files = get_files_without_gps(folder, extensions)
        all_media = [
            f
            for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in extensions and f.stat().st_size > 0
        ]
        has_gps_count = len(all_media) - len(no_gps_files)

        if not no_gps_files:
            if all_media:
                print(
                    f"  OK {folder_name}: all {len(all_media)} files already have GPS"
                )
            continue

        if args.dry_run:
            print(
                f"  WOULD WRITE {folder_name}: {len(no_gps_files)} files, "
                f"lat={lat}, lon={lon}"
            )
            continue

        count = write_gps(no_gps_files, lat, lon)
        total_written += count
        total_skipped += has_gps_count
        folders_processed += 1
        print(
            f"  WRITE {folder_name}: {count} files updated, "
            f"{has_gps_count} kept original GPS"
        )

    if args.dry_run:
        print("\nDry run complete. No files modified.")
    else:
        print(
            f"\nDone! {total_written} files updated across {folders_processed} folders, "
            f"{total_skipped} files kept original GPS."
        )


if __name__ == "__main__":
    main()
