"""Run one tool call and build its ``ExecutionRecord``.

The orchestrator owns the turn and step numbering and stores the records; this module gives
it the record of a call with the fields the evaluation reads (``docs/eval/system_interface.md``):
the tool, the hash of the exact arguments, the real outcome and error code, the latency and,
for a write, whether its read-back verified it. Only metadata is copied: no argument, result or
error text ever reaches a record.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass

from bankagent.contracts.base import Contract, args_hash
from bankagent.contracts.enums import ConversationState, StepKind, StepOutcome, ToolErrorCode
from bankagent.contracts.errors import ToolError
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import Tool, ToolContext

_IDENTIFIER_MAX = 64  # max_length of `bankagent.contracts.base.Identifier`

# Refusals by design (the call was not allowed); every other error is a failure.
BLOCKING_ERRORS: frozenset[ToolErrorCode] = frozenset(
    {ToolErrorCode.NOT_FOUND, ToolErrorCode.UNAUTHORIZED, ToolErrorCode.CONFIRMATION_REQUIRED}
)


def outcome_for(code: ToolErrorCode) -> StepOutcome:
    return StepOutcome.BLOCKED if code in BLOCKING_ERRORS else StepOutcome.FAILURE


def record_id_for(trace_id: str, turn_index: int, step_index: int) -> str:
    """Readable id of a step; hashed when a long trace id would not fit an ``Identifier``."""
    readable = f"{trace_id}-t{turn_index}-s{step_index}"
    if len(readable) <= _IDENTIFIER_MAX:
        return readable
    return f"rec-{hashlib.sha256(readable.encode('utf-8')).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class ToolCall[ResultT: Contract]:
    """A finished tool call: its record and either the result or the typed error."""

    record: ExecutionRecord
    result: ResultT | None = None
    error: ToolError | None = None

    def unwrap(self) -> ResultT:
        """The result, or the tool error re-raised."""
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise RuntimeError("a tool call has neither a result nor an error")
        return self.result


def call_tool[ArgsT: Contract, ResultT: Contract](
    tool: Tool[ArgsT, ResultT],
    ctx: ToolContext,
    args: ArgsT,
    *,
    turn_index: int,
    step_index: int,
    state: ConversationState,
    timer: Callable[[], float] = time.perf_counter,
) -> ToolCall[ResultT]:
    """Run ``tool`` and return its outcome with the record of the step.

    A ``ToolError`` is returned, not raised, so the caller always gets a record; any other
    exception is a bug and propagates.
    """
    started = timer()
    result: ResultT | None = None
    error: ToolError | None = None
    try:
        result = tool.run(ctx, args)
    except ToolError as exc:
        error = exc
    latency_ms = max(0.0, (timer() - started) * 1000.0)
    record = ExecutionRecord(
        record_id=record_id_for(ctx.trace_id, turn_index, step_index),
        trace_id=ctx.trace_id,
        session_id=ctx.session.session_id,
        turn_index=turn_index,
        step_index=step_index,
        step=StepKind.TOOL_CALL,
        state=state,
        tool=tool.spec.name,
        args_hash=args_hash(args),
        outcome=StepOutcome.SUCCESS if error is None else outcome_for(error.code),
        # Read tools have no `verified`; a write is verified only by its own read-back.
        verified=error is None and bool(getattr(result, "verified", False)),
        latency_ms=latency_ms,
        error_code=None if error is None else error.code,
        created_at=ctx.now,
    )
    return ToolCall(record=record, result=result, error=error)
