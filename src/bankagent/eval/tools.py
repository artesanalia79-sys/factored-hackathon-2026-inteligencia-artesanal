"""Instrumented tools: the harness sees every tool call regardless of what the system reports.

``instrument_tools`` wraps each real tool (Task 8) so that every call records a
``ToolObservation`` and, when the case injects ``tool_unavailable``, read tools raise
``ToolUnavailable`` before reaching the store.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel

from bankagent.contracts.base import Contract, args_hash
from bankagent.contracts.enums import FaultInjection, StepOutcome, ToolErrorCode, ToolName
from bankagent.contracts.errors import ToolError, ToolUnavailable
from bankagent.contracts.tools import Tool, ToolContext, ToolSpec
from bankagent.eval.system import ToolObservation, ToolObserver

RESOURCE_ID_FIELDS: frozenset[str] = frozenset(
    {"transaction_id", "product_id", "dispute_id", "complaint_id", "related_transaction_id"}
)
BLOCKING_ERRORS: frozenset[ToolErrorCode] = frozenset(
    {ToolErrorCode.NOT_FOUND, ToolErrorCode.UNAUTHORIZED, ToolErrorCode.CONFIRMATION_REQUIRED}
)


def resource_ids(*models: BaseModel | None) -> tuple[str, ...]:
    """Every id-like field (``transaction_id``, ``product_id``, ...) at any nesting level."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                item = getattr(value, name)
                if name in RESOURCE_ID_FIELDS and isinstance(item, str):
                    found.append(item)
                else:
                    walk(item)
        elif isinstance(value, list | tuple):
            for item in value:  # pyright: ignore[reportUnknownVariableType]
                walk(item)

    for model in models:
        walk(model)
    return tuple(dict.fromkeys(found))


def outcome_for(code: ToolErrorCode) -> StepOutcome:
    return StepOutcome.BLOCKED if code in BLOCKING_ERRORS else StepOutcome.FAILURE


class ObservedTool:
    """Implements the ``Tool`` protocol around another tool."""

    def __init__(
        self, inner: Tool[Any, Any], observer: ToolObserver, *, unavailable: bool = False
    ) -> None:
        self._inner = inner
        self._observer = observer
        self._unavailable = unavailable

    @property
    def spec(self) -> ToolSpec:
        return self._inner.spec

    def run(self, ctx: ToolContext, args: Contract, /) -> Contract:
        spec = self._inner.spec
        digest = args_hash(args)
        try:
            if self._unavailable and not spec.is_write:
                raise ToolUnavailable(f"{spec.name} unavailable (injected fault)")
            result = self._inner.run(ctx, args)
        except ToolError as exc:
            self._observer.record(
                ToolObservation(
                    tool=spec.name,
                    args_hash=digest,
                    outcome=outcome_for(exc.code),
                    resource_ids=resource_ids(args),
                    error_code=exc.code,
                    args=args,
                )
            )
            raise
        self._observer.record(
            ToolObservation(
                tool=spec.name,
                args_hash=digest,
                outcome=StepOutcome.SUCCESS,
                resource_ids=resource_ids(args, result),
                verified=bool(getattr(result, "verified", False)),
                args=args,
            )
        )
        return result


def instrument_tools(
    tools: Mapping[ToolName, Tool[Any, Any]],
    observer: ToolObserver,
    faults: Iterable[FaultInjection] = (),
) -> dict[ToolName, Tool[Any, Any]]:
    unavailable = FaultInjection.TOOL_UNAVAILABLE in set(faults)
    return {
        name: ObservedTool(tool, observer, unavailable=unavailable) for name, tool in tools.items()
    }
