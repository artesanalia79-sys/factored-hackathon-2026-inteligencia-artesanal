---
name: policy-rule
description: Add or change a dispute policy rule in policy/dispute_policy_v1.yaml with a stable rule_id, provenance (verified vs synthetic), tests and explanation keys. Use when editing policy or the policy engine.
---

# Adding or changing a policy rule

Read `docs/rules/backend.md` first. Policy is data (`policy/dispute_policy_v1.yaml`), evaluated by
a deterministic engine that returns a `PolicyDecision`.

## Rule shape

```yaml
- rule_id: DSP-WIN-01
  description: Disputes must be filed within the country window after the transaction date.
  applies_to: {intents: [dispute_unrecognized, dispute_duplicate, dispute_not_received]}
  when: "days_since_transaction > window_days[country]"
  decision: ineligible
  explanation_key: dispute.out_of_window
  provenance:
    synthetic: false          # true unless verified against a primary source
    source: "Argentina Ley 25.065 art. 26-29"
    verified_by: santiago
    verified_on: 2026-09-27
```

## Rules

- `rule_id`s are stable (`DSP-<AREA>-<NN>`); never reuse an id for a different meaning. Retire
  rules by marking them `retired: true`, and bump `policy_version`.
- Label every rule `synthetic: true` unless a human verified it against a primary source.
  MX and CO rules are synthetic until verified (TODO owners in the YAML).
- Order of evaluation: authorization → eligibility (window, status, duplicates of open cases) →
  escalation triggers (high amount, `fraud_score` threshold, repeat disputer, foreign) → proceed.
- Decisions never enable a write without `requires_confirmation: true`. Escalate and every
  non-proceed decision allow no `create_dispute`/`block_card` (the contract validator enforces it).
- Dates are compared against `as_of_date` from `_serving_metadata`, not the wall clock.
- `is_fraud` is never an input.

## Tests (one per rule, at least)

- A fixture persona that triggers the rule and one that does not (boundary days included).
- `uv run poe policy-explain <case>` output shows the rule id and explanation key.
