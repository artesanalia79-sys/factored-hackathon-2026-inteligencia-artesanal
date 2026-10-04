"""Training and evaluation of the learned router (Task 18)."""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Sequence

import numpy as np
import pytest
from sklearn.metrics import f1_score

from bankagent.contracts.enums import Intent
from bankagent.eval.metrics import Rate
from bankagent.eval.router import evaluate as evaluate_module
from bankagent.eval.router.evaluate import (
    C_GRID,
    KEYWORD_ANSWER_CONFIDENCE,
    REPEATS,
    TrainedRouter,
    abstention,
    best_c,
    grouped_splits,
    keyword_answers,
    macro_f1,
    repeat_seeds,
    scenario_bootstrap_interval,
    train_router,
)
from bankagent.router.conformal import grouped_conformal_threshold
from bankagent.router.corpus import Example, load_corpus
from bankagent.router.model import MODEL_VERSION

ONE_C = (10.0,)  # a one-value grid keeps these tests fast; `poe router` searches the full grid


@pytest.fixture(scope="module")
def examples() -> tuple[Example, ...]:
    return load_corpus().examples


@pytest.fixture(scope="module")
def trained(examples: tuple[Example, ...]) -> TrainedRouter:
    return train_router(examples, alpha=0.1, seed=42, grid=ONE_C)


def test_splits_partition_the_corpus_by_scenario(examples: tuple[Example, ...]) -> None:
    splits = grouped_splits(examples, seed=42)
    parts = (splits.fit, splits.calibration, splits.test)
    assert sorted(i for part in parts for i in part) == list(range(len(examples)))
    group_sets = [{examples[i].group for i in part} for part in parts]
    assert not group_sets[0] & group_sets[1]
    assert not group_sets[0] & group_sets[2]
    assert not group_sets[1] & group_sets[2]
    for part in parts:
        assert {examples[i].intent for i in part} == set(Intent)
    assert len(splits.fit) == 3 * len(splits.test)


def test_splits_depend_only_on_the_seed(examples: tuple[Example, ...]) -> None:
    assert grouped_splits(examples, seed=42) == grouped_splits(examples, seed=42)
    assert grouped_splits(examples, seed=42).test != grouped_splits(examples, seed=43).test


def test_the_test_split_never_shapes_the_model(
    examples: tuple[Example, ...], trained: TrainedRouter
) -> None:
    # Rewrite every test message: the split, C, the fit and q_hat must not move.
    test = set(trained.splits.test)
    altered = tuple(
        dataclasses.replace(e, text=f"{e.text} zzz qqq") if i in test else e
        for i, e in enumerate(examples)
    )
    again = train_router(altered, alpha=0.1, seed=42, grid=ONE_C)
    assert again.splits == trained.splits
    assert again.c == trained.c
    assert again.router.q_hat == trained.router.q_hat
    probe = ["Perdí mi tarjeta", "Me cobraron dos veces", "¿Cómo va mi reclamo?"]
    assert np.allclose(again.router.predict_proba(probe), trained.router.predict_proba(probe))


def test_c_is_chosen_on_the_fit_split_only(
    examples: tuple[Example, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    # With a one-value grid C cannot move, so look at what the C search is given instead.
    seen: list[set[str]] = []
    real = evaluate_module.select_c

    def spy(
        chosen_from: Sequence[Example], seed: int, grid: Sequence[float] = C_GRID
    ) -> tuple[float, dict[float, float]]:
        seen.append({e.text for e in chosen_from})
        return real(chosen_from, seed, grid)

    monkeypatch.setattr(evaluate_module, "select_c", spy)
    again = train_router(examples, alpha=0.1, seed=42, grid=ONE_C)
    assert seen == [{examples[i].text for i in again.splits.fit}]


def test_q_hat_is_the_conformal_threshold_of_the_calibration_split(
    examples: tuple[Example, ...], trained: TrainedRouter
) -> None:
    router = trained.router
    calibration = [examples[i] for i in trained.splits.calibration]
    probs = router.predict_proba([e.text for e in calibration])
    labels = np.array([router.classes.index(e.intent) for e in calibration], dtype=np.int64)
    assert router.q_hat == pytest.approx(
        grouped_conformal_threshold(probs, labels, [e.group for e in calibration], 0.1),
        abs=1e-12,
    )


def test_bootstrap_resamples_scenarios_not_dialect_rows(examples: tuple[Example, ...]) -> None:
    intent = Intent.CARD_BLOCK
    clustered = [Example("one", intent, examples[0].dialect, "a") for _ in range(5)] + [
        Example("two", intent, examples[0].dialect, "b") for _ in range(5)
    ]
    interval = scenario_bootstrap_interval(clustered, [True] * 5 + [False] * 5)
    assert interval == (0.0, 1.0)


def test_the_best_c_has_the_highest_score_and_a_tie_goes_to_the_smaller_c() -> None:
    assert best_c({1.0: 0.70, 10.0: 0.80, 100.0: 0.75}) == 10.0
    assert best_c({100.0: 0.80, 3.0: 0.80 + 1e-12, 30.0: 0.79}) == 3.0  # equal to 9 decimals


def test_the_repeats_never_reuse_the_main_split_seed() -> None:
    assert repeat_seeds(42, 3) == (43, 44, 45)
    assert 42 not in repeat_seeds(42, REPEATS)
    assert repeat_seeds(42, 0) == ()


def test_route_returns_a_consistent_router_result(trained: TrainedRouter) -> None:
    result = trained.router.route("Se me perdió la tarjeta de débito anoche, bloquéenla")
    assert set(result.probabilities) == set(Intent)
    assert sum(result.probabilities.values()) == pytest.approx(1.0)
    ranked = [result.probabilities[i] for i in result.prediction_set]
    assert ranked == sorted(ranked, reverse=True)
    assert result.abstain == (len(result.prediction_set) != 1)
    assert (result.alpha, result.q_hat) == (0.1, trained.router.q_hat)
    assert result.model_version == MODEL_VERSION
    assert max(result.probabilities, key=lambda i: result.probabilities[i]) == Intent.CARD_BLOCK


def test_training_is_deterministic(examples: tuple[Example, ...], trained: TrainedRouter) -> None:
    again = train_router(examples, alpha=0.1, seed=42, grid=ONE_C)
    assert again.router.q_hat == trained.router.q_hat
    probe = [e.text for e in examples[:20]]
    assert np.array_equal(again.router.predict_proba(probe), trained.router.predict_proba(probe))


def test_macro_f1_matches_scikit_learn() -> None:
    rng = random.Random(3)
    labels = ["a", "b", "c", "d"]
    truth = [rng.choice(labels[:3]) for _ in range(200)]  # "d" only ever predicted
    predicted = [rng.choice(labels) for _ in range(200)]
    expected = f1_score(
        truth,
        predicted,
        labels=sorted(set(truth)),
        average="macro",
        zero_division=0.0,  # pyright: ignore[reportArgumentType]
    )
    assert macro_f1(truth, predicted) == pytest.approx(float(expected))
    assert macro_f1(["a", "b"], ["c", "c"]) == 0.0


def test_abstention_metrics_on_a_hand_example() -> None:
    unr, dup, blk = Intent.DISPUTE_UNRECOGNIZED, Intent.DISPUTE_DUPLICATE, Intent.CARD_BLOCK
    truth = [unr, dup, blk, blk]
    sets = [(unr,), (unr,), (blk, unr), ()]
    result = abstention(truth, sets)
    assert result.set_coverage == Rate(2, 4)  # unr in (unr,), blk in (blk, unr)
    assert result.answered == Rate(2, 4)
    assert result.answered_accuracy == Rate(1, 2)
    assert result.mean_set_size == pytest.approx(1.0)
    assert result.empty_sets == 1


def test_the_keyword_router_answers_only_when_confident() -> None:
    unr, oos = Intent.DISPUTE_UNRECOGNIZED, Intent.OUT_OF_SCOPE
    keyword = [(unr, 0.85), (unr, 0.45), (oos, 0.3), (oos, KEYWORD_ANSWER_CONFIDENCE)]
    result = keyword_answers([unr, unr, oos, unr], keyword)
    assert result.answered == Rate(2, 4)
    assert result.answered_accuracy == Rate(1, 2)
