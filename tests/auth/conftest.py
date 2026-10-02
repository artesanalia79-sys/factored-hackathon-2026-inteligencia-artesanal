"""Shared auth fixtures: a temporary ops store, an in-memory customer directory and a clock."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bankagent.auth.service import AuthService
from bankagent.auth.settings import AuthSettings, Secret
from bankagent.contracts.enums import Language
from bankagent.store.ops import OpsStore
from bankagent.store.serving import CustomerProfile

SECRET = "test-secret-" + "k" * 40
START = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)

MARIANA = CustomerProfile("CUST-T7-001", "Mariana", "MX", "Active", Language.ES)
RAFAEL = CustomerProfile("CUST-T7-002", "Rafael", "AR", "Active", Language.PT)
CLOSED = CustomerProfile("CUST-T7-003", "Cerrada", "CO", "Closed", Language.ES)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class Directory:
    """In-memory `CustomerDirectory`; `profiles` can be mutated by a test."""

    def __init__(self) -> None:
        self.profiles = {p.customer_id: p for p in (MARIANA, RAFAEL, CLOSED)}

    def customer(self, customer_id: str) -> CustomerProfile | None:
        return self.profiles.get(customer_id)

    def active_customers(self, limit: int) -> list[CustomerProfile]:
        active = [p for p in self.profiles.values() if p.is_active]
        return sorted(active, key=lambda p: p.customer_id)[:limit]


class Otps:
    """Deterministic OTP source that remembers what it issued."""

    def __init__(self) -> None:
        self.issued: list[str] = []

    def __call__(self) -> str:
        self.issued.append(f"{len(self.issued) + 1:06d}")
        return self.issued[-1]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def directory() -> Directory:
    return Directory()


@pytest.fixture
def otps() -> Otps:
    return Otps()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[OpsStore]:
    with OpsStore(tmp_path / "ops.sqlite") as opened:
        yield opened


@pytest.fixture
def settings() -> AuthSettings:
    return AuthSettings(secret=Secret(SECRET))


@pytest.fixture
def service(
    settings: AuthSettings, store: OpsStore, directory: Directory, clock: Clock, otps: Otps
) -> AuthService:
    return AuthService(
        settings=settings, store=store, customers=directory, clock=clock, otp_factory=otps
    )


@pytest.fixture
def secret() -> str:
    return SECRET


@pytest.fixture
def persona_of(service: AuthService) -> Callable[[str], str]:
    """Opaque persona id of a customer by first name."""

    def find(first_name: str) -> str:
        return next(p.persona_id for p in service.personas() if p.first_name == first_name)

    return find


@pytest.fixture
def do_login(
    service: AuthService, otps: Otps, persona_of: Callable[[str], str]
) -> Callable[..., str]:
    """Full login; returns the session token."""

    def run(first_name: str = "Mariana") -> str:
        challenge = service.start_login(persona_of(first_name))
        return service.verify_otp(challenge.challenge_id, otps.issued[-1]).token

    return run
