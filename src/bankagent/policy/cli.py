"""``uv run poe policy-explain <customer_id> <transaction_id>``.

Evaluates one case against the real serving DB (fixture bank by default) and prints the
``PolicyDecision`` with the description and provenance of every rule it names, so a human can
see why the policy decided what it decided without reading the engine. Each rule is marked
``fired`` (it produced the decision or restricted it, with its explanation key) or ``passed``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from bankagent.contracts.decisions import PolicyDecision
from bankagent.fixtures.builder import DEFAULT_OUT as DEFAULT_BANK
from bankagent.policy.engine import evaluate
from bankagent.policy.inputs import build_inputs
from bankagent.policy.schema import POLICY_FILE, PolicyConfig, load_policy
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB


def _print_decision(config: PolicyConfig, decision: PolicyDecision) -> None:
    by_id = {rule.rule_id: rule for rule in config.rules}
    print(f"decision: {decision.decision.value}")
    actions = ", ".join(action.value for action in decision.allowed_actions) or "none"
    print(f"allowed_actions: {actions}")
    for rule_id in decision.rule_ids:
        rule = by_id.get(rule_id)
        if rule is None:
            print(f"  {rule_id}: (not found in the policy file)")
            continue
        print(f"  {rule.rule_id} [{rule.provenance.label}] {rule.description.strip()}")
        if rule.explanation_key in decision.explanation_keys:
            print(f"    fired: {rule.explanation_key}")
        else:
            print("    passed")
        print(f"    source: {rule.provenance.source}")
        if rule.todo:
            print(f"    {rule.todo}")
    if decision.sla_due_date is not None:
        print(f"sla_due_date: {decision.sla_due_date.isoformat()}")


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
        help="day the dispute is filed (default: today); drives the SLA due date and the age of "
        "the agent's own disputes, never the eligibility window (that compares to the bank's "
        "as_of_date)",
    )
    args = parser.parse_args(argv)

    serving = ServingDB(args.bank)
    config = load_policy(args.policy)
    filed_on = args.filed_on or date.today()
    store = OpsStore(args.ops_store) if args.ops_store else None
    try:
        inputs = build_inputs(
            serving, store, args.customer_id, args.transaction_id, filed_on=filed_on
        )
    finally:
        if store is not None:
            store.close()
    if inputs is None:
        raise SystemExit(
            f"no transaction {args.transaction_id!r} for customer {args.customer_id!r}"
        )
    _print_decision(config, evaluate(config, inputs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
