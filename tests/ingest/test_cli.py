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


def test_check_passes_when_manifest_and_files_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from bankagent.ingest.manifest import FileEntry, Manifest, write_manifest

    monkeypatch.setenv("AWS_PROFILE", "factored")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setattr("bankagent.ingest.cli.ROOT", tmp_path)

    bronze_dir = tmp_path / "data" / "bronze"
    bronze_dir.mkdir(parents=True)
    (bronze_dir / "customers.parquet").write_bytes(b"fake parquet bytes")

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
                row_count=1,
                byte_size=19,
                sha256="a" * 64,
                ingested_at=now,
            ),
        ),
    )
    write_manifest(manifest, bronze_dir / "_manifest.json")

    assert main(["--check"]) == 0


def test_missing_env_vars_fail_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.setattr("bankagent.ingest.cli.ROOT", tmp_path)
    assert main([]) == 1
