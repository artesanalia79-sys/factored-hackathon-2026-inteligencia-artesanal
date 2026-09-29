# Decision ledger

Small decisions, data findings and budget spends that do not merit an ADR. One row each, newest
first. Link the query, run or PR that supports it.

| Date | Area | Decision / finding | Evidence | Who |
|---|---|---|---|---|
| 2026-09-27 | serving | Serving DB timestamps are naive `TIMESTAMP` in UTC by convention (not TIMESTAMPTZ, avoids pytz on fetch). | `docs/rules/data-pipeline.md` | Santiago |
| 2026-09-27 | fixtures | Fixture determinism is checked by a content hash of all rows ordered by primary key, not by file bytes (DuckDB files are not byte-stable). | `src/bankagent/fixtures/builder.py` | Santiago |
| 2026-09-27 | policy | Business date is a configurable `as_of_date` (fixtures: 2026-06-17, end of the dataset) stored in `_serving_metadata`. | `docs/rules/data-pipeline.md` | Santiago |
| 2026-09-27 | tools | Tools raise `NotFound` for both "not yours" and "does not exist" to avoid existence disclosure. | `docs/rules/backend.md` | Santiago |
| 2026-09-27 | hosting | AWS App Runner is closed to new customers since Apr 30 2026; staging on Render free, final host decided at freeze. | ADR 0001, plan T15/T26 | Santiago |
| 2026-09-28 | ingest | Table-to-S3-key matching (`matches_table`) instead of a fixed prefix layout: the organizer dictionary documents table schemas, not the bucket's folder structure. Fails loudly (`SourceTableNotFound`) if a table has no match. | `src/bankagent/ingest/catalog.py`, PR for T4 | Santiago |
| 2026-09-28 | ingest | Bronze columns are all read and written as strings (no type coercion at ingest); typing and dq flags are the silver layer's job (T5), so a bronze re-run never silently changes semantics. | `src/bankagent/ingest/bronze.py` | Santiago |
| 2026-09-28 | ingest | Drift is row-count-or-content based per table (sha256 of the bronze parquet), not per-row diffing; a content change with the same row count is reported but a per-row diff is deferred to T5's dq flags. | `src/bankagent/ingest/manifest.py` | Santiago |
