# Evaluation and ML rules

Applies to `eval/**`, `src/bankagent/eval/**` and `src/bankagent/router/**`. Owners: Santiago
(harness, gates, red team), Juan José + Santiago (learned router). Skills: `eval-case-authoring`,
`ml-component`.

## Integrity of the held-out set

- The sealed held-out set lives in `HELDOUT_DIR`, **outside the repository**. Agents and scripts
  must never read it before the final evaluation (Task 27). Only `eval/heldout_manifest.sha256`
  is versioned.
- Held-out cases are cross-authored: nobody writes held-out cases for the component they built.
- `eval/preregistration.md` and `eval/gates.yaml` are frozen before unsealing. Any later change is
  listed as a deviation in the final report.

## Cases

- Cases are `EvalCase` contracts (YAML) referencing fixture IDs. Each case declares
  `expected_outcome`, `acceptable_outcomes`, `expected_actions`, `forbidden_events`, category,
  split, dialect and provenance. Cover every `EvalCategory` and the dialect quotas
  (ES-MX, ES-CO, ES-AR voseo, PT-BR).
- A second annotator re-labels a 20% sample; report Cohen's kappa.

## Scoring

- Scores are computed from `ExecutionRecord`s, never from the assistant's text alone.
- An automated resolution counts only if every required action is `verified=true` and no
  `UnsafeEvent` occurred. Report Wilson 95% confidence intervals, per-slice metrics and repeats.
- Both systems (`baseline_llm_only`, `proposed`) run on identical cases, tools and budgets.
- CI runs `eval-smoke` with the `StubProvider` (0 USD). Real-LLM runs are manual and budgeted.
- Definitions, gates and the held-out plan live in `eval/preregistration.md` and `eval/gates.yaml`.
  Every `UnsafeEvent` has one detector in `bankagent.eval.scorer.DETECTORS` and a unit test that
  fails without it.
- Production code never imports `bankagent.eval`; the harness adapts the agent
  (`docs/eval/system_interface.md`).

## ML component (router)

- Split by conversation/customer group, never by row. Compare against the keyword baseline and
  LLM zero-shot (the minimum T18 compares with the keyword router only; the zero-shot comparison
  moved to T29). Log runs to local MLflow (`mlruns/` is gitignored).
- Abstention uses split-conformal prediction (α = 0.1) on a calibration split disjoint from
  training and test. Document everything in a model card under `docs/models/`.
- `is_fraud` may be used as an offline label only; it is never a runtime feature.
