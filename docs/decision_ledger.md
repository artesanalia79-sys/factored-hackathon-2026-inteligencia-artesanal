# Decision ledger

Small decisions, data findings and budget spends that do not merit an ADR. One row each, newest
first. Link the query, run or PR that supports it.

| Date | Area | Decision / finding | Evidence | Who |
|---|---|---|---|---|
| 2026-09-28 | interpreter | Use an API-compatible transport schema followed by strict `InterpretationResult` validation; the shared Decimal schema contains lookaround unsupported by OpenAI Structured Outputs. Keep MLflow autologging off until traces can exclude raw PII. | `src/bankagent/interpret/openai_provider.py`, `docs/evidence/t10_interpreter_dev.md` | Jacobo |
| 2026-09-27 | serving | Serving DB timestamps are naive `TIMESTAMP` in UTC by convention (not TIMESTAMPTZ, avoids pytz on fetch). | `docs/rules/data-pipeline.md` | Santiago |
| 2026-09-27 | fixtures | Fixture determinism is checked by a content hash of all rows ordered by primary key, not by file bytes (DuckDB files are not byte-stable). | `src/bankagent/fixtures/builder.py` | Santiago |
| 2026-09-27 | policy | Business date is a configurable `as_of_date` (fixtures: 2026-06-17, end of the dataset) stored in `_serving_metadata`. | `docs/rules/data-pipeline.md` | Santiago |
| 2026-09-27 | tools | Tools raise `NotFound` for both "not yours" and "does not exist" to avoid existence disclosure. | `docs/rules/backend.md` | Santiago |
| 2026-09-27 | hosting | AWS App Runner is closed to new customers since Apr 30 2026; staging on Render free, final host decided at freeze. | ADR 0001, plan T15/T26 | Santiago |
