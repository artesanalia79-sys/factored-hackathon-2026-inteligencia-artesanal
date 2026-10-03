"""Build ``PolicyInputs`` for one case from the serving DB and the ops store.

The one place that resolves what the engine reads, so the orchestrator (T13) and
``poe policy-explain`` cannot drift apart: the agent-made dispute and the prior complaint both
count as an open case, and the bank's dispute history and the agent's own disputes both feed the
repeat-disputer rule, each against its own clock (see ``PolicyInputs``).
"""

from __future__ import annotations

from datetime import date

from bankagent.policy.engine import PolicyInputs
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB


def build_inputs(
    serving: ServingDB,
    store: OpsStore | None,
    customer_id: str,
    transaction_id: str,
    *,
    filed_on: date,
) -> PolicyInputs | None:
    """``None`` when the transaction is not this customer's (missing and foreign alike).

    ``store`` is the ops store the write tools use; ``None`` only for a read-only explanation
    with no agent-made records (``poe policy-explain`` without ``--ops-store``).
    """
    transaction = serving.transaction(customer_id, transaction_id)
    customer = serving.customer(customer_id)
    if transaction is None or customer is None:
        return None
    own_dispute = store.get_dispute(customer_id, transaction_id=transaction_id) if store else None
    open_dispute_id = (
        own_dispute.dispute_id
        if own_dispute is not None
        else serving.open_complaint_id(customer_id, transaction_id)
    )
    cards = serving.cards(customer_id, transaction.product_id)
    return PolicyInputs(
        transaction=transaction,
        customer_country=customer.country,
        as_of_date=serving.as_of_date(),
        filed_on=filed_on,
        open_dispute_id=open_dispute_id,
        risk=serving.risk_signals(customer_id, transaction_id),
        card=cards[0] if cards else None,
        last_history_dispute_date=serving.last_dispute_date(customer_id),
        last_agent_dispute_date=store.last_dispute_date(customer_id) if store else None,
    )
