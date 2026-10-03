"""Adapt the T9 policy inputs and T8 confirmation store to the T13 agent."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import ValidationError

from bankagent.contracts.base import Contract
from bankagent.contracts.decisions import PolicyDecision
from bankagent.contracts.domain import ConfirmationToken, Session
from bankagent.contracts.enums import DisputeReason, ToolName
from bankagent.contracts.errors import ToolUnavailable
from bankagent.contracts.tools import GetTransactionResult
from bankagent.orchestrator.agent import ConfirmationIssuer, PolicyEvaluator
from bankagent.policy import PolicyConfig, build_inputs, evaluate, load_policy
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB
from bankagent.tools import issue_confirmation_token
from bankagent.tools.base import INFRASTRUCTURE_ERRORS


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
        # The shared T9 builder, so the agent and `poe policy-explain` read the same facts, each
        # date against its own clock (bank history vs `as_of_date`, agent disputes vs filing day).
        try:
            inputs = build_inputs(
                serving,
                store,
                session.customer_id,
                read.transaction.transaction_id,
                filed_on=clock().date(),
            )
        except (*INFRASTRUCTURE_ERRORS, ValidationError):
            inputs = None  # raised below, outside the handler, so no row-quoting cause is kept
        if inputs is None:
            raise ToolUnavailable("policy inputs unavailable")
        return evaluate(policy, inputs)

    return decide


def build_confirmation_issuer(store: OpsStore) -> ConfirmationIssuer:
    """Issue tokens on the same ops store used by the write tools."""

    def issue(session: Session, tool: ToolName, args: Contract, now: datetime) -> ConfirmationToken:
        return issue_confirmation_token(store, session=session, tool=tool, args=args, now=now)

    return issue
