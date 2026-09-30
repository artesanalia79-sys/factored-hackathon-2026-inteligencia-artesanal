"""CLI for `uv run poe dbt-build`: verify bronze against its manifest, then `dbt build`.

Usage:
    python -m bankagent.silver.cli                      # data/bronze -> data/warehouse.duckdb
    python -m bankagent.silver.cli --memory-limit 4GB --threads 2
    python -m bankagent.silver.cli --select silver_transactions+   # partial build

`_silver_build_metadata` is dropped before dbt runs and rewritten only after a successful build,
recording `build_scope` (full / partial) and the selection.

Env overrides: BRONZE_DIR, WAREHOUSE_PATH, DUCKDB_MEMORY_LIMIT, DBT_THREADS.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from bankagent.silver.build import (
    DEFAULT_BRONZE_DIR,
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_WAREHOUSE,
    BuildConfig,
    DbtBuildError,
    build_silver,
)
from bankagent.silver.verify import BronzeVerificationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--bronze-dir", type=Path, default=Path(os.environ.get("BRONZE_DIR", DEFAULT_BRONZE_DIR))
    )
    parser.add_argument(
        "--warehouse", type=Path, default=Path(os.environ.get("WAREHOUSE_PATH", DEFAULT_WAREHOUSE))
    )
    parser.add_argument(
        "--memory-limit", default=os.environ.get("DUCKDB_MEMORY_LIMIT", DEFAULT_MEMORY_LIMIT)
    )
    parser.add_argument("--threads", type=int, default=int(os.environ.get("DBT_THREADS", "4")))
    parser.add_argument(
        "--select",
        help="dbt node selection passed to `dbt build --select`; recorded as a partial build, "
        "which `dq-report` refuses",
    )
    args = parser.parse_args(argv)

    config = BuildConfig(
        bronze_dir=args.bronze_dir,
        warehouse=args.warehouse,
        memory_limit=args.memory_limit,
        threads=args.threads,
    )
    started = time.perf_counter()
    try:
        run = build_silver(config, select=args.select)
    except BronzeVerificationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Re-run `uv run poe ingest` (or `ingest-check`) to repair bronze.", file=sys.stderr)
        return 1
    except DbtBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - started
    warned = [n.unique_id for n in run.nodes if n.status == "warn"]
    scope = f"partial ({args.select})" if args.select else "full"
    print(
        f"Silver built ({scope}) in {elapsed:.0f}s: {len(run.nodes)} nodes, "
        f"{len(warned)} warnings "
        f"(memory_limit={config.memory_limit}, threads={config.threads}) -> {config.warehouse}"
    )
    for unique_id in warned:
        print(f"  warn: {unique_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
