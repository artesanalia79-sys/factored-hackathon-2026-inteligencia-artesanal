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
