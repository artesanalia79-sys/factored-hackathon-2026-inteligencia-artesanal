## What and why

Plan task: T<!-- number --> · Closes #<!-- issue -->

<!-- One or two sentences: what changes and why. -->

## How it was tested

<!-- Commands you ran and what you observed. -->

## Definition of Done

- [ ] `uv run poe check` passes locally (ruff, pyright, pytest)
- [ ] New behavior has tests; bug fixes include a regression test
- [ ] Contracts changed → `uv run poe contracts` output committed
- [ ] Fixture YAML changed → `uv run poe fixtures --update-hash` committed
- [ ] Skills changed only in `.agents/skills/` and `uv run poe sync-skills` run
- [ ] No secrets, organizer data, `data/`, DuckDB/parquet/SQLite or held-out files
- [ ] No `customer_id` reaches prompts, LLM outputs or tool arguments
- [ ] Writes need confirmation and are verified by read-back (if applicable)
- [ ] Trade-offs recorded (ADR or `docs/decision_ledger.md`)
- [ ] Code, comments and docs in English; customer-facing text in ES/PT only

## Blocked or follow-ups

<!-- Anything not done, with owner and task number. -->
