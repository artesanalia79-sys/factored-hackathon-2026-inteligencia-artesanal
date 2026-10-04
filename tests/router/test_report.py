"""``docs/evidence/router_eval.md``: what the code produces, and how it is rendered (Task 18)."""

from __future__ import annotations

import dataclasses
import difflib
import re
from collections.abc import Mapping

import pytest

from bankagent.contracts.enums import Intent
from bankagent.eval.router.evaluate import Evaluation, Repeat, evaluate
from bankagent.eval.router.report import render_report
from bankagent.router.corpus import ROOT, load_corpus, load_external

REPORT = ROOT / "docs" / "evidence" / "router_eval.md"
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# The 20-seed section takes about 30 s to recompute; it runs the same code on more split seeds.
REPEATS_SECTION = re.compile(r"\n## Repeated grouped splits.*?(?=\n## )", re.DOTALL)


@pytest.fixture(scope="module")
def evaluation() -> Evaluation:
    return evaluate(load_corpus(), load_external(), repeats=0)


def _same_up_to_the_last_digit(fresh: str, committed: str) -> bool:
    """Same text and the same counts; a decimal may differ by one unit in its last printed
    digit, because another CPU's BLAS kernels can round a sum differently."""
    if NUMBER.sub("#", fresh) != NUMBER.sub("#", committed):
        return False
    for a, b in zip(NUMBER.findall(fresh), NUMBER.findall(committed), strict=True):
        if "." not in a or "." not in b:
            if a != b:
                return False
        elif abs(float(a) - float(b)) > 1.5 * 10.0 ** -len(a.partition(".")[2]):
            return False
    return True


def test_the_committed_report_is_what_the_code_produces(evaluation: Evaluation) -> None:
    fresh = render_report(evaluation)
    committed = REPEATS_SECTION.sub("", REPORT.read_text(encoding="utf-8"))
    diff = "\n".join(
        difflib.unified_diff(
            committed.splitlines(), fresh.splitlines(), "committed", "fresh", lineterm="", n=0
        )
    )
    assert _same_up_to_the_last_digit(fresh, committed), (
        "the router code or eval/router changed: run `uv run poe router` and commit "
        f"docs/evidence/router_eval.md\n{diff}"
    )


def test_one_unit_in_the_last_digit_is_tolerated_and_nothing_more() -> None:
    assert _same_up_to_the_last_digit("q̂ = 0.8528, 140/160", "q̂ = 0.8529, 140/160")
    assert not _same_up_to_the_last_digit("q̂ = 0.8528, 140/160", "q̂ = 0.8531, 140/160")
    assert not _same_up_to_the_last_digit("q̂ = 0.8528, 140/160", "q̂ = 0.8528, 141/160")
    assert not _same_up_to_the_last_digit("learned 87.5%", "keyword 87.5%")


def _repeat(seed: int, coverage: float) -> Repeat:
    """A split where the router never answers: no accuracy when answering."""
    return Repeat(
        seed=seed,
        c=1.0,
        q_hat=0.9,
        learned_accuracy=0.8,
        learned_macro_f1=0.8,
        keyword_accuracy=0.5,
        set_coverage=coverage,
        answered=0.0,
        answered_accuracy=float("nan"),
    )


def test_the_repeats_section_counts_the_splits_below_the_target(evaluation: Evaluation) -> None:
    text = render_report(
        dataclasses.replace(evaluation, repeats=(_repeat(43, 0.85), _repeat(44, 0.95)))
    )
    assert "## Repeated grouped splits (2 more seeds)" in text
    assert "again on split seeds 43-44." in text
    assert "Splits with set coverage below 90%: 1 of 2." in text
    assert "| Set coverage | 90.0% | 85.0% | 95.0% |" in text
    assert "| Accuracy when answering | n/a | n/a | n/a |" in text


def _review_line(evaluation: Evaluation, reviewed_by: Mapping[Intent, str | None]) -> str:
    corpus = dataclasses.replace(evaluation.corpus, reviewed_by=reviewed_by)
    text = render_report(dataclasses.replace(evaluation, corpus=corpus))
    return next(line for line in text.splitlines() if line.startswith("- Corpus review:"))


def test_the_report_says_who_reviewed_the_corpus(evaluation: Evaluation) -> None:
    everyone = dict.fromkeys(Intent, "jjresher")
    assert _review_line(evaluation, everyone) == "- Corpus review: jjresher."
    assert (
        _review_line(evaluation, {**everyone, Intent.ATTACK: None})
        == "- Corpus review: jjresher; pending for attack."
    )
    assert (
        _review_line(evaluation, dict.fromkeys(Intent))
        == "- Corpus review: pending (a teammate reviews a sample before merge)."
    )
