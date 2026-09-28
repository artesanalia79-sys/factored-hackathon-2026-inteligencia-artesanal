---
inclusion: always
---

# Technology choices and rationale

Commands and hard rules are in `AGENTS.md`. This file explains *why* the stack looks like this so
agents do not "improve" it into something else.

| Concern | Choice | Why |
|---|---|---|
| Python env | uv, Python 3.12, `uv.lock` | fast, reproducible, one tool for Python + deps on Windows/macOS/Linux |
| Task runner | poethepoet (`uv run poe`) | cross-platform (no make on Windows), lives in `pyproject.toml` |
| Analytics DB | DuckDB | local-first, no cloud account needed, fast on parquet |
| Transformations | dbt-core + dbt-duckdb | contracts, tests and lineage are judged evidence |
| API | FastAPI + Pydantic v2 | contracts double as request/response validation and JSON Schema |
| Operational store | SQLite | disputes, blocks, confirmation tokens; single-file, zero ops |
| UI | React + Vite + TypeScript | team familiarity, static build served by FastAPI |
| LLM | OpenAI `gpt-6-luna` (default), `gpt-6-sol` (judge sample only) | budget ~50 USD; see ADR 0002 |
| Offline/CI LLM | `StubProvider` | deterministic, 0 USD, doubles as the keyword baseline |
| Embeddings | local ONNX model | no per-call cost for the learned router |
| Experiments | MLflow (local file store) | run tracking for the router and eval runs |
| Red team | promptfoo | standard agent-security plugins (BOLA, BFLA, injection, PII) |
| Packaging | one Docker image | same image for Render staging and the final host |
| Hosting | Render free (staging); Cloud Run or Render (final, decided at freeze) | free tiers; AWS App Runner is closed to new customers |

## Things we deliberately do not use

- No Snowflake, Databricks or team AWS account. AWS is used only to download organizer data.
- No agent framework that lets the model pick actions freely; the state machine owns control flow.
- No TIMESTAMPTZ in the serving DB (naive UTC `TIMESTAMP`, avoids a pytz dependency).
- No unpinned dependencies and no dependency groups declared before the task that needs them.

## Budget guardrails

Every real LLM call logs tokens and cost (`config/pricing.yaml`). CI never calls a paid API.
Full evaluation runs are manual, estimated first, and recorded in `docs/decision_ledger.md`.
