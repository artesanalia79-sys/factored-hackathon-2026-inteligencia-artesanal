"""CLI wiring: env loading and the --check mode. No network calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankagent.ingest.cli import _load_dotenv, main


def test_load_dotenv_sets_missing_keys_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("AWS_PROFILE=factored\nS3_BUCKET=from-file\n", encoding="utf-8")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("S3_BUCKET", "from-shell")

    _load_dotenv(tmp_path)

    import os

    assert os.environ["AWS_PROFILE"] == "factored"
    assert os.environ["S3_BUCKET"] == "from-shell"  # existing env value is not overridden


def test_check_without_a_manifest_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AWS_PROFILE", "factored")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setattr("bankagent.ingest.cli.ROOT", tmp_path)

    assert main(["--check"]) == 1
    assert "run \033[0m" not in capsys.readouterr().err  # no ANSI noise expected either way


def _write_bronze_with_manifest(root: Path) -> Path:
    """One real bronze parquet file plus a manifest that matches it; returns the bronze dir."""
    from datetime import UTC, datetime

    import pyarrow as pa
    import pyarrow.parquet as pq

    from bankagent.ingest.manifest import FileEntry, Manifest, sha256_file, write_manifest

    bronze_dir = root / "data" / "bronze"
    bronze_dir.mkdir(parents=True)
    path = bronze_dir / "customers.parquet"
    pq.write_table(pa.table({"customer_id": ["CUST-FX-001", "CUST-FX-002"]}), path)

    now = datetime(2026, 6, 17, tzinfo=UTC)
    manifest = Manifest(
        aws_profile="factored",
        s3_bucket="test-bucket",
        started_at=now,
        finished_at=now,
        files=(
            FileEntry(
                table="customers",
                source_key="customers.csv",
                relative_path="customers.parquet",
                row_count=2,
                byte_size=path.stat().st_size,
                sha256=sha256_file(path),
                ingested_at=now,
            ),
        ),
    )
    write_manifest(manifest, bronze_dir / "_manifest.json")
    return bronze_dir


def _check_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_PROFILE", "factored")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setattr("bankagent.ingest.cli.ROOT", tmp_path)


def test_check_passes_when_manifest_and_files_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _check_env(tmp_path, monkeypatch)
    _write_bronze_with_manifest(tmp_path)

    assert main(["--check"]) == 0


def test_check_fails_when_bronze_content_differs_from_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: --check used to verify only that files exist, not their sha256/rows."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    _check_env(tmp_path, monkeypatch)
    bronze_dir = _write_bronze_with_manifest(tmp_path)
    pq.write_table(pa.table({"customer_id": ["CUST-FX-009"]}), bronze_dir / "customers.parquet")

    assert main(["--check"]) == 1
    assert "customers: sha256 differs" in capsys.readouterr().err


def test_check_fails_when_a_bronze_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _check_env(tmp_path, monkeypatch)
    bronze_dir = _write_bronze_with_manifest(tmp_path)
    (bronze_dir / "customers.parquet").unlink()

    assert main(["--check"]) == 1
    assert "customers.parquet is missing" in capsys.readouterr().err


def test_missing_env_vars_fail_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.setattr("bankagent.ingest.cli.ROOT", tmp_path)
    assert main([]) == 1
