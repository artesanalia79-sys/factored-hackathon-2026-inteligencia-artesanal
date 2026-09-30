"""Verify bronze parquet files against `_manifest.json` before any silver build."""

from __future__ import annotations

from pathlib import Path

import duckdb

from bankagent.ingest.catalog import TABLES
from bankagent.ingest.manifest import Manifest, load_manifest, sha256_file

MANIFEST_NAME = "_manifest.json"


class BronzeVerificationError(RuntimeError):
    """Bronze does not match its manifest; `problems` lists one line per failing table."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("bronze verification failed:\n  - " + "\n  - ".join(problems))
        self.problems = problems


def verify_bronze(bronze_dir: Path) -> Manifest:
    """Return the manifest if every table file exists with the recorded sha256 and row count.

    Collects every problem (missing tables, a path other than `<table>.parquet`, missing files,
    hash or row-count mismatch) before failing, so one run lists all broken tables.
    """
    manifest = load_manifest(bronze_dir / MANIFEST_NAME)
    if manifest is None:
        raise BronzeVerificationError([f"{bronze_dir / MANIFEST_NAME} not found"])
    problems: list[str] = []
    entries = manifest.by_table
    for table in TABLES:
        if table not in entries:
            problems.append(f"{table}: not in manifest")
    con = duckdb.connect()
    try:
        for table, entry in sorted(entries.items()):
            # dbt sources read `$BRONZE_DIR/<table>.parquet` (models/bronze/sources.yml), so the
            # verified file must be exactly that one.
            expected = f"{table}.parquet"
            if entry.relative_path != expected:
                problems.append(
                    f"{table}: manifest relative_path {entry.relative_path!r} is not {expected!r} "
                    "(the file dbt reads)"
                )
                continue
            path = bronze_dir / entry.relative_path
            if not path.exists():
                problems.append(f"{table}: file {entry.relative_path} is missing")
                continue
            if sha256_file(path) != entry.sha256:
                problems.append(f"{table}: sha256 differs from manifest")
                continue
            row = con.execute("SELECT count(*) FROM read_parquet(?)", [path.as_posix()]).fetchone()
            rows = int(row[0]) if row else -1
            if rows != entry.row_count:
                problems.append(f"{table}: {rows} rows, manifest says {entry.row_count}")
    finally:
        con.close()
    if problems:
        raise BronzeVerificationError(problems)
    return manifest
