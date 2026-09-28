# eval

Owner: Santiago. Rules: `docs/rules/eval.md`. Skill: `eval-case-authoring`.

| Path | Purpose | Task |
|---|---|---|
| `dev/` | development cases (`EvalCase` YAML), free to read and tune on | T12, T17 |
| `preregistration.md` | metrics, hypotheses and analysis plan, frozen before unsealing | T12 |
| `gates.yaml` | pass/fail thresholds for the final evaluation | T12 |
| `heldout_manifest.sha256` | hashes of the sealed held-out files (the files live in `HELDOUT_DIR`) | T17 |
| `reports/` | final evaluation reports | T27 |

`uv run poe eval-smoke` runs a small dev subset with the StubProvider (0 USD) and is part of CI once
Task 12 lands. **Never read or copy held-out cases into this folder.**
