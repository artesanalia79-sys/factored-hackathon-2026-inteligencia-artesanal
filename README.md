# Inteligencia Artesanal: AI-first dispute intake for LATAM Bank

Factored AI & Data Hackathon 2026. A chat agent that takes a transaction dispute from the first
customer message (Spanish or Portuguese) to a **verified** dispute case, optionally blocking the
card, or to a complete human handoff. It checks whether the customer recognizes the charge before
opening a dispute, applies a versioned policy, asks for explicit confirmation, and verifies every
write by reading it back.

> Status: M0 foundations (harness, contracts, fixture bank). See
> [`docs/plan/implementation_plan.md`](docs/plan/implementation_plan.md).

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) ≥ 0.12, git, Node 22 (web UI only).

```powershell
uv sync                 # installs Python 3.12 and pinned dependencies into .venv
uv run poe init-env     # creates .env from .env.example (prints key names only)
uv run poe hooks        # installs pre-commit hooks (secrets, data, format)
uv run poe fixtures     # builds the synthetic fixture bank (data/fixtures/bank_fixture.duckdb)
uv run poe check        # lint + format + types + tests
```

To download the organizer dataset (Task 4) store the provided AWS keys in a local profile, never in
files: `aws configure --profile factored`.

## How it works

```
AUTH → UNDERSTAND (router gate, then LLM) → IDENTIFY_TXN → RECOGNIZE → CHECK_POLICY
     → CONFIRM → ACT → VERIFY (read-back) → RESPOND        | CLARIFY | ABSTAIN | ESCALATE
```

- The LLM only interprets the message; a deterministic state machine decides.
- The model never sees or chooses `customer_id`; identity comes from the server session.
- Writes need a single-use confirmation token and are verified by read-back.

Details: [`docs/adr/0001-architecture.md`](docs/adr/0001-architecture.md).

## Repository

| Path | What |
|---|---|
| `AGENTS.md` | rules for humans and coding agents (Kiro, Claude Code, Codex) |
| `src/bankagent/` | Python package (contracts, tools, policy, orchestrator, API) |
| `data_pipeline/` | ingest + dbt (bronze, silver, gold) |
| `eval/` | evaluation harness, dev cases, pre-registration |
| `web/` | React + Vite + TypeScript UI |
| `docs/` | plan, ADRs, rules, generated contracts, evidence, limitations |

## Data and privacy

Organizer data is never committed. The public demo runs on team-authored **synthetic** personas.
The same agent also runs end to end on the organizer data, locally only:
[`docs/evidence/curated_e2e.md`](docs/evidence/curated_e2e.md).
Known limitations: [`docs/limitations.md`](docs/limitations.md).

## Team

Victor (backend), Jacobo (fullstack), Juan José (data engineering and analysis), Santiago (data
engineering and automation).
