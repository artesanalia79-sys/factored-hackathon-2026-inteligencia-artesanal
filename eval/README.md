# eval

Owner: Santiago. Rules: `docs/rules/eval.md`. Skill: `eval-case-authoring`. Code:
`src/bankagent/eval/`. Agent interface for Task 13: `docs/eval/system_interface.md`.

| Path | Purpose | Task |
|---|---|---|
| `dev/` | development cases (`EvalCase` YAML), free to read and tune on | T12, T17 |
| `preregistration.md` | hypotheses, systems, workload, metric and unsafe-event definitions, statistics | T12 |
| `gates.yaml` | pass/fail thresholds and the held-out plan, evaluated by `bankagent.eval.gates` | T12 |
| `heldout_manifest.sha256` | hashes of the sealed held-out files (the files live in `HELDOUT_DIR`) | T17 |
| `runs/` | local run outputs (`results.jsonl`, `unsafe_reasons.jsonl`, `report.md`), gitignored | T12 |
| `reports/` | final evaluation reports | T27 |

`preregistration.md` and `gates.yaml` are **drafts** until the team freezes them, before the
held-out set is unsealed.

## Smoke run (CI, 0 USD)

```bash
uv run poe eval-smoke
```

Runs the 10 dev cases x 3 repeats through two scripted fakes (`bankagent.eval.fake`) with the
`StubProvider` and a 0 USD spend limit, writes `eval/runs/smoke/report.md` and fails when the
harness self-checks fail: the `ideal` fake must be correct and safe on every case, and the `naive`
fake must trigger every `UnsafeEvent`. The report is labeled SIMULATED: fake numbers are never
results.

Pipeline: `cases` -> `simulator` (scripted user) -> `runner` (session, provider, instrumented
tools, spend limit) -> `ExecutionRecord`s + tool observations -> `scorer` (`EvalResult`, one
detector per `UnsafeEvent`) -> `metrics` (case-level, Wilson 95%, slices) -> `gates` -> `report`.

**Never read or copy held-out cases into this folder.** `load_cases` refuses `eval/heldout/` and
`HELDOUT_DIR`.
