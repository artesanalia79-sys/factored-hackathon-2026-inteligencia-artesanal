# data_pipeline

Owners: Santiago (T4), Juan José (T5, T6). Rules: `docs/rules/data-pipeline.md`. Skill: `dbt-modeling`.

| Step | Task | Command | Output (gitignored) |
|---|---|---|---|
| Ingest organizer data to bronze + manifest | T4 | `uv run poe ingest` | `data/raw/`, `data/bronze/*.parquet`, `data/bronze/_manifest.json` |
| dbt silver (contracts, dq flags, tests) | T5 | `uv run poe dbt-build` | `data/warehouse.duckdb` |
| dbt gold + serving DB | T6 | `uv run poe dbt-build` | `data/serving/bank_curated.duckdb` |

The public demo uses the synthetic fixture bank instead (`uv run poe fixtures`,
`data/fixtures/bank_fixture.duckdb`). Both serving DBs follow `src/bankagent/contracts/serving.py`.
