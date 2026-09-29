# 0003. Ban is_fraud at runtime; fraud_score only as a policy escalation input

- Status: accepted (threshold verified offline in Task 5, 2026-09-29)
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
- Rule: `fraud_score >= 30` on a disputed transaction triggers escalation to the fraud
  specialty. Verified offline in Task 5 (`docs/evidence/fraud_score_thresholds.md`) on 4.43M
  labelled transactions (4,316 fraud, base rate 0.0975%). `>= 30` escalates 2,982 transactions
  (0.067%) with precision 0.80 (lift ~816x over random escalation at the same rate) and recall
  0.55. Recall counts every fraud row, including the 891 fraud rows with a NULL score that no
  score rule can escalate; over scored fraud only it is 0.69. Purchases alone behave the same
  (precision 0.79, recall 0.54). `>= 40` reaches precision 1.0 but drops recall to 0.47, so 30
  is kept.
- Ceiling: fraud rows split 1,052 below 30, 360 in [30, 40), 2,013 at 40+ and 891 NULL, so no
  threshold can recall more than ~79% of fraud.
- All 609 non-fraud escalations sit at exactly 30.00 (the maximum non-fraud score), while the
  lowest fraud score at or above 30 is 30.01, so `> 30` would separate the label perfectly.
  That is an artefact of the synthetic generator, not evidence for `> 30`; the policy keeps
  `>= 30` (Task 9 should not tune on it). Raw scores have at most 2 decimals, so the
  `decimal(5,2)` typing in silver does not round anything at the threshold.

## Consequences

- Good: no label leakage; the agent behaves as it could in production.
- Bad: the score separates the label almost perfectly (non-fraud scores are capped at exactly
  30.00), which is a property of the synthetic generator; real-world scores would be noisier, so the
  offline precision is an upper bound and must not be quoted as production performance.
- Bad: about 21% of fraud rows have no score, so score-based escalation alone misses them; the
  other intents (unrecognized charge, card block) must not depend on the score.

## How we would know this was wrong

Offline, escalation by `fraud_score` has no better precision for `is_fraud = true` than random
escalation at the same rate.
