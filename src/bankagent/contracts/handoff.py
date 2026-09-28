"""Structured handoff to a human agent.

The orchestrator builds a ``HandoffDraft`` (no customer identity); the ``create_handoff`` tool
adds the server-side identity and ids to produce the stored ``HandoffPacket``.
"""

from __future__ import annotations

from pydantic import AwareDatetime, Field

from bankagent.contracts.base import Contract, Identifier
from bankagent.contracts.enums import ActionType, Intent, Language, Priority, Specialty


class VerifiedFact(Contract):
    """A fact read from a system of record, with where it came from."""

    key: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=500)
    source: str = Field(min_length=1, max_length=64, description="tool or table that produced it")
    ref: str | None = Field(default=None, max_length=64, description="record id, e.g. a txn id")


class ActionTaken(Contract):
    action: ActionType
    ref_id: Identifier
    verified: bool
    at: AwareDatetime


class HandoffRouting(Contract):
    specialty: Specialty
    language: Language
    priority: Priority
    agent_id: Identifier | None = None


class HandoffDraft(Contract):
    """Everything a human agent needs so the customer does not have to repeat themselves."""

    trace_id: Identifier
    language: Language
    request: str = Field(min_length=1, max_length=1000, description="customer request, redacted")
    intent: Intent
    verified_facts: tuple[VerifiedFact, ...] = ()
    actions_taken: tuple[ActionTaken, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    trigger_rule_ids: tuple[str, ...] = ()
    policy_version: str
    routing: HandoffRouting


class HandoffPacket(HandoffDraft):
    """Stored handoff. ``customer_id`` is filled server-side from the session."""

    handoff_id: Identifier
    created_at: AwareDatetime
    customer_id: Identifier
