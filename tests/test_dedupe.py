"""Tests for conservative catalog-backed dedupe policy."""

from pathlib import Path

import pytest

from phoxif.pipeline import dedupe
from phoxif.pipeline.catalog import Catalog


def _mock_metadata(monkeypatch, reader) -> None:
    monkeypatch.setattr(dedupe, "_native_metadata", reader)
    monkeypatch.setattr(
        dedupe,
        "_native_metadata_batch",
        lambda paths: {str(path): reader(path) for path in paths},
    )


def _add_candidate(
    catalog: Catalog,
    *,
    source_id: str,
    batch_id: str,
    sha256: str,
    name: str,
    size: int,
    phash: str,
    width: int,
    height: int,
    directory: str = "photos",
) -> None:
    catalog.upsert_file(
        sha256=sha256,
        size=size,
        ext=".jpg",
        media_type="image",
        phash=phash,
        width=width,
        height=height,
    )
    catalog.add_sighting(
        sha256=sha256,
        source_id=source_id,
        batch_id=batch_id,
        original_path=Path("/fixture") / directory / name,
        original_name=name,
        original_mtime="2024-01-01T00:00:00+00:00",
        original_btime=None,
        staging_path=Path("/fixture/staging") / name,
    )


def _source_batch(catalog: Catalog, source_id: str) -> str:
    catalog.register_source(source_id, source_id, "rescue")
    return catalog.start_batch(source_id, "rescue")


def test_bk_tree_radius_query_matches_brute_force() -> None:
    values = {
        f"sha-{index}": value
        for index, value in enumerate([0x0, 0x1, 0x3, 0xF, 0xFF, 0xFFFF, 0xAAAAAAAAAAAAAAAA])
    }
    tree = dedupe._BKTree()
    for sha256, value in values.items():
        tree.add(value, sha256)

    for query in (0x0, 0x7, 0xFF, 0xAAAAAAAAAAAAAAAB):
        expected = {sha256 for sha256, value in values.items() if (query ^ value).bit_count() <= 4}
        assert set(tree.query(query, 4)) == expected


def test_bk_tree_buckets_identical_hashes_without_a_zero_distance_chain() -> None:
    tree = dedupe._BKTree()
    for index in range(4_000):
        tree.add(0, f"sha-{index}")

    assert tree.root is not None
    assert len(tree.root[1]) == 4_000
    assert tree.root[2] == {}
    assert len(tree.query(0, 0)) == 4_000
    assert tree.query(0, 10, include_zero=False) == []


def test_wechat_compressed_version_loses_with_explainable_reason(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    original_sha = "a" * 64
    compressed_sha = "b" * 64
    with Catalog(database) as catalog:
        original_batch = _source_batch(catalog, "camera")
        _add_candidate(
            catalog,
            source_id="camera",
            batch_id=original_batch,
            sha256=original_sha,
            name="IMG_0001.jpg",
            size=5_000_000,
            phash="0000000000000000",
            width=4000,
            height=3000,
        )
        catalog.transition(original_sha, "unique")
        message_batch = _source_batch(catalog, "wechat")
        _add_candidate(
            catalog,
            source_id="wechat",
            batch_id=message_batch,
            sha256=compressed_sha,
            name="mmexport1700000000000.jpg",
            size=400_000,
            phash="0000000000000001",
            width=1000,
            height=750,
        )

    _mock_metadata(
        monkeypatch,
        lambda path: (
            ("2024:01:01 12:00:00", True) if path.name.startswith("IMG_") else (None, False)
        ),
    )
    result = dedupe.run(message_batch, catalog_db=database)

    assert len(result.auto_groups) == 1
    group = result.auto_groups[0]
    assert group["winner_sha256"] == original_sha
    assert group["loser_sha256"] == compressed_sha
    assert group["reason"] == "asymmetric-high-confidence"
    with Catalog(database) as catalog:
        assert catalog.file(original_sha)["status"] == "unique"
        loser = catalog.file(compressed_sha)
        assert loser["status"] == "duplicate"
        assert loser["kept_sha256"] == original_sha
        assert catalog.count("operations") == 1
        operation = catalog.connection.execute("SELECT op, detail_json FROM operations").fetchone()
        assert operation["op"] == "trash"
        assert '"reason": "near_duplicate"' in operation["detail_json"]
        assert '"status": "pending"' in operation["detail_json"]


def test_unstaged_rescue_duplicate_never_queues_its_original_for_trash(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    winner_sha = "1" * 64
    loser_sha = "2" * 64
    with Catalog(database) as catalog:
        winner_batch = _source_batch(catalog, "camera")
        _add_candidate(
            catalog,
            source_id="camera",
            batch_id=winner_batch,
            sha256=winner_sha,
            name="original.jpg",
            size=10_000,
            phash="0000000000000000",
            width=1_000,
            height=1_000,
        )
        catalog.transition(winner_sha, "unique")
        loser_batch = _source_batch(catalog, "rescue-without-copy")
        _add_candidate(
            catalog,
            source_id="rescue-without-copy",
            batch_id=loser_batch,
            sha256=loser_sha,
            name="compressed.jpg",
            size=100,
            phash="0000000000000001",
            width=100,
            height=100,
        )
        with catalog.transaction():
            catalog.connection.execute(
                "UPDATE sightings SET staging_path = NULL WHERE sha256 = ?",
                (loser_sha,),
            )
    _mock_metadata(monkeypatch, lambda _path: (None, False))

    result = dedupe.run(loser_batch, catalog_db=database)

    assert len(result.auto_groups) == 1
    with Catalog(database) as catalog:
        assert catalog.file(loser_sha)["status"] == "duplicate"
        assert catalog.count("operations") == 0


def test_symmetric_near_pair_requires_manual_review(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        first_batch = _source_batch(catalog, "camera-a")
        _add_candidate(
            catalog,
            source_id="camera-a",
            batch_id=first_batch,
            sha256="c" * 64,
            name="IMG_0002.jpg",
            size=1_000,
            phash="0000000000000000",
            width=100,
            height=100,
        )
        second_batch = _source_batch(catalog, "camera-b")
        _add_candidate(
            catalog,
            source_id="camera-b",
            batch_id=second_batch,
            sha256="d" * 64,
            name="IMG_0003.jpg",
            size=1_000,
            phash="0000000000000000",
            width=100,
            height=100,
        )
    _mock_metadata(monkeypatch, lambda _path: ("2024:01:01 12:00:00", False))

    result = dedupe.run(second_batch, catalog_db=database)

    assert len(result.review_pairs) == 1
    assert result.auto_groups == []
    with Catalog(database) as catalog:
        assert catalog.file("c" * 64)["status"] == "ingested"
        assert catalog.file("d" * 64)["status"] == "ingested"


def test_burst_and_edited_variant_are_protected(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        batch = _source_batch(catalog, "iphone")
        for sha256, name, phash in (
            ("e" * 64, "IMG_1234.jpg", "0000000000000000"),
            ("f" * 64, "IMG_E1234.jpg", "0000000000000001"),
            ("1" * 64, "IMG_2001.jpg", "0000000000000010"),
            ("2" * 64, "IMG_2002.jpg", "0000000000000011"),
        ):
            _add_candidate(
                catalog,
                source_id="iphone",
                batch_id=batch,
                sha256=sha256,
                name=name,
                size=1_000,
                phash=phash,
                width=100,
                height=100,
            )

    def metadata(path: Path) -> tuple[str | None, bool]:
        return (
            "2024:01:01 12:00:05" if path.name == "IMG_2002.jpg" else "2024:01:01 12:00:00",
            False,
        )

    _mock_metadata(monkeypatch, metadata)
    result = dedupe.run(batch, catalog_db=database, auto_threshold=1, review_threshold=1)

    assert len(result.protected_edits) == 1
    assert {item["name"] for item in result.protected_edits[0]["files"]} == {
        "IMG_1234.jpg",
        "IMG_E1234.jpg",
    }
    assert any(
        {item["name"] for item in pair["files"]} == {"IMG_2001.jpg", "IMG_2002.jpg"}
        for pair in result.burst_pairs
    )
    assert result.auto_groups == []


def test_exact_duplicate_is_one_identity_with_two_sightings(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "catalog.db"
    sha256 = "3" * 64
    with Catalog(database) as catalog:
        first_batch = _source_batch(catalog, "laptop")
        _add_candidate(
            catalog,
            source_id="laptop",
            batch_id=first_batch,
            sha256=sha256,
            name="original.jpg",
            size=1_000,
            phash="0000000000000000",
            width=100,
            height=100,
        )
        second_batch = _source_batch(catalog, "phone")
        catalog.add_sighting(
            sha256=sha256,
            source_id="phone",
            batch_id=second_batch,
            original_path=Path("/fixture/phone/copy.jpg"),
            original_name="copy.jpg",
            original_mtime="2024-01-02T00:00:00+00:00",
            original_btime=None,
            staging_path=Path("/fixture/staging/original.jpg"),
        )
    _mock_metadata(monkeypatch, lambda _path: (None, False))

    result = dedupe.run(second_batch, catalog_db=database)

    assert result.exact_groups == [{"sha256": sha256, "copies": 2}]
    with Catalog(database) as catalog:
        assert catalog.count("files") == 1
        assert catalog.count("sightings") == 2


@pytest.mark.parametrize("keep_one", [False, True])
def test_manual_review_resolution_is_revalidated(
    keep_one: bool,
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    left_sha = "4" * 64
    right_sha = "5" * 64
    with Catalog(database) as catalog:
        batch = _source_batch(catalog, "review")
        for sha256, name, phash in (
            (left_sha, "left.jpg", "0000000000000000"),
            (right_sha, "right.jpg", "0000000000000003"),
        ):
            _add_candidate(
                catalog,
                source_id="review",
                batch_id=batch,
                sha256=sha256,
                name=name,
                size=1_000,
                phash=phash,
                width=100,
                height=100,
                directory=name,
            )
    _mock_metadata(monkeypatch, lambda _path: (None, False))
    analysis = dedupe.run(batch, catalog_db=database)
    pair = analysis.review_pairs[0]

    decision = dedupe.resolve_review(
        batch,
        pair["id"],
        left_sha,
        right_sha,
        left_sha if keep_one else None,
        catalog_db=database,
    )

    with Catalog(database) as catalog:
        if keep_one:
            assert decision["decision"] == "keep-one"
            assert catalog.file(left_sha)["status"] == "unique"
            assert catalog.file(right_sha)["status"] == "duplicate"
            assert catalog.count("operations") == 1
        else:
            assert decision["decision"] == "keep-both"
            assert catalog.file(left_sha)["status"] == "unique"
            assert catalog.file(right_sha)["status"] == "unique"
            assert catalog.count("operations") == 0


def test_manual_review_rejects_tampered_pair_id(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        batch = _source_batch(catalog, "review")
        for sha256, name, phash in (
            ("6" * 64, "left.jpg", "0000000000000000"),
            ("7" * 64, "right.jpg", "0000000000000001"),
        ):
            _add_candidate(
                catalog,
                source_id="review",
                batch_id=batch,
                sha256=sha256,
                name=name,
                size=1_000,
                phash=phash,
                width=100,
                height=100,
                directory=name,
            )
    _mock_metadata(monkeypatch, lambda _path: (None, False))

    with pytest.raises(ValueError, match="identity does not match"):
        dedupe.resolve_review(
            batch,
            "tampered",
            "6" * 64,
            "7" * 64,
            None,
            catalog_db=database,
        )


def test_manual_review_rejects_a_real_pair_submitted_to_another_batch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        first_batch = _source_batch(catalog, "first")
        for sha256, name, phash in (
            ("b" * 64, "left.jpg", "0000000000000000"),
            ("c" * 64, "right.jpg", "0000000000000001"),
        ):
            _add_candidate(
                catalog,
                source_id="first",
                batch_id=first_batch,
                sha256=sha256,
                name=name,
                size=1_000,
                phash=phash,
                width=100,
                height=100,
                directory=name,
            )
        second_batch = _source_batch(catalog, "second")
        _add_candidate(
            catalog,
            source_id="second",
            batch_id=second_batch,
            sha256="d" * 64,
            name="unrelated.jpg",
            size=1_000,
            phash="ffffffffffffffff",
            width=100,
            height=100,
        )
    _mock_metadata(monkeypatch, lambda _path: (None, False))
    pair = dedupe.run(first_batch, catalog_db=database).review_pairs[0]

    with pytest.raises(ValueError, match="not pending in this batch"):
        dedupe.resolve_review(
            second_batch,
            pair["id"],
            "b" * 64,
            "c" * 64,
            None,
            catalog_db=database,
        )


def test_overlapping_auto_candidates_are_all_deferred_to_review(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        batch = _source_batch(catalog, "mixed")
        for sha256, name, size, width, phash in (
            ("8" * 64, "large.jpg", 10_000, 1000, "0000000000000000"),
            ("9" * 64, "small-a.jpg", 1_000, 100, "0000000000000001"),
            ("a" * 64, "small-b.jpg", 1_000, 100, "0000000000000002"),
        ):
            _add_candidate(
                catalog,
                source_id="mixed",
                batch_id=batch,
                sha256=sha256,
                name=name,
                size=size,
                phash=phash,
                width=width,
                height=width,
                directory=name,
            )
    _mock_metadata(monkeypatch, lambda _path: (None, False))

    result = dedupe.run(batch, catalog_db=database)

    assert result.auto_groups == []
    assert len(result.review_pairs) == 3
    assert any(pair["reason"] == "overlapping-or-protected-context" for pair in result.review_pairs)
    with Catalog(database) as catalog:
        assert catalog.count("operations") == 0
        statuses = {
            row["status"]
            for row in catalog.connection.execute("SELECT status FROM files").fetchall()
        }
        assert statuses == {"ingested"}


def test_resolution_refreshes_the_entire_overlapping_review_queue(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    with Catalog(database) as catalog:
        batch = _source_batch(catalog, "overlap")
        for sha256, name, size, width, phash in (
            ("e" * 64, "large.jpg", 10_000, 1000, "0000000000000000"),
            ("f" * 64, "small-a.jpg", 1_000, 100, "0000000000000001"),
            ("0" * 64, "small-b.jpg", 1_000, 100, "0000000000000002"),
        ):
            _add_candidate(
                catalog,
                source_id="overlap",
                batch_id=batch,
                sha256=sha256,
                name=name,
                size=size,
                phash=phash,
                width=width,
                height=width,
                directory=name,
            )
    _mock_metadata(monkeypatch, lambda _path: (None, False))
    analysis = dedupe.run(batch, catalog_db=database)
    first_pair = next(
        pair
        for pair in analysis.review_pairs
        if {item["sha256"] for item in pair["files"]} == {"e" * 64, "f" * 64}
    )

    first_decision = dedupe.resolve_review(
        batch,
        first_pair["id"],
        "e" * 64,
        "f" * 64,
        None,
        catalog_db=database,
    )
    refreshed = first_decision["refreshed_result"]
    assert all(pair["id"] != first_pair["id"] for pair in refreshed["review_pairs"])
    assert all(
        "0" * 64 in {item["sha256"] for item in pair["files"]} for pair in refreshed["review_pairs"]
    )

    remaining = refreshed["review_pairs"][0]
    second_decision = dedupe.resolve_review(
        batch,
        remaining["id"],
        remaining["files"][0]["sha256"],
        remaining["files"][1]["sha256"],
        None,
        catalog_db=database,
    )
    assert second_decision["refreshed_result"]["review_pairs"] == []


def test_identical_phash_cluster_produces_linear_review_edges(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    count = 512
    with Catalog(database) as catalog:
        batch = _source_batch(catalog, "flat-images")
        for index in range(count):
            sha256 = f"{index:064x}"
            _add_candidate(
                catalog,
                source_id="flat-images",
                batch_id=batch,
                sha256=sha256,
                name=f"flat-{index}.jpg",
                size=1_000,
                phash="0000000000000000",
                width=100,
                height=100,
                directory=str(index),
            )
    _mock_metadata(monkeypatch, lambda _path: (None, False))

    result = dedupe.run(batch, catalog_db=database)

    assert len(result.review_pairs) == count - 1
