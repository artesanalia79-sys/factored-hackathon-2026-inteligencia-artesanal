"""Base class, shared field types and canonical hashing for every contract."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

CENT = Decimal("0.01")


def _to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT)


# ---------------------------------------------------------------------------
# Shared field types
# ---------------------------------------------------------------------------
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
"""ISO 4217 alphabetic code, e.g. ``MXN``."""

CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
"""ISO 3166-1 alpha-2 code, e.g. ``MX``. Used for transaction countries (any country)."""

Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2), AfterValidator(_to_cents)]
"""Monetary amount in its original currency, normalized to cents (``100`` -> ``100.00``).

Normalization keeps ``args_hash`` stable for equal amounts written differently. Never a float.
"""

NonNegativeMoney = Annotated[Money, Field(ge=0)]

Last4 = Annotated[str, StringConstraints(pattern=r"^\d{4}$")]
"""Last four digits of a card number. The full number never leaves the core system."""

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=64)]

Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class Contract(BaseModel):
    """Base for every shared contract: immutable and strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def canonical_json(value: BaseModel | dict[str, Any]) -> str:
    """Deterministic JSON (sorted keys, no whitespace) used for hashing."""
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def args_hash(args: BaseModel) -> str:
    """sha256 of the canonical JSON of tool arguments.

    Confirmation tokens are bound to this hash, so a token issued for one exact action cannot be
    replayed for different arguments.
    """
    return hashlib.sha256(canonical_json(args).encode("utf-8")).hexdigest()
