"""Run dbt gold (`tag:gold`) on a full, current silver build, then write the serving DB.

The serving DB is created from the contract DDL (`serving.ddl()`: exact column order, types,
NOT NULL and primary keys), filled from the gold models, checked with `validate_serving_db()`
and only then moved into place. Any problem leaves the previous serving DB untouched.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import duckdb

from bankagent.contracts.enums import DataMode
from bankagent.contracts.serving import (
    SERVING_CONTRACT_VERSION,
    SERVING_TABLES,
    ddl,
    validate_serving_db,
)
from bankagent.ingest.manifest import sha256_file
from bankagent.silver.build import (
    BUILD_SCOPE_FULL,
    GOLD_SELECTOR,
    METADATA_TABLE,
    BuildConfig,
    DbtBuildError,
    DbtRun,
    run_dbt,
)
from bankagent.silver.verify import MANIFEST_NAME
from bankagent.store.selection import SERVING_DBS

# The file the runtime opens with DATA_MODE=curated (T19).
DEFAULT_SERVING_DB = SERVING_DBS[DataMode.CURATED]

# Serving table -> gold model (`_serving_metadata` -> `gold_serving_metadata`).
GOLD_MODELS: dict[str, str] = {
    table.name: "gold_" + table.name.removeprefix("_") for table in SERVING_TABLES
}
# Silver model each served entity comes from (for the silver vs gold row reconciliation).
SILVER_SOURCES: dict[str, str] = {
    "customer_profile_min": "silver_customers",
    "customer_cards": "silver_products",
    "transactions_enriched": "silver_transactions",
    "dispute_history": "silver_complaints",
    "agents_routing": "silver_service_agents",
}


class ServingBuildError(RuntimeError):
    """Silver is not buildable-on, or the serving DB failed its contract; one line per problem."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("serving build failed:\n  - " + "\n  - ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class ServingResult:
    run: DbtRun
    serving_db: Path
    metadata: dict[str, str]
    rows: dict[str, int]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def read_silver_metadata(config: BuildConfig) -> dict[str, str]:
    """Return `_silver_build_metadata` if silver is a full build of the current bronze manifest."""
    if not config.warehouse.exists():
        raise ServingBuildError([f"{config.warehouse} not found: run `uv run poe dbt-build` first"])
    con = duckdb.connect(str(config.warehouse))
    try:
        found = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [METADATA_TABLE]
        ).fetchone()
        if not found or not found[0]:
            raise ServingBuildError(
                [f"{METADATA_TABLE} missing: the last silver build failed or never ran"]
            )
        meta = dict(con.execute(f"SELECT key, value FROM {METADATA_TABLE}").fetchall())  # noqa: S608
    finally:
        con.close()

    problems: list[str] = []
    if meta.get("build_scope") != BUILD_SCOPE_FULL:
        problems.append(
            f"silver build_scope is {meta.get('build_scope')!r}, not 'full' "
            f"(selection {meta.get('selection')!r}): run `uv run poe dbt-build` without --select"
        )
    manifest = config.bronze_dir / MANIFEST_NAME
    if not manifest.exists():
        problems.append(f"{manifest} not found")
    elif sha256_file(manifest) != meta.get("manifest_sha256"):
        problems.append(
            "silver was built from another bronze manifest: run `uv run poe dbt-build` again"
        )
    if problems:
        raise ServingBuildError(problems)
    return meta


def build_gold(config: BuildConfig, silver_meta: dict[str, str], *, full_refresh: bool) -> DbtRun:
    """`dbt build --select tag:gold` with the serving vars (contract version, mode, source)."""
    dbt_vars = {
        "serving_contract_version": SERVING_CONTRACT_VERSION,
        "serving_data_mode": silver_meta["data_mode"],
        "serving_source": (
            f"dbt gold on silver built from bronze manifest sha256 {silver_meta['manifest_sha256']}"
        ),
    }
    args = ["build", "--select", GOLD_SELECTOR, "--vars", json.dumps(dbt_vars)]
    if full_refresh:
        args.append("--full-refresh")
    run = run_dbt(config, args)
    if not run.success:
        failed = ", ".join(f"{n.unique_id} ({n.status})" for n in run.failed()) or "see dbt log"
        raise DbtBuildError(f"dbt gold build failed: {failed}", run=run)
    return run


def write_serving_db(warehouse: Path, dest: Path) -> tuple[dict[str, str], dict[str, int]]:
    """Create the serving DB at `dest` from the gold models; return (metadata, rows per table).

    Built in `<dest>.tmp` and moved into place only when `validate_serving_db()` finds nothing.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    _remove_db(tmp)
    try:
        _fill(warehouse, tmp)
        con = duckdb.connect(str(tmp))
        try:
            problems = validate_serving_db(con)
            if problems:
                raise ServingBuildError(problems)
            rows = {table.name: _count(con, table.name) for table in SERVING_TABLES}
            metadata = dict(con.execute("SELECT key, value FROM _serving_metadata").fetchall())
        finally:
            con.close()
    except duckdb.Error as exc:
        _remove_db(tmp)
        raise ServingBuildError([f"{type(exc).__name__}: {exc}"]) from exc
    except ServingBuildError:
        _remove_db(tmp)
        raise
    _remove_db(dest)
    os.replace(tmp, dest)
    return metadata, rows


def _fill(warehouse: Path, tmp: Path) -> None:
    # Attach the new file to a warehouse connection (dbt-duckdb may still hold the warehouse
    # open in this process, so the warehouse is not attached to a second instance).
    con = duckdb.connect(str(warehouse))
    try:
        (database,) = con.execute("SELECT current_database()").fetchone() or ("",)
        source_db = _quote(database)
        con.execute(f"ATTACH {_literal(tmp.as_posix())} AS serving_out")
        try:
            con.execute("USE serving_out")
            con.execute(ddl())
            for table in SERVING_TABLES:
                columns = ", ".join(_quote(name) for name in table.column_names)
                con.execute(
                    f"INSERT INTO {_quote(table.name)} ({columns}) "  # noqa: S608
                    f"SELECT {columns} FROM {source_db}.main.{_quote(GOLD_MODELS[table.name])}"
                )
            con.execute("CHECKPOINT serving_out")
        finally:
            con.execute(f"USE {source_db}")
            con.execute("DETACH serving_out")
    finally:
        con.close()


def _count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    row = con.execute(f"SELECT count(*) FROM {_quote(table)}").fetchone()  # noqa: S608
    return int(row[0]) if row else 0


def _remove_db(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + ".wal")):
        candidate.unlink(missing_ok=True)


def layer_counts(warehouse: Path) -> list[tuple[str, str, int, int]]:
    """(serving table, silver source, silver rows, gold rows) for every served entity."""
    con = duckdb.connect(str(warehouse))
    try:
        return [
            (table, source, _count(con, source), _count(con, GOLD_MODELS[table]))
            for table, source in SILVER_SOURCES.items()
        ]
    finally:
        con.close()


def build_serving(
    config: BuildConfig, serving_db: Path = DEFAULT_SERVING_DB, *, full_refresh: bool = False
) -> ServingResult:
    """Check silver, build gold, then write and validate the serving DB."""
    silver_meta = read_silver_metadata(config)
    run = build_gold(config, silver_meta, full_refresh=full_refresh)
    metadata, rows = write_serving_db(config.warehouse, serving_db)
    return ServingResult(run=run, serving_db=serving_db, metadata=metadata, rows=rows)
