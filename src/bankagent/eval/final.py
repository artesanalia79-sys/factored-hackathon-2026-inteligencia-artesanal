"""Final evaluation (Task 27): both systems on the sealed held-out set.

``uv run poe eval-full`` without ``--approved-by`` only prints what would run and what it may
cost, and reads no case. With it, the command:

1. refuses gates that are not frozen, the stub provider, and a held-out folder that does not
   match ``eval/heldout_manifest.sha256`` (bytes only: nothing is parsed before that);
2. opens the set (the only caller of ``load_cases(allow_heldout=True)``) and runs ``proposed``
   and ``baseline_llm_only`` on every case, ``heldout.repeats`` times, on the real tools with a
   fresh ops store per case run, each system under half of ``budget_usd_total``;
3. writes ``eval/reports/<suite>/`` for the repository (``report.md``, ``results.jsonl``,
   ``unsafe_reasons.jsonl``, ``run_record.json``) and ``eval/runs/<suite>/transcripts.jsonl``,
   which quotes the conversations and is git-ignored.

A system that reaches its budget stops there; what it measured is kept and the report says so.
A second run of the held-out set is numbered: it is reported with the first, not instead of it.

A rehearsal runs the same code on other cases (``--cases eval/dev``, any provider). Everything
it writes goes to ``eval/runs/`` and its report says REHEARSAL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bankagent.contracts.enums import EvalSplit, StepOutcome, SystemVariant
from bankagent.eval.backend import FixtureBackendFactory
from bankagent.eval.bank import load_bank
from bankagent.eval.cases import (
    DEV_DIR,
    ROOT,
    case_set_sha256,
    is_heldout_path,
    load_cases,
    reference_problems,
)
from bankagent.eval.cli import PROVIDERS, RUNS_DIR, SYSTEMS, provider_factory_for, write_outputs
from bankagent.eval.gates import GATES_FILE, GatesConfig, load_gates
from bankagent.eval.heldout import MANIFEST_FILE, read_manifest, verify_manifest
from bankagent.eval.runner import (
    BudgetExceededError,
    CaseTrace,
    ProviderFactory,
    RunConfig,
    run_suite,
)
from bankagent.eval.scorer import ScoredRun

REPORTS_DIR = ROOT / "eval" / "reports"
SYSTEM_ORDER = ("proposed", "baseline")
NOT_RUN = 2  # nothing ran: a refusal, or the plan printed for the owner's approval


def _refuse(message: str) -> int:
    print(f"NOT RUN: {message}", file=sys.stderr)
    return NOT_RUN


def _git(*args: str) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    # Fixed arguments from this module, no shell.
    done = subprocess.run(  # noqa: S603
        [git, *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return done.stdout.strip() if done.returncode == 0 else None


def _in_manifest(cases_dir: Path, manifest: Path) -> bool:
    """A file of the folder is listed in the manifest: it is the sealed set, whatever its path."""
    if not manifest.is_file():
        return False
    listed = read_manifest(manifest)
    return any(path.name in listed for path in cases_dir.glob("*.yaml"))


def cost_bar_usd(gates: GatesConfig) -> Decimal | None:
    """The cost-per-run gate (G5b), used to size the estimate."""
    for gate in gates.gates:
        if gate.metric == "cost_per_attempted_usd" and gate.threshold is not None:
            return Decimal(str(gate.threshold))
    return None


def plan_lines(
    *, kind: str, n_cases: int, repeats: int, systems: Sequence[str], provider: str, budget: Decimal
) -> list[str]:
    runs = n_cases * repeats
    return [
        f"{kind}: {n_cases} cases x {repeats} repeats = {runs} runs per system "
        f"({', '.join(systems)}), provider {provider}",
        f"hard cap: {budget} USD per system (a system that reaches it stops there)",
    ]


def system_record(
    variant: SystemVariant, runs: Sequence[ScoredRun], expected: int
) -> dict[str, Any]:
    own = [run for run in runs if run.result.system == variant]
    records = [record for run in own for record in run.trace.records]
    return {
        "runs": len(own),
        "expected_runs": expected,
        "stopped_by_budget": any(run.trace.ended_by == "budget" for run in own),
        "crashed_runs": sum(run.trace.ended_by == "error" for run in own),
        "llm_fallback_steps": sum(record.outcome == StepOutcome.FALLBACK for record in records),
        "cost_usd": str(sum((run.result.cost_usd_total for run in own), Decimal("0"))),
        "models": sorted({run.result.versions.get("models", "none") for run in own}),
        "prompt_versions": sorted(
            {run.result.versions.get("prompt_versions", "none") for run in own}
        ),
    }


def record_section(record: dict[str, Any]) -> list[str]:
    """The run record as the last section of the report."""
    lines = ["## Run record", ""]
    if record["kind"] == "heldout":
        number = record["heldout_run_number"]
        lines.append(f"- Held-out run {number}, manifest sha256 `{record['manifest_sha256']}`.")
        if number > 1:
            lines.append(
                f"- **Re-run.** The set was already opened {number - 1} time(s): this run is "
                "reported with the first one, not instead of it (pre-registration, section 11)."
            )
    else:
        lines.append("- **REHEARSAL**: not the held-out set, not a result.")
    commit = record["git_commit"] or "unknown"
    dirty = " with uncommitted changes" if record["git_dirty"] else ""
    lines += [
        f"- Code: commit `{commit}`{dirty}.",
        f"- Provider `{record['provider']}`, {record['budget_usd_per_system']} USD cap per "
        f"system, approved by {record['approved_by'] or 'nobody (0 USD run)'}.",
        f"- Started {record['started_at']}, finished {record['finished_at']}.",
        "",
        "| System | Runs | Stopped by budget | Crashed runs | LLM fallback steps | Cost (USD) | "
        "Models | Prompt versions |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, info in record["systems"].items():
        lines.append(
            f"| {name} | {info['runs']}/{info['expected_runs']} | "
            f"{'yes' if info['stopped_by_budget'] else 'no'} | {info['crashed_runs']} | "
            f"{info['llm_fallback_steps']} | {info['cost_usd']} | {', '.join(info['models'])} | "
            f"{', '.join(info['prompt_versions'])} |"
        )
    lines += [
        "",
        "A fallback step is a call the model did not answer (the proposed agent then reads the "
        "message with keyword rules). Runs below the expected number, crashes and fallbacks are "
        "deviations to explain next to the gates.",
        "",
    ]
    return lines


def write_transcripts(traces: Sequence[CaseTrace], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for trace in traces:
            row = {
                "case_id": trace.case.case_id,
                "system": trace.variant.value,
                "repeat_index": trace.repeat_index,
                "ended_by": trace.ended_by,
                "error": trace.error,
                "turns": [
                    {"user": turn.user_text, "agent": turn.reply_text} for turn in trace.turns
                ],
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_final(
    *,
    cases_dir: Path,
    manifest: Path = MANIFEST_FILE,
    provider: str = "openai",
    system_names: Sequence[str] = SYSTEM_ORDER,
    repeats: int | None = None,
    approved_by: str | None = None,
    budget_usd: Decimal | None = None,
    reports_root: Path = REPORTS_DIR,
    runs_root: Path = RUNS_DIR,
    gates_file: Path = GATES_FILE,
    provider_factory: ProviderFactory | None = None,
) -> int:
    """``provider_factory`` replaces the provider built from its name (tests only)."""
    gates = load_gates(gates_file)
    heldout = is_heldout_path(cases_dir) or _in_manifest(cases_dir, manifest)
    repeats = repeats or gates.heldout.repeats
    cap = Decimal(str(gates.budget_usd_total)) / 2
    budget = Decimal("0") if provider == "stub" else cap
    if budget_usd is not None and provider != "stub":
        if heldout:
            return _refuse(
                "--budget-usd is for rehearsals: the held-out run uses the registered cap"
            )
        if not Decimal("0") < budget_usd <= cap:
            return _refuse(f"--budget-usd must be above 0 and at most {cap} USD")
        budget = budget_usd
    kind = "heldout" if heldout else "rehearsal"

    if heldout:
        if gates.status != "frozen":
            return _refuse("eval/gates.yaml is not frozen")
        if provider == "stub":
            return _refuse(
                "the registered run uses the real model; rehearse on the dev cases with "
                f"--cases {DEV_DIR.relative_to(ROOT).as_posix()}"
            )
        if not manifest.is_file():
            return _refuse(f"no manifest at {manifest.as_posix()}: seal the set first")
        problems = verify_manifest(cases_dir, manifest)
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return _refuse("the held-out folder does not match the manifest")
        n_files = len(read_manifest(manifest))
    else:
        n_files = len(list(cases_dir.glob("*.yaml")))

    print(
        "\n".join(
            plan_lines(
                kind=kind,
                n_cases=n_files,
                repeats=repeats,
                systems=system_names,
                provider=provider,
                budget=budget,
            )
        )
    )
    bar = cost_bar_usd(gates)
    if bar is not None and provider != "stub":
        print(
            f"at the cost bar of {bar} USD per run: {bar * n_files * repeats} USD per system; "
            "replace it with the cost per run measured on the dev cases"
        )
    if provider != "stub" and not approved_by:
        return _refuse(
            "a paid run needs the owner's approval: log the estimate in "
            "docs/decision_ledger.md, then pass --approved-by <owner>"
        )

    bank = load_bank()
    cases = load_cases(cases_dir, allow_heldout=heldout)
    if not heldout and any(case.split == EvalSplit.HELDOUT for case in cases):
        return _refuse(
            "these are held-out cases outside HELDOUT_DIR and without a manifest: seal the "
            "set and set HELDOUT_DIR first"
        )
    problems = [p for case in cases for p in reference_problems(case, bank)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if provider_factory is None:
        try:
            provider_factory, assumptions = provider_factory_for(provider, budget)
        except ValueError as exc:  # names keys, never values
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        assumptions = f"injected provider factory (declared as {provider})"

    started = datetime.now(UTC)
    earlier_runs = len(list(reports_root.glob("heldout-*"))) if reports_root.is_dir() else 0
    number = f"{earlier_runs + 1}-" if heldout else ""
    suite_id = f"{kind}-{number}{started:%Y%m%dT%H%M%SZ}"
    report_dir = (reports_root if heldout else runs_root) / suite_id
    systems = {name: SYSTEMS[name]() for name in system_names}
    traces: list[CaseTrace] = []
    with tempfile.TemporaryDirectory(prefix="bankagent-final-") as workdir:
        config = RunConfig(
            backend_factory=FixtureBackendFactory(Path(workdir)), provider_factory=provider_factory
        )
        for name, system in systems.items():
            try:
                run_suite(
                    [system],
                    cases,
                    suite_id=suite_id,
                    repeats=repeats,
                    budget_usd_per_system=budget,
                    config=config,
                    on_trace=traces.append,
                )
            except BudgetExceededError as exc:
                print(f"{name} stopped by its budget: {exc}", file=sys.stderr)
    finished = datetime.now(UTC)

    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest() if heldout else None
    runs = write_outputs(
        traces,
        bank,
        out_dir=report_dir,
        suite_id=suite_id,
        generated_at=finished,
        cases_dir=cases_dir,
        cases=cases,
        repeats=repeats,
        simulated=False,
        cost_assumptions=f"{assumptions}; spend limit {budget} USD per system",
        workload=f"sealed held-out set (manifest sha256 {manifest_sha[:16]})"
        if manifest_sha
        else None,
    )
    record: dict[str, Any] = {
        "suite_id": suite_id,
        "kind": kind,
        "heldout_run_number": earlier_runs + 1 if heldout else None,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "manifest_sha256": manifest_sha,
        "case_set_sha256": case_set_sha256(cases),
        "n_cases": len(cases),
        "repeats": repeats,
        "provider": provider,
        "approved_by": approved_by,
        "budget_usd_per_system": str(budget),
        "systems": {
            system.variant.value: system_record(system.variant, runs, len(cases) * repeats)
            for system in systems.values()
        },
    }
    (report_dir / "run_record.json").write_bytes(
        (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    )
    report = report_dir / "report.md"
    report.write_bytes(
        (report.read_text(encoding="utf-8") + "\n".join(record_section(record))).encode("utf-8")
    )
    transcripts = runs_root / suite_id / "transcripts.jsonl"
    write_transcripts(traces, transcripts)
    print(f"run record: {(report_dir / 'run_record.json').as_posix()}")
    print(f"transcripts (git-ignored, they quote the conversations): {transcripts.as_posix()}")
    incomplete = [
        n for n, info in record["systems"].items() if info["runs"] < info["expected_runs"]
    ]
    if incomplete:
        print(f"INCOMPLETE: {', '.join(incomplete)} ran fewer runs than planned", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Final evaluation on the held-out set (Task 27).")
    parser.add_argument(
        "--cases", type=Path, default=None, help="folder of the cases (default: HELDOUT_DIR)"
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST_FILE)
    parser.add_argument("--provider", choices=PROVIDERS, default="openai")
    parser.add_argument("--system", action="append", choices=sorted(SYSTEMS), default=None)
    parser.add_argument("--repeats", type=int, default=None, help="default: gates.yaml")
    parser.add_argument(
        "--approved-by", default=None, help="the owner who approved the logged cost estimate"
    )
    parser.add_argument(
        "--budget-usd", type=Decimal, default=None, help="rehearsals: a lower cap per system"
    )
    args = parser.parse_args(argv)
    directory = args.cases or (Path(env) if (env := os.environ.get("HELDOUT_DIR")) else None)
    if directory is None:
        return _refuse("set HELDOUT_DIR in .env or pass --cases")
    if not directory.is_dir():
        return _refuse("the cases folder does not exist")
    return run_final(
        cases_dir=directory.resolve(),
        manifest=args.manifest,
        provider=args.provider,
        system_names=[n for n in SYSTEM_ORDER if n in args.system] if args.system else SYSTEM_ORDER,
        repeats=args.repeats,
        approved_by=args.approved_by,
        budget_usd=args.budget_usd,
    )


if __name__ == "__main__":
    raise SystemExit(main())
