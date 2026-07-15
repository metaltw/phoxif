"""Conservative GPS evidence rules for catalog enrichment."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class GpsAnchor:
    """One native-GPS reference with a trustworthy capture time."""

    sha256: str
    captured_at: datetime
    latitude: float
    longitude: float


@dataclass(frozen=True)
class GpsEvidence:
    """One approved coordinate source and its audit metadata."""

    latitude: float
    longitude: float
    source: str
    estimated: bool
    reference_sha256: tuple[str, ...] = ()
    offset_seconds: int | None = None
    folder_key: str | None = None

    @property
    def keywords(self) -> list[str]:
        values = ["phoxif:gps-backfilled", f"phoxif:gps-src:{self.source}"]
        values.append(
            "phoxif:gps-estimated" if self.estimated else "phoxif:gps-user-confirmed"
        )
        return values


def valid_coordinates(latitude: float, longitude: float) -> bool:
    """Return whether a coordinate pair is finite and geographically valid."""
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def mapped_evidence(folder_key: str, latitude: float, longitude: float) -> GpsEvidence:
    """Build evidence from a user-authored folder mapping."""
    if not valid_coordinates(latitude, longitude):
        raise ValueError(f"Invalid GPS mapping for {folder_key}")
    return GpsEvidence(
        latitude,
        longitude,
        "folder-mapping",
        False,
        folder_key=folder_key,
    )


def distance_meters(left: GpsAnchor, right: GpsAnchor) -> float:
    """Calculate great-circle distance between two native GPS anchors."""
    radius = 6_371_000.0
    left_lat = math.radians(left.latitude)
    right_lat = math.radians(right.latitude)
    delta_lat = right_lat - left_lat
    delta_lon = math.radians(right.longitude - left.longitude)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(left_lat) * math.cos(right_lat) * math.sin(delta_lon / 2) ** 2
    )
    value = min(1.0, max(0.0, value))
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def infer_temporal_neighbor(
    captured_at: datetime,
    anchors: list[GpsAnchor],
    *,
    max_minutes: int = 30,
    max_anchor_distance_meters: float = 1_000.0,
) -> GpsEvidence | None:
    """Infer GPS only from close native anchors that do not disagree spatially."""
    if captured_at.tzinfo is None:
        raise ValueError("Target capture time must be timezone-aware")
    if not 1 <= max_minutes <= 30:
        raise ValueError("GPS temporal window must be between 1 and 30 minutes")
    eligible = [
        anchor
        for anchor in anchors
        if anchor.captured_at.tzinfo is not None
        and abs((anchor.captured_at - captured_at).total_seconds()) <= max_minutes * 60
        and valid_coordinates(anchor.latitude, anchor.longitude)
    ]
    if not eligible:
        return None
    before = [anchor for anchor in eligible if anchor.captured_at <= captured_at]
    after = [anchor for anchor in eligible if anchor.captured_at >= captured_at]
    left = max(before, key=lambda anchor: anchor.captured_at) if before else None
    right = min(after, key=lambda anchor: anchor.captured_at) if after else None
    if left is not None and right is not None and left.sha256 != right.sha256:
        if distance_meters(left, right) > max_anchor_distance_meters:
            return None
        span = (right.captured_at - left.captured_at).total_seconds()
        ratio = 0.0 if span == 0 else (captured_at - left.captured_at).total_seconds() / span
        latitude = left.latitude + (right.latitude - left.latitude) * ratio
        longitude_delta = ((right.longitude - left.longitude + 180) % 360) - 180
        longitude = ((left.longitude + longitude_delta * ratio + 180) % 360) - 180
        references = (left.sha256, right.sha256)
        offset = max(
            abs((captured_at - left.captured_at).total_seconds()),
            abs((right.captured_at - captured_at).total_seconds()),
        )
    else:
        nearest = min(eligible, key=lambda anchor: abs(anchor.captured_at - captured_at))
        latitude = nearest.latitude
        longitude = nearest.longitude
        references = (nearest.sha256,)
        offset = abs((nearest.captured_at - captured_at).total_seconds())
    return GpsEvidence(
        latitude,
        longitude,
        "temporal-neighbor",
        True,
        references,
        round(offset),
    )
