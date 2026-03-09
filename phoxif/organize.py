"""Organize photos/videos into folders by capture location (city/country).

Uses GPS metadata and Nominatim reverse geocoding to determine the location
of each file and move it into a named folder.

Usage:
    python -m phoxif.organize --config config.yaml [--dry-run]
"""

import argparse
import json
import subprocess
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from phoxif.config import load_config


def load_cache(cache_path: Path) -> dict[str, str]:
    """Load geocode cache from disk."""
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}


def save_cache(cache: dict[str, str], cache_path: Path) -> None:
    """Save geocode cache to disk."""
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def reverse_geocode(
    lat: float,
    lon: float,
    cache: dict[str, str],
    *,
    nominatim_url: str,
    accept_language: str = "en",
    zoom: int = 10,
    rate_limit_sec: float = 1.1,
) -> str:
    """Reverse geocode GPS coordinates to a location label.

    Args:
        lat: Latitude.
        lon: Longitude.
        cache: Geocode cache dict (mutated in place).
        nominatim_url: Nominatim API URL.
        accept_language: Language for results.
        zoom: Nominatim zoom level.
        rate_limit_sec: Seconds to wait between API calls.

    Returns:
        Location label string (e.g., "Tokyo" or "Japan_Tokyo").
    """
    key = f"{lat:.2f},{lon:.2f}"
    if key in cache:
        return cache[key]

    params = urllib.parse.urlencode(
        {
            "lat": f"{lat:.4f}",
            "lon": f"{lon:.4f}",
            "format": "json",
            "zoom": zoom,
            "accept-language": accept_language,
        }
    )
    url = f"{nominatim_url}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "phoxif/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        addr = data.get("address", {})
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("county")
            or addr.get("state")
            or "Unknown"
        )
        country = addr.get("country", "Unknown")
        country_code = addr.get("country_code", "")

        # Use city name alone for domestic, Country_City for international
        # Customize this logic based on your home country
        if country_code == accept_language.split("-")[-1].lower():
            label = city
        else:
            label = f"{country}_{city}"
    except Exception as e:
        print(f"  Geocode failed for {key}: {e}")
        label = "Unknown"

    cache[key] = label
    time.sleep(rate_limit_sec)
    return label


def get_file_gps(base_dir: Path) -> list[tuple[Path, float, float]]:
    """Extract GPS coordinates from all media files using exiftool.

    Args:
        base_dir: Base directory to scan.

    Returns:
        List of (filepath, latitude, longitude) tuples.
    """
    result = subprocess.run(
        [
            "exiftool",
            "-if",
            "$GPSLatitude",
            "-p",
            "$Directory/$FileName|$GPSLatitude#|$GPSLongitude#",
            "-r",
            str(base_dir),
        ],
        capture_output=True,
        text=True,
    )
    files: list[tuple[Path, float, float]] = []
    for line in result.stdout.strip().split("\n"):
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) != 3:
            continue
        filepath, lat, lon = parts
        try:
            files.append((Path(filepath), float(lat), float(lon)))
        except ValueError:
            continue
    return files


def get_all_media_files(base_dir: Path, extensions: set[str]) -> set[Path]:
    """Get all media files recursively.

    Args:
        base_dir: Base directory to scan.
        extensions: Set of file extensions to include (e.g., {".jpg", ".mov"}).

    Returns:
        Set of matching file paths.
    """
    files: set[Path] = set()
    for ext in extensions:
        files.update(base_dir.rglob(f"*{ext}"))
        files.update(base_dir.rglob(f"*{ext.upper()}"))
    return files


def move_file(filepath: Path, dest_dir: Path) -> Path:
    """Move a file to dest_dir, handling name collisions.

    Args:
        filepath: Source file path.
        dest_dir: Destination directory.

    Returns:
        Final destination path.
    """
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / filepath.name
    if dest == filepath:
        return dest
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        n = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}{suffix}"
            n += 1
    filepath.rename(dest)
    return dest


def main(argv: list[str] | None = None) -> None:
    """Entry point for location-based photo organization."""
    parser = argparse.ArgumentParser(
        description="Organize photos/videos into folders by capture location."
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--base-dir", default=None, help="Override base_dir from config"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show plan without moving files"
    )
    args = parser.parse_args(argv)

    cfg: dict[str, Any] = load_config(args.config, base_dir_override=args.base_dir)
    base_dir: Path = cfg["base_dir"]
    extensions: set[str] = cfg["extensions"]
    cache_path = base_dir / cfg["geocache_file"]
    geo_cfg = cfg["geocode"]

    cache = load_cache(cache_path)
    print("Extracting GPS data...")
    gps_files = get_file_gps(base_dir)
    all_media = get_all_media_files(base_dir, extensions)
    gps_file_set = {f for f, _, _ in gps_files}
    no_gps_files = all_media - gps_file_set

    # Collect unique locations
    unique_locs: dict[str, list[tuple[Path, float, float]]] = defaultdict(list)
    for filepath, lat, lon in gps_files:
        key = f"{lat:.2f},{lon:.2f}"
        unique_locs[key].append((filepath, lat, lon))

    print(f"Total files: {len(all_media)}")
    print(f"With GPS: {len(gps_files)}")
    print(f"Without GPS: {len(no_gps_files)}")
    print(f"Unique locations (~1km): {len(unique_locs)}")
    print(f"Cached locations: {len(cache)}")
    to_query = sum(1 for k in unique_locs if k not in cache)
    print(
        f"Need to query: {to_query} (est. {to_query * geo_cfg['rate_limit_sec']:.0f}s)"
    )
    print()

    # Reverse geocode
    location_map: dict[Path, str] = {}
    for i, (key, file_list) in enumerate(unique_locs.items()):
        lat, lon = map(float, key.split(","))
        label = reverse_geocode(
            lat,
            lon,
            cache,
            nominatim_url=cfg["nominatim_url"],
            accept_language=geo_cfg["accept_language"],
            zoom=geo_cfg["zoom"],
            rate_limit_sec=geo_cfg["rate_limit_sec"],
        )
        for filepath, _, _ in file_list:
            location_map[filepath] = label
        if key not in cache or (i + 1) % 20 == 0:
            save_cache(cache, cache_path)
            remaining = sum(
                1 for k in list(unique_locs.keys())[i + 1 :] if k not in cache
            )
            if remaining > 0:
                print(
                    f"  Progress: {i + 1}/{len(unique_locs)}, ~{remaining} queries left"
                )

    save_cache(cache, cache_path)

    # Print summary
    folder_counts: dict[str, int] = defaultdict(int)
    for label in location_map.values():
        folder_counts[label] += 1
    folder_counts["Unknown"] = len(no_gps_files)

    print("\n=== Folder Summary ===")
    for folder, count in sorted(folder_counts.items(), key=lambda x: -x[1]):
        print(f"  {folder}: {count}")
    print(f"  TOTAL: {sum(folder_counts.values())}")

    if args.dry_run:
        print("\nDry run complete. No files moved.")
        return

    # Move files
    print("\nMoving files...")
    moved = 0
    for filepath, label in location_map.items():
        dest_dir = base_dir / label
        move_file(filepath, dest_dir)
        moved += 1

    # Move no-GPS files to Unknown
    unknown_dir = base_dir / "Unknown"
    for filepath in no_gps_files:
        if filepath.parent == unknown_dir:
            continue
        move_file(filepath, unknown_dir)
        moved += 1

    print(f"Moved {moved} files.")

    # Cleanup empty directories
    for d in sorted(base_dir.rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            print(f"  Removed empty dir: {d.name}")

    print("\nDone!")


if __name__ == "__main__":
    main()
