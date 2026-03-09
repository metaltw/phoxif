"""Visually similar photo detection using perceptual hashing."""

import logging
import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image

logger = logging.getLogger(__name__)

_PHOTO_EXTENSIONS: set[str] = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
}

_HEIC_EXTENSIONS: set[str] = {".heic", ".heif"}

_THUMB_CACHE_DIR = Path("/tmp/phoxif_thumbs")


def _parse_exif_date(date_str: str | None) -> datetime | None:
    """Parse exiftool date string to datetime.

    Handles formats like:
    - "2015:12:22 14:59:28"
    - "2015:12:22 14:59:28+08:00"
    - "2015:12:22 14:59:28-05:00"

    Args:
        date_str: Date string from exiftool.

    Returns:
        Parsed datetime or None if unparsable.
    """
    if not date_str or not isinstance(date_str, str):
        return None

    date_str = date_str.strip()
    if not date_str:
        return None

    # Try with timezone offset (±HH:MM)
    for fmt in (
        "%Y:%m:%d %H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # Handle non-standard timezone like "+08:00" that strptime %z may reject
    # on some Python builds — strip colon and retry
    if "+" in date_str or date_str.count("-") > 2:
        parts = date_str.rsplit("+", 1) if "+" in date_str else date_str.rsplit("-", 1)
        if len(parts) == 2 and ":" in parts[1]:
            cleaned = (
                parts[0] + ("+" if "+" in date_str else "-") + parts[1].replace(":", "")
            )
            for fmt in ("%Y:%m:%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
                try:
                    return datetime.strptime(cleaned, fmt)
                except ValueError:
                    continue

    logger.debug("Failed to parse date: %s", date_str)
    return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute haversine distance between two GPS coordinates in meters.

    Args:
        lat1: Latitude of point 1 in degrees.
        lon1: Longitude of point 1 in degrees.
        lat2: Latitude of point 2 in degrees.
        lon2: Longitude of point 2 in degrees.

    Returns:
        Distance in meters.
    """
    r = 6_371_000.0  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _convert_heic_to_jpeg(src: Path) -> Path | None:
    """Convert HEIC/HEIF to JPEG via macOS sips for PIL compatibility.

    Uses a cache directory to avoid re-converting the same file.

    Args:
        src: Path to the HEIC/HEIF file.

    Returns:
        Path to the converted JPEG, or None on failure.
    """
    _THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Use source file's inode + mtime as cache key to detect changes
    try:
        stat = src.stat()
    except OSError:
        return None

    cache_name = f"{stat.st_ino}_{int(stat.st_mtime)}.jpg"
    cache_path = _THUMB_CACHE_DIR / cache_name

    if cache_path.exists():
        return cache_path

    # sips on macOS can convert HEIC to JPEG
    try:
        # Copy to temp first, then convert in-place with sips
        tmp_path = _THUMB_CACHE_DIR / f"_tmp_{cache_name}"
        subprocess.run(
            [
                "sips",
                "-s",
                "format",
                "jpeg",
                str(src),
                "--out",
                str(tmp_path),
            ],
            capture_output=True,
            timeout=30,
        )
        if tmp_path.exists() and tmp_path.stat().st_size > 0:
            tmp_path.rename(cache_path)
            return cache_path
        # Clean up failed temp file
        tmp_path.unlink(missing_ok=True)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("sips conversion failed for %s: %s", src, exc)

    return None


def _compute_phash(file_info: dict[str, Any]) -> imagehash.ImageHash | None:
    """Compute perceptual hash for a photo file.

    For HEIC/HEIF files, converts to JPEG via sips first.

    Args:
        file_info: Normalized file info dict.

    Returns:
        ImageHash object or None if hashing fails.
    """
    file_path = Path(file_info["path"])
    ext = file_info.get("extension", file_path.suffix.lower())

    target_path = file_path
    if ext in _HEIC_EXTENSIONS:
        converted = _convert_heic_to_jpeg(file_path)
        if converted is None:
            logger.debug("Skipping HEIC file (conversion failed): %s", file_path)
            return None
        target_path = converted

    try:
        with Image.open(target_path) as img:
            return imagehash.phash(img)
    except Exception as exc:
        logger.debug("Failed to compute phash for %s: %s", file_path, exc)
        return None


class _UnionFind:
    """Disjoint set (union-find) with path compression and union by rank."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        """Find root with path compression."""
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        """Union by rank."""
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1


def _has_gps(f: dict[str, Any]) -> bool:
    """Check if file has valid GPS coordinates."""
    return f.get("gps_lat") is not None and f.get("gps_lon") is not None


def _are_time_neighbors(dt_a: datetime, dt_b: datetime, window_sec: int) -> bool:
    """Check if two datetimes are within the time window.

    Compares naive-to-naive or aware-to-aware. If one is naive and the other
    aware, strips timezone info for comparison.
    """
    # Normalize timezone awareness
    a, b = dt_a, dt_b
    if (a.tzinfo is None) != (b.tzinfo is None):
        a = a.replace(tzinfo=None)
        b = b.replace(tzinfo=None)

    return abs((a - b).total_seconds()) <= window_sec


def find_similar_groups(
    files: list[dict[str, Any]],
    time_window_sec: int = 10,
    gps_radius_m: float = 100.0,
    hash_threshold: int = 12,
) -> list[dict[str, Any]]:
    """Find groups of visually similar photos using perceptual hashing.

    Algorithm:
    1. Filter to photo files only.
    2. Sort by DateTimeOriginal.
    3. Cluster by time window (±N seconds). If both files have GPS,
       also require proximity within the radius.
    4. Within each time cluster, compute pHash and compare hamming distance.
    5. Build final groups using union-find for transitive grouping.

    Args:
        files: List of normalized file info dicts (from scanner.py).
        time_window_sec: Maximum time difference in seconds for clustering.
        gps_radius_m: Maximum GPS distance in meters for clustering.
        hash_threshold: Maximum hamming distance (out of 64 bits) to consider
            two images similar.

    Returns:
        List of similar groups, each containing:
        - files: List of file info dicts in the group.
        - count: Number of files.
        - keep_index: Index of the highest quality file (max resolution * size).
        - similarities: List of pairwise similarity dicts.
        - reason: "burst" if all files are within 2 seconds, else "similar".
        - reclaimable_size: Total size minus the kept file.
    """
    # Step 1: Filter to photo files
    photos = [
        f
        for f in files
        if f.get("extension", Path(f["path"]).suffix.lower()) in _PHOTO_EXTENSIONS
    ]

    if len(photos) < 2:
        return []

    # Step 2: Parse dates and sort
    dated_photos: list[tuple[datetime | None, dict[str, Any]]] = []
    for p in photos:
        dt = _parse_exif_date(p.get("date"))
        dated_photos.append((dt, p))

    # Sort by date, None dates go to the end
    # Strip timezone to avoid mixing aware/naive datetimes in sort
    def _sort_key(x: tuple[datetime | None, dict[str, Any]]) -> tuple[bool, datetime]:
        dt = x[0]
        if dt is None:
            return (True, datetime.min)
        return (False, dt.replace(tzinfo=None) if dt.tzinfo else dt)

    dated_photos.sort(key=_sort_key)

    sorted_dates = [d for d, _ in dated_photos]
    sorted_files = [f for _, f in dated_photos]
    n = len(sorted_files)

    # Step 3: Find candidate pairs by time window (and optionally GPS)
    candidate_pairs: list[tuple[int, int]] = []
    for i in range(n):
        if sorted_dates[i] is None:
            continue
        for j in range(i + 1, n):
            if sorted_dates[j] is None:
                break
            # Since sorted by date, once we exceed the window we can stop
            if not _are_time_neighbors(
                sorted_dates[i], sorted_dates[j], time_window_sec
            ):
                break

            # If both have GPS, also check proximity
            fi, fj = sorted_files[i], sorted_files[j]
            if _has_gps(fi) and _has_gps(fj):
                dist = _haversine_m(
                    fi["gps_lat"], fi["gps_lon"], fj["gps_lat"], fj["gps_lon"]
                )
                if dist > gps_radius_m:
                    continue

            candidate_pairs.append((i, j))

    if not candidate_pairs:
        return []

    # Step 4: Compute pHash only for files involved in candidates
    indices_needed: set[int] = set()
    for i, j in candidate_pairs:
        indices_needed.add(i)
        indices_needed.add(j)

    hashes: dict[int, imagehash.ImageHash] = {}
    for idx in indices_needed:
        h = _compute_phash(sorted_files[idx])
        if h is not None:
            hashes[idx] = h

    # Step 5: Compare hashes and union similar pairs
    uf = _UnionFind(n)
    similar_edges: list[tuple[int, int, int]] = []  # (i, j, distance)

    for i, j in candidate_pairs:
        if i not in hashes or j not in hashes:
            continue
        distance = int(
            hashes[i] - hashes[j]
        )  # hamming distance (convert from numpy.int64)
        if distance <= hash_threshold:
            uf.union(i, j)
            similar_edges.append((i, j, distance))

    if not similar_edges:
        return []

    # Step 6: Build groups from union-find
    groups_map: dict[int, list[int]] = {}
    # Only collect indices that participated in a similar edge
    edge_indices: set[int] = set()
    for i, j, _ in similar_edges:
        edge_indices.add(i)
        edge_indices.add(j)

    for idx in edge_indices:
        root = uf.find(idx)
        groups_map.setdefault(root, []).append(idx)

    results: list[dict[str, Any]] = []
    for root, members in groups_map.items():
        members.sort()
        group_files = [sorted_files[m] for m in members]

        # Determine best quality: max(resolution * size)
        def _quality_score(f: dict[str, Any]) -> float:
            w = f.get("width") or 0
            h = f.get("height") or 0
            s = f.get("size") or 0
            return w * h * s

        keep_idx = max(
            range(len(group_files)), key=lambda k: _quality_score(group_files[k])
        )

        # Build similarities list for edges within this group
        member_set = set(members)
        similarities: list[dict[str, Any]] = []
        for i, j, distance in similar_edges:
            if i in member_set and j in member_set:
                # Map global indices to local group indices
                local_i = members.index(i)
                local_j = members.index(j)
                similarity = 1.0 - (distance / 64.0)
                similarities.append(
                    {
                        "i": local_i,
                        "j": local_j,
                        "distance": distance,
                        "similarity": round(similarity, 4),
                    }
                )

        # Determine reason: "burst" if all within 2 seconds
        reason = "similar"
        member_dates = [sorted_dates[m] for m in members]
        if all(d is not None for d in member_dates):
            max_span = max(
                abs(
                    (
                        (a.replace(tzinfo=None) if a.tzinfo else a)
                        - (b.replace(tzinfo=None) if b.tzinfo else b)
                    ).total_seconds()
                )
                for a in member_dates
                for b in member_dates
            )
            if max_span <= 2.0:
                reason = "burst"

        # Reclaimable size = total minus kept
        total_size = sum(f.get("size", 0) for f in group_files)
        kept_size = group_files[keep_idx].get("size", 0)

        results.append(
            {
                "files": group_files,
                "count": len(group_files),
                "keep_index": keep_idx,
                "similarities": similarities,
                "reason": reason,
                "reclaimable_size": total_size - kept_size,
            }
        )

    # Sort groups by reclaimable size descending for convenience
    results.sort(key=lambda g: g["reclaimable_size"], reverse=True)
    return results
