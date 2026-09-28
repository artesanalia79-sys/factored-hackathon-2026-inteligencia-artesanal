---
name: data-contracts
description: Change the shared Pydantic contracts or the serving-table contract safely, regenerate JSON Schemas and keep every consumer in sync. Use when editing src/bankagent/contracts.
---

# Changing shared contracts

Contracts in `src/bankagent/contracts/` are the interface between lanes (data, backend, eval, UI).
A careless change breaks three teammates at once.

## Rules

- All models inherit `Contract` (`extra="forbid"`, `frozen=True`). Money is `Decimal`; timestamps
  are `AwareDatetime` (UTC). Enum values are `snake_case` strings, except enums mirroring source
  data (segment, statuses, channel), which keep the source values.
- **Additive changes** (new optional field, new enum value) are fine in a normal PR.
- **Breaking changes** (rename, remove, type change, new required field) need: a note in the PR,
  a heads-up to the affected owners, and updates to every consumer in the same PR.
- Invariants live in validators (e.g. `PolicyDecision`, `RouterResult`). Do not weaken a validator
  to make a test pass; fix the producer.
- Tool argument models never contain `customer_id` (a test enforces it).
- The serving contract (`serving.py`) changes require bumping `SERVING_CONTRACT_VERSION`
  (semver: major for breaking) and coordinating with the dbt gold models.

## Steps

1. Edit the model and its tests (`tests/contracts/`).
2. `uv run poe contracts` to regenerate `docs/contracts/*.schema.json` and `serving_tables.json`.
3. If fixtures are affected: `uv run poe fixtures --update-hash`.
4. `uv run poe check`, `uv run poe contracts-check`, `uv run poe fixtures-check`.
5. Commit the regenerated files together with the code change.
