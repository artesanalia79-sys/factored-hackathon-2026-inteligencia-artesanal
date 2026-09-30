"""Build the safe audit record for a completed interpretation call."""

from __future__ import annotations

from datetime import datetime

from bankagent.contracts.decisions import InterpretationResult
from bankagent.contracts.enums import ConversationState, StepKind, StepOutcome
from bankagent.contracts.llm import StructuredCompletion
from bankagent.contracts.records import ExecutionRecord


def interpretation_record(
    completion: StructuredCompletion[InterpretationResult],
    *,
    record_id: str,
    trace_id: str,
    turn_index: int,
    step_index: int,
    created_at: datetime,
    session_id: str | None = None,
) -> ExecutionRecord:
    """Copy only metadata and metered usage; never store prompt or output text."""
    return ExecutionRecord(
        record_id=record_id,
        trace_id=trace_id,
        session_id=session_id,
        turn_index=turn_index,
        step_index=step_index,
        step=StepKind.INTERPRET,
        state=ConversationState.UNDERSTAND,
        outcome=StepOutcome.SUCCESS,
        latency_ms=completion.latency_ms,
        tokens_in=completion.usage.tokens_in,
        tokens_out=completion.usage.tokens_out,
        cost_usd=completion.usage.cost_usd,
        model=completion.model,
        prompt_version=completion.output.prompt_version,
        created_at=created_at,
    )
