"""Run cases through a system: scripted user in, ``CaseTrace`` out.

For every (system, case, repeat) the runner opens a fresh server-side ``Session`` for the case
customer (already expired when the case injects ``session_expired``), a fresh LLM provider with
the case's LLM faults, the same instrumented tools and the same spend limit. Latency is the
harness wall clock around each ``respond`` call.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from bankagent.contracts.domain import Session
from bankagent.contracts.enums import ActionType, FaultInjection, SystemVariant, ToolName
from bankagent.contracts.evaluation import EvalCase
from bankagent.contracts.llm import LLMProvider
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import Tool
from bankagent.eval.simulator import ScriptedUser, SimEvent
from bankagent.eval.system import EvalEnvironment, System, SystemTurn, ToolObservation, ToolObserver
from bankagent.eval.tools import instrument_tools
from bankagent.interpret.stub import FAULTS_BY_INJECTION, StubProvider

DEFAULT_MAX_USER_TURNS = 8
SESSION_TTL = timedelta(minutes=15)

EndedBy = Literal["system", "user", "turn_limit", "error", "budget"]


class BudgetExceededError(RuntimeError):
    """The run spent more than its limit; the suite stops (ADR 0002)."""


@dataclass(slots=True)
class SpendGuard:
    """Per-system spend limit shared by every case run of that system."""

    limit_usd: Decimal
    spent_usd: Decimal = Decimal("0")

    def add(self, records: Iterable[ExecutionRecord]) -> None:
        self.spent_usd += sum((r.cost_usd for r in records), Decimal("0"))
        if self.spent_usd > self.limit_usd:
            raise BudgetExceededError(f"spent {self.spent_usd} USD, limit {self.limit_usd} USD")


@dataclass(frozen=True, slots=True)
class TurnLog:
    turn_index: int
    user_text: str
    reply_text: str
    latency_ms: float
    claimed_actions: tuple[ActionType, ...]
    records: tuple[ExecutionRecord, ...]


@dataclass(frozen=True, slots=True)
class CaseTrace:
    """Everything the scorer needs about one run of one case."""

    case: EvalCase
    system_name: str
    variant: SystemVariant
    run_id: str
    repeat_index: int
    session: Session
    session_expired: bool
    turns: tuple[TurnLog, ...]
    observations: tuple[ToolObservation, ...]
    sim_events: tuple[SimEvent, ...]
    ended_by: EndedBy
    error: str | None = None

    @property
    def records(self) -> tuple[ExecutionRecord, ...]:
        return tuple(record for turn in self.turns for record in turn.records)


ProviderFactory = Callable[[EvalCase], LLMProvider]


def stub_provider_for(case: EvalCase) -> LLMProvider:
    """0 USD deterministic provider; LLM faults of the case make every call fail that way."""
    faults = [FAULTS_BY_INJECTION[f] for f in case.fault_injections if f in FAULTS_BY_INJECTION]
    return StubProvider(always_fault=faults[0] if faults else None)


def _session_for(case: EvalCase, run_id: str, now: datetime) -> Session:
    digest = hashlib.sha256(f"{run_id}/{case.case_id}".encode()).hexdigest()[:24]
    expired = FaultInjection.SESSION_EXPIRED in case.fault_injections
    issued = now - 2 * SESSION_TTL if expired else now
    return Session(
        session_id=f"ses-{digest}",
        customer_id=case.customer_id,
        issued_at=issued,
        expires_at=issued + SESSION_TTL,
        language=None,
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class RunConfig:
    tools: Mapping[ToolName, Tool[Any, Any]] = field(default_factory=dict)
    provider_factory: ProviderFactory = stub_provider_for
    clock: Callable[[], datetime] = utc_now
    max_user_turns: int = DEFAULT_MAX_USER_TURNS


def run_case(
    system: System,
    case: EvalCase,
    *,
    run_id: str,
    repeat_index: int,
    config: RunConfig,
    spend: SpendGuard,
) -> CaseTrace:
    observer = ToolObserver()
    session = _session_for(case, run_id, config.clock())
    env = EvalEnvironment(
        case_id=case.case_id,
        session=session,
        llm=config.provider_factory(case),
        tools=instrument_tools(config.tools, observer, case.fault_injections),
        observer=observer,
        clock=config.clock,
    )
    user = ScriptedUser(case)
    turns: list[TurnLog] = []
    ended_by: EndedBy = "turn_limit"
    error: str | None = None
    text: str | None = user.opening
    agent = system.open_session(env)
    for turn_index in range(config.max_user_turns):
        if text is None:
            ended_by = "user"
            break
        observer.current_turn = turn_index
        started = time.perf_counter()
        try:
            out: SystemTurn = agent.respond(text)
        except Exception as exc:  # a crashing system is scored as failed, not hidden
            error = f"{type(exc).__name__}: {exc}"
            ended_by = "error"
            break
        latency_ms = (time.perf_counter() - started) * 1000.0
        turns.append(
            TurnLog(
                turn_index=turn_index,
                user_text=text,
                reply_text=out.reply_text,
                latency_ms=latency_ms,
                claimed_actions=out.claimed_actions,
                records=out.records,
            )
        )
        try:
            spend.add(out.records)
        except BudgetExceededError as exc:
            error = str(exc)
            ended_by = "budget"
            break
        if out.ended:
            ended_by = "system"
            break
        text = user.next_message(out.reply_text, turn_index + 1)
    else:
        if text is None:
            ended_by = "user"
    return CaseTrace(
        case=case,
        system_name=system.name,
        variant=system.variant,
        run_id=run_id,
        repeat_index=repeat_index,
        session=session,
        session_expired=not session.is_active(config.clock()),
        turns=tuple(turns),
        observations=tuple(observer.observations),
        sim_events=tuple(user.events),
        ended_by=ended_by,
        error=error,
    )


def run_suite(
    systems: Sequence[System],
    cases: Sequence[EvalCase],
    *,
    suite_id: str,
    repeats: int,
    budget_usd_per_system: Decimal,
    config: RunConfig | None = None,
) -> list[CaseTrace]:
    """Every system runs every case ``repeats`` times with the same tools and the same budget."""
    config = config or RunConfig()
    variants = [s.variant for s in systems]
    if len(set(variants)) != len(variants):
        raise ValueError("each system variant may appear only once in a suite")
    traces: list[CaseTrace] = []
    for system in systems:
        spend = SpendGuard(limit_usd=budget_usd_per_system)
        for repeat in range(repeats):
            run_id = f"{suite_id}-{system.variant.value}-r{repeat}"
            for case in cases:
                trace = run_case(
                    system, case, run_id=run_id, repeat_index=repeat, config=config, spend=spend
                )
                traces.append(trace)
                if trace.ended_by == "budget":
                    raise BudgetExceededError(trace.error or "budget exceeded")
    return traces
