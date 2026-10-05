"""Replays preserve scorer evidence and exclude identities and non-dev material."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from bankagent.contracts.comparison import ComparisonBundle, ComparisonStep
from bankagent.contracts.enums import EvalSplit, SystemVariant
from bankagent.eval.bank import load_bank
from bankagent.eval.cases import case_set_sha256, load_cases
from bankagent.eval.cli import write_outputs
from bankagent.eval.comparison import build_comparison
from bankagent.eval.fake import Behavior, ScriptedFakeSystem
from bankagent.eval.runner import CaseTrace, run_suite
from bankagent.eval.scorer import ScoredRun, score


@pytest.fixture
def runs() -> list[ScoredRun]:
    cases = load_cases()[:1]
    bank = load_bank()
    systems = [
        ScriptedFakeSystem(variant=variant, behavior=behavior, cases=cases, bank=bank)
        for variant, behavior in (
            (SystemVariant.PROPOSED, Behavior.IDEAL),
            (SystemVariant.BASELINE_LLM_ONLY, Behavior.NAIVE),
        )
    ]
    return [
        score(trace, bank)
        for trace in run_suite(
            systems, cases, suite_id="comparison", repeats=2, budget_usd_per_system=Decimal("0")
        )
    ]


def bundle(runs: list[ScoredRun]) -> ComparisonBundle:
    return build_comparison(
        runs,
        suite_id="comparison",
        case_set_sha256=case_set_sha256(load_cases()[:1]),
        simulated=True,
        cost_assumptions="StubProvider, 0 USD",
    )


def test_preserves_scores_and_step_evidence_without_identity(runs: list[ScoredRun]) -> None:
    output = bundle(runs)
    assert len(output.runs) == 4
    assert ComparisonBundle.model_validate_json(output.model_dump_json()) == output
    for original, exported in zip(runs, output.runs, strict=True):
        assert exported.result == original.result
        assert len(exported.turns) == len(original.trace.turns)
        for source, target in zip(original.trace.turns, exported.turns, strict=True):
            assert [step.verified for step in target.steps] == [r.verified for r in source.records]
        assert original.trace.session.customer_id not in output.model_dump_json()
        assert original.trace.session.session_id not in output.model_dump_json()
    assert '"args_hash"' not in output.model_dump_json()


@pytest.mark.parametrize("field", ["user_text", "reply_text"])
def test_dialogue_is_redacted(runs: list[ScoredRun], field: str) -> None:
    # Both sides: a leak is what the naive agent's reply is expected to hold.
    original = runs[0]
    turn = replace(original.trace.turns[0], **{field: "Escribe a person@example.com"})
    changed = replace(
        original, trace=replace(original.trace, turns=(turn, *original.trace.turns[1:]))
    )
    exported = bundle([changed]).runs[0].turns[0]
    assert getattr(exported, field) == "Escribe a [REDACTED:email]"


def test_every_step_keeps_what_the_record_says_and_nothing_else(runs: list[ScoredRun]) -> None:
    output = bundle(runs)
    seen = 0
    for original, exported in zip(runs, output.runs, strict=True):
        assert exported.system_name == original.trace.system_name
        for source, target in zip(original.trace.turns, exported.turns, strict=True):
            assert [
                (s.step, s.state, s.tool, s.outcome, s.verified, s.rule_ids, s.model)
                for s in target.steps
            ] == [
                (r.step, r.state, r.tool, r.outcome, r.verified, r.rule_ids, r.model)
                for r in source.records
            ]
            seen += len(source.records)
    assert seen > 0
    steps = [step for run in output.runs for turn in run.turns for step in turn.steps]
    # The fakes exercise the fields a reviewer reads: tools, rule ids, verified steps, a model.
    assert {step.tool for step in steps} - {None}
    assert any(step.rule_ids for step in steps)
    assert any(step.verified for step in steps)
    assert {step.model for step in steps} - {None}
    assert set(ComparisonStep.model_fields) == {
        "step",
        "state",
        "tool",
        "outcome",
        "verified",
        "rule_ids",
        "model",
    }


@pytest.mark.parametrize("simulated", [True, False])
def test_the_provenance_of_the_suite_is_kept(runs: list[ScoredRun], simulated: bool) -> None:
    cases = load_cases()[:1]
    output = build_comparison(
        runs,
        suite_id="suite-7",
        case_set_sha256=case_set_sha256(cases),
        simulated=simulated,
        cost_assumptions="StubProvider, 0 USD",
    )
    assert (output.schema_version, output.suite_id, output.simulated) == (1, "suite-7", simulated)
    assert output.cost_assumptions == "StubProvider, 0 USD"
    assert output.case_set_sha256 == case_set_sha256(cases)


@pytest.mark.parametrize("split", [EvalSplit.HELDOUT, EvalSplit.PILOT, EvalSplit.REDTEAM])
def test_refuses_non_dev_cases(runs: list[ScoredRun], split: EvalSplit) -> None:
    original = runs[0]
    case = original.trace.case.model_copy(update={"split": split})
    changed = replace(original, trace=replace(original.trace, case=case))
    with pytest.raises(ValueError, match="dev cases only"):
        bundle([changed])


def test_duplicate_system_case_repeat_is_rejected(runs: list[ScoredRun]) -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        bundle([runs[0], runs[0]])


def _write(traces: list[CaseTrace], out_dir: Path, **options: Any) -> None:
    write_outputs(
        traces,
        load_bank(),
        out_dir=out_dir,
        suite_id="comparison",
        generated_at=datetime.now(UTC),
        cases_dir=out_dir,
        cases=[trace.case for trace in traces],
        repeats=1,
        cost_assumptions="StubProvider, 0 USD",
        **{"simulated": True, **options},
    )


@pytest.mark.parametrize("simulated", [True, False])
def test_the_written_replay_says_whether_the_suite_was_simulated(
    runs: list[ScoredRun], tmp_path: Path, simulated: bool
) -> None:
    _write([runs[0].trace], tmp_path, simulated=simulated)
    written = ComparisonBundle.model_validate_json(
        (tmp_path / "comparison.json").read_text(encoding="utf-8")
    )
    assert written.simulated is simulated
    assert (written.suite_id, written.cost_assumptions) == ("comparison", "StubProvider, 0 USD")


def test_a_suite_with_one_case_that_is_not_dev_gets_no_replay_and_keeps_its_report(
    runs: list[ScoredRun], tmp_path: Path
) -> None:
    dev, other = runs[0].trace, runs[1].trace
    sealed = replace(other, case=other.case.model_copy(update={"split": EvalSplit.HELDOUT}))
    (tmp_path / "comparison.json").write_text("an earlier replay", encoding="utf-8")
    _write([dev, sealed], tmp_path)
    assert not (tmp_path / "comparison.json").exists()
    assert (tmp_path / "report.md").exists()
    assert len((tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_a_caller_can_refuse_the_replay_whatever_the_cases_say(
    runs: list[ScoredRun], tmp_path: Path
) -> None:
    # The final evaluation does this for the sealed set (tests/eval/test_final.py).
    (tmp_path / "comparison.json").write_text("an earlier replay", encoding="utf-8")
    _write([runs[0].trace], tmp_path, replay=False)
    assert not (tmp_path / "comparison.json").exists()
    assert (tmp_path / "report.md").exists()


def test_a_replay_that_cannot_be_built_does_not_cost_the_report(
    runs: list[ScoredRun], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: Any, **__: Any) -> ComparisonBundle:
        raise ValueError("no replay")

    monkeypatch.setattr("bankagent.eval.cli.build_comparison", broken)
    (tmp_path / "comparison.json").write_text("an earlier replay", encoding="utf-8")
    with pytest.raises(ValueError, match="no replay"):
        _write([runs[0].trace], tmp_path)
    # The error is not hidden, and what the run measured is on disk before it.
    assert (tmp_path / "report.md").exists()
    assert len((tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert not (tmp_path / "comparison.json").exists()


def test_output_writer_removes_old_replay_when_reused_for_non_dev(
    runs: list[ScoredRun], tmp_path: Path
) -> None:
    original = runs[0].trace
    for split in (EvalSplit.DEV, EvalSplit.HELDOUT):
        # Only a synthetic dev case relabeled in memory; no sealed file is opened.
        case = original.case.model_copy(update={"split": split})
        write_outputs(
            [replace(original, case=case)],
            load_bank(),
            out_dir=tmp_path,
            suite_id="comparison",
            generated_at=datetime.now(UTC),
            cases_dir=tmp_path,
            cases=[case],
            repeats=1,
            simulated=True,
            cost_assumptions="StubProvider, 0 USD",
        )
        assert (tmp_path / "comparison.json").exists() == (split == EvalSplit.DEV)
