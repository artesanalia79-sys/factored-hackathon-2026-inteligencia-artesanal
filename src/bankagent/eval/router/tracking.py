"""Serving footprint of the router and one MLflow run per evaluation (Task 18).

Runs go to the local file store under ``mlruns/`` (gitignored and refused by the pre-commit
hook), as ``docs/rules/eval.md`` asks. MLflow 3.16 keeps that store in maintenance mode and
refuses it unless ``MLFLOW_ALLOW_FILE_STORE`` is set, so this module opts in instead of adding a
database backend. MLflow's usage telemetry is switched off: a local run sends nothing out.
"""

from __future__ import annotations

import os
import pickle
import statistics
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from bankagent.eval.metrics import Rate, nearest_rank
from bankagent.eval.router.evaluate import C_GRID, Evaluation
from bankagent.router.corpus import ROOT
from bankagent.router.model import MIN_DF, NGRAM_RANGE, LearnedRouter

EXPERIMENT = "router"
DEFAULT_TRACKING_URI = (ROOT / "mlruns").as_uri()


@dataclass(frozen=True, slots=True)
class Footprint:
    """What serving the router would cost; measured on this machine, never committed."""

    artifact_bytes: int
    load_ms: float
    route_p50_ms: float
    route_p95_ms: float


def serialize(router: LearnedRouter) -> bytes:
    return pickle.dumps(router, protocol=pickle.HIGHEST_PROTOCOL)


def measure_footprint(router: LearnedRouter, texts: Sequence[str]) -> Footprint:
    """Pickled size, time to load it back, and per-message latency of ``route``."""
    blob = serialize(router)
    start = time.perf_counter()
    pickle.loads(blob)  # noqa: S301 - bytes this process produced a line above, never a file
    load_ms = (time.perf_counter() - start) * 1000
    latencies: list[float] = []
    for text in texts:
        start = time.perf_counter()
        router.route(text)
        latencies.append((time.perf_counter() - start) * 1000)
    return Footprint(
        artifact_bytes=len(blob),
        load_ms=load_ms,
        route_p50_ms=nearest_rank(latencies, 0.50) or 0.0,
        route_p95_ms=nearest_rank(latencies, 0.95) or 0.0,
    )


def _rate(rate: Rate) -> float:
    return rate.point if rate.point is not None else float("nan")


def metrics(evaluation: Evaluation, footprint: Footprint) -> dict[str, float]:
    ev = evaluation
    out = {
        "q_hat": ev.trained.router.q_hat,
        "test_learned_accuracy": _rate(ev.learned.accuracy),
        "test_learned_macro_f1": ev.learned.macro_f1,
        "test_keyword_accuracy": _rate(ev.keyword.accuracy),
        "test_keyword_macro_f1": ev.keyword.macro_f1,
        "test_set_coverage": _rate(ev.abstention.set_coverage),
        "test_answered": _rate(ev.abstention.answered),
        "test_answered_accuracy": _rate(ev.abstention.answered_accuracy),
        "test_mean_set_size": ev.abstention.mean_set_size,
        "external_learned_accuracy": _rate(ev.external.learned.accuracy),
        "external_keyword_accuracy": _rate(ev.external.keyword.accuracy),
        "external_set_coverage": _rate(ev.external.abstention.set_coverage),
        "artifact_bytes": float(footprint.artifact_bytes),
        "load_ms": footprint.load_ms,
        "route_p50_ms": footprint.route_p50_ms,
        "route_p95_ms": footprint.route_p95_ms,
    }
    if ev.repeats:
        coverage = [r.set_coverage for r in ev.repeats]
        out |= {
            "repeats_mean_set_coverage": statistics.fmean(coverage),
            "repeats_min_set_coverage": min(coverage),
            "repeats_mean_learned_accuracy": statistics.fmean(
                r.learned_accuracy for r in ev.repeats
            ),
            "repeats_mean_keyword_accuracy": statistics.fmean(
                r.keyword_accuracy for r in ev.repeats
            ),
        }
    return out


def log_run(
    evaluation: Evaluation,
    report: Path,
    footprint: Footprint,
    tracking_uri: str = DEFAULT_TRACKING_URI,
) -> str:
    """Log params, metrics, the report and the pickled router as one run; return its id."""
    if tracking_uri.startswith("file:"):
        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    os.environ.setdefault("MLFLOW_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "true")
    import mlflow  # after the variables above, which MLflow reads when it loads

    ev = evaluation
    router = ev.trained.router
    splits = ev.trained.splits
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=router.version) as run:
        mlflow.set_tags(
            {
                "task": "T18",
                "corpus_sha256": ev.corpus.sha256,
                "external_sha256": ev.external_sha256,
            }
        )
        mlflow.log_params(
            {
                "model_version": router.version,
                "alpha": ev.alpha,
                "seed": ev.seed,
                "c": ev.trained.c,
                "c_grid": ",".join(f"{c:g}" for c in C_GRID),
                "ngram_range": f"{NGRAM_RANGE[0]}-{NGRAM_RANGE[1]}",
                "min_df": MIN_DF,
                "n_fit": len(splits.fit),
                "n_calibration": len(splits.calibration),
                "n_test": len(splits.test),
                "repeats": len(ev.repeats),
            }
        )
        mlflow.log_metrics(metrics(ev, footprint))
        mlflow.log_artifact(str(report))
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "router.pkl"
            artifact.write_bytes(serialize(router))
            mlflow.log_artifact(str(artifact))
    return run.info.run_id
