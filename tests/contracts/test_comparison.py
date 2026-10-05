"""Replay contracts retain the audit trail's verification invariant."""

from typing import Any

import pytest
from pydantic import ValidationError

from bankagent.contracts.comparison import ComparisonBundle, ComparisonStep
from bankagent.contracts.enums import (
    ConversationState,
    Outcome,
    StepKind,
    StepOutcome,
    SystemVariant,
)
from bankagent.contracts.evaluation import EvalResult


def test_failed_step_cannot_be_presented_as_verified() -> None:
    with pytest.raises(ValidationError, match="successful"):
        ComparisonStep(
            step=StepKind.RENDER,
            state=ConversationState.RESPOND,
            tool=None,
            outcome=StepOutcome.FAILURE,
            verified=True,
            rule_ids=(),
            model=None,
        )


def _bundle() -> dict[str, Any]:
    """The smallest valid replay: one run of one turn."""
    result = EvalResult(
        case_id="dev-normal-es-mx-001",
        system=SystemVariant.PROPOSED,
        run_id="suite-proposed-r0",
        repeat_index=0,
        final_outcome=Outcome.ABSTAINED,
        escalated=False,
        turns_used=1,
        correct=False,
        safe_automated_resolution=False,
        automation_attempted=False,
    )
    turn = {"user_text": "Hola", "reply_text": "Hola", "steps": []}
    return {
        "schema_version": 1,
        "suite_id": "suite",
        "case_set_sha256": "a" * 64,
        "simulated": True,
        "cost_assumptions": "0 USD",
        "runs": [
            {"system_name": "proposed", "result": result.model_dump(mode="json"), "turns": [turn]}
        ],
    }


def test_the_smallest_replay_is_valid() -> None:
    assert len(ComparisonBundle.model_validate(_bundle()).runs) == 1


def test_a_run_must_quote_as_many_turns_as_it_was_scored_on() -> None:
    raw = _bundle()
    raw["runs"][0]["turns"] = []
    with pytest.raises(ValidationError, match="turn count must match"):
        ComparisonBundle.model_validate(raw)


def test_a_replay_with_no_run_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        ComparisonBundle.model_validate({**_bundle(), "runs": []})


@pytest.mark.parametrize("version", [0, 2, "1.0"])
def test_only_the_known_schema_version_is_read(version: object) -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        ComparisonBundle.model_validate({**_bundle(), "schema_version": version})


def test_the_same_case_repeat_and_system_twice_is_refused() -> None:
    raw = _bundle()
    raw["runs"] *= 2
    with pytest.raises(ValidationError, match="duplicate case/repeat/system"):
        ComparisonBundle.model_validate(raw)


@pytest.mark.parametrize("extra", ["customer_id", "session_id", "args_hash", "tool_args"])
def test_a_field_the_replay_must_never_carry_is_refused_everywhere(extra: str) -> None:
    # Identity and tool arguments are kept out by the shape itself, not by the exporter alone.
    places = [
        lambda raw: raw,
        lambda raw: raw["runs"][0],
        lambda raw: raw["runs"][0]["result"],
        lambda raw: raw["runs"][0]["turns"][0],
    ]
    for place in places:
        raw = _bundle()
        place(raw)[extra] = "x"
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ComparisonBundle.model_validate(raw)
