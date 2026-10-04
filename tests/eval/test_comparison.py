"""Replays preserve scorer evidence and exclude identities and non-dev material."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from bankagent.contracts.comparison import ComparisonBundle
from bankagent.contracts.enums import EvalSplit, SystemVariant
from bankagent.eval.bank import load_bank
from bankagent.eval.cases import case_set_sha256, load_cases
from bankagent.eval.cli import write_outputs
from bankagent.eval.comparison import build_comparison
from bankagent.eval.fake import Behavior, ScriptedFakeSystem
from bankagent.eval.runner import run_suite
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


def test_dialogue_is_redacted(runs: list[ScoredRun]) -> None:
    original = runs[0]
    turn = replace(original.trace.turns[0], user_text="Escribe a person@example.com")
    changed = replace(
        original, trace=replace(original.trace, turns=(turn, *original.trace.turns[1:]))
    )
    output = bundle([changed]).model_dump_json()
    assert "person@example.com" not in output
    assert "REDACTED" in output


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
