"""Adapt the T9 policy inputs and T8 confirmation store to the T13 agent."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from bankagent.contracts.base import Contract
from bankagent.contracts.decisions import PolicyDecision
from bankagent.contracts.domain import ConfirmationToken, Session
from bankagent.contracts.enums import DisputeReason, ToolName
from bankagent.contracts.errors import ToolUnavailable
from bankagent.contracts.tools import GetTransactionResult
from bankagent.orchestrator.agent import ConfirmationIssuer, PolicyEvaluator
from bankagent.policy.engine import PolicyInputs, evaluate
from bankagent.policy.schema import PolicyConfig, load_policy
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB
from bankagent.tools import issue_confirmation_token


def build_policy_evaluator(
    serving: ServingDB,
    store: OpsStore,
    *,
    clock: Callable[[], datetime],
    config: PolicyConfig | None = None,
) -> PolicyEvaluator:
    """Resolve customer-scoped policy inputs; keep risk signals out of the interpreter."""
    policy = config or load_policy()

    def decide(
        session: Session, read: GetTransactionResult, _reason: DisputeReason
    ) -> PolicyDecision:
        try:
            profile = serving.customer(session.customer_id)
            if profile is None:
                raise ToolUnavailable("policy inputs unavailable")
            history = serving.last_claim_date(session.customer_id)
            agent_history = store.last_dispute_date(session.customer_id)
            last_claim = max(
                (date for date in (history, agent_history) if date is not None), default=None
            )
            inputs = PolicyInputs(
                transaction=read.transaction,
                customer_country=profile.country,
                as_of_date=serving.as_of_date(),
                filed_on=clock().date(),
                open_dispute_id=read.open_dispute_id,
                risk=serving.risk_signals(session.customer_id, read.transaction.transaction_id),
                last_claim_date=last_claim,
            )
            return evaluate(policy, inputs)
        except ToolUnavailable:
            raise
        except Exception:
            raise ToolUnavailable("policy inputs unavailable") from None

    return decide


def build_confirmation_issuer(store: OpsStore) -> ConfirmationIssuer:
    """Issue tokens on the same ops store used by the write tools."""

    def issue(session: Session, tool: ToolName, args: Contract, now: datetime) -> ConfirmationToken:
        return issue_confirmation_token(store, session=session, tool=tool, args=args, now=now)

    return issue
