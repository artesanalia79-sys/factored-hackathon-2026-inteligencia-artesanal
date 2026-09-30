"""End to end: cases -> simulator -> runner -> ExecutionRecords -> scorer -> metrics -> report."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bankagent.contracts.domain import Session
from bankagent.contracts.enums import (
    ActionType,
    ConversationState,
    Dialect,
    EvalCategory,
    FaultInjection,
    StepKind,
    StepOutcome,
    SystemVariant,
    ToolErrorCode,
    ToolName,
    UnsafeEvent,
)
from bankagent.contracts.errors import NotFound, ToolUnavailable
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import (
    TOOL_SPECS,
    GetTransactionArgs,
    GetTransactionResult,
    ToolContext,
    ToolSpec,
)
from bankagent.eval import cli
from bankagent.eval.adapters import TurnFunctionSystem
from bankagent.eval.bank import load_bank
from bankagent.eval.cases import (
    HeldoutAccessError,
    is_heldout_path,
    load_cases,
    reference_problems,
)
from bankagent.eval.fake import Behavior, ScriptedFakeSystem
from bankagent.eval.runner import (
    BudgetExceededError,
    RunConfig,
    SpendGuard,
    run_case,
    run_suite,
)
from bankagent.eval.scorer import score
from bankagent.eval.system import ToolObserver
from bankagent.eval.tools import instrument_tools

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 6, 17, 12, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Dev cases (D5)
# ---------------------------------------------------------------------------


def test_dev_cases_load_and_reference_the_fixture_bank() -> None:
    bank = load_bank()
    cases = load_cases()
    assert len(cases) == 10
    assert [p for case in cases for p in reference_problems(case, bank)] == []
    assert all(case.split.value == "dev" for case in cases)


def test_dev_cases_cover_the_approved_mix() -> None:
    cases = load_cases()
    dialects = Counter(case.dialect for case in cases)
    assert dialects == {Dialect.ES_MX: 4, Dialect.ES_CO: 2, Dialect.ES_AR: 2, Dialect.PT_BR: 2}
    categories = {case.category for case in cases}
    assert categories == set(EvalCategory) - {
        EvalCategory.UNSUPPORTED,
        EvalCategory.MISSING_OR_INCORRECT_DATA,
        EvalCategory.MULTILINGUAL_AMBIGUITY,
    }


def test_heldout_paths_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HeldoutAccessError):
        load_cases(ROOT / "eval" / "heldout")
    sealed = tmp_path / "sealed"
    monkeypatch.setenv("HELDOUT_DIR", str(sealed))
    assert is_heldout_path(sealed / "cases")
    with pytest.raises(HeldoutAccessError):
        load_cases(sealed)


# ---------------------------------------------------------------------------
# Full pipeline with the scripted fakes
# ---------------------------------------------------------------------------


def _systems() -> tuple[list[ScriptedFakeSystem], Any, Any]:
    bank = load_bank()
    cases = load_cases()
    systems = [
        ScriptedFakeSystem(
            variant=SystemVariant.PROPOSED, behavior=Behavior.IDEAL, cases=cases, bank=bank
        ),
        ScriptedFakeSystem(
            variant=SystemVariant.BASELINE_LLM_ONLY, behavior=Behavior.NAIVE, cases=cases, bank=bank
        ),
    ]
    return systems, cases, bank


def test_ideal_fake_is_correct_and_safe_on_every_dev_case() -> None:
    systems, cases, bank = _systems()
    traces = run_suite(
        systems[:1], cases, suite_id="t", repeats=2, budget_usd_per_system=Decimal("0")
    )
    results = [score(t, bank).result for t in traces]
    assert len(results) == 20
    assert all(r.correct and not r.unsafe_events for r in results)
    assert {r.repeat_index for r in results} == {0, 1}


def test_naive_fake_triggers_every_unsafe_event() -> None:
    systems, cases, bank = _systems()
    traces = run_suite(
        systems[1:], cases, suite_id="t", repeats=1, budget_usd_per_system=Decimal("0")
    )
    seen = {e for t in traces for e in score(t, bank).result.unsafe_events}
    assert seen == set(UnsafeEvent)


def test_both_systems_get_identical_cases_and_budgets() -> None:
    systems, cases, _ = _systems()
    traces = run_suite(systems, cases, suite_id="t", repeats=1, budget_usd_per_system=Decimal("0"))
    by_system = {
        v: sorted(t.case.case_id for t in traces if t.variant == v)
        for v in (SystemVariant.PROPOSED, SystemVariant.BASELINE_LLM_ONLY)
    }
    assert by_system[SystemVariant.PROPOSED] == by_system[SystemVariant.BASELINE_LLM_ONLY]


def test_smoke_command_writes_the_report_with_slice_tables(tmp_path: Path) -> None:
    assert cli.main(["smoke", "--out", str(tmp_path), "--repeats", "1"]) == 0
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    for dimension in ("language", "dialect", "segment", "category"):
        assert f"## Slice: {dimension}" in report
    assert re.search(r"\d+/\d+ = \d+\.\d% \[\d+\.\d, \d+\.\d\]", report)
    assert "SIMULATED" in report
    lines = (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 20


def test_self_check_reports_a_broken_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    from bankagent.eval import scorer

    monkeypatch.setitem(scorer.DETECTORS, UnsafeEvent.PII_LEAK, lambda ctx: [])
    systems, cases, bank = _systems()
    traces = run_suite(systems, cases, suite_id="t", repeats=1, budget_usd_per_system=Decimal("0"))
    problems = cli.self_check([score(t, bank) for t in traces])
    assert problems == ["naive fake never triggered pii_leak: the detector may be broken"]


# ---------------------------------------------------------------------------
# Budget, tools wrapper and adapter
# ---------------------------------------------------------------------------


def _record(cost: str) -> ExecutionRecord:
    return ExecutionRecord(
        record_id="r",
        trace_id="t",
        turn_index=0,
        step_index=0,
        step=StepKind.INTERPRET,
        state=ConversationState.UNDERSTAND,
        outcome=StepOutcome.SUCCESS,
        latency_ms=1.0,
        cost_usd=Decimal(cost),
        created_at=NOW,
    )


def test_spend_guard_stops_a_run_over_budget() -> None:
    guard = SpendGuard(limit_usd=Decimal("0.01"))
    guard.add([_record("0.006")])
    with pytest.raises(BudgetExceededError):
        guard.add([_record("0.006")])


class _GetTransaction:
    """Test double of the Task 8 tool: owner check by transaction id."""

    spec: ToolSpec = TOOL_SPECS[ToolName.GET_TRANSACTION]

    def run(self, ctx: ToolContext, args: GetTransactionArgs, /) -> GetTransactionResult:
        raise NotFound(args.transaction_id)


def _ctx() -> ToolContext:
    session = Session(
        session_id="ses-1",
        customer_id="CUST-FX-001",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    return ToolContext(session=session, trace_id="tr-1", now=NOW)


def test_instrumented_tool_records_blocked_calls() -> None:
    observer = ToolObserver()
    tools = instrument_tools({ToolName.GET_TRANSACTION: _GetTransaction()}, observer)
    with pytest.raises(NotFound):
        tools[ToolName.GET_TRANSACTION].run(
            _ctx(), GetTransactionArgs(transaction_id="TXN-FX-0601")
        )
    (obs,) = observer.observations
    assert obs.outcome == StepOutcome.BLOCKED
    assert obs.error_code == ToolErrorCode.NOT_FOUND
    assert obs.resource_ids == ("TXN-FX-0601",)


def test_tool_unavailable_fault_is_injected_for_reads() -> None:
    observer = ToolObserver()
    tools = instrument_tools(
        {ToolName.GET_TRANSACTION: _GetTransaction()}, observer, [FaultInjection.TOOL_UNAVAILABLE]
    )
    with pytest.raises(ToolUnavailable):
        tools[ToolName.GET_TRANSACTION].run(
            _ctx(), GetTransactionArgs(transaction_id="TXN-FX-0101")
        )
    assert observer.observations[0].error_code == ToolErrorCode.TOOL_UNAVAILABLE


class _EchoAgent:
    def __init__(self) -> None:
        self.sessions: list[str] = []

    def handle_turn(self, session: Session, text: str, /) -> Any:
        self.sessions.append(session.customer_id)

        class _Out:
            reply_text = "Te comuniqué con un asesor."
            records = ()
            ended = True
            claimed_actions = (ActionType.CREATE_HANDOFF,)

        return _Out()


def test_turn_function_adapter_passes_the_server_side_session_only() -> None:
    agent = _EchoAgent()
    captured: dict[str, Any] = {}

    def factory(**deps: Any) -> _EchoAgent:
        captured.update(deps)
        return agent

    system = TurnFunctionSystem(name="t13", variant=SystemVariant.PROPOSED, factory=factory)
    case = load_cases()[0]
    trace = run_case(
        system,
        case,
        run_id="adapter",
        repeat_index=0,
        config=RunConfig(),
        spend=SpendGuard(limit_usd=Decimal("0")),
    )
    assert agent.sessions == [case.customer_id]
    assert set(captured) == {"llm", "tools", "clock"}
    assert trace.turns[0].claimed_actions == (ActionType.CREATE_HANDOFF,)
    # The claim has no verified record behind it, so the scorer must flag it.
    assert UnsafeEvent.UNVERIFIED_CLAIM in score(trace, load_bank()).result.unsafe_events


def test_production_code_never_imports_the_eval_package() -> None:
    src = ROOT / "src" / "bankagent"
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in src.rglob("*.py")
        if "eval" not in path.relative_to(src).parts[:1]
        and re.search(r"^\s*(from|import)\s+bankagent\.eval\b", path.read_text("utf-8"), re.M)
    ]
    assert offenders == []
