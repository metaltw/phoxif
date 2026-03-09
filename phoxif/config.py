"""Configuration loader for phoxif."""

from pathlib import Path
from typing import Any

import yaml


def load_config(
    config_path: str = "config.yaml",
    base_dir_override: str | None = None,
) -> dict[str, Any]:
    """Load configuration from a YAML file.

    Args:
        config_path: Path to the YAML configuration file.
        base_dir_override: If provided, overrides the base_dir from config.

    Returns:
        Configuration dictionary with the following keys:
        - base_dir: Base directory for photo/video files.
        - extensions: Set of file extensions to process.
        - nominatim_url: Nominatim reverse geocoding API URL.
        - geocache_file: Filename for geocode cache.
        - sorter_port: Port for the web UI sorter.
        - skip_dirs: Set of directory names to skip.
        - gps_locations: Dict mapping folder names to {lat, lon}.
        - hevc: HEVC conversion settings.
        - geocode: Geocoding settings.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If required fields are missing.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            "Copy config.example.yaml to config.yaml and edit it."
        )

    with open(path) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    if not raw:
        raise ValueError(f"Config file is empty: {config_path}")

    if base_dir_override is None and "base_dir" not in raw:
        raise ValueError("Config missing required field: base_dir")

    # Normalize types
    effective_base_dir = (
        base_dir_override if base_dir_override else raw.get("base_dir", ".")
    )
    config: dict[str, Any] = {
        "base_dir": Path(effective_base_dir),
        "extensions": set(
            raw.get("extensions", [".jpg", ".jpeg", ".heic", ".png", ".mov", ".mp4"])
        ),
        "nominatim_url": raw.get(
            "nominatim_url", "https://nominatim.openstreetmap.org/reverse"
        ),
        "geocache_file": raw.get("geocache_file", ".geocache.json"),
        "sorter_port": raw.get("sorter_port", 8899),
        "skip_dirs": set(
            raw.get("skip_dirs", ["Unknown", ".thumbnails", ".previews", "__pycache__"])
        ),
    }

    # Parse GPS locations: {"Name": {"lat": x, "lon": y}} → {"Name": (lat, lon)}
    gps_raw = raw.get("gps_locations", {})
    config["gps_locations"] = {
        name: (loc["lat"], loc["lon"]) for name, loc in gps_raw.items()
    }

    # HEVC settings with defaults
    hevc_raw = raw.get("hevc", {})
    config["hevc"] = {
        "quality": hevc_raw.get("quality", 55),
        "audio_bitrate": hevc_raw.get("audio_bitrate", "128k"),
        "min_h264_size_mb": hevc_raw.get("min_h264_size_mb", 100),
        "recompress_mov": hevc_raw.get("recompress_mov", False),
    }

    # Geocode settings with defaults
    geo_raw = raw.get("geocode", {})
    config["geocode"] = {
        "accept_language": geo_raw.get("accept_language", "en"),
        "zoom": geo_raw.get("zoom", 10),
        "rate_limit_sec": geo_raw.get("rate_limit_sec", 1.1),
    }

    return config
