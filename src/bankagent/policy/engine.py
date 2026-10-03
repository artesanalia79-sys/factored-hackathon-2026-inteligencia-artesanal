"""The policy engine: a pure function from today's facts to one ``PolicyDecision``.

Deterministic and side-effect free; every input is already resolved by the caller (`PolicyInputs`,
built by `bankagent.policy.inputs.build_inputs`). Evaluation order (`docs/rules/backend.md`,
`.agents/skills/policy-rule/SKILL.md`):

1. eligibility rules (``approved_status``, ``no_open_case``, ``window``), short-circuit on the
   first failure: ``ineligible``, no write actions.
2. escalation rules (``fraud_score``, ``repeat_disputer``, ``dq_flag``), run together only if
   every eligibility rule passed: ``escalate`` (no write actions) when any trigger fires.
3. otherwise ``proceed``: ``create_dispute`` allowed, plus ``block_card`` when the action rule
   (``card_blockable``) allows the transaction's card; confirmation required,
   ``sla_due_date = filed_on + sla_days`` (``filed_on``, not ``as_of_date``: the SLA is a promise
   counted from the day the customer actually files, not from the serving DB's snapshot date).

Every decision is bound to the evaluated transaction and its card (``target_transaction_id``,
``target_product_id``), so it cannot authorize a write on another target.

``is_fraud`` is never an input (ADR 0003, AGENTS.md rule 6); only ``fraud_score`` is read, and only
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from bankagent.contracts.decisions import PolicyDecision
from bankagent.contracts.domain import CardView, TransactionRiskSignals, TransactionView
from bankagent.contracts.enums import ActionType, DecisionType, TransactionStatus
from bankagent.policy.schema import (
    ApprovedStatusRule,
    CardBlockableRule,
    DqFlagRule,
    FraudScoreRule,
    NoOpenCaseRule,
    PolicyConfig,
    RepeatDisputerRule,
    Rule,
    WindowRule,
)


@dataclass(frozen=True, slots=True)
class PolicyInputs:
    """Every fact a policy rule may read, already resolved by the caller.

    ``customer_country`` is the customer's own country (``CustomerProfile.country``), not the
    transaction's: the dispute window follows where the account is, not where the charge was
    made.

    Two clocks, never mixed:

    - ``as_of_date`` is the serving DB's build date, not the wall clock (the skill's rule). Facts
      from the bank's snapshot compare to it: the transaction date (the dispute window) and
      ``last_history_dispute_date`` (the newest dispute in ``dispute_history``).
    - ``filed_on`` is the real day the customer is filing (``ctx.now.date()`` for the
      orchestrator). Facts the agent itself recorded compare to it:
      ``last_agent_dispute_date`` (the newest dispute in the ops store, stamped with the real
      time), and the SLA due date is counted from it.

    ``open_dispute_id`` is whatever ``GetTransactionResult.open_dispute_id`` already reports: the
    agent-made dispute if any, else the newest still-open prior complaint. ``card`` is the core's
    view of the transaction's card (``None`` if the card is not the customer's).
    """

    transaction: TransactionView
    customer_country: str
    as_of_date: date
    filed_on: date
    open_dispute_id: str | None
    risk: TransactionRiskSignals | None
    card: CardView | None
    last_history_dispute_date: date | None
    last_agent_dispute_date: date | None


def _within(days: int, limit: int) -> bool:
    """A date ``days`` before its reference date is inside a ``limit``-day window.

    A negative count (a dated fact newer than its reference) is inside the window: fail closed.
    """
    return days <= limit


def _check_eligibility(rule: Rule, inputs: PolicyInputs) -> bool:
    """True when the rule's condition is violated (the case is ineligible for this rule)."""
    if isinstance(rule, ApprovedStatusRule):
        return inputs.transaction.transaction_status != TransactionStatus.APPROVED
    if isinstance(rule, NoOpenCaseRule):
        return inputs.open_dispute_id is not None
    if isinstance(rule, WindowRule):
        limit = rule.window_days.get(inputs.customer_country)
        if limit is None:
            return True  # no window for this country: fail closed
        days_elapsed = (inputs.as_of_date - inputs.transaction.transaction_ts.date()).days
        return days_elapsed > limit
    raise TypeError(f"{rule.kind} is not an eligibility rule")  # pragma: no cover


def _check_escalation(rule: Rule, inputs: PolicyInputs) -> bool:
    """True when the escalation trigger fires."""
    if isinstance(rule, FraudScoreRule):
        # A missing score (no row, or a NULL score: ~20% of curated transactions) does not
        # escalate; see DSP-ESC-01's description and ADR 0003.
        score = inputs.risk.fraud_score if inputs.risk else None
        return score is not None and score >= rule.threshold
    if isinstance(rule, RepeatDisputerRule):
        history = inputs.last_history_dispute_date
        agent = inputs.last_agent_dispute_date
        return (
            history is not None and _within((inputs.as_of_date - history).days, rule.window_days)
        ) or (agent is not None and _within((inputs.filed_on - agent).days, rule.window_days))
    if isinstance(rule, DqFlagRule):
        flags = inputs.risk.dq_flags if inputs.risk else ()
        return any(flag in rule.flags for flag in flags)
    raise TypeError(f"{rule.kind} is not an escalation rule")  # pragma: no cover


def _card_blockable(rule: Rule, inputs: PolicyInputs) -> bool:
    if isinstance(rule, CardBlockableRule):
        card = inputs.card
        return (
            card is not None
            and card.product_id == inputs.transaction.product_id
            and card.product_status in rule.statuses
        )
    raise TypeError(f"{rule.kind} is not an action rule")  # pragma: no cover


def evaluate(config: PolicyConfig, inputs: PolicyInputs) -> PolicyDecision:
    target = inputs.transaction.transaction_id
    card_target = inputs.transaction.product_id
    checked_ids: list[str] = []
    for rule in config.eligibility_rules():
        checked_ids.append(rule.rule_id)
        if _check_eligibility(rule, inputs):
            return PolicyDecision(
                decision=DecisionType.INELIGIBLE,
                rule_ids=(rule.rule_id,),
                explanation_keys=(rule.explanation_key,),
                target_transaction_id=target,
                target_product_id=card_target,
                policy_version=config.policy_version,
            )

    triggered = [rule for rule in config.escalation_rules() if _check_escalation(rule, inputs)]
    if triggered:
        return PolicyDecision(
            decision=DecisionType.ESCALATE,
            rule_ids=tuple(rule.rule_id for rule in triggered),
            explanation_keys=tuple(rule.explanation_key for rule in triggered),
            escalation_triggers=tuple(rule.kind for rule in triggered),
            target_transaction_id=target,
            target_product_id=card_target,
            policy_version=config.policy_version,
        )
    checked_ids.extend(rule.rule_id for rule in config.escalation_rules())

    actions = [ActionType.CREATE_DISPUTE]
    explanation_keys: list[str] = []
    for rule in config.action_rules():
        checked_ids.append(rule.rule_id)
        if _card_blockable(rule, inputs):
            actions.append(ActionType.BLOCK_CARD)
        else:
            explanation_keys.append(rule.explanation_key)

    # The trace names every rule that was checked, so the dispute it is stamped on shows that the
    # escalation checks ran too; explanation_keys name only the rule that restricted the actions.
    return PolicyDecision(
        decision=DecisionType.PROCEED,
        rule_ids=tuple(checked_ids),
        explanation_keys=tuple(explanation_keys),
        allowed_actions=tuple(actions),
        requires_confirmation=True,
        sla_due_date=inputs.filed_on + timedelta(days=config.sla_days),
        target_transaction_id=target,
        target_product_id=card_target,
        policy_version=config.policy_version,
    )
