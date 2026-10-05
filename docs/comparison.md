# T22: side-by-side replay

Open **Comparar agentes** on the sign-in screen, or navigate to `/compare`.
Choose a `comparison.json` exported by the evaluation harness. It stays in browser memory;
there is no upload, persistent browser storage, backend call or write action. Returning home
or reloading clears the replay. The page is a Spanish reviewer tool, separate from customer chat.

## Zero-cost walkthrough

```sh
uv run poe eval-smoke
uv run poe web-build
uv run poe serve
```

Open `/compare` and choose `eval/runs/smoke/comparison.json`. The smoke run uses the **scripted
naive and ideal fakes**, not the actual baseline and controlled agent. The visible SIMULATION
banner explains this. These are harness checks, not evidence of model quality.

Select a case and repeat, then use Previous / Next to inspect the conversation. Columns stay
paired by case ID and repeat index. A shorter or missing run is explicitly shown; the viewer
never borrows a different repeat. Follow-up user answers may differ because each agent asks
different questions. Metrics and unsafe events summarize the entire selected run, while the
conversation and execution steps show the selected turn. Reply text is quoted evidence and
may make unverified claims; verified actions come only from the scorer.

## Real systems and provenance

Existing dev runs also export this artifact automatically. For both real implementations on
the free stub (the LLM-only baseline cannot act on the keyword stub):

```sh
uv run python -m bankagent.eval.cli run --system baseline --system proposed --provider stub --budget-usd 0
```

Open `eval/runs/dev/comparison.json`. This is labeled a recorded execution, not a successful
real-model evaluation. Provider assumptions and step model/outcome are visible, including
fallbacks. Calls to a paid model require the `live-llm-check` workflow and a separately approved
hard cap. T22's development and tests spend **0 USD**.

The artifact includes suite ID, case-set hash, system names, cost assumptions, per-run scored
results and allowlisted execution steps. It uses the generated `ComparisonBundle` contract;
the browser validates its JSON Schema and rejects duplicate case/repeat/system keys and
inconsistent verified-action or safe-resolution claims, and a negative cost. Files are limited
to 8 MiB, 500 runs, 50 turns per run and 200 steps per turn. Missing counterpart runs are
supported.

## Boundaries

- Only all-dev suites export replay files. Held-out, pilot and red-team suites are excluded.
  The final evaluation (`uv run poe eval-full`) never writes one for the sealed set, whatever
  a case file says about its split: it knows the set by its manifest, and its report folder is
  committed. The report and the results are written before the replay, so a replay that
  cannot be built never costs them.
- Session identity, tool arguments, tool results and simulator ground truth are not exported.
  Dialogue passes through the existing redactor. Redaction is not a guarantee of anonymity;
  inspect synthetic dev artifacts before sharing them. Never commit run artifacts.
- Imported files are local reviewer evidence, not authenticated backend responses. Validation
  checks shape and consistency, not authenticity; the page makes no claim of a current action.
- No aggregate performance claims or gates are computed in the UI. The canonical report and
  scorer retain those responsibilities; the evaluation protocol and thresholds are unchanged.
- Keyboard controls, responsive layout, and light/dark WCAG checks are covered by Playwright.
  Full screen-reader validation remains manual.

## Validation

Run `uv run poe check`, `uv run poe contracts-check`, `uv run poe fixtures-check`,
`uv run poe web-check`, `uv run poe web-build`, and `uv run poe web-e2e`.
On Windows hosts where Poe cannot resolve `npm`, use the identical scripts directly:
`npm --prefix web run check`, `npm --prefix web run build`, and `npm --prefix web run e2e`.
