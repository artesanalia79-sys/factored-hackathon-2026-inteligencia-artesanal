---
name: live-llm-check
description: Test or debug something against a real LLM (OpenAI or an OpenAI-compatible endpoint) with a team key during development, spending in small measured steps - free checks first, then one call, a few, one conversation, one UI spec - with a hard cap, proof that the model answered rather than the keyword fallback, and the cost of every step. Use before any development call that spends money; not for the demo or the final evaluation.
---

# Checking against a real model during development

Real calls spend a small shared budget (ADR 0002), and tests and CI stay on `StubProvider`
(0 USD). This skill is how a development check spends: verifying a change, reproducing a bug,
confirming a fix. A few cents answer most questions when the calls are chosen one step at a time.

## When it does not apply

- **The demo** (the public URL, recording the video): it runs on the real model with the budget
  and caps chosen for it (`LLM_SPEND_LIMIT_USD` in the Render dashboard, T15).
- **The final evaluation (T27)** and any pre-registered run: they follow `eval/preregistration.md`
  and `docs/rules/eval.md`, are estimated first and logged in `docs/decision_ledger.md`. Their
  size is set by the protocol, not by this skill.

## 0. Before the first call

- A human asked for the check or approved it. Say what will run and what it should cost.
- Never open, print or copy `.env` (`AGENTS.md` rule 1). Load it into the process only:
  `uv run --env-file .env ...` from the repository root (uv splits the path on spaces, so use the
  relative one), or a poe task with `envfile = ".env"`. Print `set` or `missing`, never a value.
- A variable set in the shell wins over `.env` with `uv run --env-file`: force
  `LLM_PROVIDER=openai` and a small cap that way instead of editing `.env`.
- A hard cap per process far below the budget: `OpenAIProvider(spend_limit_usd=Decimal("0.02"))`
  or `LLM_SPEND_LIMIT_USD=0.02`. The provider refuses a call that would pass it.
- Synthetic utterances and the fixture bank only. Never the held-out set (`AGENTS.md` rule 3).

## 1. Climb one step at a time; stop at the first surprise

| Step | What | Calls |
|---|---|---|
| 0 | Configuration only: key set, provider, model, cap | 0 |
| 1 | One call with the smallest input that exercises the change | 1 |
| 2 | The few inputs the change is about (for example the four answers of the confirmation buttons) | 2 to 5 |
| 3 | One conversation over HTTP: `TestClient` on `create_app`, with the provider from `api.wiring.build_llm` | about 4 |
| 4 | One browser spec of the web UI (T14): in `web/`, `E2E_LLM_PROVIDER=openai uv run --env-file ../.env -- npx playwright test -g "<one spec>"` (capped at 2 cents) | about 4 |
| 5 | A broader run, such as `uv run poe eval-run --system proposed --provider openai` on the dev cases: only when asked, estimated first, logged in the ledger | tens |

Never run a whole test suite on a paid model. Go up a step only when the one below is clean.

## 2. Prove that the model answered

A conversation that ends well is not evidence: on any `LLMError` the agent falls back to the
keyword interpreter and still works, without telling the customer. Read the turn's
`ExecutionRecord`s: the `interpret` step must say `success` with the model's name, not
`fallback`. Count the fallbacks; the check passes at zero.

## 3. When a call fails

1. Reproduce it with one call. The exception names the place: `LLMMalformedOutput` (the answer
   did not fit the contract), `LLMTimeout`, `LLMUnavailable`, `SpendLimitExceeded`.
2. Read the code before spending again: the transport schema against the contract, the
   normalization of slots, the timeout. It is free and usually enough for a hypothesis.
3. At most one diagnostic call that shows what the model returned before validation, on a
   synthetic utterance:

   ```python
   original = InterpretationResult.model_validate.__func__

   def showing(cls, obj, *args, **kwargs):
       try:
           return original(cls, obj, *args, **kwargs)
       except ValidationError as exc:
           print(obj, [(error["loc"], error["type"]) for error in exc.errors()])
           raise

   InterpretationResult.model_validate = classmethod(showing)
   ```

4. Fix it with offline tests that replay the live answer through a mocked client (no network),
   then confirm with one or two live calls.

## 4. Report

- The cost of each step and the total (`OpenAIProvider.spent_usd`, or `cost_usd` summed over the
  records), the latency, and what was verified.
- One run per input is a check, not a rate: say so.
- A finding goes in `docs/decision_ledger.md`. Remove temporary scripts and stores.

The first check run this way (2026-10-03): about 20 calls, 0.0022 USD, covering the confirmation
buttons, a conversation over HTTP and a browser spec. It found that a currency word ("pesos")
made the model's whole interpretation malformed, so on messages that name their currency in
words the agent had silently used the keyword fallback.
