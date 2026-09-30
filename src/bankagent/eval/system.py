"""The interface between the harness and a system under test.

A ``System`` opens one ``SystemSession`` per case run. The harness builds the
``EvalEnvironment`` (server-side session, LLM provider, instrumented tools, clock), so both
variants receive identical cases, tools and budgets by construction.

Production code does not import this module. The proposed agent (Task 13) is wrapped by an
adapter in ``bankagent.eval.adapters``; what Task 13 must expose is documented in
``docs/eval/system_interface.md``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from bankagent.contracts.base import Contract
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import (
    ActionType,
    StepOutcome,
    SystemVariant,
    ToolErrorCode,
    ToolName,
)
from bankagent.contracts.llm import LLMProvider
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import Tool


@dataclass(frozen=True, slots=True)
class ToolObservation:
    """What the harness saw a tool do: which resources it read or wrote and how it ended.

    Linked to the system's ``ExecutionRecord`` by ``(tool, args_hash)``. ``resource_ids`` are the
    transaction, card and dispute ids in the arguments and the result, so the scorer can check
    their owner against the session customer. ``turn_index`` is stamped by the observer.
    """

    tool: ToolName
    args_hash: str
    outcome: StepOutcome
    resource_ids: tuple[str, ...] = ()
    verified: bool = False
    error_code: ToolErrorCode | None = None
    args: Contract | None = None
    turn_index: int = -1


@dataclass(slots=True)
class ToolObserver:
    """Collects ``ToolObservation``s for one case run; the runner sets ``current_turn``."""

    observations: list[ToolObservation] = field(default_factory=list)
    current_turn: int = 0

    def record(self, observation: ToolObservation) -> None:
        self.observations.append(replace(observation, turn_index=self.current_turn))


@dataclass(frozen=True, slots=True)
class EvalEnvironment:
    """Everything a system may use for one case run. Built by the harness, identical per variant.

    ``session`` is server-side: its ``customer_id`` must never reach the model in the proposed
    system (the LLM-only baseline may show it by design, see ``eval/preregistration.md``).
    """

    case_id: str
    session: Session
    llm: LLMProvider
    tools: Mapping[ToolName, Tool[Any, Any]]
    observer: ToolObserver
    clock: Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class SystemTurn:
    """The system's answer to one user message.

    ``claimed_actions`` are the actions the reply states as done (template keys in the proposed
    system). The scorer adds whatever the ES/PT claim detector finds in ``reply_text``, for both
    variants alike.
    """

    reply_text: str
    records: tuple[ExecutionRecord, ...]
    ended: bool
    claimed_actions: tuple[ActionType, ...] = ()


class SystemSession(Protocol):
    def respond(self, text: str, /) -> SystemTurn: ...


class System(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def variant(self) -> SystemVariant: ...

    def open_session(self, env: EvalEnvironment, /) -> SystemSession: ...
