# 0001. Deterministic state machine with an LLM interpreter, policy engine and verified tools

- Status: accepted
- Date: 2026-09-27
- Deciders: Santiago, Victor, Jacobo, Juan José

## Context

The challenge rewards an AI-first workflow that is safe, measurable and business-relevant. The
chosen workflow is transaction dispute intake with card block as a sub-action. Agents that let the
model choose actions and customer identifiers are vulnerable to BOLA, prompt injection and
unverified claims ("your dispute was created" when nothing was written).

## Options considered

1. **LLM-only tool-calling agent** (the model plans, picks tools and arguments): fastest to build;
   unsafe by construction (the model sees and chooses `customer_id`), hard to evaluate.
2. **Deterministic state machine + LLM as interpreter only**: more code, but every action is
   gated by policy, confirmation and read-back; behavior is reproducible and testable.
3. **Rules/keywords only**: safe and cheap, but brittle on ES/PT dialects and paraphrases.

## Decision

Option 2. Flow: AUTH → UNDERSTAND (router gate, then LLM) → IDENTIFY_TXN → RECOGNIZE → CHECK_POLICY
→ CONFIRM → ACT → VERIFY → RESPOND, with CLARIFY (max 2 rounds), ABSTAIN and ESCALATE (structured
`HandoffPacket`). Option 1 is kept as the **system baseline** (`baseline_llm_only`) with the same
tools, and option 3 as the **ML baseline** (keyword router).

Core invariants:

- The model never receives or chooses `customer_id`; identity is the server-side `Session`.
- Every write needs a single-use confirmation token bound to the exact action and arguments, and
  is verified by read-back. No text claims an action without `verified=true`.
- Customer-facing text is rendered from ES/PT templates filled with verified facts.
- `is_fraud` is never used at runtime (ADR 0003).

## Consequences

- Good: safety properties are structural and testable; the evaluation can score from
  `ExecutionRecord`s; the baseline comparison is apples to apples.
- Bad: less conversational flexibility; more states to test; templates need ES/PT upkeep.

## How we would know this was wrong

The proposed system fails to beat the LLM-only baseline on safe automated resolution, or
clarification loops dominate the conversation length on the dev set.
