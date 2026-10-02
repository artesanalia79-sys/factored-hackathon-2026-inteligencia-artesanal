"""Read-only access to the serving DB (DuckDB) for identity lookups.

Only what authentication needs lives here: the minimal customer profile. The session-scoped
tools (T8) add their own reads on top of the same file. Every statement is parameterized.
Owner: Victor (T7, T19).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from bankagent.contracts.enums import Language


@dataclass(frozen=True, slots=True)
class CustomerProfile:
    """Minimal profile used to open a session. Server-side only."""

    customer_id: str
    first_name: str
    country: str
    customer_status: str
    language: Language | None

    @property
    def is_active(self) -> bool:
        return self.customer_status == "Active"


def _profile(row: tuple[str, str, str, str, str | None]) -> CustomerProfile:
    customer_id, first_name, country, status, language = row
    return CustomerProfile(
        customer_id=customer_id,
        first_name=first_name,
        country=country,
        customer_status=status,
        language=Language(language) if language in set(Language) else None,
    )


class ServingDB:
    """Opens a short read-only connection per call; the file is never written from here."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if not Path(self._path).exists():
            raise FileNotFoundError(
                f"serving DB not found at {self._path}; run `uv run poe fixtures` "
                "(synthetic) or `uv run poe serving-build` (curated)"
            )

    def customer(self, customer_id: str) -> CustomerProfile | None:
        with duckdb.connect(self._path, read_only=True) as con:
            row = con.execute(
                "SELECT customer_id, first_name, country, customer_status, preferred_language "
                "FROM customer_profile_min WHERE customer_id = ?",
                [customer_id],
            ).fetchone()
        return None if row is None else _profile(row)

    def active_customers(self, limit: int) -> list[CustomerProfile]:
        """The first ``limit`` active customers by id (the demo persona picker)."""
        with duckdb.connect(self._path, read_only=True) as con:
            rows = con.execute(
                "SELECT customer_id, first_name, country, customer_status, preferred_language "
                "FROM customer_profile_min WHERE customer_status = 'Active' "
                "ORDER BY customer_id LIMIT ?",
                [limit],
            ).fetchall()
        return [_profile(row) for row in rows]
