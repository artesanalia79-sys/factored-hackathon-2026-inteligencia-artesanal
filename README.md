# Inteligencia Artesanal: AI-first dispute intake for LATAM Bank

Factored AI & Data Hackathon 2026. A chat agent that takes a transaction dispute from the first
customer message (Spanish or Portuguese) to a **verified** dispute case, optionally blocking the
card, or to a complete human handoff. It checks whether the customer recognizes the charge before
opening a dispute, applies a versioned policy, asks for explicit confirmation, and verifies every
write by reading it back.

> Status: evaluated. The final evaluation ran on 2026-10-04 on a sealed held-out set of 90 cases:
> seven of nine pre-registered gates pass and **two fail** (see [Results](#results)).

## Try it

- **Public demo:** <https://bankagent-staging.onrender.com>. It asks for an access code, sent with
  the submission. It runs on a free instance that sleeps after 15 minutes without traffic: the
  first request can take about a minute, and the demo state resets when it sleeps
  ([`docs/operations.md`](docs/operations.md)).
- **Login:** pick one of the synthetic personas; the one-time code is shown on screen (there is no
  SMS or email channel in the demo).
- **Side-by-side replay** of the LLM-only baseline and the controlled agent on development cases:
  `/compare` ([`docs/comparison.md`](docs/comparison.md)).

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) ≥ 0.12, git, Node 22 (web UI only).

```powershell
uv sync --group data    # Python 3.12 and pinned dependencies into .venv (the data group is needed by `poe check`)
uv run poe init-env     # creates .env from .env.example (prints key names only)
uv run poe hooks        # installs pre-commit hooks (secrets, data, format)
uv run poe fixtures     # builds the synthetic fixture bank (data/fixtures/bank_fixture.duckdb)
uv run poe check        # lint + format + types + tests
```

Run it locally, at 0 USD (the default interpreter is a keyword stub; no key is needed):

```powershell
uv run poe web-install  # the web UI's locked npm dependencies
uv run poe web-build    # builds web/dist
uv run poe serve        # API and UI at http://localhost:8000
```

`uv run poe` lists every task. The organizer dataset is not needed for any of the above. To
rebuild the data pipeline (Task 4), store the provided AWS keys in a local profile, never in
files: `aws configure --profile factored`.

## How it works

```
AUTH → UNDERSTAND (router gate, then LLM) → IDENTIFY_TXN → RECOGNIZE → CHECK_POLICY
     → CONFIRM → ACT → VERIFY (read-back) → RESPOND        | CLARIFY | ABSTAIN | ESCALATE
```

- The LLM only interprets the message; a deterministic state machine decides.
- The model never sees or chooses `customer_id`; identity comes from the server session.
- Writes need a single-use confirmation token and are verified by read-back. No reply claims an
  action that was not verified.
- When it cannot resolve safely it clarifies (at most twice), abstains, or escalates with a
  structured handoff.

Details: [`docs/adr/0001-architecture.md`](docs/adr/0001-architecture.md) (architecture),
[`docs/adr/0002-llm-provider-and-budget.md`](docs/adr/0002-llm-provider-and-budget.md) (model and
budget), [`docs/adr/0003-fraud-signals-at-runtime.md`](docs/adr/0003-fraud-signals-at-runtime.md)
(why the fraud label is never a runtime input), [`policy/dispute_policy_v1.yaml`](policy/dispute_policy_v1.yaml).

## Results

Both systems, the proposed agent and an LLM-only baseline over the same tools, ran on a sealed
held-out set: 90 cases x 3 repeats, model `gpt-6-luna`, 0.42 USD in total. Hypotheses, metrics
and gates were frozen before the set was written
([`eval/preregistration.md`](eval/preregistration.md), [`eval/gates.yaml`](eval/gates.yaml)).
Intervals are Wilson 95% over cases.

| Gate (proposed agent) | Threshold | Measured | Status |
|---|---|---|---|
| G1 critical unsafe cases | 0 | 0 of 90 (upper bound 4.1%) | PASS |
| G2 unsafe-case rate, upper bound | ≤ 5% | 1 of 90 = 1.1% [0.2, 6.0] | **FAIL** |
| G3a correct outcome, lower bound | ≥ 80% | 81 of 90 = 90.0% [82.1, 94.6] | PASS |
| G3b safe automated resolution where automation is acceptable, lower bound | ≥ 65% | 22 of 32 = 68.8% [51.4, 82.0] | **FAIL** |
| G3c same metric, not below the baseline | ≥ 43.8% | 68.8% | PASS |
| G4a missed transfers | ≤ 10% | 1 of 34 = 2.9% [0.5, 14.9] | PASS |
| G4b complete handoffs | ≥ 90% | 33 of 33 | PASS |
| G5a turn latency p95 | ≤ 10 s | 2.8 s | PASS |
| G5b cost per conversation | ≤ 0.008 USD | 0.00026 USD | PASS |

| | Proposed agent | LLM-only baseline |
|---|---|---|
| Correct outcome | 90.0% [82.1, 94.6] | 40.0% [30.5, 50.3] |
| Cases with any unsafe event | 1 of 90 | 13 of 90 (2 critical) |
| Missed transfers | 1 of 34 | 20 of 34 |

What this shows, and what it does not:

- The controls hold: no write without confirmation, no unverified claim, no PII leak and no
  action in the attack cases, and 33 of the 34 cases that need a person are transferred, all
  with a complete handoff. Zero observed is not zero risk: the bound is 4.1%.
- The agent is conservative. Where automation is acceptable it resolves 69% and otherwise
  abstains instead of acting; G3b needed a lower bound of 65% and got 51%. The one unsafe case is
  a request closed as denied that should have been served.
- The baseline is handicapped by the scripted user, which could not classify 118 of its
  questions, so the gap in correct outcomes is overstated; its unsafe events are writes it made.
- This is an offline evaluation with a scripted user on a synthetic fixture bank. The held-out
  labels were not second-annotated, and the set was opened twice (the first run was killed before
  it wrote any result). Every deviation is listed.

Full account: [`docs/evidence/final_evaluation.md`](docs/evidence/final_evaluation.md). Report of
the run: [`eval/reports/heldout-2-20261005T020410Z/report.md`](eval/reports/heldout-2-20261005T020410Z/report.md).

## Evidence

| Question | Where |
|---|---|
| Why is this problem worth solving, and what would the agent save? | [`docs/evidence/roi.md`](docs/evidence/roi.md), [`docs/evidence/call_center_baseline.md`](docs/evidence/call_center_baseline.md), [`docs/evidence/complaints_baseline.md`](docs/evidence/complaints_baseline.md) |
| What is in the data, and how good is it? | [`docs/evidence/data_audit.md`](docs/evidence/data_audit.md), [`docs/evidence/dq_report.md`](docs/evidence/dq_report.md) |
| Does the learned component beat a simple baseline? | [`docs/models/router.md`](docs/models/router.md), [`docs/evidence/router_eval.md`](docs/evidence/router_eval.md) (offline: the deployed agent does not use it) |
| How was the agent evaluated? | [`eval/README.md`](eval/README.md), [`eval/preregistration.md`](eval/preregistration.md) |
| How is it deployed and operated? | [`docs/operations.md`](docs/operations.md) |
| What was decided, and why? | [`docs/adr/`](docs/adr/), [`docs/decision_ledger.md`](docs/decision_ledger.md) |
| What does it not do? | [`docs/limitations.md`](docs/limitations.md) |

## Repository

| Path | What |
|---|---|
| `AGENTS.md` | rules for humans and coding agents (Kiro, Claude Code, Codex) |
| `src/bankagent/` | Python package (contracts, tools, policy, orchestrator, API, evaluation harness) |
| `policy/` | the versioned dispute policy |
| `data_pipeline/` | ingest + dbt (bronze, silver, gold) |
| `eval/` | dev cases, pre-registration, gates, held-out manifest, final reports |
| `web/` | React + Vite + TypeScript UI |
| `docs/` | plan, ADRs, rules, generated contracts, evidence, limitations |

## Data and privacy

Organizer data is never committed. The public demo and the evaluation run on team-authored
**synthetic** personas. The sealed held-out cases live outside the repository; only their sha256
manifest is versioned. Known limitations: [`docs/limitations.md`](docs/limitations.md).

## Team

Jacobo (fullstack: interpreter, orchestrator, API, web UI), Juan José (data engineering, analysis
and backend: auth, tools, policy), Santiago (data engineering and automation: harness, CI,
evaluation, deploy).
