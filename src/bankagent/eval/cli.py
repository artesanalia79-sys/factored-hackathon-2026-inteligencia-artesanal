"""Evaluation command line.

``python -m bankagent.eval.cli smoke`` (``uv run poe eval-smoke``): runs the dev cases through the
scripted fake systems with the ``StubProvider`` and a 0 USD budget, writes
``eval/runs/<suite>/{results.jsonl,unsafe_reasons.jsonl,report.md}`` and exits non-zero when
the harness self-checks fail:

- the ``ideal`` fake (as ``proposed``) must score every run correct with no unsafe event;
- the ``naive`` fake (as ``baseline_llm_only``) must trigger every ``UnsafeEvent`` at least once.

Gates are evaluated and reported but never decide the exit code of the smoke run: the dev set is
far below every gate's ``min_n``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from bankagent.contracts.enums import EvalSplit, SystemVariant, UnsafeEvent
from bankagent.contracts.evaluation import EvalCase
from bankagent.contracts.llm import LLMProvider
from bankagent.eval.adapters import baseline_llm_only_system, proposed_system
from bankagent.eval.backend import FixtureBackendFactory
from bankagent.eval.bank import BankIndex, load_bank
from bankagent.eval.cases import DEV_DIR, ROOT, case_set_sha256, load_cases, reference_problems
from bankagent.eval.comparison import build_comparison
from bankagent.eval.fake import Behavior, ScriptedFakeSystem
from bankagent.eval.gates import GATES_FILE, evaluate, load_gates
from bankagent.eval.metrics import system_metrics
from bankagent.eval.report import ReportContext, render
from bankagent.eval.runner import (
    CaseTrace,
    ProviderFactory,
    RunConfig,
    run_suite,
    stub_provider_for,
)
from bankagent.eval.scorer import ScoredRun, score
from bankagent.eval.system import System
from bankagent.interpret.keywords import MODEL_NAME as STUB_MODEL
from bankagent.interpret.stub import FAULTS_BY_INJECTION

RUNS_DIR = ROOT / "eval" / "runs"
SMOKE_BUDGET_USD = Decimal("0")


def self_check(runs: Sequence[ScoredRun]) -> list[str]:
    """Harness self-checks on the fake systems; returns the problems found."""
    problems: list[str] = []
    for run in runs:
        result = run.result
        if result.system == SystemVariant.PROPOSED and (not result.correct or result.unsafe_events):
            problems.append(
                f"ideal fake failed {result.case_id} (repeat {result.repeat_index}): "
                f"{result.final_outcome.value}, unsafe={[e.value for e in result.unsafe_events]}"
            )
    seen = {
        event
        for run in runs
        if run.result.system == SystemVariant.BASELINE_LLM_ONLY
        for event in run.result.unsafe_events
    }
    problems.extend(
        f"naive fake never triggered {event.value}: the detector may be broken"
        for event in UnsafeEvent
        if event not in seen
    )
    return problems


def write_outputs(
    traces: Sequence[CaseTrace],
    bank: BankIndex,
    *,
    out_dir: Path,
    suite_id: str,
    generated_at: datetime,
    cases_dir: Path,
    cases: Sequence[EvalCase],
    repeats: int,
    simulated: bool,
    cost_assumptions: str,
    workload: str | None = None,
    replay: bool = True,
) -> list[ScoredRun]:
    """Score the traces, evaluate the gates and write report.md, results and unsafe reasons.

    ``workload`` replaces the folder in the report: a sealed set is named by its manifest,
    not by a path on someone's machine. A suite of dev cases only also gets ``comparison.json``,
    the replay that quotes its conversations (T22). ``replay=False`` never writes it, whatever
    the cases say about themselves: the Task 27 command passes it for the sealed set, which it
    recognizes by its manifest and not by a label inside a file.
    """
    runs = [score(trace, bank) for trace in traces]
    metrics = {
        variant: system_metrics([r for r in runs if r.result.system == variant], bank)
        for variant in (SystemVariant.PROPOSED, SystemVariant.BASELINE_LLM_ONLY)
        if any(r.result.system == variant for r in runs)
    }
    gates = load_gates(GATES_FILE)
    gate_results = evaluate(gates, metrics)
    report = render(
        ReportContext(
            suite_id=suite_id,
            generated_at=generated_at.isoformat(timespec="seconds"),
            cases_dir=workload
            or (
                cases_dir.relative_to(ROOT).as_posix()
                if cases_dir.is_relative_to(ROOT)
                else str(cases_dir)
            ),
            case_set_sha256=case_set_sha256(cases),
            n_cases=len(cases),
            repeats=repeats,
            simulated=simulated,
            cost_assumptions=cost_assumptions,
        ),
        metrics,
        runs,
        gates,
        gate_results,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    # First: a reused output directory must not keep the replay of an earlier suite next to
    # this report, also when this suite gets none or its replay cannot be built.
    (out_dir / "comparison.json").unlink(missing_ok=True)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
        for run in runs:
            handle.write(run.result.model_dump_json() + "\n")
    with (out_dir / "unsafe_reasons.jsonl").open("w", encoding="utf-8") as handle:
        for run in runs:
            if run.unsafe_reasons:
                row = {
                    "case_id": run.result.case_id,
                    "system": run.result.system.value,
                    "repeat_index": run.result.repeat_index,
                    "reasons": {e.value: list(v) for e, v in run.unsafe_reasons.items()},
                }
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    # Last: the report and the results never wait for the replay.
    if replay and runs and all(run.trace.case.split == EvalSplit.DEV for run in runs):
        comparison = build_comparison(
            runs,
            suite_id=suite_id,
            case_set_sha256=case_set_sha256(cases),
            simulated=simulated,
            cost_assumptions=cost_assumptions,
        )
        (out_dir / "comparison.json").write_text(
            comparison.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    for variant, m in metrics.items():
        print(
            f"{variant.value:<18} cases={m.n_cases} runs={m.n_runs} "
            f"correct={m.rates['correct_outcome'].successes}/{m.rates['correct_outcome'].n} "
            f"safe_auto_in_scope={m.rates['safe_auto_in_scope'].successes}"
            f"/{m.rates['safe_auto_in_scope'].n} "
            f"unsafe_cases={m.rates['unsafe_case'].successes}/{m.rates['unsafe_case'].n} "
            f"cost_usd={m.total_cost_usd}"
        )
    print(f"gates ({gates.status}): " + ", ".join(f"{g.gate.id}={g.status}" for g in gate_results))
    print(f"report: {(out_dir / 'report.md').as_posix()}")
    return runs


def smoke(cases_dir: Path, out_dir: Path, repeats: int) -> int:
    bank = load_bank()
    cases = load_cases(cases_dir)
    problems = [p for case in cases for p in reference_problems(case, bank)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    systems = [
        ScriptedFakeSystem(
            variant=SystemVariant.PROPOSED, behavior=Behavior.IDEAL, cases=cases, bank=bank
        ),
        ScriptedFakeSystem(
            variant=SystemVariant.BASELINE_LLM_ONLY, behavior=Behavior.NAIVE, cases=cases, bank=bank
        ),
    ]
    now = datetime.now(UTC)
    suite_id = f"smoke-{now:%Y%m%dT%H%M%SZ}"
    traces = run_suite(
        systems,
        cases,
        suite_id=suite_id,
        repeats=repeats,
        budget_usd_per_system=SMOKE_BUDGET_USD,
        config=RunConfig(provider_factory=stub_provider_for),
    )
    runs = write_outputs(
        traces,
        bank,
        out_dir=out_dir,
        suite_id=suite_id,
        generated_at=now,
        cases_dir=cases_dir,
        cases=cases,
        repeats=repeats,
        simulated=True,
        cost_assumptions="StubProvider (keyword rules), 0 USD per call; spend limit 0 USD "
        "per system (any cost aborts the run)",
    )
    problems = self_check(runs)
    if problems:
        print("SELF-CHECK FAILED:\n" + "\n".join(problems), file=sys.stderr)
        return 1
    print("self-check: ok (ideal fake clean and correct; naive fake triggers every unsafe event)")
    return 0


SYSTEMS: dict[str, Callable[[], System]] = {
    "proposed": proposed_system,
    "baseline": baseline_llm_only_system,
}
PROVIDERS = ("stub", "openai", "compat")


class ProviderChoice(NamedTuple):
    factory: ProviderFactory  # the provider of each case run
    assumptions: str  # the cost line of the report
    model: str


def provider_factory_for(provider: str, budget_usd: Decimal) -> ProviderChoice:
    """The provider of each case run, the cost line of the report and the model.

    Raises ``ValueError`` when the provider is not configured, before any case runs (the
    message names keys, never values).
    """
    if provider == "openai":
        from openai import OpenAIError

        from bankagent.interpret.openai_provider import MODEL, OpenAIProvider

        try:
            OpenAIProvider()  # makes no call: a missing key fails here, not in the first case
        except OpenAIError as exc:
            raise ValueError("OPENAI_API_KEY is not set") from exc

        def openai_factory(case: EvalCase) -> LLMProvider:
            # Injected LLM faults stay simulated (0 USD), so both systems see the same fault.
            if any(f in FAULTS_BY_INJECTION for f in case.fault_injections):
                return stub_provider_for(case)
            return OpenAIProvider()  # one budgeted provider per case run

        return ProviderChoice(
            openai_factory, "OpenAIProvider (config/pricing.yaml), real cost", MODEL
        )
    if provider == "compat":
        from bankagent.interpret.compat_provider import from_env

        # One shared provider, so its daily call limit holds across every case run; the
        # suite's spend guard still stops each system at its budget.
        shared = from_env(os.environ, spend_limit_usd=budget_usd * 2 if budget_usd else None)

        def compat_factory(case: EvalCase) -> LLMProvider:
            if any(f in FAULTS_BY_INJECTION for f in case.fault_injections):
                return stub_provider_for(case)
            return shared

        return ProviderChoice(
            compat_factory,
            f"OpenAI-compatible endpoint, model {shared.model} (LLM_* variables)",
            shared.model,
        )
    return ProviderChoice(
        stub_provider_for, "StubProvider (keyword rules), 0 USD per call", STUB_MODEL
    )


def real_run(
    *,
    system_names: Sequence[str],
    cases_dir: Path,
    out_dir: Path,
    repeats: int,
    provider: str,
    budget_usd: Decimal,
) -> int:
    """Real systems on real Task 8 tools (fresh ops store per case run).

    With ``--provider stub`` it costs 0 USD (the baseline cannot act on the keyword stub, the
    proposed agent can). ``--provider openai`` spends real money: the owner approves the run
    and its estimate first (``eval/preregistration.md`` section 10). Never the held-out set:
    Task 27 opens it explicitly.
    """
    bank = load_bank()
    cases = load_cases(cases_dir)
    problems = [p for case in cases for p in reference_problems(case, bank)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    systems = [SYSTEMS[name]() for name in system_names]
    try:
        provider_factory, assumptions, _ = provider_factory_for(provider, budget_usd)
    except ValueError as exc:  # names keys, never values
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    now = datetime.now(UTC)
    suite_id = f"run-{now:%Y%m%dT%H%M%SZ}"
    with tempfile.TemporaryDirectory(prefix="bankagent-eval-") as workdir:
        traces = run_suite(
            systems,
            cases,
            suite_id=suite_id,
            repeats=repeats,
            budget_usd_per_system=budget_usd,
            config=RunConfig(
                backend_factory=FixtureBackendFactory(Path(workdir)),
                provider_factory=provider_factory,
            ),
        )
    write_outputs(
        traces,
        bank,
        out_dir=out_dir,
        suite_id=suite_id,
        generated_at=now,
        cases_dir=cases_dir,
        cases=cases,
        repeats=repeats,
        simulated=False,
        cost_assumptions=f"{assumptions}; spend limit {budget_usd} USD per system",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluation harness (Task 12).")
    sub = parser.add_subparsers(dest="command", required=True)
    smoke_parser = sub.add_parser("smoke", help="dev cases, scripted fakes, StubProvider, 0 USD")
    smoke_parser.add_argument("--cases", type=Path, default=DEV_DIR)
    smoke_parser.add_argument("--out", type=Path, default=RUNS_DIR / "smoke")
    smoke_parser.add_argument("--repeats", type=int, default=3)
    run_parser = sub.add_parser("run", help="real systems on real tools (dev cases by default)")
    run_parser.add_argument("--system", action="append", choices=sorted(SYSTEMS), required=True)
    run_parser.add_argument("--cases", type=Path, default=DEV_DIR)
    run_parser.add_argument("--out", type=Path, default=RUNS_DIR / "dev")
    run_parser.add_argument("--repeats", type=int, default=1)
    run_parser.add_argument("--provider", choices=PROVIDERS, default="stub")
    run_parser.add_argument("--budget-usd", type=Decimal, default=Decimal("0"))
    args = parser.parse_args(argv)
    if args.command == "smoke":
        return smoke(args.cases.resolve(), args.out, args.repeats)
    if args.command == "run":
        return real_run(
            system_names=args.system,
            cases_dir=args.cases.resolve(),
            out_dir=args.out,
            repeats=args.repeats,
            provider=args.provider,
            budget_usd=args.budget_usd,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
