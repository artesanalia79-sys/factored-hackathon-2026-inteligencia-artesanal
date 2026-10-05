"""Reads for the human-agent console (T21): the handoff queue, the cases it never sees because
they resolved on their own, and the execution trace behind any of them.

These queries are not scoped to a customer, so they must never be reachable from the tools the
agent calls on behalf of a customer: an id coming from the model would read another customer's
case. They live on their own class, and a test asserts that `bankagent.tools` does not import
this module. Owner: Juan José (T7); used by the agent console (T21).
"""

from __future__ import annotations

from bankagent.contracts.domain import CardBlockEvent, DisputeCase
from bankagent.contracts.handoff import HandoffPacket
from bankagent.contracts.records import ExecutionRecord
from bankagent.store.sqlite import Database


class HandoffConsole:
    def __init__(self, database: Database) -> None:
        self._db = database

    # -- handoffs: escalated to a human ------------------------------------

    def get_handoff(self, handoff_id: str) -> HandoffPacket | None:
        row = self._db.one("SELECT payload FROM handoffs WHERE handoff_id = ?", [handoff_id])
        return None if row is None else HandoffPacket.model_validate_json(row["payload"])

    def list_handoffs(self, limit: int = 50) -> list[HandoffPacket]:
        """Newest handoffs first (the queue a human agent works from)."""
        rows = self._db.all(
            "SELECT payload FROM handoffs ORDER BY created_at DESC, handoff_id LIMIT ?", [limit]
        )
        return [HandoffPacket.model_validate_json(r["payload"]) for r in rows]

    # -- disputes and card blocks: resolved without a human ----------------
    #
    # `DisputeCase` and `CardBlockEvent` deliberately carry no `customer_id` (nothing a tool
    # returns to the model may), so it is read back from its own column here instead.

    def list_disputes(self, limit: int = 50) -> list[tuple[str, DisputeCase]]:
        """Newest disputes first, each with the customer id its payload does not carry."""
        rows = self._db.all(
            "SELECT customer_id, payload FROM disputes "
            "ORDER BY created_at DESC, dispute_id LIMIT ?",
            [limit],
        )
        return [(r["customer_id"], DisputeCase.model_validate_json(r["payload"])) for r in rows]

    def list_card_blocks(self, limit: int = 50) -> list[tuple[str, CardBlockEvent]]:
        """Newest card blocks first, each with the customer id its payload does not carry."""
        rows = self._db.all(
            "SELECT customer_id, payload FROM card_blocks "
            "ORDER BY blocked_at DESC, block_id LIMIT ?",
            [limit],
        )
        return [(r["customer_id"], CardBlockEvent.model_validate_json(r["payload"])) for r in rows]

    # -- execution trace: what the agent actually did -----------------------

    def list_records(self, trace_id: str) -> list[ExecutionRecord]:
        """Every step of one trace, in order. Same read `OpsStore.list_records` offers for
        tests and offline inspection; the console is its other caller."""
        rows = self._db.all(
            "SELECT payload FROM execution_records WHERE trace_id = ? "
            "ORDER BY turn_index, step_index, created_at",
            [trace_id],
        )
        return [ExecutionRecord.model_validate_json(r["payload"]) for r in rows]
