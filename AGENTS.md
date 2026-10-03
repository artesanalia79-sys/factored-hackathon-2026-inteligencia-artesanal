# AGENTS.md

Canonical instructions for every coding agent (Kiro, Claude Code, Codex, others) and every human
working in this repository. `CLAUDE.md` imports this file; do not duplicate its content elsewhere.

## Project

AI-first **transaction dispute intake agent** for a LATAM bank (Factored AI & Data Hackathon 2026,
submission Mon Oct 5 2026). A customer chats in Spanish or Portuguese; the agent authenticates the
session, identifies the transaction, asks whether the customer recognizes it, applies a versioned
policy, asks for explicit confirmation, creates the dispute (optionally blocks the card), verifies
the write by reading it back, and answers. When it cannot resolve safely it clarifies, abstains or
escalates with a structured `HandoffPacket`.

Full plan: `docs/plan/implementation_plan.md`. Challenge material: `docs/challenge/`.
Architecture decisions: `docs/adr/`. Known limitations: `docs/limitations.md`.

## Non-negotiable rules

1. **Never read, print, copy or commit secrets.** Credentials live only in `.env` (gitignored) and
   the AWS CLI profile `factored`. Never open `private/` or `.env`; reference variables by name.
2. **Never commit data.** No organizer data, `data/`, `*.duckdb`, `*.parquet`, `*.sqlite`, PDFs or
   DOCX. Pre-commit (`check-forbidden-paths`, gitleaks) enforces this; never bypass it with
   `--no-verify`.
3. **Never read the sealed held-out set** (`HELDOUT_DIR`, anything under `eval/heldout/`). Only its
   sha256 manifest is versioned. Tuning on held-out cases invalidates the evaluation.
4. **The model never receives or chooses `customer_id`.** Identity comes from the server-side
   `Session` only. Tool argument models must not have a `customer_id` field.
5. **Every write needs a confirmation token** bound to the exact action and arguments, and must be
   verified by a read-back. No customer-facing text may claim an action unless `verified=true`.
6. **`is_fraud` is banned at runtime** (it is a label). See `docs/adr/0003-fraud-signals-at-runtime.md`.
7. **Language:** code, comments, docs, commit messages, PRs and harness files are in **English**.
   Only customer-facing templates and test utterances are in Spanish or Portuguese.
8. **Pin every dependency** exactly (`uv add "pkg==X.Y.Z"`, `npm install --save-exact`).
9. Do not commit, push, open PRs or change GitHub settings unless a human asked for it.

## Area rules (read before editing)

Before editing files in an area, read its rule file. They are the single source of truth; Kiro
loads them through `.kiro/steering/`, Claude Code through nested `CLAUDE.md` imports.

| Area | Paths | Rule file |
|---|---|---|
| Data pipeline | `data_pipeline/**`, `src/bankagent/fixtures/**`, `tests/fixtures/**` | `docs/rules/data-pipeline.md` |
| Backend / agent | `src/bankagent/**`, `policy/**`, `config/**` | `docs/rules/backend.md` |
| Evaluation / ML | `eval/**`, `src/bankagent/eval/**`, `src/bankagent/router/**` | `docs/rules/eval.md` |
| Web UI | `web/**` | `docs/rules/web.md` |

## Repository map

```
AGENTS.md / CLAUDE.md     agent instructions (this file is canonical)
.agents/skills/           canonical skills (mirrored to .claude/skills and .kiro/skills)
.kiro/steering/           Kiro steering (product, tech, structure, area includes)
docs/                     plan, ADRs, rules, contracts (generated), challenge, evidence, models
src/bankagent/            Python package
  contracts/              shared Pydantic contracts + JSON Schema export (Task 3)
  fixtures/               deterministic fixture-bank builder (Task 3)
  interpret/              LLM interpreter + StubProvider/keyword baseline (Tasks 3, 10)
  eval/                   evaluation harness: simulator, runners, scorer, metrics, gates (Task 12)
  api/ auth/ orchestrator/ policy/ tools/ store/ render/ router/ obs/
data_pipeline/            ingest (Task 4) + dbt project (Tasks 5-6)
eval/                     harness, dev cases, pre-registration, gates (Tasks 12, 17, 27)
web/                      React + Vite + TypeScript UI (Task 14)
tests/                    pytest suite; tests/fixtures/bank/*.yaml = synthetic personas
scripts/                  repo tooling (hooks, env init, skills sync, HTTP and image smoke checks)
Dockerfile / render.yaml  container image and Render staging service (Task 15, docs/operations.md)
```

## Commands

Python is managed by uv (Python 3.12). Tasks run through poethepoet: `uv run poe <task>`.

| Command | Purpose |
|---|---|
| `uv sync` | create/update `.venv` from `uv.lock` |
| `uv run poe init-env` | create `.env` from `.env.example` (prints key names only) |
| `uv run poe hooks` | install the git pre-commit hooks (once per clone) |
| `uv run poe check` | **Definition-of-Done gate**: ruff lint + format check + pyright + pytest |
| `uv run poe fmt` | auto-format and auto-fix |
| `uv run poe contracts` / `contracts-check` | export / verify `docs/contracts/*.schema.json` |
| `uv run poe fixtures` / `fixtures-check` | build / verify the synthetic fixture bank DuckDB |
| `uv run poe sync-skills` / `skills-check` | mirror / verify skills copies |
| `uv run poe secrets-scan` | run every pre-commit hook on the whole repo |
| `uv run poe serve` | run the API locally (reads `.env`) |
| `uv run poe smoke <base URL>` | one full dispute over HTTP against a running service |
| `uv run poe image-smoke` | build and check the container image (needs Docker; CI job `image` runs it) |
| `uv run poe web-install` / `web-build` | install the web UI's locked npm deps / build `web/dist` (served by `poe serve` at `/`) |
| `uv run poe web-check` / `web-e2e` | web UI: types vs contracts, strict TS, lint / Playwright against the real API |
| `uv run poe` | list every task (placeholders exit 2 until their task lands) |

## Definition of Done (every change)

- `uv run poe check` passes locally; CI is green.
- If contracts changed: `uv run poe contracts` and commit the regenerated `docs/contracts/`.
- If fixture YAML changed: `uv run poe fixtures --update-hash` and commit `content_hash.txt`.
- If a skill changed: edit only `.agents/skills/`, then `uv run poe sync-skills`.
- New behavior has tests; bug fixes have a regression test.
- Decisions with trade-offs get an ADR (`write-adr` skill) or a line in `docs/decision_ledger.md`.
- No TODO without an owner and task number, e.g. `# TODO(T9, Juan José): verify MX rule`.

## Git workflow

- Branch from `main`: `t<task>-<short-slug>` (e.g. `t8-tools-layer`). One plan task per PR.
- Conventional commits in English: `feat(tools): add create_dispute idempotency`.
- Open PRs with the template in `.github/pull_request_template.md`. Never force-push `main`.
- A PR into `main` needs one approving review from a teammate other than the author, recorded on
  GitHub (an approval, not a chat message), before it is merged. GitHub cannot enforce this on our
  private free-plan repository, so whoever merges checks it (`docs/decision_ledger.md`, 2026-10-03).

## Skills

Reusable procedures live in `.agents/skills/<name>/SKILL.md`. Use them when the task matches:

| Skill | When |
|---|---|
| `ml-component` | training/evaluating the learned router, conformal abstention, model card |
| `eval-case-authoring` | writing dev or held-out evaluation cases |
| `dbt-modeling` | adding or changing dbt models, contracts and tests |
| `data-contracts` | changing Pydantic contracts or the serving-table contract |
| `tool-contract` | adding or changing an agent tool |
| `policy-rule` | adding or changing a dispute policy rule |
| `debug-ci` | a CI run failed |
| `write-adr` | recording an architecture decision |

## Where things are decided

- Data contracts: `src/bankagent/contracts/` (generated schemas in `docs/contracts/`).
- Policy: `policy/dispute_policy_v1.yaml` (rules are labeled synthetic unless verified).
- LLM budget and models: `docs/adr/0002-llm-provider-and-budget.md`, `config/pricing.yaml`.
- Evaluation protocol: `eval/preregistration.md`, `eval/gates.yaml` (frozen before unsealing).
- Deployment and operations: `Dockerfile`, `render.yaml`, `docs/operations.md`.
