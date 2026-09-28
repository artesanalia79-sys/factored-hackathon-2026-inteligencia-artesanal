---
inclusion: always
---

# Product context

Read `AGENTS.md` first; it holds the non-negotiable rules. This file adds product context only.

## Problem

LATAM Bank (MX, CO, AR; Spanish and Portuguese speakers) handles transaction disputes through a
human call center. Our working hypothesis (to be measured, not assumed) is that part of the
"disputes" are charges customers would recognize if shown the merchant, date, channel and card.
The hackathon asks for an AI-first
workflow that resolves a real operational process safely and measurably
(`docs/challenge/problem_statement.md`).

## What we build

A chat agent that takes a dispute from first message to a verified dispute case, or to a complete
human handoff:

- Intents: unrecognized charge, duplicate charge, not received, card block, dispute status,
  human request, out of scope, attack (prompt injection / other-customer access).
- **Recognition step** before any dispute: show verified transaction facts and ask whether the
  customer recognizes the charge. Recognized charges are deflected without a dispute (tracked as
  the deflection metric).
- Card block as a sub-action when the charge is unauthorized.
- Every write is confirmed by the customer, executed idempotently and verified by read-back.

## How we will be judged

Business impact with evidence, technical quality, safety, and an honest evaluation. Our
differentiators, in order: (1) cross-authored, pre-registered, sealed held-out set with dialect
quotas and inter-annotator agreement; (2) recognition step with a deflection metric; (3) dbt
contracts, tests and published lineage; (4) cost-per-resolution ROI with sensitivity analysis;
(5) split-conformal router abstention; (6) side-by-side naive vs. controlled agent;
(7) load test; (8) small external pilot.

## Baselines we compare against

- Business: the current human call center (from the organizer data).
- System: an LLM-only agent with the same tools, `customer_id` visible, no policy, gates or router.
- ML: a keyword router.

## Users

Retail customers of every segment (Premium, Plus, Basic, Student), dialects ES-MX, ES-CO, ES-AR
(voseo) and PT-BR. Human agents receive handoff packets routed by specialty and language.
