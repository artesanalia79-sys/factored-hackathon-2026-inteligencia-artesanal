"""Generate aggregate-only evidence from the silver warehouse (`uv run poe dq-report`).

Writes `docs/evidence/dq_report.md` (row counts and dq_* flag counts per model, plus the
analyses in `data_pipeline/dbt/analyses/`) and `docs/evidence/fraud_score_thresholds.md`.
Only counts, rates and quantiles are written: never row-level values, IDs or names.
Refuses to run (exit 1) unless the warehouse carries `_silver_build_metadata` of a successful
full build, so committed evidence always matches the manifest it quotes.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from bankagent.silver.build import (
    BRONZE_ROWS_PREFIX,
    BUILD_SCOPE_FULL,
    DBT_PROJECT_DIR,
    DEFAULT_WAREHOUSE,
    METADATA_TABLE,
    ROOT,
)

EVIDENCE_DIR = ROOT / "docs" / "evidence"
ANALYSES_DIR = DBT_PROJECT_DIR / "analyses"
_REF = re.compile(r"\{\{\s*ref\(\s*'([a-z0-9_]+)'\s*\)\s*\}\}")


class IncompleteBuildError(RuntimeError):
    """The warehouse has no build metadata or comes from a partial (`--select`) build."""


def build_metadata(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Return `_silver_build_metadata` of a full build; raise `IncompleteBuildError` otherwise.

    Evidence must describe the tables it was computed from: metadata is dropped before every
    build and written only after a successful full `uv run poe dbt-build`.
    """
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [METADATA_TABLE]
    ).fetchone()
    if not exists or exists[0] == 0:
        raise IncompleteBuildError(
            f"{METADATA_TABLE} not found: the last build failed, is running, or bypassed "
            "`uv run poe dbt-build`. Re-run it before generating evidence."
        )
    metadata = dict(con.execute(f"SELECT key, value FROM {METADATA_TABLE}").fetchall())  # noqa: S608
    scope = metadata.get("build_scope")
    if scope != BUILD_SCOPE_FULL:
        selection = metadata.get("selection") or "unknown"
        raise IncompleteBuildError(
            f"last build was not full (build_scope={scope!r}, selection={selection!r}); "
            "run `uv run poe dbt-build` without --select before generating evidence."
        )
    return metadata


def render_analysis(name: str) -> str:
    """Read an analysis SQL file and resolve `{{ ref('x') }}` to the warehouse table `x`."""
    sql = (ANALYSES_DIR / f"{name}.sql").read_text(encoding="utf-8")
    return _REF.sub(lambda match: match.group(1), sql)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_fmt(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def _query(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    cursor = con.execute(sql)
    headers = [d[0] for d in cursor.description or []]
    return headers, cursor.fetchall()


def _silver_models(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name LIKE 'silver_%' ORDER BY table_name"
    ).fetchall()
    return [r[0] for r in rows]


def _flag_columns(con: duckdb.DuckDBPyConnection, model: str) -> list[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? "
        "AND column_name LIKE 'dq_%' ORDER BY ordinal_position",
        [model],
    ).fetchall()
    return [r[0] for r in rows]


def model_counts(
    con: duckdb.DuckDBPyConnection, metadata: dict[str, str]
) -> dict[str, dict[str, int | None]]:
    """Per silver model: bronze/silver row reconciliation plus the count of each dq_* flag.

    `bronze_rows` comes from the manifest recorded in the build metadata. Every bronze row with
    a business key is counted once in its key's `dq_duplicate_count`, so
    `dropped_keyless = bronze_rows - sum(dq_duplicate_count)` is exactly the number of bronze
    rows excluded for a NULL/empty key.
    """
    per_model: dict[str, dict[str, int | None]] = {}
    for model in _silver_models(con):
        flags = [f for f in _flag_columns(con, model) if f != "dq_duplicate_count"]
        selects = ", ".join(f"count(*) FILTER (WHERE {f})" for f in flags)
        sql = (
            "SELECT count(*), coalesce(sum(dq_duplicate_count), 0), "  # noqa: S608
            f"count(*) FILTER (WHERE dq_duplicate_count > 1), {selects} FROM {model}"
        )
        result = con.execute(sql).fetchone() or ()
        silver_rows, keyed_bronze_rows, dup_keys, *flag_counts = (int(v) for v in result)
        bronze_raw = metadata.get(f"{BRONZE_ROWS_PREFIX}{model.removeprefix('silver_')}")
        bronze_rows = int(bronze_raw) if bronze_raw is not None else None
        per_model[model] = {
            "bronze_rows": bronze_rows,
            "silver_rows": silver_rows,
            "dropped_keyless": None if bronze_rows is None else bronze_rows - keyed_bronze_rows,
            "dup_keys": dup_keys,
            **dict(zip(flags, flag_counts, strict=True)),
        }
    return per_model


def dq_report(con: duckdb.DuckDBPyConnection) -> str:
    metadata = build_metadata(con)
    header = {k: v for k, v in metadata.items() if not k.startswith(BRONZE_ROWS_PREFIX)}
    parts = [
        "# Silver data-quality report",
        "",
        "Generated by `uv run poe dq-report` from the silver warehouse. Aggregates only. "
        "Flag definitions: `data_pipeline/dbt/models/silver/_silver_models.yml`.",
        "",
        markdown_table(["key", "value"], sorted(header.items())),
        "",
        "## Rows and flags per model",
        "",
        "`bronze_rows` from the verified manifest; `dropped_keyless` = bronze rows excluded for "
        "a NULL/empty business key (bronze_rows - sum of dq_duplicate_count); `dup_keys` = "
        "business keys seen more than once in bronze (one row kept). Flag columns count silver "
        "rows where the flag is true (blank = the model has no such flag).",
        "",
    ]
    rows: list[list[Any]] = []
    per_model = model_counts(con, metadata)
    flag_names = {k for counts in per_model.values() for k in counts if k.startswith("dq_")}
    columns = [
        "bronze_rows",
        "silver_rows",
        "dropped_keyless",
        "dup_keys",
        *sorted(flag_names - {"dq_any"}),
        "dq_any",
    ]
    for model, counts in per_model.items():
        rows.append([model, *[counts.get(c, "") for c in columns]])
    parts.append(markdown_table(["model", *columns], rows))
    for title, name in (
        ("Exchange-rate grain", "fx_grain"),
        ("amount_usd: fixed-rate flags and daily-FX sensitivity", "amount_usd_vs_fx"),
        ("process_date lag vs. UTC event date", "process_date_lag"),
    ):
        headers, result_rows = _query(con, render_analysis(name))
        parts += ["", f"## {title}", "", f"`data_pipeline/dbt/analyses/{name}.sql`", ""]
        parts.append(markdown_table(headers, result_rows))
    return "\n".join(parts) + "\n"


def fraud_report(con: duckdb.DuckDBPyConnection) -> str:
    parts = [
        "# fraud_score threshold analysis (offline, ADR 0003)",
        "",
        "Generated by `uv run poe dq-report`. Uses the offline-only `is_fraud` label; aggregates "
        "only. `lift` = precision / base rate (1.0 = no better than random escalation at the "
        "same rate). `recall` counts every fraud row in the denominator, including fraud rows "
        "with a NULL fraud_score (no score rule can escalate them); `recall_scored_only` "
        "excludes those. Verdict: `docs/decision_ledger.md`, ADR 0003.",
    ]
    for title, name in (
        ("Distribution by label", "fraud_score_distribution"),
        ("Score bands around the threshold", "fraud_score_bands"),
        ("Thresholds (escalate when fraud_score >= threshold)", "fraud_score_thresholds"),
    ):
        headers, result_rows = _query(con, render_analysis(name))
        parts += ["", f"## {title}", "", f"`data_pipeline/dbt/analyses/{name}.sql`", ""]
        parts.append(markdown_table(headers, result_rows))
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=DEFAULT_WAREHOUSE)
    parser.add_argument("--out-dir", type=Path, default=EVIDENCE_DIR)
    args = parser.parse_args(argv)
    if not args.warehouse.exists():
        print(f"ERROR: {args.warehouse} not found; run `uv run poe dbt-build`.", file=sys.stderr)
        return 1
    con = duckdb.connect(str(args.warehouse), read_only=True)
    try:
        outputs = {"dq_report.md": dq_report(con), "fraud_score_thresholds.md": fraud_report(con)}
    except IncompleteBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        con.close()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for filename, text in outputs.items():
        (args.out_dir / filename).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out_dir / filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
