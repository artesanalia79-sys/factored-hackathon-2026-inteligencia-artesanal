"""LLM-only baseline (D1) over the real Task 8 tools, driven by a scripted LLM (0 USD)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bankagent.contracts.enums import (
    ActionType,
    Outcome,
    StepKind,
    StepOutcome,
    SystemVariant,
    ToolName,
    UnsafeEvent,
)
from bankagent.contracts.evaluation import EvalCase
from bankagent.contracts.llm import LLMProvider, LLMTimeout
from bankagent.contracts.tools import TOOL_SPECS
from bankagent.eval.adapters import baseline_llm_only_system
from bankagent.eval.backend import FixtureBackendFactory
from bankagent.eval.bank import BankIndex, load_bank
from bankagent.eval.baseline import FALLBACK_REPLY, BaselineStep, system_prompt
from bankagent.eval.cases import DEV_DIR, load_cases
from bankagent.eval.runner import CaseTrace, RunConfig, SpendGuard, run_case
from bankagent.eval.scorer import ScoredRun, score
from bankagent.interpret.stub import StubFault, StubProvider

TARGET = "TXN-FX-0101"


@pytest.fixture(scope="module")
def bank() -> BankIndex:
    return load_bank()


@pytest.fixture(scope="module")
def case() -> EvalCase:
    return next(c for c in load_cases(DEV_DIR) if c.case_id == "dev-normal-es-mx-001")


@pytest.fixture(scope="module")
def backends(tmp_path_factory: pytest.TempPathFactory) -> FixtureBackendFactory:
    return FixtureBackendFactory(tmp_path_factory.mktemp("baseline-backend"))


def call(tool: ToolName, **args: object) -> BaselineStep:
    return BaselineStep(
        action="call_tool",
        tool=tool,
        args_json=json.dumps(args),
        reply_text=None,
        end_conversation=False,
    )


def reply(text: str, *, end: bool = False) -> BaselineStep:
    return BaselineStep(
        action="reply", tool=None, args_json=None, reply_text=text, end_conversation=end
    )


ASK = reply("Encontré el cargo de ELECTROMUNDO por 2,450 MXN. ¿Confirmas que abro una disputa?")
DISPUTE = call(ToolName.CREATE_DISPUTE, transaction_id=TARGET, reason="unrecognized")
DONE = reply("Listo, registré la disputa por el cargo de ELECTROMUNDO.", end=True)


def run(
    case: EvalCase,
    backends: FixtureBackendFactory,
    steps: Sequence[BaselineStep] = (),
    *,
    provider: LLMProvider | None = None,
    repeat: int = 0,
) -> CaseTrace:
    llm = provider or StubProvider(scripted=steps)
    return run_case(
        baseline_llm_only_system(),
        case,
        run_id=f"run-baseline-{repeat}",
        repeat_index=repeat,
        config=RunConfig(backend_factory=backends, provider_factory=lambda _case: llm),
        spend=SpendGuard(limit_usd=Decimal("0")),
    )


def scored(trace: CaseTrace, bank: BankIndex) -> ScoredRun:
    return score(trace, bank)


def test_a_confirmed_dispute_is_a_safe_automated_resolution(
    case: EvalCase, backends: FixtureBackendFactory, bank: BankIndex
) -> None:
    steps = [call(ToolName.SEARCH_TRANSACTIONS, merchant_query="ELECTROMUNDO"), ASK, DISPUTE, DONE]
    trace = run(case, backends, steps)
    result = scored(trace, bank).result
    assert result.system == SystemVariant.BASELINE_LLM_ONLY
    assert result.final_outcome == Outcome.AUTOMATED_RESOLUTION
    assert result.verified_actions == (ActionType.CREATE_DISPUTE,)
    assert result.unsafe_events == ()
    assert result.correct
    assert result.safe_automated_resolution
    # The harness issued the token right before the write, in the turn of the customer's yes.
    steps_of_write_turn = [r.step for r in trace.turns[1].records]
    assert steps_of_write_turn.index(StepKind.CONFIRMATION) < steps_of_write_turn.index(
        StepKind.TOOL_CALL
    )


def test_a_write_without_asking_is_measured_not_prevented(
    case: EvalCase, backends: FixtureBackendFactory, bank: BankIndex
) -> None:
    trace = run(case, backends, [DISPUTE, DONE])
    result = scored(trace, bank).result
    assert result.verified_actions == (ActionType.CREATE_DISPUTE,)
    assert UnsafeEvent.ACTION_WITHOUT_CONFIRMATION in result.unsafe_events


def test_every_repeat_gets_a_fresh_ops_store(
    case: EvalCase, backends: FixtureBackendFactory, bank: BankIndex
) -> None:
    for repeat in range(2):
        result = scored(run(case, backends, [ASK, DISPUTE, DONE], repeat=repeat), bank).result
        assert result.final_outcome == Outcome.AUTOMATED_RESOLUTION, repeat


def test_invalid_arguments_go_back_to_the_model_without_a_tool_call(
    case: EvalCase, backends: FixtureBackendFactory
) -> None:
    bad = BaselineStep(
        action="call_tool",
        tool=ToolName.GET_TRANSACTION,
        args_json='{"transaction": 1}',
        reply_text=None,
        end_conversation=False,
    )
    trace = run(case, backends, [bad, reply("¿Me das más detalles?"), reply("Gracias.", end=True)])
    assert trace.ended_by != "error"
    assert not any(r.step == StepKind.TOOL_CALL for r in trace.records)


def test_another_customers_transaction_is_refused_by_the_tools(
    case: EvalCase, backends: FixtureBackendFactory, bank: BankIndex
) -> None:
    steps = [call(ToolName.GET_TRANSACTION, transaction_id="TXN-FX-0601"), DONE]
    trace = run(case, backends, steps)
    reads = [r for r in trace.records if r.tool == ToolName.GET_TRANSACTION]
    assert [r.outcome for r in reads] == [StepOutcome.BLOCKED]
    assert UnsafeEvent.CROSS_CUSTOMER_DISCLOSURE not in scored(trace, bank).result.unsafe_events


def test_an_llm_failure_ends_with_the_fallback_reply(
    case: EvalCase, backends: FixtureBackendFactory
) -> None:
    trace = run(case, backends, provider=StubProvider(always_fault=StubFault.TIMEOUT))
    assert trace.ended_by == "system"
    assert trace.turns[0].reply_text == FALLBACK_REPLY
    assert [r.outcome for r in trace.records] == [StepOutcome.FAILURE]


def test_llm_steps_are_recorded_with_model_and_prompt_version(
    case: EvalCase, backends: FixtureBackendFactory
) -> None:
    trace = run(case, backends, [ASK, DISPUTE, DONE])
    llm_steps = [r for r in trace.records if r.step == StepKind.INTERPRET]
    assert len(llm_steps) == 3
    assert {r.prompt_version for r in llm_steps} == {BaselineStep.PROMPT_VERSION}
    assert all(r.model for r in llm_steps)


def test_the_prompt_shows_the_customer_id_but_no_tool_takes_one(case: EvalCase) -> None:
    from bankagent.eval.runner import _session_for, utc_now  # pyright: ignore[reportPrivateUsage]

    session = _session_for(case, "run-x", utc_now())
    prompt = system_prompt(session)
    assert case.customer_id in prompt
    for spec in TOOL_SPECS.values():
        assert "customer_id" not in json.dumps(spec.args_model.model_json_schema())


def test_the_baseline_needs_real_tools(case: EvalCase) -> None:
    with pytest.raises(ValueError, match="backend_factory"):
        run_case(
            baseline_llm_only_system(),
            case,
            run_id="run-no-backend",
            repeat_index=0,
            config=RunConfig(provider_factory=lambda _case: StubProvider(scripted=[DONE])),
            spend=SpendGuard(limit_usd=Decimal("0")),
        )


def test_run_command_runs_the_baseline_on_real_tools(tmp_path: Path) -> None:
    from bankagent.eval import cli

    out = tmp_path / "dev"
    assert cli.main(["run", "--system", "baseline", "--out", str(out)]) == 0
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "baseline_llm_only" in report
    assert len((out / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 14


def test_a_handoff_gets_its_server_side_fields(
    case: EvalCase, backends: FixtureBackendFactory, bank: BankIndex
) -> None:
    draft = {
        "language": "es",
        "request": "El cliente pide hablar con un asesor.",
        "intent": "human_request",
        "open_questions": ["¿Qué cargo quiere reclamar?"],
        "routing": {"specialty": "disputes", "language": "es", "priority": "medium"},
    }
    steps = [call(ToolName.CREATE_HANDOFF, draft=draft), reply("Te paso con un asesor.", end=True)]
    trace = run(case, backends, steps)
    result = scored(trace, bank).result
    assert result.final_outcome == Outcome.ESCALATED
    handoffs = [o for o in trace.observations if o.tool == ToolName.CREATE_HANDOFF]
    assert [o.outcome for o in handoffs] == [StepOutcome.SUCCESS]


def test_injected_llm_faults_stay_simulated_with_the_real_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from bankagent.eval import cli
    from bankagent.interpret import openai_provider

    real = StubProvider()
    monkeypatch.setattr(openai_provider, "OpenAIProvider", lambda: real)
    providers: dict[str, LLMProvider] = {}

    def fake_suite(_systems: object, cases: Sequence[EvalCase], **kwargs: Any) -> list[CaseTrace]:
        providers.update({c.case_id: kwargs["config"].provider_factory(c) for c in cases})
        return []

    monkeypatch.setattr(cli, "run_suite", fake_suite)
    monkeypatch.setattr(cli, "write_outputs", lambda *_a, **_k: [])
    source = (DEV_DIR / "dev-normal-es-mx-001.yaml").read_text(encoding="utf-8")
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "a.yaml").write_text(source, encoding="utf-8")
    faulted = source.replace("dev-normal-es-mx-001", "dev-timeout-es-mx-001").replace(
        "fault_injections: []", "fault_injections: [llm_timeout]"
    )
    (cases_dir / "b.yaml").write_text(faulted, encoding="utf-8")
    args = ["run", "--system", "baseline", "--provider", "openai", "--cases", str(cases_dir)]
    assert cli.main([*args, "--out", str(tmp_path / "out")]) == 0
    assert providers["dev-normal-es-mx-001"] is real
    faulty = providers["dev-timeout-es-mx-001"]
    assert faulty is not real
    with pytest.raises(LLMTimeout):
        faulty.complete_structured(
            system="x", messages=[], response_model=BaselineStep, timeout_s=1
        )


class _RecordingStub(StubProvider):
    """A scripted stub that keeps the messages of every call."""

    def __init__(self, steps: Sequence[BaselineStep]) -> None:
        super().__init__(scripted=steps)
        self.seen: list[list[str]] = []

    def complete_structured(self, **kwargs: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.seen.append([m.content for m in kwargs["messages"]])
        return super().complete_structured(**kwargs)


def test_tool_results_go_back_to_the_model(case: EvalCase, backends: FixtureBackendFactory) -> None:
    llm = _RecordingStub([call(ToolName.GET_TRANSACTION, transaction_id=TARGET), DONE])
    run(case, backends, provider=llm)
    second_call = llm.seen[1]
    assert second_call[-1].startswith("[TOOL RESULT]")
    assert TARGET in second_call[-1]


def test_the_compatible_provider_is_shared_and_faults_stay_simulated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from bankagent.eval import cli
    from bankagent.interpret.compat_provider import OpenAICompatibleProvider

    for key, value in {
        "LLM_BASE_URL": "https://llm.example.com/v1",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "gpt-6-luna",
    }.items():
        monkeypatch.setenv(key, value)
    built: list[LLMProvider] = []

    def fake_suite(_systems: object, cases: Sequence[EvalCase], **kwargs: Any) -> list[CaseTrace]:
        factory = kwargs["config"].provider_factory
        built.extend(factory(c) for c in [*cases, *cases])
        return []

    monkeypatch.setattr(cli, "run_suite", fake_suite)
    monkeypatch.setattr(cli, "write_outputs", lambda *_a, **_k: [])
    source = (DEV_DIR / "dev-normal-es-mx-001.yaml").read_text(encoding="utf-8")
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "a.yaml").write_text(source, encoding="utf-8")
    faulted = source.replace("dev-normal-es-mx-001", "dev-timeout-es-mx-001").replace(
        "fault_injections: []", "fault_injections: [llm_timeout]"
    )
    (cases_dir / "b.yaml").write_text(faulted, encoding="utf-8")
    args = ["run", "--system", "baseline", "--provider", "compat", "--cases", str(cases_dir)]
    assert cli.main([*args, "--out", str(tmp_path / "out")]) == 0
    real = [p for p in built if isinstance(p, OpenAICompatibleProvider)]
    assert len(real) == 2
    assert real[0] is real[1]
    assert sum(isinstance(p, StubProvider) for p in built) == 2
