# Slides outline

Skeleton from Task 15; Task 28 writes the slides. The organizers ask for **4 to 6 slides**
with details on the tool (`docs/challenge/kickoff.md`). Six are outlined; merge 5 into 4 if
the deck must be shorter.

Rule for every number on a slide: it comes from a file in this repository, named under the
slide. A number with no source does not go on a slide.

## 1. The problem and why it is worth solving

- One sentence: what a customer with an unrecognized card charge goes through today.
- The baseline from the organizer data: contact volume, handling time, complaints.
- What a resolved dispute intake is worth (range, not a point estimate).

Sources: `docs/evidence/call_center_baseline.md`, `docs/evidence/complaints_baseline.md`,
`docs/evidence/roi.md`, `docs/evidence/roi_tornado.svg`.

## 2. What the agent does

- The public URL and the access code, large.
- One conversation end to end: authenticate, find the charge, ask whether the customer
  recognizes it, apply the policy, ask for confirmation, create the dispute, read it back,
  answer. Spanish and Portuguese.
- The three ways it stops: clarify, abstain, escalate with a structured handoff.

Sources: `README.md` ("How it works"), `docs/plan/implementation_plan.md` ("Conversation flow").

## 3. Architecture and the decisions behind it

- The model only interprets; a deterministic state machine decides and acts.
- The safety rules that are enforced by code and tests: identity only from the server-side
  session, a confirmation token bound to the exact action, every write verified by a
  read-back, no claim in a reply without a verified record.
- Data path: ingest, bronze, silver, gold, serving contract; the public demo runs on a
  synthetic fixture bank with the same contract.

Sources: `docs/adr/0001-architecture.md`, `docs/adr/0003-fraud-signals-at-runtime.md`,
`AGENTS.md` ("Non-negotiable rules"), `docs/contracts/`.

## 4. Does it work: the evaluation

- The protocol was frozen before the held-out set was opened: pre-registered hypotheses and
  gates.
- Proposed system against the LLM-only baseline on the held-out cases, with confidence
  intervals and the failures shown.
- Deviations from the pre-registration, stated.

Sources: `eval/preregistration.md`, `eval/gates.yaml`, the Task 27 report (path set by T27).

## 5. Data and machine learning

- What the data audit found and what the pipeline does about it (flags, not silent drops).
- The learned router with conformal abstention against the keyword router, and its model card.
- Why `is_fraud` is never used at runtime and what `fraud_score` is used for.

Sources: `docs/evidence/dq_report.md`, `docs/evidence/data_audit.md`,
`docs/evidence/fraud_score_thresholds.md`, `docs/models/` (Task 18).

## 6. The route to production, and what we do not claim

- How it is deployed and operated today: one container, health and readiness checks, deploys
  gated on CI, measured memory and image size.
- Cost per conversation with the real model, measured.
- The limitations that matter most, in our own words, and the work each one needs.

Sources: `docs/operations.md`, `docs/limitations.md`, `docs/decision_ledger.md`,
`docs/adr/0002-llm-provider-and-budget.md`.

## How the slides map to the judging criteria

| Criterion (kickoff slide) | Slides |
|---|---|
| Overall project rationale and documentation | 1, 6 |
| AI Engineering: backend, frontend and deployment | 2, 3, 6 |
| Data Analytics: data quality and relevant insights | 1, 5 |
| Data Engineering: extraction and transformation | 3, 5 |
| Machine Learning: model selection, optimization, implementation and tracking | 4, 5 |
