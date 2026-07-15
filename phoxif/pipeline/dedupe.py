"""Catalog-backed exact and conservative near-duplicate analysis."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from phoxif.pipeline.catalog import Catalog

_CAMERA_NAME = re.compile(r"^(?:IMG_|DSC|\d{8}[_-]\d{6})", re.IGNORECASE)
_EDITED_NAME = re.compile(r"^IMG_(E?)(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    """One catalog file with the evidence required by the winner rule."""

    sha256: str
    path: str
    name: str
    source_id: str
    original_dir: str
    size: int
    width: int | None
    height: int | None
    phash: str
    status: str
    native_date: str | None
    has_gps: bool
    mtime_epoch: float

    @property
    def pixels(self) -> int:
        return (self.width or 0) * (self.height or 0)

    @property
    def winner_key(self) -> tuple[bool, bool, int, bool, int, float]:
        return (
            self.native_date is not None,
            self.has_gps,
            self.pixels,
            bool(_CAMERA_NAME.match(self.name)),
            self.size,
            -self.mtime_epoch,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"pixels": self.pixels}


@dataclass(frozen=True)
class DedupeResult:
    """Serializable dry-run result; no user file is deleted."""

    batch_id: str
    exact_groups: list[dict[str, Any]]
    auto_groups: list[dict[str, Any]]
    review_pairs: list[dict[str, Any]]
    burst_pairs: list[dict[str, Any]]
    protected_edits: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _native_metadata(path: Path) -> tuple[str | None, bool]:
    """Read only native date/GPS fields from one working copy."""
    try:
        result = subprocess.run(
            [
                "exiftool",
                "-json",
                "-n",
                "-DateTimeOriginal",
                "-GPSLatitude",
                "-GPSLongitude",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, False
    if result.returncode != 0:
        return None, False
    try:
        payload = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError, TypeError):
        return None, False
    date = payload.get("DateTimeOriginal")
    has_gps = payload.get("GPSLatitude") is not None and payload.get("GPSLongitude") is not None
    return str(date) if date else None, has_gps


def _native_metadata_batch(
    paths: list[Path], chunk_size: int = 256
) -> dict[str, tuple[str | None, bool]]:
    """Batch-read native evidence without spawning one exiftool per file."""
    metadata = {str(path): (None, False) for path in paths}
    for start in range(0, len(paths), chunk_size):
        chunk = paths[start : start + chunk_size]
        try:
            result = subprocess.run(
                [
                    "exiftool",
                    "-json",
                    "-n",
                    "-DateTimeOriginal",
                    "-GPSLatitude",
                    "-GPSLongitude",
                    *[str(path) for path in chunk],
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0 and not result.stdout:
            continue
        try:
            payloads = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            continue
        for payload in payloads:
            source = payload.get("SourceFile")
            if source is None:
                continue
            date = payload.get("DateTimeOriginal")
            has_gps = (
                payload.get("GPSLatitude") is not None and payload.get("GPSLongitude") is not None
            )
            metadata[str(Path(source))] = (str(date) if date else None, has_gps)
    return metadata


def _date_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], pattern).timestamp()
        except ValueError:
            continue
    return None


def _hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _candidate_row(catalog: Catalog, sha256: str):
    return catalog.connection.execute(
        """
        SELECT files.*, sightings.source_id, sightings.original_path,
               sightings.original_name, sightings.original_mtime, sightings.staging_path
        FROM files JOIN sightings ON sightings.sha256 = files.sha256
        WHERE files.sha256 = ?
        ORDER BY sightings.seen_at ASC LIMIT 1
        """,
        (sha256,),
    ).fetchone()


def _candidate_from_row(
    row,
    metadata: tuple[str | None, bool] | None = None,
) -> Candidate:
    if row is None or row["phash"] is None:
        raise KeyError("Missing dedupe candidate")
    working_path = Path(row["staging_path"] or row["original_path"])
    native_date, has_gps = metadata if metadata is not None else _native_metadata(working_path)
    try:
        mtime_epoch = datetime.fromisoformat(row["original_mtime"]).timestamp()
    except (TypeError, ValueError):
        mtime_epoch = float("inf")
    return Candidate(
        sha256=row["sha256"],
        path=str(working_path),
        name=row["original_name"],
        source_id=row["source_id"],
        original_dir=str(Path(row["original_path"]).parent),
        size=int(row["size"]),
        width=row["width"],
        height=row["height"],
        phash=row["phash"],
        status=row["status"],
        native_date=native_date,
        has_gps=has_gps,
        mtime_epoch=mtime_epoch,
    )


def _candidate(catalog: Catalog, sha256: str) -> Candidate:
    return _candidate_from_row(_candidate_row(catalog, sha256))


class _BKTree:
    """Metric index for exact Hamming-radius phash queries."""

    def __init__(self) -> None:
        self.root: tuple[int, list[str], dict[int, Any]] | None = None

    def add(self, value: int, sha256: str) -> None:
        if self.root is None:
            self.root = (value, [sha256], {})
            return
        node = self.root
        while True:
            distance = (value ^ node[0]).bit_count()
            if distance == 0:
                node[1].append(sha256)
                return
            child = node[2].get(distance)
            if child is None:
                node[2][distance] = (value, [sha256], {})
                return
            node = child

    def query(self, value: int, radius: int, *, include_zero: bool = True) -> list[str]:
        if self.root is None:
            return []
        matches: list[str] = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            distance = (value ^ node[0]).bit_count()
            if distance <= radius and (include_zero or distance != 0):
                matches.extend(node[1])
            lower, upper = distance - radius, distance + radius
            stack.extend(child for edge, child in node[2].items() if lower <= edge <= upper)
        return matches


def _is_edited_pair(left: Candidate, right: Candidate) -> bool:
    left_match = _EDITED_NAME.match(left.name)
    right_match = _EDITED_NAME.match(right.name)
    if left_match is None or right_match is None:
        return False
    return (
        left.source_id == right.source_id
        and left.original_dir == right.original_dir
        and left_match.group(2) == right_match.group(2)
        and left_match.group(1) != right_match.group(1)
    )


def _is_burst(left: Candidate, right: Candidate) -> bool:
    left_time = _date_seconds(left.native_date)
    right_time = _date_seconds(right.native_date)
    return (
        left.source_id == right.source_id
        and left.original_dir == right.original_dir
        and left_time is not None
        and right_time is not None
        and abs(left_time - right_time) <= 10
    )


def _asymmetric(left: Candidate, right: Candidate) -> bool:
    date_asymmetry = (left.native_date is None) != (right.native_date is None)
    pixel_ratio = min(left.pixels, right.pixels) / max(left.pixels, right.pixels, 1)
    size_ratio = min(left.size, right.size) / max(left.size, right.size, 1)
    return date_asymmetry or pixel_ratio <= 0.6 or size_ratio <= 0.6


def _pair_dict(left: Candidate, right: Candidate, distance: int, reason: str) -> dict[str, Any]:
    return {
        "id": hashlib.sha256(f"{left.sha256}:{right.sha256}".encode()).hexdigest()[:16],
        "distance": distance,
        "reason": reason,
        "files": [left.to_dict(), right.to_dict()],
    }


def run(
    batch_id: str,
    *,
    catalog_db: Path,
    auto_threshold: int = 4,
    review_threshold: int = 10,
    persist: bool = True,
) -> DedupeResult:
    """Analyze one batch and persist only high-confidence duplicate decisions."""
    if not 0 <= auto_threshold <= review_threshold <= 64:
        raise ValueError("Expected 0 <= auto_threshold <= review_threshold <= 64")
    with Catalog(catalog_db) as catalog:
        batch = catalog.connection.execute(
            "SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise KeyError(f"Unknown batch: {batch_id}")

        exact_rows = catalog.connection.execute(
            """
            SELECT files.sha256, COUNT(sightings.id) AS copies
            FROM files JOIN sightings ON sightings.sha256 = files.sha256
            WHERE files.sha256 IN (SELECT sha256 FROM sightings WHERE batch_id = ?)
            GROUP BY files.sha256 HAVING COUNT(sightings.id) > 1
            """,
            (batch_id,),
        ).fetchall()
        exact_groups = [
            {"sha256": row["sha256"], "copies": int(row["copies"])} for row in exact_rows
        ]

        new_rows = catalog.connection.execute(
            """
            SELECT DISTINCT files.sha256 FROM files
            JOIN sightings ON sightings.sha256 = files.sha256
            WHERE sightings.batch_id = ? AND files.status = 'ingested'
              AND files.media_type = 'image' AND files.phash IS NOT NULL
            """,
            (batch_id,),
        ).fetchall()
        all_rows = catalog.connection.execute(
            """
            SELECT sha256 FROM files
            WHERE status IN ('ingested', 'unique')
              AND media_type = 'image' AND phash IS NOT NULL
            """
        ).fetchall()
        new_sha = {row["sha256"] for row in new_rows}
        all_sha = [row["sha256"] for row in all_rows]
        rows = {sha: _candidate_row(catalog, sha) for sha in all_sha}
        working_paths = [
            Path(row["staging_path"] or row["original_path"])
            for row in rows.values()
            if row is not None
        ]
        metadata = _native_metadata_batch(working_paths)
        candidates = {
            sha: _candidate_from_row(
                row,
                metadata.get(str(Path(row["staging_path"] or row["original_path"]))),
            )
            for sha, row in rows.items()
        }
        tree = _BKTree()
        phash_groups: dict[int, list[str]] = {}
        for sha256, candidate in candidates.items():
            value = int(candidate.phash, 16)
            tree.add(value, sha256)
            phash_groups.setdefault(value, []).append(sha256)

        auto_groups: list[dict[str, Any]] = []
        review_pairs: list[dict[str, Any]] = []
        burst_pairs: list[dict[str, Any]] = []
        protected_edits: list[dict[str, Any]] = []
        auto_candidates: list[tuple[Candidate, Candidate, int]] = []
        compared: set[tuple[str, str]] = set()
        auto_members: set[str] = set()

        # An identical-phash bucket is represented as a star instead of every
        # possible pair. This preserves a review path for the whole component
        # without quadratic work for common placeholder/solid-color images.
        for group in phash_groups.values():
            ordered = sorted(group)
            if len(ordered) < 2 or not new_sha.intersection(ordered):
                continue
            representative = ordered[0]
            for other in ordered[1:]:
                if representative in new_sha or other in new_sha:
                    compared.add((representative, other))

        for left_sha in sorted(new_sha):
            for right_sha in tree.query(
                int(candidates[left_sha].phash, 16),
                review_threshold,
                include_zero=False,
            ):
                if left_sha == right_sha:
                    continue
                pair = tuple(sorted((left_sha, right_sha)))
                compared.add(pair)

        for pair in sorted(compared):
            left, right = candidates[pair[0]], candidates[pair[1]]
            distance = _hamming(left.phash, right.phash)
            if distance > review_threshold:
                continue
            if _is_edited_pair(left, right):
                protected_edits.append(_pair_dict(left, right, distance, "edited-variant"))
            elif _is_burst(left, right):
                burst_pairs.append(_pair_dict(left, right, distance, "burst-within-10s"))
            elif distance <= auto_threshold and _asymmetric(left, right):
                winner, loser = sorted(
                    (left, right), key=lambda item: item.winner_key, reverse=True
                )
                if loser.status != "ingested":
                    review_pairs.append(
                        _pair_dict(left, right, distance, "would-replace-processed-file")
                    )
                    continue
                auto_candidates.append((winner, loser, distance))
            else:
                review_pairs.append(_pair_dict(left, right, distance, "manual-review"))

        protected_members = {
            file_info["sha256"]
            for pair in [*review_pairs, *burst_pairs, *protected_edits]
            for file_info in pair["files"]
        }
        auto_degree: dict[str, int] = {}
        for winner, loser, _distance in auto_candidates:
            auto_degree[winner.sha256] = auto_degree.get(winner.sha256, 0) + 1
            auto_degree[loser.sha256] = auto_degree.get(loser.sha256, 0) + 1
        blocked_auto = protected_members | {
            sha256 for sha256, degree in auto_degree.items() if degree > 1
        }

        for winner, loser, distance in auto_candidates:
            if winner.sha256 in blocked_auto or loser.sha256 in blocked_auto:
                review_pairs.append(
                    _pair_dict(winner, loser, distance, "overlapping-or-protected-context")
                )
                continue
            group_id = hashlib.sha256(f"near:{winner.sha256}:{loser.sha256}".encode()).hexdigest()[
                :16
            ]
            if persist:
                catalog.mark_near_duplicate(
                    batch_id,
                    winner.sha256,
                    loser.sha256,
                    group_id,
                )
            auto_members.update((winner.sha256, loser.sha256))
            auto_groups.append(
                _pair_dict(winner, loser, distance, "asymmetric-high-confidence")
                | {"winner_sha256": winner.sha256, "loser_sha256": loser.sha256}
            )

        review_members = {
            file_info["sha256"] for pair in review_pairs for file_info in pair["files"]
        }
        if persist:
            catalog.mark_batch_unique(batch_id, exclude=auto_members | review_members)
        return DedupeResult(
            batch_id=batch_id,
            exact_groups=exact_groups,
            auto_groups=auto_groups,
            review_pairs=review_pairs,
            burst_pairs=burst_pairs,
            protected_edits=protected_edits,
        )


def resolve_review(
    batch_id: str,
    pair_id: str,
    left_sha256: str,
    right_sha256: str,
    keep_sha256: str | None,
    *,
    catalog_db: Path,
    auto_threshold: int = 4,
    review_threshold: int = 10,
) -> dict[str, Any]:
    """Apply one explicit human decision after recomputing pair safety."""
    if left_sha256 == right_sha256:
        raise ValueError("A review pair must contain two different files")
    current = run(
        batch_id,
        catalog_db=catalog_db,
        auto_threshold=auto_threshold,
        review_threshold=review_threshold,
        persist=False,
    )
    requested_members = {left_sha256, right_sha256}
    queued_pair = next(
        (
            pair
            for pair in current.review_pairs
            if {str(item["sha256"]) for item in pair["files"]} == requested_members
        ),
        None,
    )
    if queued_pair is None:
        raise ValueError("Review pair is not pending in this batch")
    if queued_pair["id"] != pair_id:
        raise ValueError("Review pair identity does not match catalog content")

    with Catalog(catalog_db) as catalog:
        left = _candidate(catalog, left_sha256)
        right = _candidate(catalog, right_sha256)
        if left.status not in {"ingested", "unique"} or right.status not in {
            "ingested",
            "unique",
        }:
            raise ValueError("Review pair is no longer pending dedupe")
        if left.status != "ingested" and right.status != "ingested":
            raise ValueError("Review pair was already resolved")
        ordered = sorted((left, right), key=lambda item: item.sha256)
        distance = _hamming(ordered[0].phash, ordered[1].phash)
        expected_id = _pair_dict(ordered[0], ordered[1], distance, "review")["id"]
        if pair_id != expected_id:
            raise ValueError("Review pair identity does not match catalog content")
        if distance > review_threshold:
            raise ValueError("Pair is outside the configured review threshold")

        protected_reason = None
        if _is_edited_pair(left, right):
            protected_reason = "edited-variant"
        elif _is_burst(left, right):
            protected_reason = "burst-within-10s"

        if keep_sha256 is None:
            catalog.mark_files_unique({left_sha256, right_sha256})
            decision = {
                "pair_id": pair_id,
                "decision": "keep-both",
                "protected_reason": protected_reason,
            }
        else:
            if protected_reason is not None:
                raise ValueError(f"Protected pair cannot discard a file: {protected_reason}")
            if keep_sha256 not in {left_sha256, right_sha256}:
                raise ValueError("Chosen keeper is not part of the review pair")
            loser_sha256 = right_sha256 if keep_sha256 == left_sha256 else left_sha256
            loser = catalog.file(loser_sha256)
            if loser is None or loser["status"] != "ingested":
                raise ValueError("Cannot replace a file that already passed dedupe")
            group_id = hashlib.sha256(f"manual:{keep_sha256}:{loser_sha256}".encode()).hexdigest()[
                :16
            ]
            catalog.mark_near_duplicate(
                batch_id,
                keep_sha256,
                loser_sha256,
                group_id,
                reason="manual_near_duplicate",
            )
            decision = {
                "pair_id": pair_id,
                "decision": "keep-one",
                "winner_sha256": keep_sha256,
                "loser_sha256": loser_sha256,
            }

    refreshed = run(
        batch_id,
        catalog_db=catalog_db,
        auto_threshold=auto_threshold,
        review_threshold=review_threshold,
    )
    return decision | {"refreshed_result": refreshed.to_dict()}
