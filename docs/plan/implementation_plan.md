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

> Superseded by the scope reduction below (2026-10-02): the cut order above already happened for
> T22-T25, and T18-T21 shrank to a minimum version. Nothing is deleted; the rest moves to T29.

## Scope reduction (2026-10-02)

Why: three days to submission (freeze end of Sat Oct 3, email Mon Oct 5) and the visible part of
the product does not exist yet: no UI (T14), no public URL (T15), no held-out set (T17), no final
numbers (T27), no video or slides (T28). Review rounds on T9 and T11 went deeper than the
remaining budget allows. This is a change of priority, not a verdict that the work is not worth
doing: everything that is reduced or postponed is listed in **T29** so it is not lost.

**Rule from now on.** A review blocks a merge only when it would break the demo, break a safety
rule in `AGENTS.md`, or make a claim we cannot back. Everything else becomes a line in
`docs/limitations.md` with an owner and a task number. The policy (`dispute-v1.1`) is frozen: no
new rules and no more tuning of the synthetic placeholders.

**Critical path (ship this first, in this order).**

| # | Task | What "done" means now |
|---|---|---|
| 1 | T13 finish | The four demo-visible gaps from the PR #46 review: card-block offer, copy for an ineligible decision, a first "Hola" does not end the chat, two matching charges get a question a customer can answer. Plus the simulator order fix (`_CONFIRM` before `_RECOGNIZE`). Merged (PR #46, with #48 and #53), and `proposed_system()` is wired into the harness (one adapter, `docs/eval/system_interface.md` section 4): `uv run poe eval-run --system proposed` runs the dev cases. |
| 2 | T15 | Dockerfile, `/health`, `/ready`, a public URL. Independent of T13, start now. Include a way to reset the demo (a persona escalates on its second dispute until the ops store is reset, `docs/limitations.md`). |
| 3 | T14 | One screen: persona login, chat, confirm/cancel. No console, no side-by-side. |
| 4 | T17 | Reduced held-out, see below. |
| 5 | T27 | One run, both systems (the LLM-only baseline is built: `bankagent.eval.baseline`), deviations from the pre-registration listed. Pre-registration and gates frozen on 2026-10-03. |
| 6 | T28 | README, 4-6 slides, 3-minute video, fresh-clone dry run, email. |

**Reduced (minimum version, still P0 unless noted).**

| Task | Before | Now |
|---|---|---|
| T17 | ≥ 200 cases, ≥ 80 automatable, ≥ 30 escalating | Floor n ≥ 80 (`eval/gates.yaml`: G2 cannot pass below n = 73) with ≥ 30 automatable and ≥ 30 escalating cases, so every gate can be evaluated (G3 `min_n` lowered before the freeze; at n = 80 G2 tolerates no unsafe case, at n = 120 one). Keep the dialect quotas (each of es-MX, es-CO, es-AR, pt-BR ≥ 20%), the sha256 manifest and the 20% second-annotator sample with kappa. Include about 10 attack cases (prompt injection, access to another customer's transaction). The size reduction is listed in `eval/preregistration.md` section 11; T27 repeats it in the report. |
| T18 | ONNX embeddings + logistic regression vs. keywords vs. LLM zero-shot | TF-IDF (char n-grams) + logistic regression vs. the keyword router, split-conformal α = 0.1, MLflow run, one-page model card. ONNX and the LLM zero-shot comparator move to T29. |
| T20 | Fallback matrix, PII redaction, operations doc | PII redaction in logs and a short `docs/operations.md`. The tool-unavailable fallback is already in T13. |
| T21 | Console, evidence view, `/lineage` | `/lineage` only (static `dbt docs generate`, served by FastAPI), plus a plain handoff list if time allows. Evidence view moves to T29. |

**Lower priority (T19: P0 → P2; T22-T25: P1 → P2; not started, moved to T29).** T19 (the public demo runs on fixtures
anyway, because no data is committed; the agent on curated data stays a local check), T22, T23,
T24, T25. For T24: the attack cases inside T17 cover the safety claim we can back in time, and the
promptfoo plugins stay as the follow-up.

**Schedule.**

| When | Target |
|---|---|
| Fri Oct 2 evening | T13 merged. T15 started. T17 case writing split across the team (about 20 cases each, cross-authored). |
| Sat Oct 3 | T14, T15 live, T18 minimum, `/lineage`, T20 minimum. Freeze and tag at end of day (T26). |
| Sun Oct 4 | T27 single run. T28: README, slides, video, clean-clone dry run. |
| Mon Oct 5 | Send the email in the morning, with margin before the deadline. |

## Dependencies

```
T1 → T2 → T3
T3 → T4 → T5 → T6          T6 → T16          T5, T13 → T18
T3 → T7 → T8 → T9          T6, T13 → T19     T14, T5 → T21
T3 → T10 → T11             T12 → T17         T14 → T22
T3 → T12                   T13 → T20         T15 → T23, T25
T9, T11 → T13 → T14 → T15  T19 → T24         T17, T18, T19, T20 → T26 → T27 → T28
```

After the scope reduction T26 waits on T14, T15, T17, T18 (minimum), T20 (minimum) only; T19 and
T22-T25 no longer block it.

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

**T5. dbt silver** [P0] (Santiago)
Enforced contracts, `dq_*` flags, tests, `dq_report.md`; reproduce or refute other teams' public
data findings and record them in `docs/decision_ledger.md`. Verify the `fraud_score` threshold
(ADR 0003).

**T6. Gold + serving DB** [P0] (Juan José)
Gold models exactly per the serving contract, plus `cc_contact_baseline` and
`complaints_baseline`; an incremental model with a late-arrival fixture; forbidden-columns check;
`DATA_MODE` (`synthetic` | `curated`).

**T7. Identity, sessions, operational store** [P0] (Juan José)
Persona login + mock OTP, signed TTL session tokens (`APP_SECRET_KEY`), SQLite ops store for
disputes, blocks and confirmation tokens.

**T8. Tools layer** [P0] (Juan José)
All tools per `TOOL_SPECS`: session-scoped parameterized SQL, BOLA tests, confirmation tokens,
idempotency, read-back verification.

**T9. Policy engine** [P0] (Juan José)
`policy/dispute_policy_v1.yaml` with stable rule ids, labeled synthetic unless verified
(no country's dispute window is verified yet: `DSP-WIN-01` is a synthetic 90-day placeholder
until a human checks Argentina Ley 25.065 arts. 26-29 and the MX/CO equivalents, see
`docs/limitations.md` and T29); `poe policy-explain`.

**T10. LLM interpreter** [P0] (Jacobo)
OpenAI `gpt-6-luna` structured outputs behind `LLMProvider`; cassettes; `config/pricing.yaml`;
MLflow autolog; spend limit; prompt versioning.

**T11. ES/PT templates from verified facts** [P0] (Jacobo)
Templates per state and language filled only with verified facts; neutral recognition prompt.

**T12. Evaluation harness core + pre-registration** [P0] (Santiago)
Scripted-user simulator driven by `FactSheet`; runners for both systems; scorer from
`ExecutionRecord`s; official metrics + deflection; Wilson CIs; slices; repeats;
`eval/preregistration.md`; `eval/gates.yaml`; `eval-smoke` in CI (StubProvider, 0 USD).

**T13. Orchestrator walking skeleton incl. recognition step** [P0] (Jacobo)
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

### Cleanup

**T29. Post-freeze cleanup of what the plan did not reach** [P2] (whoever has time; owner per line)
One place for the work that was reduced or postponed by the scope reduction, so it is tracked and
not forgotten. Each line keeps its original task number. Nothing here blocks the submission.

| From | Pending work |
|---|---|
| T9 | Primary-source check of the dispute windows for AR, MX and CO (`DSP-WIN-01`); business sign-off on `DSP-ESC-02` and `DSP-ESC-03`; the two descoped rules (high amount, foreign transaction) if a threshold is ever signed off. |
| T13 | What the T13 finish left open (`docs/limitations.md`): a lock per session instead of the global one, which first needs the OpenAI provider's spend accounting made atomic (T10); conversation state persisted and expired for more than one worker; the keyword limits at the new questions (a negated answer to "which charge?", dates, a correction typed at the card-block question); the refusal copy for a card block with no dispute and for a claim's status; a native review of the new Portuguese copy. Done or decided in the T13 finish (decision ledger, 2026-10-02): handoff routing, rule ids on the refusal records, and the confirmation token issued at the "yes" (kept). |
| T17 | Grow the held-out set toward the pre-registered n ≥ 200. |
| T18 | ONNX embeddings and the LLM zero-shot comparator. |
| T19 | Agent on `DATA_MODE=curated` end to end, with evidence from a local run. |
| T20 | Full fallback matrix (LLM timeout, malformed, unavailable), Langfuse export. |
| T21 | Agent console and evidence view per case. |
| T22-T25 | Side-by-side UI, load test, promptfoo red team (plugins listed in T24), external pilot. |
| T8 | Customer-local date parsing and a one-day search window (`docs/limitations.md`). |

## P2 backlog

LLM paraphrase of templates; dispute-status follow-up; verifier-sabotage test; Langfuse export.
