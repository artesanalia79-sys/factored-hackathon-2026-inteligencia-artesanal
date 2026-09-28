# 0003. Ban is_fraud at runtime; fraud_score only as a policy escalation input

- Status: proposed (threshold pending Task 5 verification)
- Date: 2026-09-27
- Deciders: Santiago, Victor, Juan José

## Context

The organizer transactions table includes `is_fraud` (a ground-truth label) and `fraud_score`
(a model score). A real intake agent would not know the label at the moment a customer calls, so
using it would inflate every metric and would not transfer to production.

## Options considered

1. **Use `is_fraud` to decide disputes**: unrealistic label leakage; rejected.
2. **Ignore all fraud signals**: realistic but throws away a signal a bank would have.
3. **Expose `fraud_score` to policy only**: the LLM never sees it; policy may escalate high-risk
   cases to the fraud specialty.

## Decision

Option 3.

- `is_fraud` is in `FORBIDDEN_COLUMNS` of the serving contract and never reaches the serving DB,
  prompts or features. It may be used offline for analysis and as an evaluation label.
- `fraud_score` is read through `TransactionRiskSignals`, separate from `TransactionView`, so it
  cannot leak into customer-facing text or the model context.
- Proposed rule: `fraud_score >= 30` on a disputed transaction triggers escalation to the fraud
  specialty. **The threshold is provisional**: Task 5 must check the score distribution and its
  relation to `is_fraud` offline, and record the result in `docs/decision_ledger.md`.

## Consequences

- Good: no label leakage; the agent behaves as it could in production.
- Bad: if `fraud_score` turns out to be noise in this dataset, the rule adds escalations without
  benefit; the threshold may change after Task 5.

## How we would know this was wrong

Offline, escalation by `fraud_score` has no better precision for `is_fraud = true` than random
escalation at the same rate.
