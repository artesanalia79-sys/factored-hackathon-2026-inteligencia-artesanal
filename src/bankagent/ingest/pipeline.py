"""Orchestrate one ingest run: list, download, convert to bronze, write the manifest, check drift.

`run_ingest` takes the S3 listing/downloading functions as parameters (`list_objects_fn`,
`download_fn`) so tests can supply a fake source and never touch the network or real credentials.
The default CLI (`cli.py`) wires the real `bankagent.ingest.s3_source` implementation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bankagent.ingest.bronze import concat_tables, read_source_table, write_bronze_table
from bankagent.ingest.catalog import TABLES, SourceObject
from bankagent.ingest.manifest import (
    FileEntry,
    Manifest,
    diff_manifests,
    load_manifest,
    sha256_file,
    utc_now,
    write_manifest,
)

logger = logging.getLogger(__name__)

ListObjectsFn = Callable[[], list[SourceObject]]
DownloadFn = Callable[[list[SourceObject], Path], list[Path]]


class IngestDriftError(RuntimeError):
    """Bronze content changed since the previous manifest and drift was not explicitly allowed."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("bronze drift detected:\n" + "\n".join(f"  - {p}" for p in problems))
        self.problems = problems


DEFAULT_SOURCE_PREFIX = "data/"
"""The organizer bucket root also holds sibling folders (e.g. a dated backup) that are not part
of the active dataset. Scoping every listing to this prefix keeps them out. Override with the
`INGEST_SOURCE_PREFIX` env var only if the organizers change the bucket layout."""


@dataclass(frozen=True, slots=True)
class IngestConfig:
    aws_profile: str
    s3_bucket: str
    region: str
    source_prefix: str
    raw_dir: Path
    bronze_dir: Path
    manifest_path: Path
    tables: tuple[str, ...] = TABLES
    allow_drift: bool = False


def resolve_config(*, root: Path, allow_drift: bool = False) -> IngestConfig:
    """Build an `IngestConfig` from `.env` values already loaded into the process environment."""
    import os

    missing = [key for key in ("AWS_PROFILE", "S3_BUCKET") if not os.environ.get(key)]
    if missing:
        raise ValueError(
            f"missing required environment variable(s): {', '.join(missing)}. "
            "Run `uv run poe init-env` and fill them from the organizer data dictionary."
        )
    return IngestConfig(
        aws_profile=os.environ["AWS_PROFILE"],
        s3_bucket=os.environ["S3_BUCKET"],
        region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        source_prefix=os.environ.get("INGEST_SOURCE_PREFIX", DEFAULT_SOURCE_PREFIX),
        raw_dir=root / "data" / "raw",
        bronze_dir=root / "data" / "bronze",
        manifest_path=root / "data" / "bronze" / "_manifest.json",
        allow_drift=allow_drift,
    )


def _ingest_table(
    table: str, objects: list[SourceObject], *, config: IngestConfig, download_fn: DownloadFn
) -> FileEntry:
    from bankagent.ingest.s3_source import resolve_table_keys

    matches = resolve_table_keys(objects, table)
    raw_paths = download_fn(matches, config.raw_dir)
    arrow_table = concat_tables([read_source_table(path) for path in raw_paths])

    bronze_path = config.bronze_dir / f"{table}.parquet"
    write_bronze_table(arrow_table, bronze_path)

    return FileEntry(
        table=table,
        source_key=matches[0].key
        if len(matches) == 1
        else f"{len(matches)} objects (see raw/{table}*)",
        relative_path=bronze_path.relative_to(config.bronze_dir).as_posix(),
        row_count=arrow_table.num_rows,
        byte_size=bronze_path.stat().st_size,
        sha256=sha256_file(bronze_path),
        ingested_at=utc_now(),
    )


def run_ingest(
    config: IngestConfig, *, list_objects_fn: ListObjectsFn, download_fn: DownloadFn
) -> tuple[Manifest, list[str]]:
    """Run the full ingest. Returns (new manifest, drift problems vs. the previous manifest).

    Raises `IngestDriftError` if drift is found and `config.allow_drift` is False.
    """
    started_at = utc_now()
    objects = list_objects_fn()
    logger.info("listed %d objects in s3://%s", len(objects), config.s3_bucket)

    entries = [
        _ingest_table(table, objects, config=config, download_fn=download_fn)
        for table in config.tables
    ]
    manifest = Manifest(
        aws_profile=config.aws_profile,
        s3_bucket=config.s3_bucket,
        started_at=started_at,
        finished_at=utc_now(),
        files=tuple(entries),
    )

    previous = load_manifest(config.manifest_path)
    problems = diff_manifests(previous, manifest) if previous is not None else []
    if problems and not config.allow_drift:
        raise IngestDriftError(problems)

    write_manifest(manifest, config.manifest_path)
    return manifest, problems
