"""``uv run poe policy-explain <customer_id> <transaction_id>``.

Evaluates one case against the real serving DB (fixture bank by default) and prints the
``PolicyDecision`` with the description and provenance of every rule it names, so a human can
see why the policy decided what it decided without reading the engine.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from bankagent.contracts.enums import DecisionType
from bankagent.contracts.errors import NotFound
from bankagent.fixtures.builder import DEFAULT_OUT as DEFAULT_BANK
from bankagent.policy.engine import evaluate
from bankagent.policy.inputs import build_inputs
from bankagent.policy.schema import POLICY_FILE, PolicyConfig, load_policy
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB


def _print_decision(
    config: PolicyConfig, decision: DecisionType, rule_ids: tuple[str, ...]
) -> None:
    by_id = {rule.rule_id: rule for rule in config.rules}
    print(f"decision: {decision.value}")
    for rule_id in rule_ids:
        rule = by_id.get(rule_id)
        if rule is None:
            print(f"  {rule_id}: (not found in {POLICY_FILE})")
            continue
        flag = "synthetic" if rule.provenance.synthetic else "verified"
        print(f"  {rule.rule_id} [{flag}] {rule.description.strip()}")
        print(f"    explanation_key: {rule.explanation_key}")
        print(f"    source: {rule.provenance.source}")
        if rule.todo:
            print(f"    {rule.todo}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain one policy decision (Task 9).")
    parser.add_argument("customer_id")
    parser.add_argument("transaction_id")
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK, help="serving DB (DuckDB)")
    parser.add_argument("--ops-store", type=Path, default=None, help="ops store (SQLite), optional")
    parser.add_argument("--policy", type=Path, default=POLICY_FILE, help="policy YAML")
    parser.add_argument(
        "--filed-on",
        type=date.fromisoformat,
        default=None,
        help="day the dispute is filed (default: today); drives the SLA due date, never the "
        "eligibility window (that compares to the bank's as_of_date)",
    )
    args = parser.parse_args(argv)

    serving = ServingDB(args.bank)
    store = OpsStore(args.ops_store) if args.ops_store else None
    config = load_policy(args.policy)
    filed_on = args.filed_on or date.today()
    try:
        inputs = build_inputs(serving, store, args.customer_id, args.transaction_id, filed_on)
    except NotFound as exc:
        raise SystemExit(str(exc)) from exc
    decision = evaluate(config, inputs)
    _print_decision(config, decision.decision, decision.rule_ids)
    if decision.sla_due_date is not None:
        print(f"sla_due_date: {decision.sla_due_date.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
