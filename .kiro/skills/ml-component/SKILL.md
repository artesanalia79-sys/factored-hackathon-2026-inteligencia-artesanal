---
name: ml-component
description: Train, evaluate and document the learned intent router (embeddings + logistic regression) with grouped splits, baselines, split-conformal abstention, MLflow tracking and a model card. Use when working on src/bankagent/router or any learned component.
---

# ML component: learned router with conformal abstention

Goal: a small, honest learned component that beats the keyword baseline and knows when to abstain.
Read `docs/rules/eval.md` first. Owners: Juan José + Santiago (Task 18).

**What exists (T18 minimum, scope reduction of 2026-10-02).** `src/bankagent/router/` (model,
conformal sets, corpus loader; its evaluation is in the harness, `src/bankagent/eval/router/`): TF-IDF
character n-grams + logistic regression instead of ONNX embeddings, compared with the keyword
router only (the LLM zero-shot comparison and ONNX are T29), trained on the synthetic corpus in
`eval/router/corpus/` (one file per intent, scenario groups in five dialects) and checked on
`eval/router/external_check.yaml`. `uv run poe router` evaluates and writes
`docs/evidence/router_eval.md`; the card is `docs/models/router.md`. The router is offline: the
agent's router gate is still the keyword attack gate. After any change to the corpus or the router
code, rerun `poe router` and commit the report: a test checks that it is what the code produces.

## 1. Data and splits

- Training data: dev-pool utterances (`eval/dev/`) plus synthetic paraphrases. **Never** held-out.
- Split by group (`conversation_id` or `customer_id`), never by row, so paraphrases of the same
  conversation do not leak across splits:

```python
from sklearn.model_selection import GroupShuffleSplit

gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
train_idx, test_idx = next(gss.split(X, y, groups=groups))
# Then split train again into fit (75%) and calibration (25%) with the same technique.
```

- Keep three disjoint sets: `fit`, `calibration`, `test`. Record their sizes and label counts.
- In T18 the group is the corpus scenario, and `StratifiedGroupKFold` (5 folds: test,
  calibration, three fit) also balances intents; choose hyperparameters inside `fit` only.

## 2. Baselines (always report all three)

1. Keyword router: `bankagent.interpret.keywords` (the StubProvider rules).
2. LLM zero-shot: the real interpreter's intent field on the same test set (budgeted run).
3. Learned: local ONNX sentence embeddings + `LogisticRegression(class_weight="balanced")`.

## 3. Metrics

Macro-F1, per-class recall, and for abstention: coverage (share with a singleton prediction set),
accuracy on covered cases, and empirical set coverage. Slice by language and dialect.

## 4. Split-conformal abstention (α = 0.1)

```python
from bankagent.router.conformal import grouped_conformal_threshold, prediction_set

# One nonconformity score per scenario: the worst of its five dialect versions.
# The rank uses the number of scenarios, not the number of messages.
q_hat = grouped_conformal_threshold(cal_probs, cal_labels, cal_groups, alpha=0.1)
members = prediction_set(new_probs, q_hat)
```

- The router abstains when the prediction set is not a singleton (`RouterResult` enforces
  `abstain == (len(prediction_set) != 1)`). Abstention hands off to the LLM interpreter or clarify.
- Verify empirical coverage on `test` is ≥ 1 − α (within sampling error) and report it. With
  grouped data one split's coverage varies a lot: report its mean over repeated splits too.
- The finite-sample guarantee is over exchangeable scenarios. Use the maximum score per scenario
  if all dialect versions of a new scenario must be covered together; row-level calibration on
  five correlated dialects falsely counts five independent observations. Resample whole scenarios
  for uncertainty intervals in the report. A higher threshold may reduce the answer rate.
- Compute the rank with decimal arithmetic (`Decimal(str(alpha))`), not a fixed floating-point
  tolerance: subtracting `1e-9` can lower a genuinely noninteger rank near a boundary.
- Do not use `np.quantile(scores, level, method="higher")` with `level = ceil(...) / n`: numpy's
  index `(n - 1) * level` lands one rank above the order statistic. `bankagent.router.conformal`
  has the tested version.

## 5. Tracking

```python
import mlflow

mlflow.set_tracking_uri("file:./mlruns")  # gitignored; MLflow 3.16 needs MLFLOW_ALLOW_FILE_STORE=true
mlflow.set_experiment("router")
with mlflow.start_run(run_name="logreg-onnx-v1"):
    mlflow.log_params({"alpha": 0.1, "embedder": EMBEDDER, "C": C, "seed": 42})
    mlflow.log_metrics({"macro_f1": f1, "coverage": cov, "covered_accuracy": acc})
```

`bankagent.eval.router.tracking.log_run` sets `MLFLOW_ALLOW_FILE_STORE` and `MLFLOW_DISABLE_TELEMETRY`
before importing MLflow; do the same in any new script.

## 6. Model card (`docs/models/router.md`)

Sections: intended use, out-of-scope use, training data (sources, sizes, languages), splits,
baselines and results table with confidence intervals, conformal calibration, slices, known
failure modes, ethical considerations (no protected attributes), how to retrain, version.

## Done when

- Tests cover the conformal functions (threshold monotonic in α, singleton ⇒ not abstain).
- Model artifact size is small enough for the 512 MB container; loading time is logged.
- Results and the model card are committed; `mlruns/` is not.
