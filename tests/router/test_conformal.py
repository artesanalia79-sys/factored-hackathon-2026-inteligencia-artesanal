"""Split-conformal threshold and prediction sets of the learned router (Task 18)."""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest
from pydantic import ValidationError

from bankagent.contracts.decisions import RouterResult
from bankagent.contracts.enums import Intent
from bankagent.router.conformal import conformal_rank, conformal_threshold, prediction_set


def _calibration(rng: np.random.Generator, n: int, k: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Calibrated probabilities: each label is drawn from its own row's distribution."""
    probs = rng.dirichlet(np.full(k, 0.6), size=n)
    labels = np.array([rng.choice(k, p=row) for row in probs], dtype=np.int64)
    return probs, labels


@pytest.mark.parametrize(
    ("n", "alpha", "rank"),
    [
        (159, 0.1, 144),  # (n + 1)(1 - alpha) = 144 exactly, in spite of 160 * 0.9 = 144.00..03
        (160, 0.1, 145),  # 144.9 -> 145
        (9, 0.1, 9),  # the smallest n that can reach 90%
        (8, 0.1, 9),  # 8.1 -> 9 > n: not enough calibration messages
        (99, 0.05, 95),
    ],
)
def test_rank_is_the_finite_sample_order_statistic(n: int, alpha: float, rank: int) -> None:
    assert conformal_rank(n, alpha) == rank


def test_threshold_is_the_rank_th_smallest_score() -> None:
    true_class_probs = np.array([0.95, 0.9, 0.85, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3])
    probs = np.column_stack([true_class_probs, 1.0 - true_class_probs])
    labels = np.zeros(9, dtype=np.int64)
    # Scores 1 - p: 0.05 ... 0.7; rank ceil(10 * 0.9) = 9 is the largest one.
    assert conformal_threshold(probs, labels, 0.1) == pytest.approx(0.7)
    # rank ceil(10 * 0.5) = 5: the fifth smallest score, 0.3.
    assert conformal_threshold(probs, labels, 0.5) == pytest.approx(0.3)


def test_too_few_calibration_messages_put_every_intent_in_the_set() -> None:
    probs = np.full((8, 3), 1 / 3)
    labels = np.zeros(8, dtype=np.int64)
    q_hat = conformal_threshold(probs, labels, 0.1)
    assert q_hat == 1.0
    assert sorted(prediction_set(np.array([0.98, 0.01, 0.01]), q_hat)) == [0, 1, 2]


def test_threshold_never_grows_with_alpha() -> None:
    probs, labels = _calibration(np.random.default_rng(7), 300)
    alphas = [0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8]
    thresholds = [conformal_threshold(probs, labels, a) for a in alphas]
    assert all(a >= b for a, b in pairwise(thresholds))
    assert thresholds[0] > thresholds[-1]


def test_prediction_set_is_ordered_by_probability_and_can_be_empty() -> None:
    probs = np.array([0.2, 0.5, 0.3])  # scores 1 - p: 0.8, 0.5, 0.7
    assert prediction_set(probs, 0.85) == (1, 2, 0)
    assert prediction_set(probs, 0.75) == (1, 2)
    assert prediction_set(probs, 0.6) == (1,)
    assert prediction_set(probs, 0.1) == ()


def test_bad_inputs_are_refused() -> None:
    probs = np.full((10, 2), 0.5)
    labels = np.zeros(10, dtype=np.int64)
    for alpha in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="alpha"):
            conformal_threshold(probs, labels, alpha)
    with pytest.raises(ValueError, match="one row per"):
        conformal_threshold(probs[:5], labels, 0.1)
    with pytest.raises(ValueError, match="at least one"):
        conformal_rank(0, 0.1)


def test_empirical_coverage_meets_the_guarantee_on_exchangeable_data() -> None:
    # Theory: 1 - alpha <= E[coverage] <= 1 - alpha + 1 / (n + 1) for continuous scores.
    rng = np.random.default_rng(2026)
    alpha, n_cal, n_test, repetitions = 0.1, 200, 1000, 300
    coverage = []
    for repetition in range(repetitions):
        cal_probs, cal_labels = _calibration(rng, n_cal)
        q_hat = conformal_threshold(cal_probs, cal_labels, alpha)
        test_probs, test_labels = _calibration(rng, n_test)
        # The rule of prediction_set, vectorized; checked against it on a few rows.
        covered = 1.0 - test_probs[np.arange(n_test), test_labels] <= q_hat
        if repetition == 0:
            for row, label, inside in zip(
                test_probs[:50], test_labels[:50], covered[:50], strict=True
            ):
                assert (int(label) in prediction_set(row, q_hat)) == bool(inside)
        coverage.append(float(np.mean(covered)))
    mean = float(np.mean(coverage))
    assert 1 - alpha - 0.005 <= mean <= 1 - alpha + 1 / (n_cal + 1) + 0.005


@pytest.mark.parametrize(
    ("members", "abstain"),
    [
        ((Intent.CARD_BLOCK,), False),
        ((Intent.CARD_BLOCK, Intent.DISPUTE_UNRECOGNIZED), True),
        ((), True),
    ],
)
def test_the_router_answers_only_with_a_one_intent_set(
    members: tuple[Intent, ...], abstain: bool
) -> None:
    result = RouterResult(
        prediction_set=members, abstain=abstain, alpha=0.1, q_hat=0.8, model_version="t"
    )
    assert result.intent == (None if abstain else members[0])
    with pytest.raises(ValidationError, match="singleton"):
        RouterResult(
            prediction_set=members, abstain=not abstain, alpha=0.1, q_hat=0.8, model_version="t"
        )
