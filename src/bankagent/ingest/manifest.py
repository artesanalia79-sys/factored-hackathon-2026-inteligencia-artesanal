"""The bronze manifest: what was ingested, its size and its sha256, plus drift detection.

`_manifest.json` sits next to the bronze files (`data/bronze/_manifest.json`, gitignored) and is
the only way we know a re-run produced the same data as last time, or changed because the
organizer's source data changed underneath us.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from pydantic import AwareDatetime, Field

from bankagent.contracts.base import Contract

MANIFEST_VERSION = 1
CHUNK_SIZE = 1024 * 1024


class FileEntry(Contract):
    """One bronze parquet file produced from one source table."""

    table: str
    source_key: str = Field(description="S3 key it was ingested from")
    relative_path: str = Field(description="path under data/bronze/, POSIX separators")
    row_count: int = Field(ge=0)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ingested_at: AwareDatetime


class Manifest(Contract):
    """The full ingest manifest: one entry per table, plus run metadata."""

    manifest_version: int = MANIFEST_VERSION
    data_mode: str = "curated"
    aws_profile: str
    s3_bucket: str
    started_at: AwareDatetime
    finished_at: AwareDatetime
    files: tuple[FileEntry, ...]

    @property
    def by_table(self) -> dict[str, FileEntry]:
        return {entry.table: entry for entry in self.files}


def sha256_file(path: Path) -> str:
    """Stream a file through sha256 without loading it fully into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> Manifest | None:
    if not path.exists():
        return None
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))


def write_manifest(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(manifest.model_dump_json())
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


class DriftKind:
    """Human labels for the kinds of drift `diff_manifests` can report."""

    NEW_TABLE = "new_table"
    MISSING_TABLE = "missing_table"
    ROW_COUNT_CHANGED = "row_count_changed"
    CONTENT_CHANGED = "content_changed"


def diff_manifests(previous: Manifest, current: Manifest) -> list[str]:
    """Return a human-readable line per drifting table. Empty means no drift."""
    problems: list[str] = []
    before, after = previous.by_table, current.by_table
    for table in sorted(after.keys() - before.keys()):
        problems.append(f"{DriftKind.NEW_TABLE}: {table} (was not in the previous manifest)")
    for table in sorted(before.keys() - after.keys()):
        problems.append(f"{DriftKind.MISSING_TABLE}: {table} (missing from this run)")
    for table in sorted(before.keys() & after.keys()):
        old, new = before[table], after[table]
        if old.row_count != new.row_count:
            problems.append(
                f"{DriftKind.ROW_COUNT_CHANGED}: {table} {old.row_count} -> {new.row_count} rows"
            )
        elif old.sha256 != new.sha256:
            problems.append(f"{DriftKind.CONTENT_CHANGED}: {table} same row count, content differs")
    return problems


def utc_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)
