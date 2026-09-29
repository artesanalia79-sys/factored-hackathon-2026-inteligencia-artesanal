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

## Hard rules

- `FORBIDDEN_COLUMNS` in `serving.py` (e.g. `is_fraud`, email, phones, address, document number,
  date of birth, full product number, geo coordinates, income, credit score, IP) never reach the
  serving DB. `is_fraud` may only be used offline for analysis and evaluation labels.
- Timestamps in the serving DB are DuckDB `TIMESTAMP` (naive) in **UTC by convention**. Money is
  `DECIMAL(15,2)` in the original currency plus `amount_usd` from a documented FX table.
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
