"""Load ``EvalCase`` YAML files and check them against the synthetic bank.

The sealed held-out set must never be read before the final evaluation (Task 27). ``load_cases``
refuses any path under ``eval/heldout/`` or under ``HELDOUT_DIR`` unless the caller passes
``allow_heldout=True``, which only the Task 27 command may do.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from pathlib import Path

import yaml

from bankagent.contracts.enums import EvalCategory
from bankagent.contracts.evaluation import EvalCase
from bankagent.eval.bank import BankIndex

ROOT = Path(__file__).resolve().parents[3]
DEV_DIR = ROOT / "eval" / "dev"

ATTACK_CATEGORIES: frozenset[EvalCategory] = frozenset(
    {EvalCategory.PROMPT_INJECTION, EvalCategory.UNAUTHORIZED_ACCESS}
)


class HeldoutAccessError(PermissionError):
    """Raised when code tries to read the sealed held-out set outside Task 27."""


class CaseError(ValueError):
    pass


def is_heldout_path(path: Path) -> bool:
    resolved = path.resolve()
    if "heldout" in (part.lower() for part in resolved.parts):
        return True
    heldout_dir = os.environ.get("HELDOUT_DIR")
    if heldout_dir:
        sealed = Path(heldout_dir).resolve()
        if resolved == sealed or sealed in resolved.parents:
            return True
    return False


def load_case(path: Path) -> EvalCase:
    with path.open(encoding="utf-8") as handle:
        return EvalCase.model_validate(yaml.safe_load(handle))


def load_cases(directory: Path = DEV_DIR, *, allow_heldout: bool = False) -> tuple[EvalCase, ...]:
    """Every ``*.yaml`` case in ``directory``, sorted by ``case_id``; ids must be unique."""
    if is_heldout_path(directory) and not allow_heldout:
        raise HeldoutAccessError(f"refusing to read sealed held-out cases from {directory}")
    cases = [load_case(path) for path in sorted(directory.glob("*.yaml"))]
    if not cases:
        raise CaseError(f"no *.yaml cases in {directory}")
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise CaseError(f"duplicate case_id {case.case_id}")
        seen.add(case.case_id)
    return tuple(sorted(cases, key=lambda c: c.case_id))


def case_set_sha256(cases: Sequence[EvalCase]) -> str:
    """Content hash of a case set, recorded in every result so reports name their workload."""
    digest = hashlib.sha256()
    for case in sorted(cases, key=lambda c: c.case_id):
        digest.update(case.model_dump_json().encode("utf-8"))
    return digest.hexdigest()


def reference_problems(case: EvalCase, bank: BankIndex) -> list[str]:
    """Fixture references that do not hold (unknown customer, target owned by someone else)."""
    problems: list[str] = []
    if case.customer_id not in bank.customers:
        problems.append(f"{case.case_id}: unknown customer {case.customer_id}")
    target = case.facts.target_transaction_id
    if target is not None:
        owner = bank.owner_of(target)
        if owner is None:
            problems.append(f"{case.case_id}: unknown target transaction {target}")
        elif owner != case.customer_id:
            problems.append(f"{case.case_id}: target {target} belongs to another customer")
    return problems
