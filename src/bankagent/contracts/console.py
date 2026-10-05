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
    """A policy rule id next to the human-readable text from ``dispute_policy_v1.yaml``."""

    rule_id: str = Field(min_length=1, max_length=16)
    # Trusted content from dispute_policy_v1.yaml, not external input: the cap is a sanity bound,
    # not a security boundary. The longest rule description on file is 741 characters.
    description: str = Field(min_length=1, max_length=2000)


class ConsoleHandoffEntry(Contract):
    """One row of the handoff queue: escalated to a human, not resolved automatically.

    ``rule_explanations`` here is short by nature: ``HandoffPacket.trigger_rule_ids`` already
    names only the rule(s) that actually caused the escalation, not every rule the policy engine
    checked.
    """

    handoff: HandoffPacket
    customer_name: str | None = None
    rule_explanations: tuple[RuleExplanation, ...] = ()


class ConsoleHandoffDetail(Contract):
    """One handoff plus the execution trace of the turn that created it."""

    entry: ConsoleHandoffEntry
    records: tuple[ExecutionRecord, ...] = ()


class ConsoleDisputeEntry(Contract):
    """One dispute the agent resolved on its own, with the customer id its own contract omits.

    ``DisputeCase.rule_ids`` is every rule the policy engine checked, not only the ones that
    changed anything: for a proceed decision that is most of the rulebook, checked and passed.
    ``rule_explanations`` keeps only the decisive one (today, only ``card_blockable`` can still
    read differently from "proceed": whether this card qualifies for an offered block).
    ``checked_rule_ids`` lists the rest, by id only, so the full audit trail is still here without
    a wall of description text that explains nothing case-specific.
    """

    customer_id: Identifier
    customer_name: str | None = None
    case: DisputeCase
    rule_explanations: tuple[RuleExplanation, ...] = ()
    checked_rule_ids: tuple[str, ...] = ()


class ConsoleCardBlockEntry(Contract):
    """One card block the agent carried out, with the customer id its own contract omits."""

    customer_id: Identifier
    customer_name: str | None = None
    event: CardBlockEvent
