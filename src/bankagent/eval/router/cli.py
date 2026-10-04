"""``uv run poe router`` and ``uv run poe router-predict "<message>"`` (Task 18).

``train`` splits the corpus, chooses ``C``, fits, calibrates, scores both routers, repeats the
pipeline on more split seeds, writes ``docs/evidence/router_eval.md`` and logs an MLflow run.
``predict`` trains the same model (same corpus, seed and split) and routes one message. Nothing
is saved between the two, so no pickle is ever loaded from disk. Costs nothing: no LLM call.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from bankagent.eval.metrics import Rate
from bankagent.eval.router.evaluate import (
    ALPHA,
    REPEATS,
    SEED,
    evaluate,
    keyword_intent,
    train_router,
)
from bankagent.eval.router.report import render_report
from bankagent.eval.router.tracking import DEFAULT_TRACKING_URI, log_run, measure_footprint
from bankagent.router.corpus import (
    ROOT,
    CorpusError,
    load_corpus,
    load_external,
    shared_messages,
)

DEFAULT_REPORT = ROOT / "docs" / "evidence" / "router_eval.md"


def _alpha(value: str) -> float:
    """``--alpha``: a number strictly between 0 and 1."""
    try:
        alpha = float(value)
    except ValueError:
        alpha = math.nan
    if not 0.0 < alpha < 1.0:
        raise argparse.ArgumentTypeError(f"alpha must be a number in (0, 1), got {value!r}")
    return alpha


def _repeats(value: str) -> int:
    """``--repeats``: a whole number, zero or more."""
    try:
        repeats = int(value)
    except ValueError:
        repeats = -1
    if repeats < 0:
        raise argparse.ArgumentTypeError(f"repeats must be a whole number >= 0, got {value!r}")
    return repeats


def _percent(rate: Rate) -> str:
    """A rate for the console: ``n/a`` when nothing was counted (a router that never answers)."""
    return "n/a" if rate.point is None else f"{rate.point:.1%}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bankagent.eval.router.cli",
        description="Train, evaluate and query the learned intent router (Task 18).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train", help="evaluate, write the report, log an MLflow run")
    train.add_argument("--alpha", type=_alpha, default=ALPHA)
    train.add_argument("--seed", type=int, default=SEED)
    train.add_argument("--repeats", type=_repeats, default=REPEATS, help="extra split seeds")
    train.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    train.add_argument("--tracking-uri", default=DEFAULT_TRACKING_URI)
    train.add_argument("--no-mlflow", action="store_true", help="skip the MLflow run")
    predict = commands.add_parser("predict", help="route one message")
    predict.add_argument("text")
    predict.add_argument("--alpha", type=_alpha, default=ALPHA)
    predict.add_argument("--seed", type=int, default=SEED)
    return parser


def _train(args: argparse.Namespace) -> int:
    corpus, external = load_corpus(), load_external()
    shared = shared_messages(corpus, external)
    if shared:
        print(f"the external check repeats corpus messages: {list(shared)}", file=sys.stderr)
        return 1
    evaluation = evaluate(corpus, external, alpha=args.alpha, seed=args.seed, repeats=args.repeats)
    report: Path = args.report
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(evaluation), encoding="utf-8")
    router = evaluation.trained.router
    test = [corpus.examples[i].text for i in evaluation.trained.splits.test]
    footprint = measure_footprint(router, test)
    conformal = evaluation.abstention
    print(f"corpus: {len(corpus.examples)} messages, sha256 {corpus.sha256[:12]}")
    print(
        f"split seed {args.seed}: C = {evaluation.trained.c:g}, q_hat = {router.q_hat:.4f}; "
        f"test accuracy learned {_percent(evaluation.learned.accuracy)}, "
        f"keyword {_percent(evaluation.keyword.accuracy)}"
    )
    print(
        f"abstention: set coverage {_percent(conformal.set_coverage)}, answered "
        f"{_percent(conformal.answered)} at {_percent(conformal.answered_accuracy)} accuracy"
    )
    if evaluation.repeats:
        coverage = [r.set_coverage for r in evaluation.repeats]
        print(
            f"{len(coverage)} more split seeds: set coverage mean "
            f"{sum(coverage) / len(coverage):.1%}, min {min(coverage):.1%}"
        )
    print(
        f"external check: learned {evaluation.external.learned.accuracy.successes}/"
        f"{evaluation.external.learned.accuracy.n}, keyword "
        f"{evaluation.external.keyword.accuracy.successes}/{evaluation.external.keyword.accuracy.n}"
    )
    print(
        f"footprint: {footprint.artifact_bytes / 1024:.0f} KiB pickled, load "
        f"{footprint.load_ms:.1f} ms, route p50 {footprint.route_p50_ms:.2f} ms, "
        f"p95 {footprint.route_p95_ms:.2f} ms"
    )
    print(f"wrote {report}")
    if not args.no_mlflow:
        run_id = log_run(evaluation, report, footprint, args.tracking_uri)
        print(f"MLflow run {run_id} (experiment 'router') in {args.tracking_uri}")
    return 0


def _predict(args: argparse.Namespace) -> int:
    trained = train_router(load_corpus().examples, alpha=args.alpha, seed=args.seed)
    result = trained.router.route(args.text)
    print(result.model_dump_json(indent=2))
    intent, confidence = keyword_intent(args.text)
    print(f"keyword router: {intent.value} (confidence {confidence:g})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _train(args) if args.command == "train" else _predict(args)
    except CorpusError as exc:
        print(f"corpus error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
