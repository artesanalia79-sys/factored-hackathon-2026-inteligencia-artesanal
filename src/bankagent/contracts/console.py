"""Read views for the human-agent console (T21).

Unlike every other contract in this package, these are unscoped by customer on purpose: the
bank side sees across customers, the way a real human agent's queue would. They are never built
from model output and never accepted as request bodies, only returned by
``bankagent.api.console``.
"""

from __future__ import annotations

from pydantic import Field

from bankagent.contracts.base import Contract, Identifier
from bankagent.contracts.domain import CardBlockEvent, DisputeCase
from bankagent.contracts.handoff import HandoffPacket
from bankagent.contracts.records import ExecutionRecord


class RuleExplanation(Contract):
    """A policy rule id next to the human-readable text from ``dispute_policy_v1.yaml``.

    ``decisive`` marks a rule that actually decided something case-specific, as opposed to one
    that was checked and simply passed. For a handoff this is always true: ``trigger_rule_ids``
    is already curated to the rule(s) that caused the escalation. For a dispute it separates the
    one rule a `proceed` decision can still vary on (today, only ``card_blockable``: whether this
    card qualifies for an offered block) from the rest of the rulebook, which this case also
    checked and passed — shown in full too, so the whole evaluation stays visible, just not all
    weighted the same.
    """

    rule_id: str = Field(min_length=1, max_length=16)
    # Trusted content from dispute_policy_v1.yaml, not external input: the cap is a sanity bound,
    # not a security boundary. The longest rule description on file is 741 characters.
    description: str = Field(min_length=1, max_length=2000)
    decisive: bool = True


class ConsoleHandoffEntry(Contract):
    """One row of the handoff queue: escalated to a human, not resolved automatically."""

    handoff: HandoffPacket
    customer_name: str | None = None
    rule_explanations: tuple[RuleExplanation, ...] = ()


class ConsoleHandoffDetail(Contract):
    """One handoff plus the execution trace of the turn that created it."""

    entry: ConsoleHandoffEntry
    records: tuple[ExecutionRecord, ...] = ()


class ConsoleDisputeEntry(Contract):
    """One dispute the agent resolved on its own, with the customer id its own contract omits."""

    customer_id: Identifier
    customer_name: str | None = None
    case: DisputeCase
    rule_explanations: tuple[RuleExplanation, ...] = ()


class ConsoleCardBlockEntry(Contract):
    """One card block the agent carried out, with the customer id its own contract omits."""

    customer_id: Identifier
    customer_name: str | None = None
    event: CardBlockEvent
