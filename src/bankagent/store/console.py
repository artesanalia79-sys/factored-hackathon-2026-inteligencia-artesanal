"""Reads for the human-agent console (handoff queue). NOT for customer sessions.

These queries are not scoped to a customer, so they must never be reachable from the tools the
agent calls on behalf of a customer: an id coming from the model would read another customer's
handoff. They live on their own class, and a test asserts that `bankagent.tools` does not import
this module. Owner: Juan José (T7); used by the agent console (T21).
"""

from __future__ import annotations

from bankagent.contracts.handoff import HandoffPacket
from bankagent.store.sqlite import Database


class HandoffConsole:
    def __init__(self, database: Database) -> None:
        self._db = database

    def get_handoff(self, handoff_id: str) -> HandoffPacket | None:
        row = self._db.one("SELECT payload FROM handoffs WHERE handoff_id = ?", [handoff_id])
        return None if row is None else HandoffPacket.model_validate_json(row["payload"])

    def list_handoffs(self, limit: int = 50) -> list[HandoffPacket]:
        """Newest handoffs first (the queue a human agent works from)."""
        rows = self._db.all(
            "SELECT payload FROM handoffs ORDER BY created_at DESC, handoff_id LIMIT ?", [limit]
        )
        return [HandoffPacket.model_validate_json(r["payload"]) for r in rows]
