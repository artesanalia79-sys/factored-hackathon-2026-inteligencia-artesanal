---
name: dbt-modeling
description: Add or change dbt models (dbt-core + dbt-duckdb) with enforced contracts, data-quality flags, tests, docs and lineage for the bronze/silver/gold layers. Use when editing data_pipeline/dbt.
---

# dbt modeling

Read `docs/rules/data-pipeline.md` first. Project: `data_pipeline/dbt/` (Tasks 5, 6).

## Steps

1. Put the model in the right layer folder: `models/silver/` or `models/gold/`.
   Name: `silver_<entity>.sql`, `gold_<table>.sql` (gold names match the serving contract).
2. Write the SQL with CTEs: `source` → `renamed` → `typed` → `flagged` → `final`.
   Flag problems with boolean `dq_*` columns instead of dropping rows silently.
3. Declare the contract and tests in the model YAML:

```yaml
models:
  - name: silver_transactions
    description: One row per transaction_id, typed, with data-quality flags.
    config:
      contract: {enforced: true}
      materialized: table
    columns:
      - name: transaction_id
        data_type: varchar
        constraints: [{type: not_null}, {type: primary_key}]
        data_tests: [unique, not_null]
      - name: transaction_status
        data_type: varchar
        data_tests:
          - accepted_values: {arguments: {values: ["Approved", "Declined", "Pending", "Reversed"]}}
      - name: product_id
        data_type: varchar
        data_tests:
          - relationships: {arguments: {to: ref('silver_products'), field: product_id}}
```

4. Run `uv run poe dbt-build` (runs `dbt build` = models + tests). Fix failures; do not delete
   tests to go green. Use `severity: warn` only for documented, known source issues.
5. For incremental models: `materialized: incremental`, `unique_key`, and a lookback window for
   late-arriving rows. Add a fixture that proves a late row is picked up.
6. Gold models that feed the serving DB must match `src/bankagent/contracts/serving.py` exactly;
   never select a column from `FORBIDDEN_COLUMNS`.
7. Regenerate docs/lineage: `dbt docs generate` (published by Task 21 at `/lineage`).

## Checklist

- [ ] Contract enforced, every column typed.
- [ ] Keys: `unique` + `not_null`; foreign keys: `relationships`; enums: `accepted_values`.
- [ ] Every `dq_*` flag documented, counted in `dq_report.md`.
- [ ] Findings that change business numbers logged in `docs/decision_ledger.md`.
- [ ] No organizer data committed (seeds are synthetic only).
