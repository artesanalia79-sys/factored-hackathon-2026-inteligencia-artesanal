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

`preregistration.md` and `gates.yaml` were **frozen on 2026-10-03**, before the held-out set was
written; any later change is a deviation (`preregistration.md`, section 11).

## Real systems (`uv run poe eval-run`)

Runs `baseline` and `proposed` (the Task 13 agent) on the real Task 8 tools, a
fresh ops store per case run. `--provider stub` costs 0 USD; `--provider openai` costs money and
needs the owner's approval of an estimate first. See `docs/eval/system_interface.md`.

## Smoke run (CI, 0 USD)

```bash
uv run poe eval-smoke
```

Runs the 15 dev cases x 3 repeats through two scripted fakes (`bankagent.eval.fake`) with the
`StubProvider` and a 0 USD spend limit, writes `eval/runs/smoke/report.md` and fails when the
harness self-checks fail: the `ideal` fake must be correct and safe on every case, and the `naive`
fake must trigger every `UnsafeEvent`. The report is labeled SIMULATED: fake numbers are never
results.

## Real agent on the dev set (CI, 0 USD)

```bash
uv run pytest tests/eval/test_proposed_dev_run.py -q
```

Runs the 15 dev cases x 2 repeats through `bankagent.eval.adapters.proposed_system()` (the Task 13
agent with the `StubProvider`, the fixture bank, the real tools and policy, and a new ops store per
case run) and fails unless every run is correct, with no unsafe event and no question the scripted
user cannot classify. See `docs/eval/system_interface.md`, section 4.

Pipeline: `cases` -> `simulator` (scripted user) -> `runner` (session, provider, instrumented
tools, spend limit) -> `ExecutionRecord`s + tool observations -> `scorer` (`EvalResult`, one
detector per `UnsafeEvent`) -> `metrics` (case-level, Wilson 95%, slices) -> `gates` -> `report`.

## Held-out set (`uv run poe heldout`, Task 17)

The cases live in `HELDOUT_DIR` (set it in `.env`: an absolute path outside the repository), one
`<case_id>.yaml` per case with `split: heldout`. The commands print counts, file names and field
names, never an utterance or a label, so a coding agent may run them. In this order:

| Step | Command | What it does |
|---|---|---|
| 1 | `uv run poe heldout --cases <my folder> check --partial` | each author, on their own cases: valid `EvalCase`, ids `heldout-<author>-<number>`, fixture ids, no repeated conversation, no copy of a dev case |
| 2 | `uv run poe heldout check` | the whole set against the plan in `gates.yaml`: n ≥ 80, ≥ 30 automatable, ≥ 30 that require escalation, each dialect ≥ 20%, every category; prints the composition (attacks included) |
| 3 | `uv run poe heldout sample --out <folder outside the repo>` | blind sheets (no labels, category or notes) of 20% of each author's cases, in `cases-by-<author>/`, for a teammate who is not that author |
| 4 | `uv run poe heldout kappa --annotations <folder>` | Cohen's kappa of `expected_outcome` and `requires_escalation`, and the case ids to resolve |
| 5 | `uv run poe heldout seal` | step 2 again, then writes `heldout_manifest.sha256` (commit it) |
| 6 | `uv run poe heldout verify` | bytes against the manifest; Task 27 runs it before opening the set |

A label changed while resolving a disagreement is changed before step 5: after the seal a case
does not change. The manifest has the `sha256sum` format (`sha256sum -c` from inside
`HELDOUT_DIR` checks it too).

**Never read or copy held-out cases into this folder.** `load_cases` refuses `eval/heldout/` and
`HELDOUT_DIR`.
