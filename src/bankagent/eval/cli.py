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
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bankagent.contracts.enums import SystemVariant, UnsafeEvent
from bankagent.eval.bank import load_bank
from bankagent.eval.cases import DEV_DIR, ROOT, case_set_sha256, load_cases, reference_problems
from bankagent.eval.fake import Behavior, ScriptedFakeSystem
from bankagent.eval.gates import GATES_FILE, evaluate, load_gates
from bankagent.eval.metrics import system_metrics
from bankagent.eval.report import ReportContext, render
from bankagent.eval.runner import RunConfig, run_suite, stub_provider_for
from bankagent.eval.scorer import ScoredRun, score

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
    runs = [score(trace, bank) for trace in traces]
    metrics = {
        variant: system_metrics([r for r in runs if r.result.system == variant], bank)
        for variant in (SystemVariant.PROPOSED, SystemVariant.BASELINE_LLM_ONLY)
    }
    gates = load_gates(GATES_FILE)
    gate_results = evaluate(gates, metrics)
    report = render(
        ReportContext(
            suite_id=suite_id,
            generated_at=now.isoformat(timespec="seconds"),
            cases_dir=cases_dir.relative_to(ROOT).as_posix()
            if cases_dir.is_relative_to(ROOT)
            else str(cases_dir),
            case_set_sha256=case_set_sha256(cases),
            n_cases=len(cases),
            repeats=repeats,
            simulated=True,
            cost_assumptions="StubProvider (keyword rules), 0 USD per call; spend limit 0 USD "
            "per system (any cost aborts the run)",
        ),
        metrics,
        runs,
        gates,
        gate_results,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
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
    problems = self_check(runs)
    if problems:
        print("SELF-CHECK FAILED:\n" + "\n".join(problems), file=sys.stderr)
        return 1
    print("self-check: ok (ideal fake clean and correct; naive fake triggers every unsafe event)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluation harness (Task 12).")
    sub = parser.add_subparsers(dest="command", required=True)
    smoke_parser = sub.add_parser("smoke", help="dev cases, scripted fakes, StubProvider, 0 USD")
    smoke_parser.add_argument("--cases", type=Path, default=DEV_DIR)
    smoke_parser.add_argument("--out", type=Path, default=RUNS_DIR / "smoke")
    smoke_parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    if args.command == "smoke":
        return smoke(args.cases.resolve(), args.out, args.repeats)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
