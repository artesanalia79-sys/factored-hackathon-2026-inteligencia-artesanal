"""Adapters from the real systems to ``bankagent.eval.system.System``.

Dependency direction: production code never imports ``bankagent.eval``. Task 13 exposes an agent
factory and a turn method with the structural shape below (documented in
``docs/eval/system_interface.md``); this module wraps it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from bankagent.contracts.domain import Session
from bankagent.contracts.enums import ActionType, SystemVariant, ToolName
from bankagent.contracts.llm import LLMProvider
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import Tool
from bankagent.eval.baseline import BaselineSystem
from bankagent.eval.system import EvalEnvironment, SystemTurn


class AgentTurnOutput(Protocol):
    """What one call of the Task 13 turn method returns (any object with these attributes)."""

    @property
    def reply_text(self) -> str: ...

    @property
    def records(self) -> Sequence[ExecutionRecord]: ...

    @property
    def ended(self) -> bool: ...

    @property
    def claimed_actions(self) -> Sequence[ActionType]: ...


class Agent(Protocol):
    def handle_turn(self, session: Session, text: str, /) -> AgentTurnOutput: ...


class AgentFactory(Protocol):
    """Builds one agent per conversation from injected dependencies."""

    def __call__(
        self,
        *,
        llm: LLMProvider,
        tools: Mapping[ToolName, Tool[Any, Any]],
        clock: Callable[[], datetime],
    ) -> Agent: ...


class TurnFunctionSystem:
    """``System`` over an agent factory: one fresh agent per case run."""

    def __init__(self, *, name: str, variant: SystemVariant, factory: AgentFactory) -> None:
        self._name = name
        self._variant = variant
        self._factory = factory

    @property
    def name(self) -> str:
        return self._name

    @property
    def variant(self) -> SystemVariant:
        return self._variant

    def open_session(self, env: EvalEnvironment, /) -> _AgentSession:
        agent = self._factory(llm=env.llm, tools=env.tools, clock=env.clock)
        return _AgentSession(agent, env.session)


class _AgentSession:
    def __init__(self, agent: Agent, session: Session) -> None:
        self._agent = agent
        self._session = session

    def respond(self, text: str, /) -> SystemTurn:
        out = self._agent.handle_turn(self._session, text)
        return SystemTurn(
            reply_text=out.reply_text,
            records=tuple(out.records),
            ended=out.ended,
            claimed_actions=tuple(out.claimed_actions),
        )


def proposed_system() -> TurnFunctionSystem:
    """The real proposed agent. Not available until Task 13 is merged into ``main``.

    Its factory must bind the T9 policy and the confirmation issuer to the run's own stores
    (``EvalEnvironment.backend``), see ``docs/eval/system_interface.md`` section 4.
    """
    # TODO(T13, Santiago): when PR #46 merges, wire create_agent here as documented in
    # docs/eval/system_interface.md section 4 (one adapter, no change to the harness).
    raise NotImplementedError("the proposed agent (Task 13) is not on main yet")


def baseline_llm_only_system() -> BaselineSystem:
    """The real LLM-only baseline (D1): ``bankagent.eval.baseline``. Needs real tools."""
    return BaselineSystem()
