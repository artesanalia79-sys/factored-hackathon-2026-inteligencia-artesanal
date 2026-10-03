"""Build ``PolicyInputs`` from the real stores.

The one place that assembles a request's facts, so the orchestrator (T13), ``poe policy-explain``
and the tests agree on what each input means and none re-implements, or forgets, a read (PR #44
review, round 2: the two-clock merge used to live only in the CLI and a test helper).
"""

from __future__ import annotations

from datetime import date

from bankagent.contracts.errors import NotFound
from bankagent.policy.engine import PolicyInputs
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB

TRANSACTION_NOT_FOUND = "transaction not found"
CUSTOMER_NOT_FOUND = "customer not found"


def build_inputs(
    serving: ServingDB,
    store: OpsStore | None,
    customer_id: str,
    transaction_id: str,
    filed_on: date,
) -> PolicyInputs:
    """``store`` is optional (``None`` for the LLM-only evaluation baseline, which has no policy
    by design): without it, an agent-made dispute never counts toward the repeat-disputer trigger
    and an agent-made dispute on this transaction is not found either (only the pre-agent history
    is read for both).
    """
    transaction = serving.transaction(customer_id, transaction_id)
    if transaction is None:
        raise NotFound(TRANSACTION_NOT_FOUND)
    customer = serving.customer(customer_id)
    if customer is None:
        raise NotFound(CUSTOMER_NOT_FOUND)
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
        filed_on=filed_on,
        open_dispute_id=open_dispute_id,
        risk=serving.risk_signals(customer_id, transaction_id),
        last_history_claim_date=serving.last_claim_date(customer_id),
        last_agent_dispute_date=store.last_dispute_date(customer_id) if store else None,
    )
