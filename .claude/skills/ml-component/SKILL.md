---
name: ml-component
description: Train, evaluate and document the learned intent router (embeddings + logistic regression) with grouped splits, baselines, split-conformal abstention, MLflow tracking and a model card. Use when working on src/bankagent/router or any learned component.
---

# ML component: learned router with conformal abstention

Goal: a small, honest learned component that beats the keyword baseline and knows when to abstain.
Read `docs/rules/eval.md` first. Owners: Juan José + Santiago (Task 18).

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

## 2. Baselines (always report all three)

1. Keyword router: `bankagent.interpret.keywords` (the StubProvider rules).
2. LLM zero-shot: the real interpreter's intent field on the same test set (budgeted run).
3. Learned: local ONNX sentence embeddings + `LogisticRegression(class_weight="balanced")`.

## 3. Metrics

Macro-F1, per-class recall, and for abstention: coverage (share with a singleton prediction set),
accuracy on covered cases, and empirical set coverage. Slice by language and dialect.

## 4. Split-conformal abstention (α = 0.1)

```python
import numpy as np

def conformal_threshold(cal_probs: np.ndarray, cal_labels: np.ndarray, alpha: float = 0.1) -> float:
    """Nonconformity = 1 - p(true class). Returns q_hat for the (1 - alpha) guarantee."""
    n = len(cal_labels)
    scores = 1.0 - cal_probs[np.arange(n), cal_labels]
    level = min(1.0, np.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(scores, level, method="higher"))

def prediction_set(probs: np.ndarray, q_hat: float) -> list[int]:
    return [k for k, p in enumerate(probs) if 1.0 - p <= q_hat]
```

- The router abstains when the prediction set is not a singleton (`RouterResult` enforces
  `abstain == (len(prediction_set) != 1)`). Abstention hands off to the LLM interpreter or clarify.
- Verify empirical coverage on `test` is ≥ 1 − α (within sampling error) and report it.

## 5. Tracking

```python
import mlflow

mlflow.set_tracking_uri("file:./mlruns")  # gitignored
mlflow.set_experiment("router")
with mlflow.start_run(run_name="logreg-onnx-v1"):
    mlflow.log_params({"alpha": 0.1, "embedder": EMBEDDER, "C": C, "seed": 42})
    mlflow.log_metrics({"macro_f1": f1, "coverage": cov, "covered_accuracy": acc})
```

## 6. Model card (`docs/models/router.md`)

Sections: intended use, out-of-scope use, training data (sources, sizes, languages), splits,
baselines and results table with confidence intervals, conformal calibration, slices, known
failure modes, ethical considerations (no protected attributes), how to retrain, version.

## Done when

- Tests cover the conformal functions (threshold monotonic in α, singleton ⇒ not abstain).
- Model artifact size is small enough for the 512 MB container; loading time is logged.
- Results and the model card are committed; `mlruns/` is not.
