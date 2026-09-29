"""Manifest hashing, serialization and drift detection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bankagent.ingest.manifest import (
    DriftKind,
    FileEntry,
    Manifest,
    diff_manifests,
    load_manifest,
    sha256_file,
    write_manifest,
)

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def _entry(table: str, *, row_count: int = 10, sha256: str = "a" * 64) -> FileEntry:
    return FileEntry(
        table=table,
        source_key=f"{table}.csv",
        relative_path=f"{table}.parquet",
        row_count=row_count,
        byte_size=1024,
        sha256=sha256,
        ingested_at=NOW,
    )


def _manifest(*entries: FileEntry) -> Manifest:
    return Manifest(
        aws_profile="factored",
        s3_bucket="test-bucket",
        started_at=NOW,
        finished_at=NOW,
        files=entries,
    )


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello world" * 1000)
    import hashlib

    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha256_file(path) == expected


def test_write_and_load_round_trip(tmp_path: Path) -> None:
    manifest = _manifest(_entry("customers"), _entry("products"))
    path = tmp_path / "_manifest.json"
    write_manifest(manifest, path)

    loaded = load_manifest(path)
    assert loaded == manifest
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_load_manifest_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_manifest(tmp_path / "does_not_exist.json") is None


def test_no_drift_when_manifests_match() -> None:
    manifest = _manifest(_entry("customers"))
    assert diff_manifests(manifest, manifest) == []


def test_detects_new_and_missing_tables() -> None:
    before = _manifest(_entry("customers"))
    after = _manifest(_entry("customers"), _entry("products"))
    assert diff_manifests(before, after) == [
        f"{DriftKind.NEW_TABLE}: products (was not in the previous manifest)"
    ]
    assert diff_manifests(after, before) == [
        f"{DriftKind.MISSING_TABLE}: products (missing from this run)"
    ]


def test_detects_row_count_change() -> None:
    before = _manifest(_entry("customers", row_count=100))
    after = _manifest(_entry("customers", row_count=101))
    problems = diff_manifests(before, after)
    assert problems == [f"{DriftKind.ROW_COUNT_CHANGED}: customers 100 -> 101 rows"]


def test_detects_content_change_with_same_row_count() -> None:
    before = _manifest(_entry("customers", sha256="a" * 64))
    after = _manifest(_entry("customers", sha256="b" * 64))
    problems = diff_manifests(before, after)
    assert problems == [f"{DriftKind.CONTENT_CHANGED}: customers same row count, content differs"]


def test_identical_content_is_not_drift() -> None:
    before = _manifest(_entry("customers", sha256="a" * 64))
    after = _manifest(_entry("customers", sha256="a" * 64))
    assert diff_manifests(before, after) == []
