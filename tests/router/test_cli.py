"""``poe router`` / ``poe router-predict`` and the committed evaluation report (Task 18)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bankagent.contracts.decisions import RouterResult
from bankagent.eval.router import cli
from bankagent.router.corpus import (
    EXTERNAL_GROUP,
    ROOT,
    Example,
    ExternalCheck,
    load_corpus,
    load_external,
)

REPORT = ROOT / "docs" / "evidence" / "router_eval.md"


def _hash(report: str, label: str) -> str:
    match = re.search(rf"{label} sha256: `([0-9a-f]{{64}})`", report)
    assert match, f"no '{label} sha256' line in the report"
    return match.group(1)


def test_the_committed_report_matches_the_corpus() -> None:
    report = REPORT.read_text(encoding="utf-8")
    hint = "eval/router changed: run `uv run poe router` and commit docs/evidence/router_eval.md"
    assert _hash(report, "Corpus") == load_corpus().sha256, hint
    assert _hash(report, "External check") == load_external().sha256, hint


def test_train_writes_the_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "router_eval.md"
    code = cli.main(["train", "--repeats", "1", "--no-mlflow", "--report", str(report)])
    assert code == 0
    text = report.read_text(encoding="utf-8")
    assert _hash(text, "Corpus") == load_corpus().sha256
    for heading in ("## Test split", "## Abstention", "## Repeated grouped splits (1 more"):
        assert heading in text
    out = capsys.readouterr().out
    assert "external check: learned" in out
    assert "MLflow run" not in out


def test_train_logs_one_mlflow_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Set (and later restore) what log_run would otherwise add to the process environment.
    for name in (
        "MLFLOW_ALLOW_FILE_STORE",
        "MLFLOW_DISABLE_TELEMETRY",
        "MLFLOW_DISABLE_AGENT_HINT",
    ):
        monkeypatch.setenv(name, "true")
    uri = (tmp_path / "mlruns").as_uri()
    report = tmp_path / "router_eval.md"
    args = ["train", "--repeats", "0", "--report", str(report), "--tracking-uri", uri]
    assert cli.main(args) == 0

    import mlflow

    mlflow.set_tracking_uri(uri)
    runs = mlflow.search_runs(experiment_names=["router"], output_format="list")
    assert len(runs) == 1
    run = runs[0]
    assert run.data.params["alpha"] == "0.1"
    assert run.data.tags["corpus_sha256"] == load_corpus().sha256
    for metric in ("test_set_coverage", "test_learned_accuracy", "artifact_bytes", "load_ms"):
        assert metric in run.data.metrics
    artifacts = {a.path for a in mlflow.MlflowClient().list_artifacts(run.info.run_id)}
    assert artifacts == {"router.pkl", "router_eval.md"}


def test_predict_prints_a_router_result(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["predict", "Me cobraron dos veces la misma compra en el súper"]) == 0
    out = capsys.readouterr().out
    payload, keyword_line = out.rsplit("keyword router:", 1)
    result = RouterResult.model_validate_json(payload)
    assert result.abstain == (len(result.prediction_set) != 1)
    assert keyword_line.strip().startswith("dispute_duplicate")


def test_train_refuses_an_external_check_that_repeats_the_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = load_corpus().examples[0]
    leaked = ExternalCheck(
        (Example(first.text, first.intent, first.dialect, EXTERNAL_GROUP),), "0" * 64
    )
    monkeypatch.setattr(cli, "load_external", lambda: leaked)
    report = tmp_path / "router_eval.md"
    assert cli.main(["train", "--no-mlflow", "--report", str(report)]) == 1
    assert "repeats corpus messages" in capsys.readouterr().err
    assert not report.exists()
