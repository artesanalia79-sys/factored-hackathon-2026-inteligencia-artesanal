"""Held-out tooling (Task 17): checks, manifest, blind sample and Cohen's kappa.

Every case here is synthetic and written to a temporary folder; no test reads ``HELDOUT_DIR``.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from bankagent.contracts.enums import Dialect, EvalCategory, Outcome
from bankagent.contracts.evaluation import EvalCase
from bankagent.eval import heldout
from bankagent.eval.bank import load_bank
from bankagent.eval.cases import load_cases
from bankagent.eval.gates import GATES_FILE, HeldoutPlan, load_gates

ROOT = Path(__file__).resolve().parents[2]
DIALECTS = [dialect.value for dialect in heldout.QUOTA_DIALECTS]
CATEGORIES = [category.value for category in EvalCategory]
AUTHORS = ["jacobo", "juanjose", "santiago"]
SMALL_PLAN = HeldoutPlan(
    min_cases=12,
    repeats=3,
    min_automatable_cases=4,
    min_requires_escalation_cases=4,
    min_dialect_share=0.2,
)


def _raw(index: int, **overrides: Any) -> dict[str, Any]:
    """Case ``index`` of a synthetic set: categories, dialects, authors and labels cycle."""
    dialect = DIALECTS[index % len(DIALECTS)]
    escalates = index % 2 == 1
    outcome = "escalated" if escalates else "automated_resolution"
    author = overrides.get("author", AUTHORS[index % len(AUTHORS)])
    raw: dict[str, Any] = {
        "case_id": f"heldout-{heldout.person(author)}-{index:03d}",
        "split": "heldout",
        "category": CATEGORIES[index % len(CATEGORIES)],
        "language": dialect.split("-")[0],
        "dialect": dialect,
        "customer_id": "CUST-FX-001",
        "turns": [{"text": f"Mensaje sintético número {index}"}],
        "facts": {"target_transaction_id": "TXN-FX-0101", "recognizes_charge": False},
        "expected_outcome": outcome,
        "acceptable_outcomes": [outcome],
        "expected_actions": ["create_handoff" if escalates else "create_dispute"],
        "requires_escalation": escalates,
        "provenance": "cross_authored",
        "author": AUTHORS[index % len(AUTHORS)],
        "notes": "synthetic test case",
    }
    raw.update(overrides)
    return raw


def _cases(n: int) -> list[EvalCase]:
    return [EvalCase.model_validate(_raw(index)) for index in range(n)]


def _write(directory: Path, raws: list[dict[str, Any]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for raw in raws:
        body = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        (directory / f"{raw['case_id']}.yaml").write_bytes(body.encode("utf-8"))
    return directory


def _files(raws: list[dict[str, Any]]) -> list[heldout.CaseFile]:
    return [
        heldout.CaseFile(name=f"{raw['case_id']}.yaml", case=EvalCase.model_validate(raw))
        for raw in raws
    ]


# ---------------------------------------------------------------------------
# The held-out plan
# ---------------------------------------------------------------------------


def test_a_set_that_meets_the_plan_has_no_problems() -> None:
    assert heldout.plan_problems(_cases(12), SMALL_PLAN) == []


def test_the_frozen_plan_needs_the_registered_composition() -> None:
    # 88 cases, 44 automatable, 44 escalating, 22 per dialect, 8 per category.
    plan = load_gates(GATES_FILE).heldout
    assert heldout.plan_problems(_cases(88), plan) == []
    assert heldout.plan_problems(_cases(79), plan) == ["79 cases, the plan needs at least 80"]


def test_every_shortfall_of_the_plan_is_named() -> None:
    all_automatable = [
        EvalCase.model_validate(
            _raw(
                index,
                expected_outcome="automated_resolution",
                acceptable_outcomes=["automated_resolution"],
                requires_escalation=False,
            )
        )
        for index in range(12)
    ]
    assert heldout.plan_problems(all_automatable, SMALL_PLAN) == [
        "0 cases that require escalation, the plan needs at least 4"
    ]
    none_automatable = [
        EvalCase.model_validate(
            _raw(index, expected_outcome="escalated", acceptable_outcomes=["escalated"])
        )
        for index in range(12)
    ]
    assert heldout.plan_problems(none_automatable, SMALL_PLAN)[0] == (
        "0 automatable cases, the plan needs at least 4"
    )
    one_dialect = [
        EvalCase.model_validate(_raw(index, language="es", dialect="es-MX")) for index in range(12)
    ]
    assert heldout.plan_problems(one_dialect, SMALL_PLAN) == [
        f"{dialect.value} is 0 of 12 cases (0.0%), the plan needs at least 20%"
        for dialect in (Dialect.ES_CO, Dialect.ES_AR, Dialect.PT_BR)
    ]
    one_category = [EvalCase.model_validate(_raw(index, category="normal")) for index in range(12)]
    assert heldout.plan_problems(one_category, SMALL_PLAN) == [
        f"no case of category {category.value}"
        for category in EvalCategory
        if category != EvalCategory.NORMAL
    ]


def test_each_minimum_of_the_plan_is_inclusive() -> None:
    def labelled(automatable: int, escalating: int) -> list[EvalCase]:
        outcomes = [
            "automated_resolution"
            if index < automatable
            else "escalated"
            if index < automatable + escalating
            else "abstained"
            for index in range(12)
        ]
        return [
            EvalCase.model_validate(
                _raw(
                    index,
                    expected_outcome=outcome,
                    acceptable_outcomes=[outcome],
                    requires_escalation=outcome == "escalated",
                    expected_actions=[],
                )
            )
            for index, outcome in enumerate(outcomes)
        ]

    assert heldout.plan_problems(labelled(4, 4), SMALL_PLAN) == []
    assert heldout.plan_problems(labelled(3, 4), SMALL_PLAN) == [
        "3 automatable cases, the plan needs at least 4"
    ]
    assert heldout.plan_problems(labelled(4, 3), SMALL_PLAN) == [
        "3 cases that require escalation, the plan needs at least 4"
    ]


def test_an_acceptable_automated_resolution_makes_a_case_automatable() -> None:
    case = EvalCase.model_validate(
        _raw(1, acceptable_outcomes=["escalated", "automated_resolution"])
    )
    assert case.expected_outcome == Outcome.ESCALATED
    assert heldout.is_automatable(case)


# ---------------------------------------------------------------------------
# Single cases
# ---------------------------------------------------------------------------


def test_valid_cases_have_no_problems() -> None:
    files = _files([_raw(index) for index in range(4)])
    assert heldout.case_problems(files, load_bank(), load_cases()) == []


def test_each_case_problem_is_reported_with_its_file_name() -> None:
    bank = load_bank()
    dev = load_cases()
    copied = next(case for case in dev if case.case_id == "dev-normal-es-mx-001")
    raws = [
        _raw(0, split="dev"),
        _raw(1, customer_id="CUST-FX-999", facts={}),
        _raw(2, facts={"target_transaction_id": "TXN-FX-0601"}),  # belongs to CUST-FX-006
        _raw(3, turns=[{"text": "  mensaje   SINTÉTICO número 0 "}]),  # same as case 0
        _raw(4, turns=[{"text": copied.turns[0].text}]),
    ]
    files = _files(raws)
    files.append(heldout.CaseFile(name="another-name.yaml", case=files[0].case))
    assert heldout.case_problems(files, bank, dev) == [
        "heldout-jacobo-000.yaml: split must be heldout",
        "heldout-juanjose-001.yaml: customer_id is not in the fixture bank",
        "heldout-santiago-002.yaml: facts.target_transaction_id is not a transaction of "
        "customer_id",
        "heldout-jacobo-003.yaml: same customer and messages as heldout-jacobo-000.yaml",
        "heldout-juanjose-004.yaml: same messages as a dev case",
        "another-name.yaml: the file name must be <case_id>.yaml",
        "another-name.yaml: split must be heldout",
        "another-name.yaml: same case_id as heldout-jacobo-000.yaml",
        "another-name.yaml: same customer and messages as heldout-jacobo-000.yaml",
    ]


def test_a_case_id_names_its_author_and_a_number_only() -> None:
    # The id is on the blind sheet and in the versioned manifest: one that names the category
    # tells the second annotator the label (PR #62 review).
    raws = [
        _raw(0, case_id="heldout-prompt-injection-000"),
        _raw(1, case_id="heldout-jacobo-001"),  # written by juanjose
        _raw(2, author="Juan José", case_id="heldout-juanjose-002"),
    ]
    assert heldout.case_problems(_files(raws), load_bank(), load_cases()) == [
        "heldout-prompt-injection-000.yaml: case_id must be heldout-jacobo-<number>",
        "heldout-jacobo-001.yaml: case_id must be heldout-juanjose-<number>",
    ]


def test_a_dev_message_is_refused_with_any_customer_and_any_spelling() -> None:
    # The agent was tuned on the dev messages, whoever the customer is (PR #62 review).
    dev = load_cases()
    text = next(case for case in dev if case.case_id == "dev-normal-es-mx-001").turns[0].text
    raws = [
        _raw(0, turns=[{"text": text}], customer_id="CUST-FX-002", facts={}),
        _raw(1, turns=[{"text": text.upper().replace(",", "") + "!"}]),
    ]
    assert heldout.case_problems(_files(raws), load_bank(), dev) == [
        "heldout-jacobo-000.yaml: same messages as a dev case",
        "heldout-juanjose-001.yaml: same messages as a dev case",
    ]


def test_problems_never_quote_an_utterance_or_a_label(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_text = "FRASE-SECRETA del cliente"
    raws = [
        _raw(0, turns=[{"text": secret_text}], expected_outcome="ETIQUETA-SECRETA"),
        _raw(1, turns=[{"text": secret_text}], extra_field="VALOR-SECRETO"),
        _raw(2, turns=[{"text": secret_text}], acceptable_outcomes=["denied"]),
    ]
    directory = _write(tmp_path / "set", raws)
    (directory / "broken.yaml").write_bytes(f"turns: [{secret_text}\n".encode())

    files, problems = heldout.read_case_files(directory)

    assert files == []
    assert [problem.split(":")[0] for problem in problems] == [
        "broken.yaml",
        "heldout-jacobo-000.yaml",
        "heldout-juanjose-001.yaml",
        "heldout-santiago-002.yaml",
    ]
    assert heldout.main(["--cases", str(directory), "check", "--partial"]) == 1
    printed = capsys.readouterr()
    for text in ("\n".join(problems), printed.out, printed.err):
        assert "SECRET" not in text.upper()


# ---------------------------------------------------------------------------
# Commands: check, seal, verify
# ---------------------------------------------------------------------------


def test_seal_writes_the_manifest_and_verify_detects_every_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "set", [_raw(index) for index in range(88)])
    manifest = tmp_path / "manifest.sha256"
    base = ["--cases", str(directory)]

    assert heldout.main([*base, "seal", "--manifest", str(manifest)]) == 0

    lines = manifest.read_text(encoding="utf-8").splitlines()
    names = sorted(path.name for path in directory.glob("*.yaml"))
    assert [line.split("  ")[1] for line in lines] == names
    first = directory / names[0]
    assert lines[0] == f"{hashlib.sha256(first.read_bytes()).hexdigest()}  {names[0]}"
    assert heldout.main([*base, "verify", "--manifest", str(manifest)]) == 0

    first.write_bytes(first.read_bytes() + b"\n")
    (directory / names[1]).unlink()
    added = _raw(500)
    _write(directory, [added])
    capsys.readouterr()
    assert heldout.main([*base, "verify", "--manifest", str(manifest)]) == 1
    assert capsys.readouterr().err.splitlines()[:3] == [
        f"{names[0]}: content differs from the manifest",
        f"{names[1]}: listed in the manifest, missing",
        f"{added['case_id']}.yaml: not in the manifest",
    ]


def test_seal_refuses_a_set_below_the_plan(tmp_path: Path) -> None:
    directory = _write(tmp_path / "set", [_raw(index) for index in range(12)])
    manifest = tmp_path / "manifest.sha256"
    assert heldout.main(["--cases", str(directory), "seal", "--manifest", str(manifest)]) == 1
    assert not manifest.exists()


def test_one_authors_part_is_checked_without_the_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _write(tmp_path / "part", [_raw(index) for index in range(12)])
    assert heldout.main(["--cases", str(directory), "check", "--partial"]) == 0
    out = capsys.readouterr().out
    assert "cases: 12" in out
    assert "require escalation: 6" in out
    assert "dialects: es-AR 3 (25%), es-CO 3 (25%), es-MX 3 (25%), pt-BR 3 (25%)" in out
    assert heldout.main(["--cases", str(directory), "check"]) == 1


def test_verify_needs_a_manifest(tmp_path: Path) -> None:
    directory = _write(tmp_path / "set", [_raw(0)])
    missing = tmp_path / "none.sha256"
    assert heldout.main(["--cases", str(directory), "verify", "--manifest", str(missing)]) == 1


def test_the_cases_folder_must_exist_outside_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HELDOUT_DIR", raising=False)
    assert heldout.main(["check"]) == 2
    assert heldout.main(["--cases", str(tmp_path / "absent"), "check"]) == 2
    assert heldout.main(["--cases", str(ROOT / "eval" / "dev"), "check"]) == 2
    monkeypatch.setenv("HELDOUT_DIR", str(_write(tmp_path / "set", [_raw(0)])))
    assert heldout.main(["check", "--partial"]) == 0


# ---------------------------------------------------------------------------
# Second annotator
# ---------------------------------------------------------------------------


def test_the_sample_is_a_fifth_of_each_authors_cases_and_repeatable() -> None:
    cases = _cases(88)  # 30, 29 and 29 cases per author
    sample = heldout.blind_sample(cases)
    assert {author: len(chosen) for author, chosen in sample.items()} == {
        "jacobo": 6,
        "juanjose": 6,
        "santiago": 6,
    }
    assert all(case.author == author for author, chosen in sample.items() for case in chosen)
    assert sample == heldout.blind_sample(list(reversed(cases)))
    assert sample != heldout.blind_sample(cases, seed="another")


def test_a_blind_sheet_has_the_scenario_and_no_labels() -> None:
    case = _cases(2)[1]
    sheet = yaml.safe_load(heldout.blind_sheet(case))
    assert list(sheet) == [
        *heldout.SHEET_FIELDS,
        "annotator",
        "expected_outcome",
        "requires_escalation",
    ]
    assert sheet["turns"] == [{"text": case.turns[0].text}]
    assert sheet["facts"]["target_transaction_id"] == "TXN-FX-0101"
    assert sheet["annotator"] is sheet["expected_outcome"] is sheet["requires_escalation"] is None
    labels = set(EvalCase.model_fields) - set(heldout.SHEET_FIELDS)
    assert labels >= {"category", "acceptable_outcomes", "expected_actions", "notes", "author"}


def test_cohen_kappa() -> None:
    # 50 items: both yes 20, both no 15, yes/no 5, no/yes 10 -> po 0.70, pe 0.50, kappa 0.40.
    first = ["yes"] * 25 + ["no"] * 25
    second = ["yes"] * 20 + ["no"] * 5 + ["yes"] * 10 + ["no"] * 15
    agreement = heldout.cohen_kappa(first, second)
    assert (agreement.n, agreement.agreed) == (50, 35)
    assert agreement.kappa == pytest.approx(0.4)
    # Different marginals (yes 30/50 against 20/50): po 0.60, pe 0.48, kappa 0.12 / 0.52.
    third = ["yes"] * 30 + ["no"] * 20
    fourth = ["yes"] * 15 + ["no"] * 15 + ["yes"] * 5 + ["no"] * 15
    assert heldout.cohen_kappa(third, fourth).kappa == pytest.approx(0.12 / 0.52)
    assert heldout.cohen_kappa(first, first).kappa == pytest.approx(1.0)
    assert heldout.cohen_kappa(["yes", "no"], ["no", "yes"]).kappa == pytest.approx(-1.0)
    assert heldout.cohen_kappa(["yes"] * 4, ["yes"] * 4).kappa is None
    assert heldout.cohen_kappa([], []).kappa is None
    with pytest.raises(ValueError, match="same items"):
        heldout.cohen_kappa(["yes"], [])


def _fill(sheet: Path, *, annotator: str, outcome: str, escalation: bool) -> None:
    text = sheet.read_text(encoding="utf-8")
    text = text.replace("annotator: null", f"annotator: {annotator}")
    text = text.replace("expected_outcome: null", f"expected_outcome: {outcome}")
    text = text.replace(
        "requires_escalation: null", f"requires_escalation: {str(escalation).lower()}"
    )
    sheet.write_bytes(text.encode("utf-8"))


def test_sample_then_kappa_reports_agreement_and_disagreements(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raws = [_raw(index) for index in range(88)]
    by_id = {raw["case_id"]: raw for raw in raws}
    base = ["--cases", str(_write(tmp_path / "set", raws))]
    sheets = tmp_path / "sheets"

    assert heldout.main([*base, "sample", "--out", str(sheets)]) == 0
    paths = sorted(sheets.rglob("*.yaml"))
    assert len(paths) == 18
    assert {path.parent.name for path in paths} == {f"cases-by-{author}" for author in AUTHORS}
    assert heldout.main([*base, "kappa", "--annotations", str(sheets)]) == 1  # nothing filled in

    for path in paths:
        raw = by_id[path.stem]
        other = next(author for author in AUTHORS if author != raw["author"])
        _fill(
            path,
            annotator=other,
            outcome=raw["expected_outcome"],
            escalation=raw["requires_escalation"],
        )
    disputed = paths[0]
    original = by_id[disputed.stem]
    agreed_sheet = disputed.read_bytes()
    disputed.write_bytes(
        agreed_sheet.replace(
            f"expected_outcome: {original['expected_outcome']}".encode(),
            b"expected_outcome: abstained",
        )
    )
    capsys.readouterr()
    assert heldout.main([*base, "kappa", "--annotations", str(sheets)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "second-annotator sample: 18 of 88 cases"
    assert out[1].startswith("expected_outcome: agreement 17/18, Cohen's kappa 0.")
    assert out[2] == "requires_escalation: agreement 18/18, Cohen's kappa 1.000"
    assert out[3] == f"disagreements to resolve: {disputed.stem}"

    # An author cannot be the second annotator of their own case.
    annotator = next(
        line for line in agreed_sheet.decode().splitlines() if line.startswith("annotator:")
    )
    disputed.write_bytes(
        agreed_sheet.replace(annotator.encode(), f"annotator: {original['author']}".encode())
    )
    assert heldout.main([*base, "kappa", "--annotations", str(sheets)]) == 1
    assert f"{disputed.stem}: annotated by its own author" in capsys.readouterr().err


def test_a_valid_agreement_needs_a_fifth_once_each_by_another_person() -> None:
    cases = _cases(10)  # 20% of 10 is 2; the first two are by jacobo and juanjose
    first, second = cases[0], cases[1]

    def note(case: EvalCase, annotator: str) -> heldout.Annotation:
        return heldout.Annotation(
            case.case_id, annotator, case.expected_outcome, case.requires_escalation
        )

    _, problems = heldout.annotation_report(cases, [note(first, "santiago"), note(second, "x")])
    assert problems == []
    _, problems = heldout.annotation_report(cases, [note(first, "santiago")])
    assert problems == ["1 annotated cases, 20% of 10 needs 2"]
    twice = [note(first, "santiago"), note(first, "juanjose"), note(second, "santiago")]
    _, problems = heldout.annotation_report(cases, twice)
    assert problems == [f"{first.case_id}: annotated more than once"]
    # The author is not a second annotator, however the name is written (PR #62 review).
    _, problems = heldout.annotation_report(cases, [note(first, "Jacobo "), note(second, "x")])
    assert problems[0] == f"{first.case_id}: annotated by its own author"


def test_an_author_name_cannot_lead_the_sheets_out_of_the_folder(tmp_path: Path) -> None:
    raws = [_raw(index, author="x/../../escaped") for index in range(5)]
    base = ["--cases", str(_write(tmp_path / "set", raws))]
    assert heldout.main([*base, "sample", "--out", str(tmp_path / "sheets")]) == 0
    sheets = [path for path in tmp_path.rglob("*.yaml") if path.parent != tmp_path / "set"]
    assert sheets
    assert {path.parent.relative_to(tmp_path).as_posix() for path in sheets} == {
        "sheets/cases-by-xescaped"
    }


def test_the_sheets_are_never_written_inside_the_repository(tmp_path: Path) -> None:
    base = ["--cases", str(_write(tmp_path / "set", [_raw(0)]))]
    # A name of this run only, removed afterwards: a broken guard must not leave sheets behind.
    inside = ROOT / "eval" / "runs" / f"sheets-test-{tmp_path.name}"
    try:
        assert heldout.main([*base, "sample", "--out", str(inside)]) == 1
        assert not inside.exists()
    finally:
        shutil.rmtree(inside, ignore_errors=True)
