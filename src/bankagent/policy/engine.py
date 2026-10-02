"""The policy engine: a pure function from today's facts to one ``PolicyDecision``.

Deterministic and side-effect free; every input is already resolved by the caller (`PolicyInputs`).
Evaluation order (`docs/rules/backend.md`, `.agents/skills/policy-rule/SKILL.md`):

1. eligibility rules (``approved_status``, ``no_open_case``, ``window``), short-circuit on the
   first failure: ``ineligible``, no write actions.
2. escalation rules (``fraud_score``, ``repeat_disputer``), run together only if every
   eligibility rule passed: ``escalate`` (no write actions) when any trigger fires.
3. otherwise ``proceed``: ``create_dispute`` and ``block_card`` allowed, confirmation required,
   ``sla_due_date = as_of_date + sla_days``.

``is_fraud`` is never an input (ADR 0003, AGENTS.md rule 6); only ``fraud_score`` is read, and only
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from bankagent.contracts.decisions import PolicyDecision
from bankagent.contracts.domain import TransactionRiskSignals, TransactionView
from bankagent.contracts.enums import ActionType, DecisionType, TransactionStatus
from bankagent.policy.schema import (
    ApprovedStatusRule,
    FraudScoreRule,
    NoOpenCaseRule,
    PolicyConfig,
    RepeatDisputerRule,
    Rule,
    WindowRule,
)

PROCEED_ACTIONS: tuple[ActionType, ...] = (ActionType.CREATE_DISPUTE, ActionType.BLOCK_CARD)


@dataclass(frozen=True, slots=True)
class PolicyInputs:
    """Every fact a policy rule may read, already resolved by the caller.

    ``customer_country`` is the customer's own country (``CustomerProfile.country``), not the
    transaction's: the dispute window follows where the account is, not where the charge was
    made. ``as_of_date`` is the serving DB's build date, not the wall clock (the skill's rule).
    ``open_dispute_id`` is whatever ``GetTransactionResult.open_dispute_id`` already reports: the
    agent-made dispute if any, else the newest still-open prior complaint.
    """

    transaction: TransactionView
    customer_country: str
    as_of_date: date
    open_dispute_id: str | None
    risk: TransactionRiskSignals | None
    last_claim_date: date | None


def _check_eligibility(rule: Rule, inputs: PolicyInputs) -> bool:
    """True when the rule's condition is violated (the case is ineligible for this rule)."""
    if isinstance(rule, ApprovedStatusRule):
        return inputs.transaction.transaction_status != TransactionStatus.APPROVED
    if isinstance(rule, NoOpenCaseRule):
        return inputs.open_dispute_id is not None
    if isinstance(rule, WindowRule):
        limit = rule.window_days.get(inputs.customer_country)
        if limit is None:
            return True  # no verified window for this country: fail closed
        days_elapsed = (inputs.as_of_date - inputs.transaction.transaction_ts.date()).days
        return days_elapsed > limit
    raise TypeError(f"{rule.kind} is not an eligibility rule")  # pragma: no cover


def _check_escalation(rule: Rule, inputs: PolicyInputs) -> bool:
    """True when the escalation trigger fires."""
    if isinstance(rule, FraudScoreRule):
        score = inputs.risk.fraud_score if inputs.risk else None
        return score is not None and score >= rule.threshold
    if isinstance(rule, RepeatDisputerRule):
        if inputs.last_claim_date is None:
            return False
        return (inputs.as_of_date - inputs.last_claim_date).days <= rule.window_days
    raise TypeError(f"{rule.kind} is not an escalation rule")  # pragma: no cover


def evaluate(config: PolicyConfig, inputs: PolicyInputs) -> PolicyDecision:
    checked_ids: list[str] = []
    for rule in config.eligibility_rules():
        checked_ids.append(rule.rule_id)
        if _check_eligibility(rule, inputs):
            return PolicyDecision(
                decision=DecisionType.INELIGIBLE,
                rule_ids=(rule.rule_id,),
                explanation_keys=(rule.explanation_key,),
                policy_version=config.policy_version,
            )

    triggered = [rule for rule in config.escalation_rules() if _check_escalation(rule, inputs)]
    if triggered:
        return PolicyDecision(
            decision=DecisionType.ESCALATE,
            rule_ids=tuple(rule.rule_id for rule in triggered),
            explanation_keys=tuple(rule.explanation_key for rule in triggered),
            escalation_triggers=tuple(rule.kind for rule in triggered),
            policy_version=config.policy_version,
        )

    return PolicyDecision(
        decision=DecisionType.PROCEED,
        rule_ids=tuple(checked_ids),
        allowed_actions=PROCEED_ACTIONS,
        requires_confirmation=True,
        sla_due_date=inputs.as_of_date + timedelta(days=config.sla_days),
        policy_version=config.policy_version,
    )
