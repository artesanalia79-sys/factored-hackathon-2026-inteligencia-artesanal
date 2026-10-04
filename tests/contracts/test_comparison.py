"""Replay contracts retain the audit trail's verification invariant."""

import pytest
from pydantic import ValidationError

from bankagent.contracts.comparison import ComparisonStep
from bankagent.contracts.enums import ConversationState, StepKind, StepOutcome


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
