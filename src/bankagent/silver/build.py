"""Run dbt (programmatic `dbtRunner`) against verified bronze and record build metadata."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import duckdb

from bankagent.ingest.manifest import Manifest, sha256_file
from bankagent.silver.verify import MANIFEST_NAME, verify_bronze

ROOT = Path(__file__).resolve().parents[3]
DBT_PROJECT_DIR = ROOT / "data_pipeline" / "dbt"
DEFAULT_BRONZE_DIR = ROOT / "data" / "bronze"
DEFAULT_WAREHOUSE = ROOT / "data" / "warehouse.duckdb"
DEFAULT_MEMORY_LIMIT = "6GB"
METADATA_TABLE = "_silver_build_metadata"
BUILD_SCOPE_FULL = "full"
BUILD_SCOPE_PARTIAL = "partial"
BRONZE_ROWS_PREFIX = "bronze_rows."
SILVER_SELECTOR = "tag:silver"
GOLD_SELECTOR = "tag:gold"


class DbtBuildError(RuntimeError):
    """dbt reported a failure (a model error or an error-severity test).

    ``run`` holds the per-node results when dbt got far enough to report them.
    """

    def __init__(self, message: str, run: DbtRun | None = None) -> None:
        super().__init__(message)
        self.run = run


@dataclass(frozen=True, slots=True)
class BuildConfig:
    bronze_dir: Path = DEFAULT_BRONZE_DIR
    warehouse: Path = DEFAULT_WAREHOUSE
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    threads: int = 4
    # dbt writes target/ and logs/ here; defaults to the (gitignored) project folders.
    target_path: Path | None = None
    log_path: Path | None = None


@dataclass(slots=True)
class NodeResult:
    unique_id: str
    resource_type: str
    status: str
    failures: int | None = None


@dataclass(slots=True)
class DbtRun:
    success: bool
    nodes: list[NodeResult] = field(default_factory=list)

    def failed(self) -> list[NodeResult]:
        return [n for n in self.nodes if n.status in {"error", "fail", "skipped"}]


@contextmanager
def _env(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def run_dbt(config: BuildConfig, args: Sequence[str]) -> DbtRun:
    """Invoke one dbt command (e.g. ``["build"]``) with env vars derived from ``config``."""
    from dbt.cli.main import dbtRunner

    config.warehouse.parent.mkdir(parents=True, exist_ok=True)
    cli = [
        *args,
        "--project-dir",
        str(DBT_PROJECT_DIR),
        "--profiles-dir",
        str(DBT_PROJECT_DIR),
    ]
    # Absolute paths: dbt resolves relative ones against the project dir, which would drop
    # artefacts inside the repo (data_pipeline/dbt/...).
    if config.target_path is not None:
        cli += ["--target-path", str(config.target_path.resolve())]
    if config.log_path is not None:
        cli += ["--log-path", str(config.log_path.resolve())]
    env = {
        "BRONZE_DIR": config.bronze_dir.resolve().as_posix(),
        "WAREHOUSE_PATH": config.warehouse.resolve().as_posix(),
        "DUCKDB_MEMORY_LIMIT": config.memory_limit,
        "DBT_THREADS": str(config.threads),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    with _env(env):
        result = dbtRunner().invoke(cli)
    nodes: list[NodeResult] = []
    for item in getattr(result.result, "results", None) or []:
        node = item.node
        nodes.append(
            NodeResult(
                unique_id=node.unique_id,
                resource_type=str(node.resource_type),
                status=str(item.status),
                failures=item.failures,
            )
        )
    if result.exception is not None:
        raise DbtBuildError(f"dbt {' '.join(args)} crashed: {result.exception}")
    return DbtRun(success=bool(result.success), nodes=nodes)


def write_build_metadata(
    warehouse: Path, bronze_dir: Path, manifest: Manifest, selection: str | None = None
) -> dict[str, str]:
    """Replace `_silver_build_metadata` (key/value) with this build's provenance.

    ``selection`` is the dbt ``--select`` string of a partial build (``build_scope=partial``);
    ``None`` means every model and test was built (``build_scope=full``). Per-table bronze row
    counts are stored as ``bronze_rows.<table>`` so reports can reconcile bronze vs. silver.
    """
    values = {
        "manifest_sha256": sha256_file(bronze_dir / MANIFEST_NAME),
        "data_mode": manifest.data_mode,
        "build_scope": BUILD_SCOPE_FULL if selection is None else BUILD_SCOPE_PARTIAL,
        "selection": selection or "",
        "dbt_version": version("dbt-core"),
        "dbt_duckdb_version": version("dbt-duckdb"),
        "duckdb_version": duckdb.__version__,
        "built_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "bronze_row_count": str(sum(entry.row_count for entry in manifest.files)),
    }
    for entry in manifest.files:
        values[f"{BRONZE_ROWS_PREFIX}{entry.table}"] = str(entry.row_count)
    con = duckdb.connect(str(warehouse))
    try:
        con.execute(f"CREATE OR REPLACE TABLE {METADATA_TABLE} (key VARCHAR, value VARCHAR)")
        con.executemany(f"INSERT INTO {METADATA_TABLE} VALUES (?, ?)", list(values.items()))  # noqa: S608
    finally:
        con.close()
    return values


def drop_build_metadata(warehouse: Path) -> None:
    """Remove `_silver_build_metadata` so a failed or interrupted build leaves none behind."""
    if not warehouse.exists():
        return
    con = duckdb.connect(str(warehouse))
    try:
        con.execute(f"DROP TABLE IF EXISTS {METADATA_TABLE}")
    finally:
        con.close()


def build_silver(config: BuildConfig, select: str | None = None) -> DbtRun:
    """Verify bronze, drop stale metadata, run `dbt build`, then record metadata.

    ``select`` restricts the build (``dbt build --select``) and is recorded as a partial build,
    which `dq-report` refuses. Raises `BronzeVerificationError` or `DbtBuildError` on failure;
    metadata is only written after a successful build.
    """
    manifest = verify_bronze(config.bronze_dir)
    drop_build_metadata(config.warehouse)
    # Silver only: gold (T6) is built afterwards by `poe serving-build` with the vars it needs,
    # so it is excluded even when a selector such as `silver_transactions+` reaches it.
    args = ["build", "--select", select or SILVER_SELECTOR, "--exclude", GOLD_SELECTOR]
    run = run_dbt(config, args)
    if not run.success:
        failed = ", ".join(f"{n.unique_id} ({n.status})" for n in run.failed()) or "see dbt log"
        raise DbtBuildError(f"dbt build failed: {failed}", run=run)
    write_build_metadata(config.warehouse, config.bronze_dir, manifest, selection=select)
    return run
