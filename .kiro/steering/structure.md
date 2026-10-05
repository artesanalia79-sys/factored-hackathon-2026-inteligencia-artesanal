---
inclusion: always
---

# Structure and conventions

The repository map and commands are in `AGENTS.md`. These are the coding conventions.

## Python

- Package `bankagent` in `src/` layout. Import shared types from `bankagent.contracts` only.
- Type hints everywhere; pyright `standard` must pass. Prefer Pydantic models (frozen,
  `extra="forbid"`) over dicts at module boundaries; dataclasses for internal plumbing.
- Money is `Decimal`, never `float`. Contract timestamps are timezone-aware UTC (`AwareDatetime`).
- Ruff (line length 100) formats and lints. No bare `except`; raise typed errors from
  `bankagent.contracts.errors`.
- Module layout per component: `__init__.py` docstring with owner and plan task, public functions
  first, private helpers prefixed with `_`.

## Tests

- pytest under `tests/`, mirroring `src/bankagent/` (`tests/tools/test_writes.py`).
- Tests use the synthetic fixture bank and the `StubProvider`; no network, no paid APIs, no
  organizer data. Name tests after behavior: `test_other_customers_transaction_is_not_found`.

## Naming

- Files and modules: `snake_case`. Classes: `PascalCase`. Enum values: `snake_case` strings,
  except enums that mirror source data (segment, statuses, channel), which keep the source values
  (`"Approved"`, `"Premium"`) so the serving DB needs no remapping.
- Fixture IDs: `CUST-FX-###`, `CARD-FX-###`, `TXN-FX-####`, `CMP-FX-###`, `AGT-FX-##`.
- Policy rule IDs: `DSP-<AREA>-<NN>` (e.g. `DSP-WIN-01`), stable once published.
- Branches: `t<task>-<slug>`. Commits: conventional commits in English.

## Documentation

- ADRs in `docs/adr/NNNN-title.md` (template `0000-template.md`).
- Small decisions and data findings: one line in `docs/decision_ledger.md`.
- Generated files (`docs/contracts/`) are never edited by hand.
