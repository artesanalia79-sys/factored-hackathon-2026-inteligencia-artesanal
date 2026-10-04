# Data pipeline rules

Applies to `data_pipeline/**`, `src/bankagent/fixtures/**` and `tests/fixtures/**`.
Owners: Santiago (Tasks 4, 5), Juan José (Tasks 6, 16, 19). Skills: `dbt-modeling`, `data-contracts`.

## Sources and secrets

- Organizer data is downloaded with the AWS CLI profile from `AWS_PROFILE` (default `factored`).
  Bucket and region come from `.env` (`S3_BUCKET`, `AWS_DEFAULT_REGION`). Never hard-code them.
- Raw files land in `data/raw/`, bronze parquet in `data/bronze/`. Everything under `data/` is
  gitignored and blocked by pre-commit. Never commit organizer-derived rows, even samples.
- Every ingest writes `data/bronze/_manifest.json` with row counts and sha256 per file, and fails on
  unexpected drift against the previous manifest. Implementation: `src/bankagent/ingest/`
  (`uv run poe ingest`, needs `uv sync --group data`); use `--allow-drift` when a source change is
  expected, `--check` to verify the current manifest without downloading anything.

## Layers

- **Bronze**: faithful copy, typed as strings where the source is ambiguous. No business logic.
- **Silver** (dbt): typed, deduplicated, one row per business key, `dq_*` boolean flags instead of
  silently dropping rows. Every model has a contract (`contract: {enforced: true}`) and tests
  (`unique`, `not_null`, `relationships`, `accepted_values`) for its keys and enums.
- **Gold / serving**: exactly the tables and columns in `src/bankagent/contracts/serving.py`
  (`SERVING_CONTRACT_VERSION`). The build must call `validate_serving_db()` and fail on mismatch.
  Implementation: `uv run poe serving-build` (`src/bankagent/gold/`, models in
  `data_pipeline/dbt/models/gold/`); `uv run poe dbt-build` builds silver only.
- **Runtime** (Task 19): `DATA_MODE` picks the serving DB (`synthetic`: the fixture bank;
  `curated`: `data/serving/bank_curated.duckdb`, local only). The service refuses a file that
  records another mode or fails `validate_serving_db()` (`src/bankagent/store/selection.py`).
  After rebuilding the curated serving DB run `uv run poe curated-check` (also parses every
  row into the views the runtime uses) and `uv run poe curated-e2e` (the agent end to end on
  it), and commit the regenerated `docs/evidence/curated_e2e.md`.

## Hard rules

- `FORBIDDEN_COLUMNS` in `serving.py` (e.g. `is_fraud`, email, phones, address, document number,
  date of birth, full product number, geo coordinates, income, credit score, IP) never reach the
  serving DB. `is_fraud` may only be used offline for analysis and evaluation labels.
- Timestamps in the serving DB are DuckDB `TIMESTAMP` (naive) in **UTC by convention**. Money is
  `DECIMAL(15,2)` in the original currency plus `amount_usd` from a **fixed rate per currency**,
  never the daily FX table. Curated follows the source's own convention
  (`vars.amount_usd_fixed_rate` in `data_pipeline/dbt/dbt_project.yml`: ARS 350, COP 4000; USD is
  `amount`); synthetic uses `fx_rates_per_usd` in `tests/fixtures/bank/fixture.yaml`. The daily FX
  table is exposed only as silver's `fx_rate_to_usd`, for sensitivity. Do not switch `amount_usd`
  to daily rates: it would mix two conventions in one column (decision ledger, 2026-09-29).
- `_serving_metadata` must record `data_mode`, `as_of_date`, `built_at`, `source` and
  `contract_version`. Dispute windows are computed against `as_of_date`, not the wall clock.
- Findings that change a business number (duplicates, incoherent codes, late arrivals) go to
  `docs/decision_ledger.md` with the query that reproduces them.

## Fixture bank (synthetic, public)

- `tests/fixtures/bank/*.yaml` holds team-authored synthetic personas only. No real names, no
  organizer rows. The builder is deterministic (seeded) and its content hash is committed in
  `tests/fixtures/bank/content_hash.txt`.
- After editing fixture YAML: `uv run poe fixtures --update-hash`, then `uv run poe fixtures-check`.
- Evaluation cases reference fixture IDs (`CUST-FX-*`, `TXN-FX-*`); changing an ID breaks cases.
