"""``uv run poe policy-explain <customer_id> <transaction_id>``.

Evaluates one case against the real serving DB (fixture bank by default) and prints the
``PolicyDecision`` with the description and provenance of every rule it names, so a human can
see why the policy decided what it decided without reading the engine.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bankagent.contracts.enums import DecisionType
from bankagent.fixtures.builder import DEFAULT_OUT as DEFAULT_BANK
from bankagent.policy.engine import PolicyInputs, evaluate
from bankagent.policy.schema import POLICY_FILE, PolicyConfig, load_policy
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB


def _inputs(
    serving: ServingDB, store: OpsStore | None, customer_id: str, transaction_id: str
) -> PolicyInputs:
    transaction = serving.transaction(customer_id, transaction_id)
    if transaction is None:
        raise SystemExit(f"no transaction {transaction_id!r} for customer {customer_id!r}")
    customer = serving.customer(customer_id)
    if customer is None:
        raise SystemExit(f"no customer {customer_id!r}")
    own_dispute = store.get_dispute(customer_id, transaction_id=transaction_id) if store else None
    open_dispute_id = (
        own_dispute.dispute_id
        if own_dispute is not None
        else serving.open_complaint_id(customer_id, transaction_id)
    )
    return PolicyInputs(
        transaction=transaction,
        customer_country=customer.country,
        as_of_date=serving.as_of_date(),
        open_dispute_id=open_dispute_id,
        risk=serving.risk_signals(customer_id, transaction_id),
        last_claim_date=serving.last_claim_date(customer_id),
    )


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
    args = parser.parse_args(argv)

    serving = ServingDB(args.bank)
    store = OpsStore(args.ops_store) if args.ops_store else None
    config = load_policy(args.policy)
    inputs = _inputs(serving, store, args.customer_id, args.transaction_id)
    decision = evaluate(config, inputs)
    _print_decision(config, decision.decision, decision.rule_ids)
    if decision.sla_due_date is not None:
        print(f"sla_due_date: {decision.sla_due_date.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
