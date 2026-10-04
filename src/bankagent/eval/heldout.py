"""Check, seal and sample the held-out set (Task 17).

The held-out cases live in ``HELDOUT_DIR``, outside the repository; only
``eval/heldout_manifest.sha256`` is versioned. These commands are the last step of writing the
set, before it is sealed:

- ``check``: every file is a valid ``EvalCase`` that references the fixture bank, and the set
  meets the held-out plan of ``eval/gates.yaml`` (size, automatable and escalating cases,
  dialect shares, every category).
- ``seal``: ``check``, then write the manifest (one ``sha256  file name`` line per case).
- ``verify``: compare the files with the manifest, bytes only. Task 27 runs it before it opens
  the set.
- ``sample``: blind sheets (no labels) of 20% of each author's cases for a second annotator.
- ``kappa``: Cohen's kappa between the authors' labels and the filled sheets.

They print counts, file names and field names, never an utterance or a label value, so running
them shows nobody (and no coding agent) what a case says.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import sys
from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from bankagent.contracts.enums import Dialect, EvalCategory, EvalSplit, Outcome
from bankagent.contracts.evaluation import EvalCase
from bankagent.eval.bank import BankIndex, load_bank
from bankagent.eval.cases import ATTACK_CATEGORIES, DEV_DIR, ROOT, load_cases
from bankagent.eval.gates import GATES_FILE, HeldoutPlan, load_gates
from bankagent.interpret.keywords import normalize

MANIFEST_FILE = ROOT / "eval" / "heldout_manifest.sha256"
QUOTA_DIALECTS: tuple[Dialect, ...] = (
    Dialect.ES_MX,
    Dialect.ES_CO,
    Dialect.ES_AR,
    Dialect.PT_BR,
)
SAMPLE_SHARE = 0.2
SAMPLE_SEED = "t17"
# What a second annotator sees: the scenario, without the author's labels, category or notes.
SHEET_FIELDS: tuple[str, ...] = (
    "case_id",
    "language",
    "dialect",
    "customer_id",
    "turns",
    "facts",
    "fault_injections",
)
_SHEET_FOOTER = (
    "# Second annotator: fill in the three lines below without opening the original case.\n"
    "# expected_outcome is what a careful human agent following the policy would do: one of\n"
    "# {outcomes}.\n"
    "annotator: null\n"
    "expected_outcome: null\n"
    "requires_escalation: null\n"
)


@dataclass(frozen=True, slots=True)
class CaseFile:
    name: str
    case: EvalCase


@dataclass(frozen=True, slots=True)
class Annotation:
    case_id: str
    annotator: str
    expected_outcome: Outcome
    requires_escalation: bool


@dataclass(frozen=True, slots=True)
class Agreement:
    n: int
    agreed: int
    kappa: float | None  # None when both annotators used one and the same label throughout


# ---------------------------------------------------------------------------
# Reading and checking
# ---------------------------------------------------------------------------


def _inside_repo(path: Path) -> bool:
    return path.resolve().is_relative_to(ROOT)


def read_case_files(directory: Path) -> tuple[list[CaseFile], list[str]]:
    """The files that parse, and one problem per file that does not (no input values)."""
    files: list[CaseFile] = []
    problems: list[str] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            with path.open(encoding="utf-8") as handle:
                raw = yaml.safe_load(handle)
            case = EvalCase.model_validate(raw)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" near line {mark.line + 1}" if mark is not None else ""
            problems.append(f"{path.name}: not valid YAML{where}")
        except ValidationError as exc:
            for error in exc.errors(include_input=False, include_url=False, include_context=False):
                location = ".".join(str(part) for part in error["loc"]) or "case"
                problems.append(f"{path.name}: {location}: {error['msg']}")
        else:
            files.append(CaseFile(name=path.name, case=case))
    return files, problems


def person(name: str) -> str:
    """An author or annotator as one key: "Juan José", "juan-jose" and "JuanJose" are one person."""
    return re.sub(r"[^a-z0-9]", "", normalize(name))


def _messages(case: EvalCase) -> tuple[str, ...]:
    """The messages as letters and digits only (no case, accents, punctuation or spaces), so a
    copy stays a copy: "2,450" and "2450" are one amount."""
    return tuple("".join(re.findall(r"[a-z0-9]+", normalize(turn.text))) for turn in case.turns)


def _conversation(case: EvalCase) -> tuple[str, tuple[str, ...]]:
    return case.customer_id, _messages(case)


def case_problems(
    files: Sequence[CaseFile], bank: BankIndex, dev_cases: Sequence[EvalCase] = ()
) -> list[str]:
    """Problems of single cases: id and file name, split, fixture references, copies."""
    problems: list[str] = []
    # A dev message is one the agent was tuned on, whoever the customer is.
    dev_messages = {_messages(case) for case in dev_cases}
    seen_ids: dict[str, str] = {}
    seen_conversations: dict[tuple[str, tuple[str, ...]], str] = {}
    for item in files:
        case = item.case
        author = person(case.author)
        # The id is on the blind sheet and in the versioned manifest: one that names the
        # category would tell the second annotator, and anyone reading the manifest, the label.
        if not re.fullmatch(rf"heldout-{re.escape(author)}-[0-9]+", case.case_id):
            problems.append(f"{item.name}: case_id must be heldout-{author}-<number>")
        if item.name != f"{case.case_id}.yaml":
            problems.append(f"{item.name}: the file name must be <case_id>.yaml")
        if case.split != EvalSplit.HELDOUT:
            problems.append(f"{item.name}: split must be {EvalSplit.HELDOUT.value}")
        if case.case_id in seen_ids:
            problems.append(f"{item.name}: same case_id as {seen_ids[case.case_id]}")
        seen_ids.setdefault(case.case_id, item.name)
        if case.customer_id not in bank.customers:
            problems.append(f"{item.name}: customer_id is not in the fixture bank")
        target = case.facts.target_transaction_id
        if target is not None and bank.owner_of(target) != case.customer_id:
            problems.append(
                f"{item.name}: facts.target_transaction_id is not a transaction of customer_id"
            )
        conversation = _conversation(case)
        if _messages(case) in dev_messages:
            problems.append(f"{item.name}: same messages as a dev case")
        if conversation in seen_conversations:
            problems.append(
                f"{item.name}: same customer and messages as {seen_conversations[conversation]}"
            )
        seen_conversations.setdefault(conversation, item.name)
    return problems


def is_automatable(case: EvalCase) -> bool:
    return Outcome.AUTOMATED_RESOLUTION in case.acceptable_outcomes


def plan_problems(cases: Sequence[EvalCase], plan: HeldoutPlan) -> list[str]:
    """Where the set falls short of the held-out plan (``eval/gates.yaml``, pre-registration)."""
    problems: list[str] = []
    n = len(cases)
    if n < plan.min_cases:
        problems.append(f"{n} cases, the plan needs at least {plan.min_cases}")
    automatable = sum(is_automatable(case) for case in cases)
    if automatable < plan.min_automatable_cases:
        problems.append(
            f"{automatable} automatable cases, the plan needs at least {plan.min_automatable_cases}"
        )
    escalating = sum(case.requires_escalation for case in cases)
    if escalating < plan.min_requires_escalation_cases:
        problems.append(
            f"{escalating} cases that require escalation, the plan needs at least "
            f"{plan.min_requires_escalation_cases}"
        )
    dialects = Counter(case.dialect for case in cases)
    for dialect in QUOTA_DIALECTS:
        share = dialects[dialect] / n if n else 0.0
        if share < plan.min_dialect_share:
            problems.append(
                f"{dialect.value} is {dialects[dialect]} of {n} cases ({share:.1%}), the plan "
                f"needs at least {plan.min_dialect_share:.0%}"
            )
    present = {case.category for case in cases}
    problems.extend(
        f"no case of category {category.value}"
        for category in EvalCategory
        if category not in present
    )
    return problems


def composition(cases: Sequence[EvalCase]) -> list[str]:
    """Counts only: what the set is made of."""
    n = len(cases)

    def table(counter: Counter[str]) -> str:
        return ", ".join(f"{key} {count}" for key, count in sorted(counter.items())) or "-"

    dialects = Counter(case.dialect.value for case in cases)
    shares = ", ".join(
        f"{key} {count} ({count / n:.0%})" for key, count in sorted(dialects.items())
    )
    return [
        f"cases: {n}",
        f"automatable (automated_resolution acceptable): {sum(map(is_automatable, cases))}",
        f"require escalation: {sum(case.requires_escalation for case in cases)}",
        f"attacks: {sum(case.category in ATTACK_CATEGORIES for case in cases)}",
        f"out of scope: {sum(not case.in_scope for case in cases)}",
        f"dialects: {shares or '-'}",
        f"categories: {table(Counter(case.category.value for case in cases))}",
        f"authors: {table(Counter(case.author for case in cases))}",
        f"provenance: {table(Counter(case.provenance.value for case in cases))}",
    ]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def manifest_lines(directory: Path) -> list[str]:
    """``sha256  file name`` per case file, sorted by name (the format ``sha256sum -c`` reads)."""
    return [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(directory.glob("*.yaml"))
    ]


def parse_manifest(lines: Iterable[str]) -> dict[str, str]:
    """File name -> sha256."""
    return {name: digest for digest, _, name in (line.partition("  ") for line in lines) if name}


def read_manifest(manifest: Path) -> dict[str, str]:
    return parse_manifest(manifest.read_text(encoding="utf-8").splitlines())


def verify_manifest(directory: Path, manifest: Path) -> list[str]:
    """Files that are missing, changed or not listed. Reads bytes, never parses a case."""
    expected = read_manifest(manifest)
    actual = parse_manifest(manifest_lines(directory))
    problems = [
        f"{name}: listed in the manifest, missing" for name in expected if name not in actual
    ]
    problems.extend(
        f"{name}: content differs from the manifest"
        for name, digest in expected.items()
        if name in actual and actual[name] != digest
    )
    problems.extend(f"{name}: not in the manifest" for name in actual if name not in expected)
    return sorted(problems)


# ---------------------------------------------------------------------------
# Second annotator: blind sample and agreement
# ---------------------------------------------------------------------------


def blind_sample(
    cases: Sequence[EvalCase], *, share: float = SAMPLE_SHARE, seed: str = SAMPLE_SEED
) -> dict[str, list[EvalCase]]:
    """For each author (``person`` key), ``share`` of their cases (rounded up), by a seeded hash."""
    by_author: dict[str, list[EvalCase]] = {}
    for case in cases:
        by_author.setdefault(person(case.author), []).append(case)

    def rank(case: EvalCase) -> str:
        return hashlib.sha256(f"{seed}:{case.case_id}".encode()).hexdigest()

    return {
        author: sorted(
            sorted(own, key=rank)[: math.ceil(share * len(own))], key=lambda c: c.case_id
        )
        for author, own in sorted(by_author.items())
    }


def blind_sheet(case: EvalCase) -> str:
    """The scenario of a case as YAML, followed by the empty lines the annotator fills in."""
    scenario = case.model_dump(mode="json", include=set(SHEET_FIELDS))
    body = yaml.safe_dump(
        {key: scenario[key] for key in SHEET_FIELDS},
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    )
    outcomes = ", ".join(outcome.value for outcome in Outcome)
    return body + _SHEET_FOOTER.format(outcomes=outcomes)


def sheet_folder(author: str) -> str:
    """Letters and digits only, so an author name can never lead out of ``--out``."""
    return f"cases-by-{person(author) or 'unnamed'}"


def write_sample(sample: dict[str, list[EvalCase]], out_dir: Path) -> int:
    """One folder per author, to be annotated by a teammate who is not that author."""
    written = 0
    for author, cases in sample.items():
        folder = out_dir / sheet_folder(author)
        folder.mkdir(parents=True, exist_ok=True)
        for case in cases:
            (folder / f"{case.case_id}.yaml").write_bytes(blind_sheet(case).encode("utf-8"))
            written += 1
    return written


def read_annotations(directory: Path) -> tuple[list[Annotation], list[str]]:
    annotations: list[Annotation] = []
    problems: list[str] = []
    for path in sorted(directory.rglob("*.yaml")):
        try:
            with path.open(encoding="utf-8") as handle:
                raw: Any = yaml.safe_load(handle)
            annotator = raw["annotator"]
            escalation = raw["requires_escalation"]
            if not isinstance(annotator, str) or not annotator.strip():
                raise ValueError("annotator")
            if not isinstance(escalation, bool):
                raise ValueError("requires_escalation")
            annotations.append(
                Annotation(
                    case_id=str(raw["case_id"]),
                    annotator=annotator.strip(),
                    expected_outcome=Outcome(raw["expected_outcome"]),
                    requires_escalation=escalation,
                )
            )
        except (yaml.YAMLError, KeyError, TypeError, ValueError):
            problems.append(
                f"{path.name}: fill in annotator, expected_outcome and requires_escalation"
            )
    return annotations, problems


def cohen_kappa(first: Sequence[Hashable], second: Sequence[Hashable]) -> Agreement:
    """Cohen's kappa of two annotators over the same items, in the same order."""
    if len(first) != len(second):
        raise ValueError("both annotators must label the same items")
    n = len(first)
    if n == 0:
        return Agreement(n=0, agreed=0, kappa=None)
    agreed = sum(a == b for a, b in zip(first, second, strict=True))
    observed = agreed / n
    counts_first, counts_second = Counter(first), Counter(second)
    expected = sum(counts_first[label] * counts_second[label] for label in counts_first) / (n * n)
    kappa = None if math.isclose(expected, 1.0) else (observed - expected) / (1 - expected)
    return Agreement(n=n, agreed=agreed, kappa=kappa)


def annotation_report(
    cases: Sequence[EvalCase], annotations: Sequence[Annotation]
) -> tuple[list[str], list[str]]:
    """Report lines (counts and case ids) and the problems that make the agreement invalid."""
    by_id = {case.case_id: case for case in cases}
    problems: list[str] = []
    pairs: list[tuple[EvalCase, Annotation]] = []
    seen: set[str] = set()
    for annotation in annotations:
        case = by_id.get(annotation.case_id)
        if case is None:
            problems.append(f"{annotation.case_id}: not a case of the held-out set")
        elif annotation.case_id in seen:
            problems.append(f"{annotation.case_id}: annotated more than once")
        elif person(annotation.annotator) == person(case.author):
            problems.append(f"{annotation.case_id}: annotated by its own author")
        else:
            pairs.append((case, annotation))
        seen.add(annotation.case_id)
    needed = math.ceil(SAMPLE_SHARE * len(cases))
    if len(pairs) < needed:
        problems.append(f"{len(pairs)} annotated cases, 20% of {len(cases)} needs {needed}")
    lines = [f"second-annotator sample: {len(pairs)} of {len(cases)} cases"]
    labels: tuple[tuple[str, list[Hashable], list[Hashable]], ...] = (
        (
            "expected_outcome",
            [case.expected_outcome for case, _ in pairs],
            [annotation.expected_outcome for _, annotation in pairs],
        ),
        (
            "requires_escalation",
            [case.requires_escalation for case, _ in pairs],
            [annotation.requires_escalation for _, annotation in pairs],
        ),
    )
    for name, authors, annotators in labels:
        agreement = cohen_kappa(authors, annotators)
        kappa = (
            "not defined (one label only)" if agreement.kappa is None else f"{agreement.kappa:.3f}"
        )
        lines.append(f"{name}: agreement {agreement.agreed}/{agreement.n}, Cohen's kappa {kappa}")
    disagreements = sorted(
        case.case_id
        for case, annotation in pairs
        if case.expected_outcome != annotation.expected_outcome
        or case.requires_escalation != annotation.requires_escalation
    )
    lines.append("disagreements to resolve: " + (", ".join(disagreements) or "none"))
    return lines, problems


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _fail(problems: Sequence[str]) -> int:
    print("\n".join(problems), file=sys.stderr)
    print(f"FAILED: {len(problems)} problem(s)", file=sys.stderr)
    return 1


def _checked(directory: Path, *, partial: bool) -> tuple[list[CaseFile], list[str]]:
    files, problems = read_case_files(directory)
    if not files and not problems:
        return files, [f"no *.yaml cases in {directory}"]
    problems.extend(case_problems(files, load_bank(), load_cases(DEV_DIR)))
    cases = [item.case for item in files]
    print("\n".join(composition(cases)))
    if not partial:
        problems.extend(plan_problems(cases, load_gates(GATES_FILE).heldout))
    return files, problems


def check(directory: Path, *, partial: bool) -> int:
    _, problems = _checked(directory, partial=partial)
    if problems:
        return _fail(problems)
    if partial:
        print("check: ok (one author's part: the plan of the whole set was not checked)")
    else:
        print("check: ok")
    return 0


def seal(directory: Path, manifest: Path) -> int:
    _, problems = _checked(directory, partial=False)
    if problems:
        return _fail(problems)
    lines = manifest_lines(directory)
    manifest.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    print(f"sealed: {len(lines)} files listed in {manifest.as_posix()}")
    return 0


def verify(directory: Path, manifest: Path) -> int:
    if not manifest.is_file():
        return _fail([f"no manifest at {manifest.as_posix()}: the set is not sealed"])
    problems = verify_manifest(directory, manifest)
    if problems:
        return _fail(problems)
    print(f"verify: ok, {len(read_manifest(manifest))} files match {manifest.as_posix()}")
    return 0


def sample(directory: Path, out_dir: Path) -> int:
    if _inside_repo(out_dir):
        return _fail(["the sheets quote held-out cases: --out must be outside the repository"])
    files, problems = read_case_files(directory)
    if problems:
        return _fail(problems)
    chosen = blind_sample([item.case for item in files])
    written = write_sample(chosen, out_dir)
    for author, cases in chosen.items():
        print(f"{sheet_folder(author)}: {len(cases)} sheets, for an annotator other than {author}")
    print(f"sample: {written} sheets of {len(files)} cases in {out_dir}")
    return 0


def kappa(directory: Path, annotations_dir: Path) -> int:
    files, problems = read_case_files(directory)
    annotations, annotation_problems = read_annotations(annotations_dir)
    lines, report_problems = annotation_report([item.case for item in files], annotations)
    problems = [*problems, *annotation_problems, *report_problems]
    if problems:
        return _fail(problems)
    print("\n".join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Held-out set: check, seal, sample (Task 17).")
    parser.add_argument(
        "--cases", type=Path, default=None, help="folder of the cases (default: HELDOUT_DIR)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check_parser = sub.add_parser("check", help="validate the cases and the held-out plan")
    check_parser.add_argument(
        "--partial", action="store_true", help="one author's part: skip the plan of the whole set"
    )
    seal_parser = sub.add_parser("seal", help="check, then write the sha256 manifest")
    verify_parser = sub.add_parser("verify", help="compare the files with the manifest")
    for manifest_parser in (seal_parser, verify_parser):
        manifest_parser.add_argument("--manifest", type=Path, default=MANIFEST_FILE)
    sample_parser = sub.add_parser("sample", help="blind sheets for the second annotator")
    sample_parser.add_argument("--out", type=Path, required=True)
    kappa_parser = sub.add_parser("kappa", help="Cohen's kappa against the filled sheets")
    kappa_parser.add_argument("--annotations", type=Path, required=True)
    args = parser.parse_args(argv)

    directory = args.cases or (Path(env) if (env := os.environ.get("HELDOUT_DIR")) else None)
    if directory is None:
        print("ERROR: set HELDOUT_DIR in .env or pass --cases", file=sys.stderr)
        return 2
    if not directory.is_dir():
        print("ERROR: the cases folder does not exist", file=sys.stderr)
        return 2
    if _inside_repo(directory):
        print("ERROR: held-out cases must live outside the repository", file=sys.stderr)
        return 2
    if args.command == "check":
        return check(directory, partial=args.partial)
    if args.command == "seal":
        return seal(directory, args.manifest)
    if args.command == "verify":
        return verify(directory, args.manifest)
    if args.command == "sample":
        return sample(directory, args.out)
    return kappa(directory, args.annotations)


if __name__ == "__main__":
    raise SystemExit(main())
