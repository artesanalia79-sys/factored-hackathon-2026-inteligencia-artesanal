# data_pipeline

Owners: Santiago (T4, T5), Juan José (T6, T19). Rules: `docs/rules/data-pipeline.md`. Skill: `dbt-modeling`.

| Step | Task | Command | Output (gitignored) |
|---|---|---|---|
| Ingest organizer data to bronze + manifest | T4 | `uv run poe ingest` | `data/raw/`, `data/bronze/*.parquet`, `data/bronze/_manifest.json` |
| dbt silver (contracts, dq flags, tests) | T5 | `uv run poe dbt-build` | `data/warehouse.duckdb` |
| Silver evidence (aggregates only) | T5 | `uv run poe dq-report` | `docs/evidence/dq_report.md`, `docs/evidence/fraud_score_thresholds.md` (committed) |
| dbt gold + curated serving DB (validated) | T6 | `uv run poe serving-build` | `data/warehouse.duckdb` (`gold_*`), `data/serving/bank_curated.duckdb` |
| The agent end to end on the curated serving DB | T19 | `uv run poe curated-check`, `uv run poe curated-e2e` | `data/runtime/curated_e2e/` (conversations); `docs/evidence/curated_e2e.md` (counts only, committed) |

The public demo uses the synthetic fixture bank instead (`uv run poe fixtures`,
`data/fixtures/bank_fixture.duckdb`). Both serving DBs follow `src/bankagent/contracts/serving.py`.

## Silver (T5)

Needs `uv sync --group data` (dbt-core 1.12.5, dbt-duckdb 1.11.0). `uv run poe dbt-build`
(`src/bankagent/silver/cli.py`) first verifies every bronze file against `_manifest.json`
(sha256 + row count, `<table>.parquet` paths, lists every failing table), drops any previous
`_silver_build_metadata`, runs `dbt build --select tag:silver` (models + tests; gold is excluded) and, only if it
succeeds, writes
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

## Gold and serving DB (T6)

`uv run poe serving-build` (`src/bankagent/gold/cli.py`) needs a full, successful `poe dbt-build`
of the current bronze manifest (it reads `_silver_build_metadata` and refuses a partial or stale
silver). It runs `dbt build --select tag:gold`, creates the serving DB from the contract DDL
(`serving.ddl()`), copies the six serving models into it, runs `validate_serving_db()` and only
then replaces `data/serving/bank_curated.duckdb`; any problem leaves the previous file untouched.
It prints the silver vs gold row counts.

- Models: `dbt/models/gold/gold_<serving table>.sql` (`gold_serving_metadata` →
  `_serving_metadata`), contracts in `_gold_models.yml`, mappings and windows in
  `dbt_project.yml` `vars` (`gold_*`). No gold model selects a FORBIDDEN_COLUMN.
- `gold_transactions_enriched` is incremental with a 3-day lookback on `process_date`; use
  `uv run poe serving-build --full-refresh` to reload rows older than the window.
- `gold_cc_contact_baseline` and `gold_complaints_baseline` stay in the warehouse for T16; they are
  not served.
- Runtime selection (T19): `DATA_MODE=curated` opens `data/serving/bank_curated.duckdb`; the
  service refuses a file that records another mode or does not fit the contract (local only;
  the public demo stays on the fixture bank). `uv run poe curated-check` checks the file,
  including every row against the views the runtime parses it into, and
  `uv run poe curated-e2e` runs the agent on it (`docs/evidence/curated_e2e.md`).
- CI builds gold on the synthetic silver plus labelled `*-GD-*` rows (`tests/gold/`), including a
  synthetic late-arriving row; the real data has none.
- Decisions and their evidence queries (`dbt/analyses/gold_*.sql`): `docs/decision_ledger.md`.
