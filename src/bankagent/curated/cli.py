"""CLI of the curated end-to-end run (T19): `uv run poe curated-e2e`, `uv run poe curated-check`.

Usage:
    python -m bankagent.curated.cli                  # 10 cases per scenario, seed 19
    python -m bankagent.curated.cli --per-scenario 3 --no-report
    python -m bankagent.curated.cli --scenario escalate_fraud --scenario recognized
                                                     # some scenarios: no report unless --report
    python -m bankagent.curated.cli --check          # only the serving DB checks

Needs the curated serving DB (`uv run poe serving-build`). Reads no `.env`: the app is built
from an environment made here (`DATA_MODE=curated`, the keyword interpreter, a new ops store and
a signing key of its own), so the run does not depend on, or touch, the developer's settings.

Prints counts only. The conversations, which hold organizer data, go to the work folder: under
`data/` (git-ignored) by default, and never elsewhere inside the repository. The report under
`docs/evidence/` holds no value of any row.

Exit codes: 0 every case and service check passed; 1 something failed, or nothing could run;
2 the serving DB is missing, is not curated or does not fit the contract (with `--check`, also
when a row does not fit the view the runtime reads it into).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from bankagent.contracts.enums import DataMode
from bankagent.contracts.serving import SERVING_CONTRACT_VERSION
from bankagent.curated.cases import Scenario, pick_cases
from bankagent.curated.report import ReportLeak, render_report, write_transcripts
from bankagent.curated.rows import check_rows
from bankagent.curated.run import run_cases
from bankagent.policy.schema import load_policy
from bankagent.store.selection import ROOT, SERVING_DBS, ServingConfigError, open_serving_db
from bankagent.store.serving import ServingDB

DEFAULT_REPORT = ROOT / "docs" / "evidence" / "curated_e2e.md"
DEFAULT_WORK_ROOT = ROOT / "data" / "runtime" / "curated_e2e"
OK, FAILED, NOT_CURATED = 0, 1, 2


def _say(message: str) -> None:
    print(message, flush=True)


def _open(serving_db: Path) -> ServingDB | None:
    """The curated serving DB, checked like the app checks it; ``None`` (and why) otherwise."""
    env = {"DATA_MODE": DataMode.CURATED.value, "SERVING_DB_PATH": str(serving_db)}
    try:
        return open_serving_db(env)
    except (FileNotFoundError, ServingConfigError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return None


def keeps_data_local(folder: Path) -> bool:
    """Whether git cannot pick ``folder`` up: outside the repository, or under its ``data/``.

    The transcripts and the run's ops store hold organizer data, and the commit hook blocks
    ``data/`` and database files, not a text file somewhere else in the tree.
    """
    resolved = folder.resolve()
    return not resolved.is_relative_to(ROOT) or resolved.is_relative_to(ROOT / "data")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--serving-db", type=Path, default=SERVING_DBS[DataMode.CURATED])
    parser.add_argument("--check", action="store_true", help="only check the serving DB")
    parser.add_argument("--per-scenario", type=int, default=10, metavar="N")
    parser.add_argument("--seed", default="19", help="picks the cases (default: 19)")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[scenario.value for scenario in Scenario],
        help="run only this scenario (repeatable; default: all)",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        help="new folder for the ops store and the transcripts (default: under data/runtime/)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="where to write the report (default: docs/evidence/curated_e2e.md, and only for a "
        "run of every scenario)",
    )
    parser.add_argument("--no-report", action="store_true", help="do not write the report")
    args = parser.parse_args(argv)
    if args.per_scenario < 1:
        parser.error("--per-scenario must be at least 1")
    # From the caller's folder, like any path on a command line (the app reads a relative
    # SERVING_DB_PATH from the repository root).
    args.serving_db = args.serving_db.resolve()
    now = datetime.now(UTC)
    workdir = args.workdir or DEFAULT_WORK_ROOT / now.strftime("%Y%m%dT%H%M%S%fZ")
    if not args.check and not keeps_data_local(workdir):
        print(
            "ERROR: --workdir would put organizer data inside the repository: use a folder "
            "under data/ (git-ignored) or outside the repository",
            file=sys.stderr,
        )
        return FAILED

    serving = _open(args.serving_db)
    if serving is None:
        return NOT_CURATED
    _say(
        f"serving DB: data_mode={serving.data_mode()}, as_of_date={serving.as_of_date()}, "
        f"validate_serving_db: 0 problems (serving contract {SERVING_CONTRACT_VERSION})"
    )
    rows = check_rows(args.serving_db)
    _say(
        f"rows against the runtime's views: {rows.transactions:,} transactions "
        f"({rows.transaction_shapes:,} distinct shapes), {rows.cards:,} cards "
        f"({rows.card_shapes:,}): {len(rows.problems)} problems"
    )
    for problem in rows.problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    if args.check:
        return NOT_CURATED if rows.problems else OK

    policy = load_policy()
    asked = tuple(
        scenario
        for scenario in Scenario
        if args.scenario is None or scenario.value in args.scenario
    )
    cases = pick_cases(
        args.serving_db,
        policy,
        serving.as_of_date(),
        per_scenario=args.per_scenario,
        seed=args.seed,
        scenarios=asked,
    )
    if not cases:
        print("ERROR: no row of the serving DB fits any scenario asked for", file=sys.stderr)
        return FAILED
    try:
        result = run_cases(args.serving_db, workdir, policy, cases)
    except FileExistsError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return FAILED
    write_transcripts(result, workdir / "transcripts.jsonl")

    for scenario in asked:
        ran = [case for case in result.cases if case.case.scenario == scenario]
        passed = sum(case.passed for case in ran)
        note = "" if ran else "  (no row of the serving DB fits)"
        _say(f"  {scenario.value:26} {passed:>3}/{len(ran):<3} passed{note}")
    for case in result.cases:
        if not case.passed:
            _say(f"  FAILED {case.case.case_id}: {'; '.join(case.failures)}")
    for probe in result.probes:
        _say(f"  FAILED service check: {probe}")
    passed = sum(case.passed for case in result.cases)
    _say(
        f"{passed} of {len(result.cases)} cases passed in {result.seconds:.0f} s; service "
        f"checks: {'all held' if not result.probes else 'FAILED'}"
    )
    _say(f"transcripts (organizer data, never commit): {workdir / 'transcripts.jsonl'}")

    report = args.report
    if report is None and not args.no_report:
        if args.scenario is None:
            report = DEFAULT_REPORT
        else:
            # The committed evidence is a run of every scenario; part of one must not replace it.
            _say("no report: only some scenarios ran (pass --report PATH to write one)")
    if report is not None and not args.no_report:
        try:
            text = render_report(
                result,
                asked=asked,
                per_scenario=args.per_scenario,
                seed=args.seed,
                policy_version=policy.policy_version,
                run_on=now.date(),
            )
        except ReportLeak as error:
            print(f"ERROR: report not written: {error}", file=sys.stderr)
            return FAILED
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text, encoding="utf-8")
        _say(f"report (counts only): {report}")
    return OK if result.passed else FAILED


if __name__ == "__main__":
    raise SystemExit(main())
