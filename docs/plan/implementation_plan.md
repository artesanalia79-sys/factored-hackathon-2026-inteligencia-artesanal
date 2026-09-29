# Implementation plan

Team Inteligencia Artesanal: Victor (backend), Jacobo (fullstack), Juan José (data engineering and
analysis), Santiago (data engineering and automation: harness, CI, evaluation, deploy).
Workflow: **transaction dispute intake** with **card block** as a sub-action.
Submission: **Mon Oct 5, 2026**. Code freeze: end of **Sat Oct 3, 2026**.

Principles: Pareto first, unblock others first, always shippable ("deliver something"). Every
teammate has parallel work from day 1 thanks to shared contracts, a stub LLM and a fixture bank.

## Milestones

| Milestone | When | Tasks | Exit criterion |
|---|---|---|---|
| M0 Foundations | D1 | 1-3 | CI green; contracts, stub LLM and fixture bank usable by every lane |
| M1 Shippable v0 | D2-3 | 4-15 | end-to-end dispute on fixtures, staging URL, submission skeleton |
| M2 Real data + learned router | D4-5 | 16-21 | curated data wired, router with abstention, observability, lineage |
| M3 Hardening | D5-6 | 22-26 | side-by-side, load test, red team, pilot; freeze end of Sat Oct 3 |
| M4 Final evaluation + submission | D7-8 | 27-28 | held-out unsealed once, report, video, submitted Mon Oct 5 |

**Cut order if behind:** 25 → 24 → 22 → 23 → evidence-view polish (21) → LLM comparator (18).
**Never cut:** 1-3, 11-15, 17, 26-28.

## Dependencies

```
T1 → T2 → T3
T3 → T4 → T5 → T6          T6 → T16          T5, T13 → T18
T3 → T7 → T8 → T9          T6, T13 → T19     T14, T5 → T21
T3 → T10 → T11             T12 → T17         T14 → T22
T3 → T12                   T13 → T20         T15 → T23, T25
T9, T11 → T13 → T14 → T15  T19 → T24         T17, T18, T19, T20 → T26 → T27 → T28
```

## Conversation flow

```
AUTH → UNDERSTAND (router gate, then LLM interpreter)
     → IDENTIFY_TXN
     → RECOGNIZE? (show verified merchant, date, channel, card last4)
         └─ recognized → resolved without a dispute (deflection)
     → CHECK_POLICY → CONFIRM → ACT (create dispute; offer card block if unauthorized)
     → VERIFY (read-back) → RESPOND
Any state → CLARIFY (max 2 rounds) | ABSTAIN | ESCALATE (HandoffPacket)
```

Core rules: the model never receives or chooses `customer_id`; every write needs a confirmation
token bound to the exact action plus a read-back; no text claims an action without
`verified=true`; `is_fraud` is never used at runtime.

## Baselines

- **Business:** the current human call center, measured from the organizer data.
- **System:** an LLM-only agent with the same tools, `customer_id` visible, no policy, gates or
  router (`baseline_llm_only`).
- **ML:** a keyword router (the StubProvider rules).

## Differentiators (in order of importance)

1. Cross-authored, pre-registered, sealed held-out set of ≥ 200 cases with dialect quotas
   (ES-MX, ES-CO, ES-AR voseo, PT-BR) and Cohen's kappa on a 20% second-annotator sample. **P0**
2. Recognition step with a deflection metric. **P0**
3. dbt contracts, tests and published lineage. **P0**
4. Cost-per-resolution ROI with tornado sensitivity analysis. **P0**
5. Split-conformal router abstention (α = 0.1). **P0**
6. Side-by-side naive vs. controlled agent UI. **P1**
7. Load test. **P1**
8. External pilot with 5-10 people. **P1, conditional**

## Tasks

### M0 Foundations (D1)

**T1. Repo safety, remote and challenge docs** [P0] (Santiago)
Move organizer originals to `private/`; `.gitignore`; remote URL; `.env.example` and
`scripts/init_env.py` (never prints values; AWS keys only via `aws configure --profile factored`);
`check_forbidden_paths` hook + gitleaks + pre-commit; versioned challenge docs without credentials.
*Tests:* staging a runtime-built fake AWS key and a force-added `private/` file are both blocked.

**T2. Tool-neutral harness and CI** [P0] (Santiago)
uv project (Python 3.12), poe tasks, `AGENTS.md` (canonical) + `CLAUDE.md` import, area rules in
`docs/rules/`, Kiro steering, nested Claude imports, 8 skills in `.agents/skills` mirrored by
`scripts/sync_skills.py`, CI workflow, CODEOWNERS, PR template, ADRs, docs skeletons.
Create GitHub issues and labels for all tasks after user confirmation.
*Tests:* `poe check`, `skills-check` detects drift, CI green on the first PR.

**T3. Shared contracts, stub LLM and fixture bank** [P0] (all, led by Victor)
Pydantic v2 contracts (`bankagent.contracts`) + JSON Schema export; `Tool` and `LLMProvider`
protocols; `StubProvider` with deterministic ES/PT keyword rules and fault modes; synthetic fixture
personas in YAML and a deterministic DuckDB builder validated against the serving contract.
*Tests:* round-trip and invariants, schema snapshot (`contracts-check`), fixture content hash
(`fixtures-check`), stub intents in ES/PT, fault injection, determinism.

### M1 Shippable v0 (D2-3)

**T4. Ingest to bronze with manifest** [P0] (Santiago)
Download with `AWS_PROFILE`; `data/raw` → bronze parquet; `_manifest.json` with rows and sha256
per file; drift detection against the previous manifest; control file. `poe ingest`.

**T5. dbt silver** [P0] (Juan José)
Enforced contracts, `dq_*` flags, tests, `dq_report.md`; reproduce or refute other teams' public
data findings and record them in `docs/decision_ledger.md`. Verify the `fraud_score` threshold
(ADR 0003).

**T6. Gold + serving DB** [P0] (Juan José)
Gold models exactly per the serving contract, plus `cc_contact_baseline` and
`complaints_baseline`; an incremental model with a late-arrival fixture; forbidden-columns check;
`DATA_MODE` (`synthetic` | `curated`).

**T7. Identity, sessions, operational store** [P0] (Victor)
Persona login + mock OTP, signed TTL session tokens (`APP_SECRET_KEY`), SQLite ops store for
disputes, blocks and confirmation tokens.

**T8. Tools layer** [P0] (Victor)
All tools per `TOOL_SPECS`: session-scoped parameterized SQL, BOLA tests, confirmation tokens,
idempotency, read-back verification.

**T9. Policy engine** [P0] (Victor)
`policy/dispute_policy_v1.yaml` with stable rule ids, labeled synthetic unless verified
(Argentina Ley 25.065 arts. 26-29 verified; MX/CO TODO-verify by a human); `poe policy-explain`.

**T10. LLM interpreter** [P0] (Jacobo)
OpenAI `gpt-6-luna` structured outputs behind `LLMProvider`; cassettes; `config/pricing.yaml`;
MLflow autolog; spend limit; prompt versioning.

**T11. ES/PT templates from verified facts** [P0] (Jacobo)
Templates per state and language filled only with verified facts; neutral recognition prompt.

**T12. Evaluation harness core + pre-registration** [P0] (Santiago)
Scripted-user simulator driven by `FactSheet`; runners for both systems; scorer from
`ExecutionRecord`s; official metrics + deflection; Wilson CIs; slices; repeats;
`eval/preregistration.md`; `eval/gates.yaml`; `eval-smoke` in CI (StubProvider, 0 USD).

**T13. Orchestrator walking skeleton incl. recognition step** [P0] (Victor + Jacobo)
State machine end to end, `POST /api/chat/turn`, max 2 clarification rounds, handoff packets.

**T14. Chat UI** [P0] (Jacobo)
React + Vite + TS served by FastAPI; login, chat, confirmation dialog; Playwright smoke test.

**T15. Container + Render staging + submission skeleton** [P0] (Santiago)
One Docker image under 512 MB; Render deploy hook; `/health` and `/ready`;
`docs/submission/{slides_outline,video_script,checklist}.md`.

### M2 Real data + learned router (D4-5)

**T16. Problem evidence + ROI** [P0] (Juan José)
Call-center and complaints baselines from gold; cost per resolution; tornado sensitivity.

**T17. Held-out + dev pool** [P0] (all; Santiago coordinates)
Cross-authored cases with dialect quotas; `HELDOUT_DIR` outside the repo; only the sha256 manifest
committed; 20% second-annotator sample and kappa.

**T18. Learned router with conformal abstention** [P0] (Juan José + Santiago)
Local ONNX embeddings + logistic regression vs. keywords vs. LLM zero-shot; split-conformal
α = 0.1; MLflow; model card.

**T19. Wire curated data** [P0] (Juan José + Victor)
Serving DB built from organizer data (`DATA_MODE=curated`, local only), same contract.

**T20. Observability and reliability** [P0] (Santiago + Victor)
Fallback matrix (LLM timeout/malformed/unavailable, tool unavailable), PII redaction in logs,
`docs/operations.md`.

**T21. Agent console, evidence view, `/lineage`** [P0 lineage / P1 views] (Jacobo + Juan José)
Handoff queue view, evidence view per case, dbt docs published at `/lineage`.

### M3 Hardening (D5-6)

**T22. Side-by-side naive vs. controlled** [P1] (Jacobo)

**T23. Load test (k6 or locust)** [P1] (Santiago)

**T24. promptfoo red team** [P1] (Santiago + Jacobo)
Plugins: bola, bfla, rbac, pii, hijacking, system-prompt-override, indirect-prompt-injection,
excessive-agency, off-topic. Remote generation allowed; fall back to local generation if not viable.

**T25. External pilot** [P1, conditional] (Santiago coordinates)
5-10 people. Fallback: 40 more cross-authored held-out cases.

**T26. Freeze + final deploy** [P0] (Santiago; a human picks the host: Cloud Run or Render)

### M4 Final evaluation + submission (D7-8)

**T27. Final evaluation** [P0] (all)
Unseal once, 3 repeats, both systems, deviations from the pre-registration listed.

**T28. Submission package** [P0] (all)
README, 4-6 slides, video, fresh-clone dry run, email to hackathon.admin@factored.ai.

## P2 backlog

LLM paraphrase of templates; dispute-status follow-up; verifier-sabotage test; Langfuse export.
