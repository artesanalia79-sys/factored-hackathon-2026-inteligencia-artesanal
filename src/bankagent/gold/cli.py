"""CLI for `uv run poe serving-build`: dbt gold on verified silver, then the curated serving DB.

Usage:
    python -m bankagent.gold.cli                  # -> data/serving/bank_curated.duckdb
    python -m bankagent.gold.cli --full-refresh   # rebuild the incremental model from scratch
    python -m bankagent.gold.cli --serving-db /tmp/serving.duckdb

Needs a full, successful `uv run poe dbt-build` of the current bronze manifest. The runtime reads
the result with `DATA_MODE=curated` and `SERVING_DB_PATH=data/serving/bank_curated.duckdb`.

Env overrides: BRONZE_DIR, WAREHOUSE_PATH, DUCKDB_MEMORY_LIMIT, DBT_THREADS.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from bankagent.gold.build import (
    DEFAULT_SERVING_DB,
    ServingBuildError,
    build_serving,
    layer_counts,
)
from bankagent.silver.build import (
    DEFAULT_BRONZE_DIR,
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_WAREHOUSE,
    BuildConfig,
    DbtBuildError,
)


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
    parser.add_argument("--serving-db", type=Path, default=DEFAULT_SERVING_DB)
    parser.add_argument(
        "--memory-limit", default=os.environ.get("DUCKDB_MEMORY_LIMIT", DEFAULT_MEMORY_LIMIT)
    )
    parser.add_argument("--threads", type=int, default=int(os.environ.get("DBT_THREADS", "4")))
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="rebuild the incremental gold model from scratch (late rows older than the lookback)",
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
        result = build_serving(config, args.serving_db, full_refresh=args.full_refresh)
    except (ServingBuildError, DbtBuildError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - started
    warned = [n.unique_id for n in result.run.nodes if n.status == "warn"]
    print(
        f"Serving DB built in {elapsed:.0f}s: {len(result.run.nodes)} gold nodes, "
        f"{len(warned)} warnings, validate_serving_db OK -> {result.serving_db}"
    )
    for unique_id in warned:
        print(f"  warn: {unique_id}")
    print("  metadata: " + ", ".join(f"{k}={v}" for k, v in sorted(result.metadata.items())))
    print(f"  {'serving table':24} {'silver source':32} {'silver':>12} {'gold':>12}")
    for table, source, silver_rows, gold_rows in layer_counts(config.warehouse):
        print(f"  {table:24} {source:32} {silver_rows:>12,} {gold_rows:>12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
