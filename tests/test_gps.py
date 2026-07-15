"""Tests for conservative GPS inference rules."""

from datetime import datetime, timedelta, timezone

import pytest

from phoxif.pipeline.gps import GpsAnchor, infer_temporal_neighbor, mapped_evidence


def test_user_mapping_is_explicit_not_temporal_estimation() -> None:
    evidence = mapped_evidence("confirmed-folder", 20.5, -30.25)

    assert evidence.source == "folder-mapping"
    assert evidence.estimated is False
    assert "phoxif:gps-user-confirmed" in evidence.keywords


def test_temporal_neighbor_interpolates_two_consistent_native_anchors() -> None:
    target = datetime(2024, 1, 1, 10, 10, tzinfo=timezone.utc)
    anchors = [
        GpsAnchor("a" * 64, target - timedelta(minutes=10), 20.0, 30.0),
        GpsAnchor("b" * 64, target + timedelta(minutes=10), 20.002, 30.002),
    ]

    evidence = infer_temporal_neighbor(target, anchors)

    assert evidence is not None
    assert evidence.latitude == pytest.approx(20.001)
    assert evidence.longitude == pytest.approx(30.001)
    assert evidence.reference_sha256 == ("a" * 64, "b" * 64)
    assert evidence.offset_seconds == 600
    assert "phoxif:gps-estimated" in evidence.keywords


def test_temporal_neighbor_rejects_distant_or_spatially_conflicting_anchors() -> None:
    target = datetime(2024, 1, 1, 10, 10, tzinfo=timezone.utc)

    assert infer_temporal_neighbor(
        target,
        [GpsAnchor("a" * 64, target - timedelta(minutes=31), 20.0, 30.0)],
    ) is None


def test_temporal_neighbor_interpolates_across_antimeridian() -> None:
    target = datetime(2024, 1, 1, 10, 10, tzinfo=timezone.utc)
    anchors = [
        GpsAnchor("a" * 64, target - timedelta(minutes=10), 0.0, 179.999),
        GpsAnchor("b" * 64, target + timedelta(minutes=10), 0.0, -179.999),
    ]

    evidence = infer_temporal_neighbor(target, anchors)

    assert evidence is not None
    assert abs(evidence.longitude) == pytest.approx(180.0)
    assert infer_temporal_neighbor(
        target,
        [
            GpsAnchor("a" * 64, target - timedelta(minutes=5), 20.0, 30.0),
            GpsAnchor("b" * 64, target + timedelta(minutes=5), 21.0, 31.0),
        ],
    ) is None
