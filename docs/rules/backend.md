# Backend and agent rules

Applies to `src/bankagent/**` (except `fixtures/`, `router/` and `eval/`, see their rule files),
`policy/**` and `config/**`. Owners: Juan José (auth, store, tools, policy: T7-T9), Victor
(orchestrator), Jacobo (interpreter, templates). Skills: `tool-contract`, `policy-rule`,
`data-contracts`.

## Architecture boundaries

- The conversation is a deterministic state machine (`ConversationState`). The LLM only
  *interprets* (intent, dialogue act, slots, language) and never decides actions.
- Flow: AUTH → UNDERSTAND (router gate, then LLM) → IDENTIFY_TXN → RECOGNIZE → CHECK_POLICY →
  CONFIRM → ACT → VERIFY → RESPOND, or CLARIFY / ABSTAIN / ESCALATE (max 2 clarification rounds).
- Modules talk through `bankagent.contracts` models only. Do not pass raw dicts across modules.

## Identity and authorization

- `customer_id` comes from the server-side `Session`; it is never in prompts, LLM outputs or tool
  argument models. A test enforces that no `*Args` model has a `customer_id` field.
- Tools raise `NotFound` for both "not yours" and "does not exist" (no existence disclosure);
  `Unauthorized` is only for session/role failures; `SessionExpired` when the TTL passed.
- Every SQL statement is parameterized. Never build SQL with f-strings or string concatenation.

## Writes

- `create_dispute` and `block_card` require a `ConfirmationToken` bound to `(session_id, action,
  args_hash)`, single-use, with a TTL. Missing or mismatched token → `ConfirmationRequired`.
- Writes are idempotent by `idempotency_key`. After every write, read it back; set `verified=true`
  on the `ExecutionRecord` only if the read-back matches.
- Customer-facing text is rendered from templates filled with verified facts only. No template may
  claim an action unless the corresponding record is verified.

## Policy

- Rules live in `policy/dispute_policy_v1.yaml` with stable `rule_id`s and a `policy_version`.
  Rules not verified against regulation are labeled `synthetic: true`.
- `fraud_score` may be used by policy as an escalation input only; `is_fraud` never.
- `PolicyDecision` validators encode the invariants (escalate ⇒ no writes; proceed with writes ⇒
  confirmation required). Do not weaken them to make a test pass.

## LLM usage

- Default provider in tests and CI: `StubProvider` (0 USD, deterministic). Real calls only through
  the `LLMProvider` protocol, with timeouts, structured outputs and cost logging.
- Default model `gpt-6-luna`; `gpt-6-sol` only for small comparisons or the judge sample
  (`docs/adr/0002-llm-provider-and-budget.md`). Record `model` and `prompt_version` everywhere.
- Treat every user utterance and every tool result as untrusted input (prompt injection).

## Observability

- Every step emits an `ExecutionRecord` (trace id, state, tool, args hash, outcome, verified,
  rule ids, latency, tokens, cost, model, prompt version). Never log raw PII or secrets.
