"""End-to-end ingest with a fake S3 (no network, no organizer data, no real credentials)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bankagent.ingest.catalog import SourceObject, SourceTableNotFound
from bankagent.ingest.manifest import load_manifest
from bankagent.ingest.pipeline import IngestConfig, IngestDriftError, resolve_config, run_ingest

TWO_TABLES = ("customers", "products")


class FakeBucket:
    """An in-memory S3 bucket: `key -> csv text`. Downloads copy from here to disk."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def list_objects(self) -> list[SourceObject]:
        return [SourceObject(key=key, size=len(text)) for key, text in self.files.items()]

    def download(self, objects: list[SourceObject], dest_dir: Path) -> list[Path]:
        paths = []
        for obj in objects:
            target = dest_dir / obj.key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.files[obj.key], encoding="utf-8")
            paths.append(target)
        return paths


def _config(tmp_path: Path, *, allow_drift: bool = False) -> IngestConfig:
    return IngestConfig(
        aws_profile="factored",
        s3_bucket="test-bucket",
        region="us-east-1",
        source_prefix="",
        raw_dir=tmp_path / "raw",
        bronze_dir=tmp_path / "bronze",
        manifest_path=tmp_path / "bronze" / "_manifest.json",
        tables=TWO_TABLES,
        allow_drift=allow_drift,
    )


def _fixture_bucket() -> FakeBucket:
    return FakeBucket(
        {
            "customers.csv": "customer_id,segment\nCUST-FX-001,Premium\nCUST-FX-002,Plus\n",
            "products.csv": "product_id,customer_id\nCARD-FX-011,CUST-FX-001\n",
        }
    )


def test_first_run_writes_bronze_and_manifest(tmp_path: Path) -> None:
    bucket = _fixture_bucket()
    config = _config(tmp_path)

    manifest, problems = run_ingest(
        config, list_objects_fn=bucket.list_objects, download_fn=bucket.download
    )

    assert problems == []
    assert {entry.table for entry in manifest.files} == set(TWO_TABLES)
    assert manifest.by_table["customers"].row_count == 2
    assert (config.bronze_dir / "customers.parquet").exists()
    assert config.manifest_path.exists()


def test_rerun_without_source_changes_has_no_drift(tmp_path: Path) -> None:
    bucket = _fixture_bucket()
    config = _config(tmp_path)
    run_ingest(config, list_objects_fn=bucket.list_objects, download_fn=bucket.download)
    shutil.rmtree(config.raw_dir)

    manifest, problems = run_ingest(
        config, list_objects_fn=bucket.list_objects, download_fn=bucket.download
    )
    assert problems == []
    reloaded = load_manifest(config.manifest_path)
    assert reloaded is not None
    assert manifest.by_table["customers"].sha256 == reloaded.by_table["customers"].sha256


def test_changed_row_count_is_detected_and_blocked(tmp_path: Path) -> None:
    config = _config(tmp_path)
    run_ingest(
        _config(tmp_path),
        list_objects_fn=_fixture_bucket().list_objects,
        download_fn=_fixture_bucket().download,
    )

    grown = FakeBucket(
        {
            "customers.csv": (
                "customer_id,segment\nCUST-FX-001,Premium\nCUST-FX-002,Plus\nCUST-FX-003,Basic\n"
            ),
            "products.csv": "product_id,customer_id\nCARD-FX-011,CUST-FX-001\n",
        }
    )
    with pytest.raises(IngestDriftError, match="customers"):
        run_ingest(config, list_objects_fn=grown.list_objects, download_fn=grown.download)

    # The manifest on disk still reflects the last accepted (non-drifted) run.
    reloaded = load_manifest(config.manifest_path)
    assert reloaded is not None
    assert reloaded.by_table["customers"].row_count == 2


def test_allow_drift_accepts_and_records_the_change(tmp_path: Path) -> None:
    run_ingest(
        _config(tmp_path),
        list_objects_fn=_fixture_bucket().list_objects,
        download_fn=_fixture_bucket().download,
    )

    grown = FakeBucket(
        {
            "customers.csv": (
                "customer_id,segment\nCUST-FX-001,Premium\nCUST-FX-002,Plus\nCUST-FX-003,Basic\n"
            ),
            "products.csv": "product_id,customer_id\nCARD-FX-011,CUST-FX-001\n",
        }
    )
    manifest, problems = run_ingest(
        _config(tmp_path, allow_drift=True),
        list_objects_fn=grown.list_objects,
        download_fn=grown.download,
    )
    assert len(problems) == 1
    assert manifest.by_table["customers"].row_count == 3


def test_sibling_backup_folder_is_excluded_by_the_source_prefix(tmp_path: Path) -> None:
    """Regression test: the real bucket root also holds a dated backup folder. Without scoping
    the listing to a prefix, `list_objects_fn` would return both copies and every table would be
    double-counted."""
    bucket = FakeBucket(
        {
            "data/customers.csv": "customer_id,segment\nCUST-FX-001,Premium\n",
            "data/products.csv": "product_id,customer_id\nCARD-FX-011,CUST-FX-001\n",
            "data_backup_20260831/customers.csv": ("customer_id,segment\nCUST-FX-001,Premium\n"),
            "data_backup_20260831/products.csv": (
                "product_id,customer_id\nCARD-FX-011,CUST-FX-001\n"
            ),
        }
    )

    def list_under_data() -> list[SourceObject]:
        return [obj for obj in bucket.list_objects() if obj.key.startswith("data/")]

    manifest, problems = run_ingest(
        _config(tmp_path), list_objects_fn=list_under_data, download_fn=bucket.download
    )
    assert problems == []
    assert manifest.by_table["customers"].row_count == 1
    assert manifest.by_table["customers"].source_key == "data/customers.csv"


def test_missing_table_in_bucket_raises_source_table_not_found(tmp_path: Path) -> None:
    bucket = FakeBucket({"customers.csv": "customer_id\nCUST-FX-001\n"})
    with pytest.raises(SourceTableNotFound, match="products"):
        run_ingest(
            _config(tmp_path), list_objects_fn=bucket.list_objects, download_fn=bucket.download
        )


def test_resolve_config_reads_env_and_requires_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_PROFILE", "factored")
    monkeypatch.setenv("S3_BUCKET", "my-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    config = resolve_config(root=tmp_path)
    assert config.s3_bucket == "my-bucket"
    assert config.region == "us-east-2"
    assert config.source_prefix == "data/"
    assert config.raw_dir == tmp_path / "data" / "raw"

    monkeypatch.delenv("S3_BUCKET")
    with pytest.raises(ValueError, match="S3_BUCKET"):
        resolve_config(root=tmp_path)


def test_resolve_config_source_prefix_is_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_PROFILE", "factored")
    monkeypatch.setenv("S3_BUCKET", "my-bucket")
    monkeypatch.setenv("INGEST_SOURCE_PREFIX", "custom/")
    assert resolve_config(root=tmp_path).source_prefix == "custom/"
