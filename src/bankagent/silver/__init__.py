"""Silver layer (dbt-core + dbt-duckdb): verified bronze -> typed, deduplicated, dq-flagged tables.

Owner: Santiago (Task 5). Requires the `data` dependency group: `uv sync --group data`.
Rules: `docs/rules/data-pipeline.md`. dbt project: `data_pipeline/dbt/`.

- `verify`: check bronze parquet against `_manifest.json` (sha256 + row counts) before a build.
- `build`: run dbt programmatically and record `_silver_build_metadata` in the warehouse.
- `synthetic`: deterministic synthetic bronze (with seeded defects) for CI tests.
- `report`: aggregate-only `docs/evidence/dq_report.md` and fraud-threshold evidence.
"""
