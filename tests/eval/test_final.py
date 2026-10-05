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

from bankagent.contracts.comparison import ComparisonBundle
from bankagent.contracts.evaluation import EvalCase
from bankagent.contracts.llm import ChatMessage, LLMProvider, StructuredCompletion
from bankagent.eval import final, heldout
from bankagent.eval.cases import DEV_DIR
from bankagent.eval.gates import GATES_FILE
from bankagent.eval.runner import ProviderFactory, stub_provider_for
from bankagent.interpret.stub import StubFault, StubProvider

DEV_CASES = (
    "dev-normal-es-co-001",
    "dev-human-es-ar-001",
    "dev-injection-es-ar-001",
    "dev-recognized-es-mx-001",
)
REPEATS = 3  # eval/gates.yaml, heldout.repeats
RUNS = len(DEV_CASES) * REPEATS  # per system
FAKE_KEY = "test-key-not-real"
APPROVED: dict[str, Any] = {"provider": "openai", "approved_by": "santiago"}


def _sealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, split: str = "heldout"
) -> tuple[Path, Path]:
    """A folder of four cases with ``split: heldout``, its manifest, and HELDOUT_DIR set to it."""
    directory = tmp_path / "sealed"
    directory.mkdir()
    for index, name in enumerate(DEV_CASES, start=1):
        raw = yaml.safe_load((DEV_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
        raw.update(case_id=f"heldout-test-{index:03d}", split=split)
        body = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        (directory / f"{raw['case_id']}.yaml").write_bytes(body.encode("utf-8"))
    manifest = tmp_path / "manifest.sha256"
    _seal(directory, manifest)
    monkeypatch.setenv("HELDOUT_DIR", str(directory))
    return directory, manifest


def _seal(directory: Path, manifest: Path) -> None:
    manifest.write_bytes(("\n".join(heldout.manifest_lines(directory)) + "\n").encode("utf-8"))


def _unreadable(directory: Path, manifest: Path) -> None:
    """A sealed file that is no case at all: a command that reads the cases fails on it."""
    (directory / "heldout-test-001.yaml").write_bytes(b"not: [a case")
    _seal(directory, manifest)


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


def _record(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "run_record.json").read_text(encoding="utf-8"))


def _report(run_dir: Path) -> str:
    return (run_dir / "report.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Nothing runs without the conditions of the registered run
# ---------------------------------------------------------------------------


def test_without_approval_only_the_plan_is_printed_and_no_case_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    directory, manifest = _sealed(tmp_path, monkeypatch)
    _unreadable(directory, manifest)  # the plan is made from bytes only

    assert _run(tmp_path, directory, manifest, provider="openai") == final.NOT_RUN

    printed = capsys.readouterr()
    assert "heldout: 4 cases x 3 repeats = 12 runs per system" in printed.out
    assert "provider openai, model gpt-6-luna" in printed.out
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


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"repeats": 1}, "the held-out run uses the registered 3 repeats"),
        ({"system_names": ("proposed",)}, "the held-out run compares both systems"),
        ({"system_names": ("baseline",)}, "the held-out run compares both systems"),
        ({"budget_usd": Decimal("0.25")}, "the held-out run uses the registered cap"),
    ],
)
def test_the_heldout_run_keeps_the_registered_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    change: dict[str, Any],
    message: str,
) -> None:
    # Fewer repeats or one system alone would open the set for a result that cannot be compared
    # with the registered one, and the registered run would then be a re-run.
    directory, manifest = _sealed(tmp_path, monkeypatch)
    _unreadable(directory, manifest)
    code = _run(
        tmp_path, directory, manifest, **APPROVED, provider_factory=stub_provider_for, **change
    )
    assert code == final.NOT_RUN
    assert message in capsys.readouterr().err
    assert _written(tmp_path) == []


def test_a_missing_key_stops_the_run_before_any_case_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Before, the cases were read and the first case run crashed with a traceback.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    directory, manifest = _sealed(tmp_path, monkeypatch)
    _unreadable(directory, manifest)
    assert _run(tmp_path, directory, manifest, **APPROVED) == final.NOT_RUN
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err
    assert _written(tmp_path) == []


def test_a_model_other_than_the_registered_one_is_refused_on_the_heldout_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in {
        "LLM_BASE_URL": "https://llm.example.com/v1",
        "LLM_API_KEY": FAKE_KEY,
        "LLM_MODEL": "gpt-6-sol",
    }.items():
        monkeypatch.setenv(key, value)
    directory, manifest = _sealed(tmp_path, monkeypatch)
    _unreadable(directory, manifest)
    code = _run(tmp_path, directory, manifest, provider="compat", approved_by="santiago")
    assert code == final.NOT_RUN
    assert "the registered model is gpt-6-luna" in capsys.readouterr().err
    assert _written(tmp_path) == []
    # The same endpoint serving the registered model passes this check and stops at approval.
    monkeypatch.setenv("LLM_MODEL", "gpt-6-luna")
    assert _run(tmp_path, directory, manifest, provider="compat") == final.NOT_RUN
    assert "provider compat, model gpt-6-luna" in capsys.readouterr().out


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

    assert _run(tmp_path, directory, manifest, **APPROVED, provider_factory=stub_provider_for) == 0

    (report_dir,) = (tmp_path / "reports").glob("*")
    assert report_dir.name.startswith("heldout-1-")
    report = _report(report_dir)
    assert "sealed held-out set (manifest sha256 " in report
    assert str(tmp_path) not in report  # a path on someone's machine is not provenance
    assert "\n\n## Run record\n\n- Held-out run 1, manifest sha256 `" in report
    assert "REHEARSAL" not in report
    assert "Re-run" not in report
    assert "Incomplete" not in report
    assert "model `gpt-6-luna`" in report
    assert "approved by santiago" in report
    results = (report_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(results) == RUNS * 2
    record = _record(report_dir)
    assert (record["kind"], record["status"], record["n_cases"]) == ("heldout", "complete", 4)
    assert record["repeats"] == REPEATS
    assert record["git_commit"]
    assert set(record["systems"]) == {"proposed", "baseline_llm_only"}
    for info in record["systems"].values():
        assert (info["runs"], info["expected_runs"]) == (RUNS, RUNS)
        assert not info["stopped_by_budget"]
        assert info["stopped_by_error"] is None
        assert info["crashed_runs"] == 0
    # The conversations are quoted only under the git-ignored runs folder.
    assert not list(report_dir.glob("transcripts*"))
    transcripts = (tmp_path / "runs" / report_dir.name / "transcripts.jsonl").read_text("utf-8")
    rows = [json.loads(line) for line in transcripts.splitlines()]
    assert len(rows) == RUNS * 2
    opening = (
        EvalCase.model_validate(
            yaml.safe_load((directory / "heldout-test-001.yaml").read_text(encoding="utf-8"))
        )
        .turns[0]
        .text
    )
    assert opening in transcripts
    assert opening not in report
    # How the scripted user read each question it answered, for the human check of a sample.
    first = next(
        r for r in rows if r["case_id"] == "heldout-test-001" and r["system"] == "proposed"
    )
    assert [t["question_read_as"] for t in first["turns"]] == ["recognize", "confirm", None]

    # A second run is numbered and says it does not replace the first.
    assert _run(tmp_path, directory, manifest, **APPROVED, provider_factory=stub_provider_for) == 0
    second = next(p for p in (tmp_path / "reports").glob("*") if p.name.startswith("heldout-2-"))
    assert "- Held-out run 2" in _report(second)
    assert "**Re-run.** The set was already opened 1 time(s)" in _report(second)


@pytest.mark.parametrize("split", ["heldout", "dev"])
def test_no_file_of_the_report_folder_quotes_a_sealed_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, split: str
) -> None:
    # The replay of T22 quotes every conversation, and the report folder of the sealed set is
    # committed. `poe heldout seal` refuses a case that calls itself a dev case, but this
    # command knows the set by its manifest: a label inside a file must not decide it.
    directory, manifest = _sealed(tmp_path, monkeypatch, split=split)

    assert _run(tmp_path, directory, manifest, **APPROVED, provider_factory=stub_provider_for) == 0

    (report_dir,) = (tmp_path / "reports").glob("*")
    assert report_dir.name.startswith("heldout-1-")
    assert sorted(path.name for path in report_dir.iterdir()) == [
        "report.md",
        "results.jsonl",
        "run_record.json",
        "unsafe_reasons.jsonl",
    ]
    openings = [
        yaml.safe_load(path.read_text(encoding="utf-8"))["turns"][0]["text"]
        for path in directory.glob("*.yaml")
    ]
    for written in report_dir.iterdir():
        text = written.read_text(encoding="utf-8")
        assert not any(opening in text for opening in openings), written.name
    assert not list((tmp_path / "runs").rglob("comparison.json"))


def test_the_code_is_read_before_the_run_writes_into_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The new report folder is untracked: read after it was written, a clean checkout was
    # recorded "with uncommitted changes" on every held-out run.
    directory, manifest = _sealed(tmp_path, monkeypatch)
    reports = tmp_path / "reports"

    def git(*args: str) -> str:
        if args[0] == "status":
            return "?? eval/reports/" if reports.exists() else ""
        return "abc123"

    monkeypatch.setattr(final, "_git", git)
    assert _run(tmp_path, directory, manifest, **APPROVED, provider_factory=stub_provider_for) == 0
    (report_dir,) = reports.glob("*")
    assert (_record(report_dir)["git_commit"], _record(report_dir)["git_dirty"]) == (
        "abc123",
        False,
    )
    assert "- Code: commit `abc123`.\n" in _report(report_dir)


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
    assert "- **REHEARSAL**: not the held-out set, not a result." in _report(run_dir)
    record = _record(run_dir)
    assert record["kind"] == "rehearsal"
    assert record["heldout_run_number"] is None
    assert record["systems"]["proposed"]["runs"] == len(list(DEV_DIR.glob("*.yaml")))
    assert record["systems"]["proposed"]["llm_fallback_steps"] == 0
    # A rehearsal on the dev cases gets its replay, under the git-ignored runs folder.
    replay = ComparisonBundle.model_validate_json(
        (run_dir / "comparison.json").read_text(encoding="utf-8")
    )
    assert not replay.simulated
    assert {run.result.case_id for run in replay.runs} == {
        path.stem for path in DEV_DIR.glob("*.yaml")
    }


def test_a_rehearsal_may_lower_the_cap_but_runs_at_least_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    none = tmp_path / "none.sha256"
    low = Decimal("0.25")
    assert _run(tmp_path, DEV_DIR, none, provider="openai", budget_usd=low) == final.NOT_RUN
    assert "hard cap: 0.25 USD per system" in capsys.readouterr().out  # the plan, not a run
    over = Decimal("5.01")
    assert _run(tmp_path, DEV_DIR, none, provider="openai", budget_usd=over) == final.NOT_RUN
    assert "at most 5.0 USD" in capsys.readouterr().err
    assert _run(tmp_path, DEV_DIR, none, provider="stub", repeats=0) == final.NOT_RUN
    assert "--repeats must be at least 1" in capsys.readouterr().err
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
        tmp_path, directory, manifest, **APPROVED, provider_factory=lambda _case: _CostlyStub()
    )
    assert code == 1
    printed = capsys.readouterr().err
    assert "proposed stopped by its budget" in printed
    assert "INCOMPLETE: proposed" in printed
    (report_dir,) = (tmp_path / "reports").glob("*")
    record = _record(report_dir)
    assert record["status"] == "incomplete"
    info = record["systems"]["proposed"]
    assert info["stopped_by_budget"]
    assert 0 < info["runs"] < info["expected_runs"]
    assert Decimal(info["cost_usd"]) > Decimal("5")
    report = _report(report_dir)
    assert "- **Incomplete**: a system ran fewer runs than planned" in report
    assert f"| proposed | {info['runs']}/{RUNS} | yes | no |" in report
    # One system's budget does not stop the other: the baseline has its own (and the keyword
    # stub cannot answer its requests, so it spends nothing here).
    baseline = record["systems"]["baseline_llm_only"]
    assert (baseline["runs"], baseline["stopped_by_budget"]) == (RUNS, False)
    assert "INCOMPLETE: proposed ran" in printed


def _failing_after(finished: int) -> ProviderFactory:
    """A provider factory that serves ``finished`` case runs, fails once like a harness bug,
    then works again: whatever runs after the failure shows in the record."""
    calls: list[EvalCase] = []

    def factory(case: EvalCase) -> LLMProvider:
        calls.append(case)
        if len(calls) == finished + 1:
            raise RuntimeError("harness bug")
        return stub_provider_for(case)

    return factory


@pytest.mark.parametrize("finished", [0, 2])
def test_a_harness_error_stops_the_run_and_keeps_what_it_measured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    finished: int,
) -> None:
    # Before, the error escaped with a traceback: a paid run lost everything it had measured.
    directory, manifest = _sealed(tmp_path, monkeypatch)
    code = _run(
        tmp_path, directory, manifest, **APPROVED, provider_factory=_failing_after(finished)
    )
    assert code == 1
    assert "proposed stopped by an error: RuntimeError: harness bug" in capsys.readouterr().err
    (report_dir,) = (tmp_path / "reports").glob("*")
    record = _record(report_dir)
    assert record["status"] == "incomplete"
    assert record["systems"]["proposed"]["stopped_by_error"] == "RuntimeError"
    assert record["systems"]["proposed"]["runs"] == finished
    assert record["systems"]["baseline_llm_only"]["runs"] == 0  # nothing runs after an error
    assert f"| proposed | {finished}/{RUNS} | no | RuntimeError |" in _report(report_dir)
    transcripts = tmp_path / "runs" / report_dir.name / "transcripts.jsonl"
    assert len(transcripts.read_text("utf-8").splitlines()) == finished
    if finished:
        results = (report_dir / "results.jsonl").read_text("utf-8").splitlines()
        assert len(results) == finished
    else:
        assert "No run finished: there is nothing to score." in _report(report_dir)


def test_ctrl_c_stops_the_run_and_keeps_what_it_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The owner may stop a paid run that goes wrong; it is still an opening of the set.
    directory, manifest = _sealed(tmp_path, monkeypatch)
    served: list[EvalCase] = []

    def interrupted_after_two(case: EvalCase) -> LLMProvider:
        served.append(case)
        if len(served) == 3:
            raise KeyboardInterrupt
        return stub_provider_for(case)

    code = _run(tmp_path, directory, manifest, **APPROVED, provider_factory=interrupted_after_two)
    assert code == 1
    (report_dir,) = (tmp_path / "reports").glob("*")
    record = _record(report_dir)
    assert record["status"] == "incomplete"
    assert record["systems"]["proposed"]["stopped_by_error"] == "KeyboardInterrupt"
    assert record["systems"]["proposed"]["runs"] == 2


def test_a_run_that_dies_still_counts_as_an_opening_of_the_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, manifest = _sealed(tmp_path, monkeypatch)

    def dies(_case: EvalCase) -> LLMProvider:
        raise SystemExit(1)  # in-process stand-in for a killed process or a lost machine

    with pytest.raises(SystemExit):
        _run(tmp_path, directory, manifest, **APPROVED, provider_factory=dies)
    (first,) = (tmp_path / "reports").glob("*")
    assert first.name.startswith("heldout-1-")
    assert _record(first)["status"] == "running"

    assert _run(tmp_path, directory, manifest, **APPROVED, provider_factory=stub_provider_for) == 0
    second = next(p for p in (tmp_path / "reports").glob("*") if p.name.startswith("heldout-2-"))
    assert "**Re-run.** The set was already opened 1 time(s)" in _report(second)


def test_calls_the_model_did_not_answer_are_flagged_after_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The proposed agent goes on with keyword rules when the model fails, so a network problem
    # looks like a result unless the run says how often it happened.
    directory, manifest = _sealed(tmp_path, monkeypatch)

    def unavailable(_case: EvalCase) -> LLMProvider:
        return StubProvider(always_fault=StubFault.UNAVAILABLE)

    assert _run(tmp_path, directory, manifest, **APPROVED, provider_factory=unavailable) == 0
    (report_dir,) = (tmp_path / "reports").glob("*")
    fallbacks = _record(report_dir)["systems"]["proposed"]["llm_fallback_steps"]
    assert fallbacks > 0
    assert f"CHECK: proposed had 0 crashed runs and {fallbacks} calls" in capsys.readouterr().err


def test_a_scorer_error_keeps_the_conversations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, manifest = _sealed(tmp_path, monkeypatch)

    def broken(*_args: object, **_kwargs: object) -> list[object]:
        raise RuntimeError("scorer bug")

    monkeypatch.setattr(final, "write_outputs", broken)
    with pytest.raises(RuntimeError, match="scorer bug"):
        _run(tmp_path, directory, manifest, **APPROVED, provider_factory=stub_provider_for)
    (report_dir,) = (tmp_path / "reports").glob("*")
    assert _record(report_dir)["status"] == "running"  # the set still counts as opened
    transcripts = tmp_path / "runs" / report_dir.name / "transcripts.jsonl"
    assert len(transcripts.read_text("utf-8").splitlines()) == RUNS * 2
