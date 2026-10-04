"""Final evaluation command (Task 27) on synthetic sealed sets in temporary folders.

No test reads ``HELDOUT_DIR`` or calls a real model: the "sealed" cases are dev cases renamed,
and the paid provider is replaced by the stub through ``provider_factory``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from bankagent.contracts.evaluation import EvalCase
from bankagent.contracts.llm import ChatMessage, StructuredCompletion
from bankagent.eval import final, heldout
from bankagent.eval.cases import DEV_DIR
from bankagent.eval.gates import GATES_FILE
from bankagent.eval.runner import stub_provider_for
from bankagent.interpret.stub import StubProvider

DEV_CASES = (
    "dev-normal-es-co-001",
    "dev-human-es-ar-001",
    "dev-injection-es-ar-001",
    "dev-recognized-es-mx-001",
)


def _sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A folder of four cases with ``split: heldout``, its manifest, and HELDOUT_DIR set to it."""
    directory = tmp_path / "sealed"
    directory.mkdir()
    for index, name in enumerate(DEV_CASES, start=1):
        raw = yaml.safe_load((DEV_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
        raw.update(case_id=f"heldout-test-{index:03d}", split="heldout")
        body = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        (directory / f"{raw['case_id']}.yaml").write_bytes(body.encode("utf-8"))
    manifest = tmp_path / "manifest.sha256"
    manifest.write_bytes(("\n".join(heldout.manifest_lines(directory)) + "\n").encode("utf-8"))
    monkeypatch.setenv("HELDOUT_DIR", str(directory))
    return directory, manifest


def _run(tmp_path: Path, cases_dir: Path, manifest: Path, **kwargs: Any) -> int:
    return final.run_final(
        cases_dir=cases_dir,
        manifest=manifest,
        reports_root=tmp_path / "reports",
        runs_root=tmp_path / "runs",
        **kwargs,
    )


def _written(tmp_path: Path) -> list[Path]:
    return [*(tmp_path / "reports").glob("*"), *(tmp_path / "runs").glob("*")]


# ---------------------------------------------------------------------------
# Nothing runs without the conditions of the registered run
# ---------------------------------------------------------------------------


def test_without_approval_only_the_plan_is_printed_and_no_case_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, manifest = _sealed(tmp_path, monkeypatch)
    # A file that is no case at all: reading it would fail, so the plan is made from bytes only.
    (directory / "heldout-test-001.yaml").write_bytes(b"not: [a case")
    manifest.write_bytes(("\n".join(heldout.manifest_lines(directory)) + "\n").encode("utf-8"))

    assert _run(tmp_path, directory, manifest, provider="openai") == final.NOT_RUN

    printed = capsys.readouterr()
    assert "heldout: 4 cases x 3 repeats = 12 runs per system" in printed.out
    assert "hard cap: 5.0 USD per system" in printed.out
    assert "at the cost bar of 0.008 USD per run: 0.096 USD per system" in printed.out
    assert "--approved-by" in printed.err
    assert _written(tmp_path) == []


def test_the_stub_never_opens_the_heldout_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, manifest = _sealed(tmp_path, monkeypatch)
    assert _run(tmp_path, directory, manifest, provider="stub") == final.NOT_RUN
    assert "rehearse on the dev cases" in capsys.readouterr().err
    assert _written(tmp_path) == []


def test_a_file_changed_after_the_seal_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, manifest = _sealed(tmp_path, monkeypatch)
    changed = directory / "heldout-test-002.yaml"
    changed.write_bytes(changed.read_bytes() + b"\n")
    code = _run(tmp_path, directory, manifest, provider="openai", approved_by="santiago")
    assert code == final.NOT_RUN
    assert "heldout-test-002.yaml: content differs from the manifest" in capsys.readouterr().err
    assert _written(tmp_path) == []


def test_an_unsealed_set_and_draft_gates_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, manifest = _sealed(tmp_path, monkeypatch)
    missing = tmp_path / "none.sha256"
    assert _run(tmp_path, directory, missing, provider="openai", approved_by="x") == final.NOT_RUN
    assert "seal the set first" in capsys.readouterr().err

    draft = yaml.safe_load(GATES_FILE.read_text(encoding="utf-8"))
    draft.update(status="draft", frozen_at=None)
    gates_file = tmp_path / "gates.yaml"
    gates_file.write_text(yaml.safe_dump(draft), encoding="utf-8")
    code = _run(
        tmp_path, directory, manifest, provider="openai", approved_by="x", gates_file=gates_file
    )
    assert code == final.NOT_RUN
    assert "not frozen" in capsys.readouterr().err
    assert _written(tmp_path) == []


def test_the_sealed_set_is_recognized_by_its_manifest_whatever_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, manifest = _sealed(tmp_path, monkeypatch)
    monkeypatch.delenv("HELDOUT_DIR")  # the folder is "sealed": no "heldout" in its path
    # With the manifest it is the held-out set, so the stub is refused ...
    assert _run(tmp_path, directory, manifest, provider="stub") == final.NOT_RUN
    assert "rehearse on the dev cases" in capsys.readouterr().err
    # ... and without one, held-out cases are not run as a rehearsal either.
    assert _run(tmp_path, directory, tmp_path / "none.sha256", provider="stub") == final.NOT_RUN
    assert "these are held-out cases" in capsys.readouterr().err
    assert _written(tmp_path) == []


def test_the_command_needs_a_cases_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HELDOUT_DIR", raising=False)
    assert final.main([]) == final.NOT_RUN
    assert final.main(["--cases", str(tmp_path / "absent")]) == final.NOT_RUN


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def test_the_heldout_run_writes_the_report_the_record_and_the_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, manifest = _sealed(tmp_path, monkeypatch)
    run = {
        "provider": "openai",
        "approved_by": "santiago",
        "repeats": 1,
        "provider_factory": stub_provider_for,
    }

    assert _run(tmp_path, directory, manifest, **run) == 0

    (report_dir,) = (tmp_path / "reports").glob("*")
    assert report_dir.name.startswith("heldout-1-")
    report = (report_dir / "report.md").read_text(encoding="utf-8")
    assert "sealed held-out set (manifest sha256 " in report
    assert str(tmp_path) not in report  # a path on someone's machine is not provenance
    assert "- Held-out run 1, manifest sha256 `" in report
    assert "REHEARSAL" not in report
    assert "Re-run" not in report
    assert "approved by santiago" in report
    results = (report_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(results) == len(DEV_CASES) * 2
    record = json.loads((report_dir / "run_record.json").read_text(encoding="utf-8"))
    assert record["kind"] == "heldout"
    assert record["n_cases"] == len(DEV_CASES)
    assert set(record["systems"]) == {"proposed", "baseline_llm_only"}
    for info in record["systems"].values():
        assert (info["runs"], info["expected_runs"]) == (4, 4)
        assert not info["stopped_by_budget"]
        assert info["crashed_runs"] == 0
    # The conversations are quoted only under the git-ignored runs folder.
    assert not list(report_dir.glob("transcripts*"))
    transcripts = (tmp_path / "runs" / report_dir.name / "transcripts.jsonl").read_text("utf-8")
    assert len(transcripts.splitlines()) == len(DEV_CASES) * 2
    opening = (
        EvalCase.model_validate(
            yaml.safe_load((directory / "heldout-test-001.yaml").read_text(encoding="utf-8"))
        )
        .turns[0]
        .text
    )
    assert opening in transcripts
    assert opening not in report

    # A second run is numbered and says it does not replace the first.
    assert _run(tmp_path, directory, manifest, **run) == 0
    second = next(p for p in (tmp_path / "reports").glob("*") if p.name.startswith("heldout-2-"))
    assert "- Held-out run 2" in (second / "report.md").read_text(encoding="utf-8")
    assert "**Re-run.** The set was already opened 1 time(s)" in (second / "report.md").read_text(
        encoding="utf-8"
    )


def test_a_rehearsal_on_the_dev_cases_stays_out_of_the_reports(tmp_path: Path) -> None:
    code = _run(
        tmp_path,
        DEV_DIR,
        tmp_path / "none.sha256",
        provider="stub",
        repeats=1,
        system_names=("proposed",),
    )
    assert code == 0
    assert not (tmp_path / "reports").exists()
    (run_dir,) = (tmp_path / "runs").glob("*")
    assert run_dir.name.startswith("rehearsal-")
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "- **REHEARSAL**: not the held-out set, not a result." in report
    record = json.loads((run_dir / "run_record.json").read_text(encoding="utf-8"))
    assert record["kind"] == "rehearsal"
    assert record["heldout_run_number"] is None
    assert record["systems"]["proposed"]["runs"] == len(list(DEV_DIR.glob("*.yaml")))
    assert record["systems"]["proposed"]["llm_fallback_steps"] == 0


def test_a_rehearsal_may_lower_the_cap_and_the_heldout_run_may_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    none = tmp_path / "none.sha256"
    low = Decimal("0.25")
    assert _run(tmp_path, DEV_DIR, none, provider="openai", budget_usd=low) == final.NOT_RUN
    assert "hard cap: 0.25 USD per system" in capsys.readouterr().out  # the plan, not a run
    over = Decimal("5.01")
    assert _run(tmp_path, DEV_DIR, none, provider="openai", budget_usd=over) == final.NOT_RUN
    assert "at most 5.0 USD" in capsys.readouterr().err
    directory, manifest = _sealed(tmp_path, monkeypatch)
    code = _run(tmp_path, directory, manifest, provider="openai", approved_by="x", budget_usd=low)
    assert code == final.NOT_RUN
    assert "the held-out run uses the registered cap" in capsys.readouterr().err
    assert _written(tmp_path) == []


class _CostlyStub:
    """The stub, but every call costs 3 USD: the second call passes the 5 USD cap of a system."""

    name = "costly-stub"
    model = "costly-stub"

    def __init__(self) -> None:
        self._stub = StubProvider()

    def complete_structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        timeout_s: float,
    ) -> StructuredCompletion[T]:
        done = self._stub.complete_structured(
            system=system, messages=messages, response_model=response_model, timeout_s=timeout_s
        )
        usage = done.usage.model_copy(update={"cost_usd": Decimal("3")})
        return done.model_copy(update={"usage": usage})


def test_a_system_stopped_by_its_budget_keeps_what_it_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory, manifest = _sealed(tmp_path, monkeypatch)
    code = _run(
        tmp_path,
        directory,
        manifest,
        provider="openai",
        approved_by="santiago",
        repeats=1,
        system_names=("proposed",),
        provider_factory=lambda _case: _CostlyStub(),
    )
    assert code == 1
    printed = capsys.readouterr().err
    assert "proposed stopped by its budget" in printed
    assert "INCOMPLETE: proposed ran fewer runs than planned" in printed
    (report_dir,) = (tmp_path / "reports").glob("*")
    record = json.loads((report_dir / "run_record.json").read_text(encoding="utf-8"))
    info = record["systems"]["proposed"]
    assert info["stopped_by_budget"]
    assert 0 < info["runs"] < info["expected_runs"]
    assert Decimal(info["cost_usd"]) > Decimal("5")
    assert f"| proposed | {info['runs']}/4 | yes |" in (report_dir / "report.md").read_text(
        encoding="utf-8"
    )
