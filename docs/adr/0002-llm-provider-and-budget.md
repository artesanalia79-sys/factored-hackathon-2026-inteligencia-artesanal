# 0002. OpenAI gpt-6-luna by default, deterministic stub in CI, hard budget guardrails

- Status: accepted
- Date: 2026-09-27
- Deciders: Santiago, Jacobo

## Context

The team has an OpenAI coupon of about 50 USD for development, evaluation (≥ 200 held-out cases,
3 repeats, two systems) and the demo. CI must never spend money. Prices below are the list prices
checked on 2026-09-27 and must be re-checked before the final evaluation.

| Model | Input (USD / 1M tokens) | Output (USD / 1M tokens) | Use |
|---|---|---|---|
| `gpt-6-luna` | 0.10 | 0.50 | default interpreter and LLM-only baseline |
| `gpt-6-sol` | 2.00 | 10.00 | small comparisons and the LLM-judge sample only |

## Options considered

1. **gpt-6-sol everywhere**: best quality, but the full evaluation alone could exceed the budget.
2. **gpt-6-luna by default, sol only for small samples**: full evaluation estimated at a few USD.
3. **Local open model**: 0 USD per call, but the 512 MB container and team time make it unrealistic.

## Decision

Option 2, behind the `LLMProvider` protocol so the provider can change without touching the
orchestrator. Structured outputs (Pydantic `response_model`) with timeouts and typed errors
(`LLMTimeout`, `LLMMalformedOutput`, `LLMUnavailable`) that the fallback matrix handles.
`StubProvider` (keyword rules, 0 USD, deterministic) is the default for tests, CI and offline work,
and doubles as the keyword baseline. Embeddings for the learned router are local (ONNX).

Guardrails:

- Prices live in `config/pricing.yaml`; every call logs tokens and cost in its `ExecutionRecord`.
- A per-run spend limit aborts the run; real-LLM evaluation runs are estimated first and logged in
  `docs/decision_ledger.md`.
- Cassettes (recorded responses) make LLM-dependent tests replayable without network.
- An OpenAI project-level monthly budget is set by the key owner (Jacobo).

## Consequences

- Good: the whole evaluation fits the budget with margin; CI is free and deterministic.
- Bad: luna may be weaker on dialect nuance; the router gate and clarification must compensate.

## How we would know this was wrong

Dev-set intent accuracy of luna is clearly below sol on the same cases (report both on a small
sample), or spend per full evaluation run exceeds 10 USD.

## Update (2026-10-05, after the final evaluation)

The decision is unchanged; this note records where the repository differs from the text above,
so a reader can check one against the other.

- Spend: the final evaluation (both systems, 90 held-out cases x 3 repeats, `gpt-6-luna`) cost
  0.42 USD against the 10 USD bar above; prices were re-checked on 2026-10-04, unchanged
  (`docs/evidence/final_evaluation.md`, `docs/decision_ledger.md`).
- The held-out set has 90 cases, not 200 or more (scope reduction of 2026-10-02,
  `docs/plan/implementation_plan.md`).
- Not built: the ONNX embeddings (the learned router of Task 18 uses TF-IDF character n-grams and
  is offline, `docs/models/router.md`) and the cassettes (the real provider is tested offline
  against synthetic structured outputs and a fake client, `tests/interpret/`; no recorded model
  response is replayed). No run with `gpt-6-sol` is recorded, so the luna-against-sol comparison
  was not done and the first condition above was never checked. No judge sample was needed: the
  evaluation scores with deterministic detectors, not with a model (`eval/preregistration.md`,
  section 5). The embeddings, the cassettes and the comparison are Task 29
  (`docs/plan/implementation_plan.md`).
