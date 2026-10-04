"""TF-IDF character n-grams and logistic regression, with split-conformal abstention (Task 18).

Not in the request path: the agent's router gate is still the keyword attack gate, and this model
is evaluated offline (``docs/models/router.md``). It stays free of ``bankagent.eval`` so that the
orchestrator can adopt it later (T29).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from bankagent.contracts.decisions import RouterResult
from bankagent.contracts.enums import Intent
from bankagent.interpret.keywords import normalize
from bankagent.router.conformal import grouped_conformal_threshold, prediction_set

MODEL_VERSION = "router-tfidf-logreg-v1"
NGRAM_RANGE = (2, 5)
MIN_DF = 2


def build_pipeline(c: float, *, seed: int) -> Pipeline:
    """Character n-grams inside word boundaries, on the keyword interpreter's normalization
    (lowercase, no accents), so typos and missing accents still share most features; then a
    class-balanced multinomial logistic regression."""
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=NGRAM_RANGE,
                    min_df=MIN_DF,
                    sublinear_tf=True,
                    preprocessor=normalize,
                ),
            ),
            (
                "logreg",
                LogisticRegression(C=c, class_weight="balanced", max_iter=5000, random_state=seed),
            ),
        ]
    )


@dataclass(frozen=True)
class LearnedRouter:
    """A fitted pipeline plus the conformal threshold computed on a disjoint calibration split."""

    pipeline: Pipeline
    classes: tuple[Intent, ...]
    q_hat: float
    alpha: float
    version: str = MODEL_VERSION

    @classmethod
    def fit(
        cls,
        fit_texts: Sequence[str],
        fit_labels: Sequence[Intent],
        cal_texts: Sequence[str],
        cal_labels: Sequence[Intent],
        *,
        cal_groups: Sequence[str],
        c: float,
        alpha: float,
        seed: int,
    ) -> LearnedRouter:
        pipeline = build_pipeline(c, seed=seed)
        pipeline.fit(list(fit_texts), [label.value for label in fit_labels])
        classes = tuple(Intent(value) for value in pipeline.classes_)
        index = {intent: k for k, intent in enumerate(classes)}
        unseen = sorted({label.value for label in cal_labels if label not in index})
        if unseen:
            raise ValueError(f"calibration intents missing from the fit split: {unseen}")
        probs = np.asarray(pipeline.predict_proba(list(cal_texts)), dtype=np.float64)
        labels = np.array([index[label] for label in cal_labels], dtype=np.int64)
        return cls(
            pipeline,
            classes,
            grouped_conformal_threshold(probs, labels, cal_groups, alpha),
            alpha,
        )

    def predict_proba(self, texts: Sequence[str]) -> NDArray[np.float64]:
        """One row per message, columns in the order of ``classes``."""
        return np.asarray(self.pipeline.predict_proba(list(texts)), dtype=np.float64)

    def prediction_sets(self, probs: NDArray[np.float64]) -> list[tuple[Intent, ...]]:
        return [tuple(self.classes[k] for k in prediction_set(row, self.q_hat)) for row in probs]

    def route(self, text: str) -> RouterResult:
        return self.route_many([text])[0]

    def route_many(self, texts: Sequence[str]) -> list[RouterResult]:
        probs = self.predict_proba(texts)
        return [
            RouterResult(
                prediction_set=members,
                probabilities={
                    intent: min(1.0, max(0.0, float(p)))
                    for intent, p in zip(self.classes, row, strict=True)
                },
                abstain=len(members) != 1,
                alpha=self.alpha,
                q_hat=self.q_hat,
                model_version=self.version,
            )
            for row, members in zip(probs, self.prediction_sets(probs), strict=True)
        ]
