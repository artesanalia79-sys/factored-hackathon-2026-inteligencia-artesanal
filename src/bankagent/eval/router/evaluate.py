"""Offline evaluation of the learned router against the keyword router (Task 18).

Part of the harness (production code never imports ``bankagent.eval``), so it takes its Wilson
intervals from ``bankagent.eval.metrics``. Splits are stratified by intent and grouped by
scenario; ``C`` is chosen by grouped cross-validation inside the fit split, so neither the
calibration nor the test split shapes the model. The keyword router
(``bankagent.interpret.keywords``) needs no training and is scored on the same test messages.

BLAS runs on one thread: the matrices are small, so more threads only add overhead (six times
slower on a 4-CPU container, forty times on a 22-CPU laptop), and one thread keeps the
floating-point sums in the same order from run to run. Another CPU's BLAS kernels can still round
a sum differently (around the 15th digit), so the report test allows one unit in the last printed
digit of a decimal.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from sklearn.model_selection import StratifiedGroupKFold
from threadpoolctl import threadpool_limits

from bankagent.contracts.enums import Intent
from bankagent.eval.metrics import Rate
from bankagent.interpret.keywords import detect_intent, normalize
from bankagent.router.corpus import Corpus, Example, ExternalCheck, language_of
from bankagent.router.model import LearnedRouter, build_pipeline

ALPHA = 0.1
SEED = 42
REPEATS = 20
C_GRID: tuple[float, ...] = (1.0, 3.0, 10.0, 30.0, 100.0)
N_FOLDS = 5  # one test fold, one calibration fold, three fit folds: 60/20/20
INNER_FOLDS = 4  # choosing C inside the fit split
INTENTS: tuple[Intent, ...] = tuple(Intent)
# The keyword router's own low-confidence answers are the generic "cargo" fallback (0.45) and
# out_of_scope when no rule matched (0.3). Below this confidence it counts as not answering.
KEYWORD_ANSWER_CONFIDENCE = 0.5


def macro_f1(truth: Sequence[str], predicted: Sequence[str]) -> float:
    """Mean F1 over the labels present in ``truth``; a label never predicted scores 0."""
    pairs = list(zip(truth, predicted, strict=True))
    scores: list[float] = []
    for label in sorted(set(truth)):
        hits = sum(1 for t, p in pairs if t == label and p == label)
        guessed = sum(1 for _, p in pairs if p == label)
        actual = sum(1 for t, _ in pairs if t == label)
        precision = hits / guessed if guessed else 0.0
        recall = hits / actual
        total = precision + recall
        scores.append(2 * precision * recall / total if total else 0.0)
    return statistics.fmean(scores) if scores else 0.0


def keyword_intent(text: str) -> tuple[Intent, float]:
    """The keyword router's intent and confidence for one message (the StubProvider's rules)."""
    intent, confidence, _ = detect_intent(normalize(text))
    return intent, confidence


# ---------------------------------------------------------------------------
# Splits and training
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Splits:
    fit: tuple[int, ...]
    calibration: tuple[int, ...]
    test: tuple[int, ...]


def _folds(
    examples: Sequence[Example], n_splits: int, seed: int
) -> list[tuple[list[int], list[int]]]:
    labels = [e.intent.value for e in examples]
    groups = [e.group for e in examples]
    splitter = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    return [
        ([int(i) for i in kept], [int(i) for i in held])
        for kept, held in splitter.split(np.zeros(len(labels)), labels, groups)
    ]


def grouped_splits(examples: Sequence[Example], seed: int) -> Splits:
    """Disjoint fit, calibration and test indices; a scenario group lives in exactly one."""
    folds = _folds(examples, N_FOLDS, seed)
    test, calibration = sorted(folds[0][1]), sorted(folds[1][1])
    held = set(test) | set(calibration)
    fit = [i for i in range(len(examples)) if i not in held]
    return Splits(tuple(fit), tuple(calibration), tuple(test))


def best_c(scores: Mapping[float, float]) -> float:
    """The ``C`` with the highest mean macro-F1; a tie (to 9 decimals) goes to the smaller C,
    the stronger regularization."""
    return min(scores, key=lambda c: (-round(scores[c], 9), c))


def select_c(
    examples: Sequence[Example], seed: int, grid: Sequence[float] = C_GRID
) -> tuple[float, dict[float, float]]:
    """Mean macro-F1 of each ``C`` over grouped folds of ``examples``, and the best one."""
    texts = [e.text for e in examples]
    labels = [e.intent.value for e in examples]
    folds = _folds(examples, INNER_FOLDS, seed)
    scores: dict[float, float] = {}
    for c in grid:
        fold_scores: list[float] = []
        for kept, held in folds:
            pipeline = build_pipeline(c, seed=seed)
            pipeline.fit([texts[i] for i in kept], [labels[i] for i in kept])
            predicted = [str(p) for p in pipeline.predict([texts[i] for i in held])]
            fold_scores.append(macro_f1([labels[i] for i in held], predicted))
        scores[c] = statistics.fmean(fold_scores)
    return best_c(scores), scores


@dataclass(frozen=True)
class TrainedRouter:
    router: LearnedRouter
    splits: Splits
    c: float
    c_scores: Mapping[float, float]


def train_router(
    examples: Sequence[Example], *, alpha: float, seed: int, grid: Sequence[float] = C_GRID
) -> TrainedRouter:
    """Split, choose ``C`` on the fit split, fit on it and calibrate ``q_hat`` on calibration."""
    splits = grouped_splits(examples, seed)
    fit = [examples[i] for i in splits.fit]
    calibration = [examples[i] for i in splits.calibration]
    with threadpool_limits(limits=1):
        c, scores = select_c(fit, seed, grid)
        router = LearnedRouter.fit(
            [e.text for e in fit],
            [e.intent for e in fit],
            [e.text for e in calibration],
            [e.intent for e in calibration],
            c=c,
            alpha=alpha,
            seed=seed,
        )
    return TrainedRouter(router, splits, c, scores)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Classification:
    accuracy: Rate
    macro_f1: float  # over the intents present in the truth
    recall: Mapping[Intent, Rate]
    confusion: Mapping[Intent, Mapping[Intent, int]]  # true intent -> predicted intent -> count


def classification(truth: Sequence[Intent], predicted: Sequence[Intent]) -> Classification:
    pairs = list(zip(truth, predicted, strict=True))
    confusion = {t: dict.fromkeys(INTENTS, 0) for t in INTENTS}
    for t, p in pairs:
        confusion[t][p] += 1
    return Classification(
        accuracy=Rate(sum(t == p for t, p in pairs), len(pairs)),
        macro_f1=macro_f1([t.value for t in truth], [p.value for p in predicted]),
        recall={t: Rate(confusion[t][t], sum(confusion[t].values())) for t in INTENTS},
        confusion=confusion,
    )


@dataclass(frozen=True, slots=True)
class Abstention:
    set_coverage: Rate  # the true intent is inside the prediction set
    answered: Rate  # a one-intent set: the router answers instead of abstaining
    answered_accuracy: Rate  # among answered messages, the one intent is right
    mean_set_size: float
    empty_sets: int


def abstention(truth: Sequence[Intent], sets: Sequence[tuple[Intent, ...]]) -> Abstention:
    pairs = list(zip(truth, sets, strict=True))
    answered = [(t, s[0]) for t, s in pairs if len(s) == 1]
    return Abstention(
        set_coverage=Rate(sum(t in s for t, s in pairs), len(pairs)),
        answered=Rate(len(answered), len(pairs)),
        answered_accuracy=Rate(sum(t == p for t, p in answered), len(answered)),
        mean_set_size=statistics.fmean(len(s) for _, s in pairs) if pairs else 0.0,
        empty_sets=sum(1 for _, s in pairs if not s),
    )


@dataclass(frozen=True, slots=True)
class KeywordAnswers:
    """The keyword router with its own abstention: it answers only above 0.5 confidence."""

    answered: Rate
    answered_accuracy: Rate


def keyword_answers(
    truth: Sequence[Intent], keyword: Sequence[tuple[Intent, float]]
) -> KeywordAnswers:
    answered = [
        (t, intent)
        for t, (intent, confidence) in zip(truth, keyword, strict=True)
        if confidence >= KEYWORD_ANSWER_CONFIDENCE
    ]
    return KeywordAnswers(
        answered=Rate(len(answered), len(truth)),
        answered_accuracy=Rate(sum(t == p for t, p in answered), len(answered)),
    )


@dataclass(frozen=True, slots=True)
class Scored:
    """Both routers on one list of messages."""

    truth: tuple[Intent, ...]
    learned: tuple[Intent, ...]  # top-1 intent of the learned router
    sets: tuple[tuple[Intent, ...], ...]
    keyword: tuple[tuple[Intent, float], ...]


def score(router: LearnedRouter, examples: Sequence[Example]) -> Scored:
    texts = [e.text for e in examples]
    probs = router.predict_proba(texts)
    return Scored(
        truth=tuple(e.intent for e in examples),
        learned=tuple(router.classes[int(k)] for k in np.argmax(probs, axis=1)),
        sets=tuple(router.prediction_sets(probs)),
        keyword=tuple(keyword_intent(t) for t in texts),
    )


@dataclass(frozen=True, slots=True)
class Slice:
    name: str
    learned_accuracy: Rate
    keyword_accuracy: Rate
    set_coverage: Rate
    answered: Rate


def slices(examples: Sequence[Example], scored: Scored) -> tuple[Slice, ...]:
    """Per language, then per dialect, in a fixed order."""
    keys: list[tuple[str, list[int]]] = []
    for language in sorted({language_of(e.dialect).value for e in examples}):
        keys.append((language, [i for i, e in enumerate(examples) if e.language.value == language]))
    for dialect in sorted({e.dialect.value for e in examples}):
        keys.append((dialect, [i for i, e in enumerate(examples) if e.dialect.value == dialect]))
    out: list[Slice] = []
    for name, idx in keys:
        truth = [scored.truth[i] for i in idx]
        sets = [scored.sets[i] for i in idx]
        conformal = abstention(truth, sets)
        out.append(
            Slice(
                name=name,
                learned_accuracy=classification(truth, [scored.learned[i] for i in idx]).accuracy,
                keyword_accuracy=classification(
                    truth, [scored.keyword[i][0] for i in idx]
                ).accuracy,
                set_coverage=conformal.set_coverage,
                answered=conformal.answered,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# The whole evaluation
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Repeat:
    """The full pipeline (split, C, fit, calibration) on one more split seed."""

    seed: int
    c: float
    q_hat: float
    learned_accuracy: float
    learned_macro_f1: float
    keyword_accuracy: float
    set_coverage: float
    answered: float
    answered_accuracy: float


@dataclass(frozen=True, slots=True)
class ExternalRow:
    text: str
    intent: Intent
    learned: Intent
    learned_set: tuple[Intent, ...]
    keyword: Intent


@dataclass(frozen=True, slots=True)
class ExternalResult:
    learned: Classification
    keyword: Classification
    abstention: Abstention
    keyword_answers: KeywordAnswers
    rows: tuple[ExternalRow, ...]


@dataclass(frozen=True)
class Evaluation:
    alpha: float
    seed: int
    corpus: Corpus
    external_sha256: str
    trained: TrainedRouter
    split_counts: Mapping[str, Mapping[Intent, int]]
    learned: Classification
    keyword: Classification
    abstention: Abstention
    keyword_answers: KeywordAnswers
    slices: tuple[Slice, ...]
    repeats: tuple[Repeat, ...]
    external: ExternalResult


def _point(rate: Rate) -> float:
    return rate.point if rate.point is not None else float("nan")


def repeat_seeds(seed: int, repeats: int) -> tuple[int, ...]:
    """The split seeds of the repeats: the ones after ``seed``, never ``seed`` itself."""
    return tuple(seed + 1 + r for r in range(repeats))


def _repeat(examples: Sequence[Example], *, alpha: float, seed: int) -> Repeat:
    trained = train_router(examples, alpha=alpha, seed=seed)
    test = [examples[i] for i in trained.splits.test]
    scored = score(trained.router, test)
    learned = classification(scored.truth, scored.learned)
    conformal = abstention(scored.truth, scored.sets)
    return Repeat(
        seed=seed,
        c=trained.c,
        q_hat=trained.router.q_hat,
        learned_accuracy=_point(learned.accuracy),
        learned_macro_f1=learned.macro_f1,
        keyword_accuracy=_point(
            classification(scored.truth, [k for k, _ in scored.keyword]).accuracy
        ),
        set_coverage=_point(conformal.set_coverage),
        answered=_point(conformal.answered),
        answered_accuracy=_point(conformal.answered_accuracy),
    )


def _external(router: LearnedRouter, external: ExternalCheck) -> ExternalResult:
    scored = score(router, external.examples)
    rows = tuple(
        ExternalRow(e.text, e.intent, learned, members, keyword)
        for e, learned, members, (keyword, _) in zip(
            external.examples, scored.learned, scored.sets, scored.keyword, strict=True
        )
    )
    return ExternalResult(
        learned=classification(scored.truth, scored.learned),
        keyword=classification(scored.truth, [k for k, _ in scored.keyword]),
        abstention=abstention(scored.truth, scored.sets),
        keyword_answers=keyword_answers(scored.truth, scored.keyword),
        rows=rows,
    )


def evaluate(
    corpus: Corpus,
    external: ExternalCheck,
    *,
    alpha: float = ALPHA,
    seed: int = SEED,
    repeats: int = REPEATS,
) -> Evaluation:
    examples = corpus.examples
    trained = train_router(examples, alpha=alpha, seed=seed)
    test = [examples[i] for i in trained.splits.test]
    scored = score(trained.router, test)
    split_counts = {
        name: {t: sum(1 for i in idx if examples[i].intent == t) for t in INTENTS}
        for name, idx in (
            ("fit", trained.splits.fit),
            ("calibration", trained.splits.calibration),
            ("test", trained.splits.test),
        )
    }
    return Evaluation(
        alpha=alpha,
        seed=seed,
        corpus=corpus,
        external_sha256=external.sha256,
        trained=trained,
        split_counts=split_counts,
        learned=classification(scored.truth, scored.learned),
        keyword=classification(scored.truth, [k for k, _ in scored.keyword]),
        abstention=abstention(scored.truth, scored.sets),
        keyword_answers=keyword_answers(scored.truth, scored.keyword),
        slices=slices(test, scored),
        repeats=tuple(_repeat(examples, alpha=alpha, seed=s) for s in repeat_seeds(seed, repeats)),
        external=_external(trained.router, external),
    )
