# data_pipeline

Owners: Santiago (T4, T5), Juan José (T6). Rules: `docs/rules/data-pipeline.md`. Skill: `dbt-modeling`.

| Step | Task | Command | Output (gitignored) |
|---|---|---|---|
| Ingest organizer data to bronze + manifest | T4 | `uv run poe ingest` | `data/raw/`, `data/bronze/*.parquet`, `data/bronze/_manifest.json` |
| dbt silver (contracts, dq flags, tests) | T5 | `uv run poe dbt-build` | `data/warehouse.duckdb` |
| Silver evidence (aggregates only) | T5 | `uv run poe dq-report` | `docs/evidence/dq_report.md`, `docs/evidence/fraud_score_thresholds.md` (committed) |
| dbt gold + serving DB | T6 | `uv run poe dbt-build` | `data/serving/bank_curated.duckdb` |

The public demo uses the synthetic fixture bank instead (`uv run poe fixtures`,
`data/fixtures/bank_fixture.duckdb`). Both serving DBs follow `src/bankagent/contracts/serving.py`.

## Silver (T5)

Needs `uv sync --group data` (dbt-core 1.12.5, dbt-duckdb 1.11.0). `uv run poe dbt-build`
(`src/bankagent/silver/cli.py`) first verifies every bronze file against `_manifest.json`
(sha256 + row count, `<table>.parquet` paths, lists every failing table), drops any previous
`_silver_build_metadata`, runs `dbt build` (models + tests) and, only if it succeeds, writes
`_silver_build_metadata` (manifest sha256, data_mode, per-table bronze rows, `build_scope`
full/partial and the `--select` string, dbt/duckdb versions, UTC built_at). `uv run poe dq-report`
refuses to run when that table is missing or the build was partial.

- Project: `dbt/` (`dbt_project.yml`, committed `profiles.yml` without secrets). Sources read
  `$BRONZE_DIR/<table>.parquet` directly.
- Env overrides (or CLI flags): `BRONZE_DIR` (default `data/bronze`), `WAREHOUSE_PATH`
  (`data/warehouse.duckdb`), `DUCKDB_MEMORY_LIMIT` (`6GB`), `DBT_THREADS` (`4`).
- Models: `dbt/models/silver/silver_<table>.sql` (CTEs source → renamed → typed → flagged →
  final), contracts and tests in `_silver_models.yml`, enums in `dbt_project.yml` `vars.enums`.
- Full build on the curated bronze (23.5M rows): ~8 minutes on a 12-thread, 15 GB laptop
  (see `docs/decision_ledger.md`).
- CI builds silver on deterministic synthetic bronze with seeded defects
  (`src/bankagent/silver/synthetic.py`, `tests/silver/`).
- Partial rebuilds: `uv run poe dbt-build --select <selector>` (verified, recorded as partial).
  Calling `dbt` directly bypasses the manifest verification and never writes build metadata, so
  its tables cannot back committed evidence; use it only for local debugging.
