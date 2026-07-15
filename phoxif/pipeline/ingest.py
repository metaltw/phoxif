"""Idempotent, source-preserving ingest into the permanent catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import imagehash
from PIL import Image

from phoxif.api.scanner import scan_folder
from phoxif.pipeline.catalog import DEFAULT_CATALOG_PATH, Catalog

Mode = Literal["rescue", "inbox"]
DEFAULT_STAGING_ROOT = Path("~/.phoxif/staging").expanduser()
IMAGE_EXTENSIONS = {".bmp", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


@dataclass(frozen=True)
class BatchResult:
    """Observable result of a completed ingest batch."""

    batch_id: str
    source_id: str
    mode: Mode
    scanned: int
    new_files: int
    new_sightings: int
    already_known: int
    archived_reunions: int
    staged_files: int
    verified_staging: int
    phash_failures: int
    total_bytes: int

    def to_dict(self) -> dict[str, str | int]:
        """Return a JSON-ready representation."""
        return asdict(self)


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _image_phash(path: Path) -> str | None:
    target = path
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if path.suffix.lower() in {".heic", ".heif"}:
        temp_dir = tempfile.TemporaryDirectory(prefix="phoxif-phash-")
        target = Path(temp_dir.name) / "decoded.jpg"
        try:
            result = subprocess.run(
                ["sips", "-s", "format", "jpeg", str(path), "--out", str(target)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                return None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
    try:
        with Image.open(target) as image:
            return str(imagehash.phash(image))
    except (OSError, ValueError):
        return None
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def _iso_timestamp(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _preflight_staging(staging_root: Path, required_bytes: int) -> None:
    staging_root.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(staging_root).free
    minimum = int(required_bytes * 1.2)
    if free_bytes < minimum:
        raise OSError(
            f"Staging requires {minimum} bytes including safety margin; "
            f"only {free_bytes} bytes are free"
        )


def _reject_overlapping_roots(source_root: Path, staging_root: Path) -> None:
    """Prevent the pipeline from scanning or copying into its own work tree."""
    if staging_root.is_relative_to(source_root) or source_root.is_relative_to(staging_root):
        raise ValueError("Rescue source and staging directory must not overlap")


def _reject_catalog_inside_source(source_root: Path, catalog_db: Path) -> None:
    """Prevent SQLite and its WAL files from being created in a source tree."""
    if catalog_db.is_relative_to(source_root):
        raise ValueError("Catalog database must be outside every photo source")


def _verified_copy(path: Path | None, sha256: str) -> bool:
    """Return whether a recorded working copy still exists and is intact."""
    return (
        path is not None
        and not path.is_symlink()
        and path.is_file()
        and _sha256(path) == sha256
    )


def _quarantine_corrupt_staging(path: Path, staging_root: Path) -> None:
    """Move a corrupt phoxif-owned copy aside without deleting it."""
    if (not path.exists() and not path.is_symlink()) or not path.is_relative_to(staging_root):
        return
    quarantine = staging_root / ".corrupt"
    quarantine.mkdir(parents=True, exist_ok=True)
    path.replace(quarantine / f"{path.name}.{time.time_ns()}.corrupt")


def _stage_copy(source: Path, destination: Path, sha256: str) -> bool:
    """Publish a verified copy once without replacing any existing file."""
    if destination.is_symlink():
        raise RuntimeError(f"Staging destination cannot be a symlink: {destination}")
    if destination.exists():
        if _sha256(destination) != sha256:
            raise RuntimeError(f"Staging collision at {destination}")
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".phoxif-stage-",
        dir=destination.parent,
    ) as work_dir:
        temporary = Path(work_dir) / destination.name
        shutil.copy2(source, temporary)
        if _sha256(temporary) != sha256:
            raise RuntimeError(f"Staging verification failed for {source}")
        try:
            destination.hardlink_to(temporary)
        except FileExistsError:
            if destination.is_symlink():
                raise RuntimeError(f"Staging destination race created a symlink: {destination}")
            if _sha256(destination) != sha256:
                raise RuntimeError(f"Staging destination race at {destination}")
            return False
    return True


def run(
    source_id: str,
    root: Path,
    mode: Mode,
    *,
    label: str | None = None,
    catalog_db: Path = DEFAULT_CATALOG_PATH,
    staging_root: Path = DEFAULT_STAGING_ROOT,
) -> BatchResult:
    """Ingest one source without modifying any source-owned file.

    Args:
        source_id: Stable lowercase source slug.
        root: Source directory.
        mode: ``rescue`` copies to staging; ``inbox`` uses intake files in place.
        label: Optional human-readable source name.
        catalog_db: Catalog location.
        staging_root: Rescue working-copy root.

    Returns:
        Batch counters and persistent batch identifier.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    if mode not in {"rescue", "inbox"}:
        raise ValueError(f"Unsupported ingest mode: {mode}")

    resolved_catalog = Path(catalog_db).expanduser().resolve()
    _reject_catalog_inside_source(root, resolved_catalog)

    scan = scan_folder(root, MEDIA_EXTENSIONS)
    files = scan["files"]
    total_bytes = sum(int(file_info["size"]) for file_info in files)
    if mode == "rescue":
        resolved_staging = Path(staging_root).expanduser().resolve()
        _reject_overlapping_roots(root, resolved_staging)
        _preflight_staging(resolved_staging, total_bytes)

    with Catalog(resolved_catalog) as catalog:
        catalog.register_source(source_id, label or root.name, mode)
        batch_id = catalog.start_batch(source_id, mode)
        counters = {
            "scanned": len(files),
            "new_files": 0,
            "new_sightings": 0,
            "already_known": 0,
            "archived_reunions": 0,
            "staged_files": 0,
            "verified_staging": 0,
            "phash_failures": 0,
            "total_bytes": total_bytes,
        }

        try:
            for file_info in files:
                source_path = Path(file_info["path"]).resolve()
                stat = source_path.stat()
                sha256 = _sha256(source_path)
                extension = source_path.suffix.lower()
                media_type = "image" if extension in IMAGE_EXTENSIONS else "video"
                phash = _image_phash(source_path) if media_type == "image" else None
                if media_type == "image" and phash is None:
                    counters["phash_failures"] += 1

                existing = catalog.file(sha256)
                archived = existing is not None and existing["status"] == "archived"
                duplicate = existing is not None and existing["status"] == "duplicate"

                staging_path: Path | None
                if mode == "rescue" and not archived and not duplicate:
                    exact_copy = catalog.sighting_staging_path(
                        sha256,
                        source_id,
                        source_path,
                    )
                    candidates = [
                        candidate
                        for candidate in [exact_copy, *catalog.rescue_staging_paths(sha256)]
                        if candidate is not None
                    ]
                    staging_path = next(
                        (candidate for candidate in candidates if _verified_copy(candidate, sha256)),
                        None,
                    )
                    if staging_path is None:
                        resolved_staging = Path(staging_root).expanduser().resolve()
                        for candidate in candidates:
                            if (candidate.exists() or candidate.is_symlink()) and not _verified_copy(
                                candidate,
                                sha256,
                            ):
                                _quarantine_corrupt_staging(candidate, resolved_staging)
                        staging_path = (
                            resolved_staging
                            / "objects"
                            / sha256[:2]
                            / f"{sha256}{extension}"
                        )
                        if (
                            staging_path.exists() or staging_path.is_symlink()
                        ) and not _verified_copy(staging_path, sha256):
                            _quarantine_corrupt_staging(staging_path, resolved_staging)
                        if _stage_copy(source_path, staging_path, sha256):
                            counters["staged_files"] += 1
                    if not _verified_copy(staging_path, sha256):
                        raise RuntimeError(f"Working-copy verification failed for {source_path}")
                    counters["verified_staging"] += 1
                elif mode == "inbox":
                    staging_path = source_path
                else:
                    staging_path = None

                final_stat = source_path.stat()
                if (
                    final_stat.st_size != stat.st_size
                    or final_stat.st_mtime_ns != stat.st_mtime_ns
                ):
                    raise RuntimeError(f"Source changed during ingest: {source_path}")

                birthtime = getattr(stat, "st_birthtime", None)
                record, created, new_sighting = catalog.record_ingest(
                    sha256=sha256,
                    size=stat.st_size,
                    ext=extension,
                    media_type=media_type,
                    phash=phash,
                    width=file_info.get("width"),
                    height=file_info.get("height"),
                    source_id=source_id,
                    batch_id=batch_id,
                    original_path=source_path,
                    original_name=source_path.name,
                    original_mtime=_iso_timestamp(stat.st_mtime),
                    original_btime=_iso_timestamp(birthtime),
                    staging_path=staging_path,
                )
                counters["new_files" if created else "already_known"] += 1
                if new_sighting:
                    counters["new_sightings"] += 1
                elif mode == "rescue" and staging_path is not None:
                    catalog.update_sighting_staging_path(
                        sha256,
                        source_id,
                        source_path,
                        staging_path,
                    )
                if record["status"] == "archived":
                    counters["archived_reunions"] += 1
                    if mode == "inbox":
                        catalog.queue_archived_reunion(
                            batch_id=batch_id,
                            sha256=sha256,
                            source_path=source_path,
                        )

            result = BatchResult(
                batch_id=batch_id,
                source_id=source_id,
                mode=mode,
                **counters,
            )
            catalog.finish_batch(batch_id, result.to_dict())
            return result
        except Exception as error:
            catalog.fail_batch(batch_id, str(error))
            raise


def _main() -> None:
    """Run one catalog ingest from the command line."""
    parser = argparse.ArgumentParser(description="Safely ingest a photo source")
    parser.add_argument("root", type=Path, help="Photo source directory")
    parser.add_argument("--source", required=True, help="Stable lowercase source ID")
    parser.add_argument("--mode", choices=("rescue", "inbox"), default="rescue")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--staging", type=Path, default=DEFAULT_STAGING_ROOT)
    args = parser.parse_args()
    result = run(
        args.source,
        args.root,
        args.mode,
        catalog_db=args.catalog,
        staging_root=args.staging,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    _main()
