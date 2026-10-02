"""Persona login with a mock OTP, server-side sessions and signed session tokens.

Flow: ``personas()`` → ``start_login(persona_id)`` → ``verify_otp(challenge_id, code)`` →
``authenticate(token)`` on every request. The client never sends ``customer_id``: it picks an
opaque ``persona_id`` (a keyed hash), and the server maps it to the customer.

Attempt limits:

- per challenge: ``otp_max_attempts`` wrong codes close the challenge for good;
- per customer: ``lockout_failures`` wrong codes inside ``lockout_window`` block new challenges
  and verifications until the window passes, so asking for a new code does not reset the count.

Every verification first spends one attempt and counts one failure inside a single store
transaction (lockout check included), and only then compares the code; a success clears the
count. Concurrent guesses therefore cannot exceed either limit.

Client strings are untrusted: anything that is not plain ASCII is rejected before it reaches a
hash, a comparison or the database. Failures never say why (unknown persona, wrong, expired or
reused code look the same). Logs carry
event names and opaque ids only: no secret, token, OTP, name or ``customer_id``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from bankagent.auth.settings import AuthSettings
from bankagent.auth.tokens import SessionTokens
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import Language
from bankagent.contracts.errors import SessionExpired, Unauthorized
from bankagent.store.ops import LoginChallenge, OpsStore
from bankagent.store.serving import CustomerProfile

log = logging.getLogger("bankagent.auth")

OTP_DIGITS = 6


class AuthError(Exception):
    """Base of login failures. ``code`` is safe to return to the client."""

    code = "auth_error"


class InvalidCredentials(AuthError):
    """Unknown persona, or a wrong, expired or already used code. Deliberately unspecific."""

    code = "invalid_credentials"


class TooManyAttempts(AuthError):
    """The challenge or the customer is locked. ``retry_after_seconds`` is 0 when asking for a
    new code is enough."""

    code = "too_many_attempts"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(self.code)
        self.retry_after_seconds = retry_after_seconds


class CustomerDirectory(Protocol):
    def customer(self, customer_id: str) -> CustomerProfile | None: ...

    def active_customers(self, limit: int) -> list[CustomerProfile]: ...


@dataclass(frozen=True, slots=True)
class Persona:
    persona_id: str
    first_name: str
    country: str
    language: Language | None


@dataclass(frozen=True, slots=True)
class ChallengeIssued:
    challenge_id: str
    expires_at: datetime
    mock_otp: str | None


@dataclass(frozen=True, slots=True)
class LoginSuccess:
    token: str
    expires_at: datetime
    first_name: str
    language: Language | None


def generate_otp() -> str:
    return f"{secrets.randbelow(10**OTP_DIGITS):0{OTP_DIGITS}d}"


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(18)}"


class AuthService:
    def __init__(
        self,
        *,
        settings: AuthSettings,
        store: OpsStore,
        customers: CustomerDirectory,
        clock: Callable[[], datetime],
        otp_factory: Callable[[], str] = generate_otp,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._settings = settings
        self._store = store
        self._customers = customers
        self._clock = clock
        self._otp_factory = otp_factory
        self._new_id = id_factory
        self._tokens = SessionTokens(settings.secret, settings.issuer)

    # -- personas ----------------------------------------------------------

    def _keyed_hash(self, purpose: str, value: str) -> str:
        message = f"{purpose}:{value}".encode()
        return hmac.new(self._settings.secret.reveal(), message, hashlib.sha256).hexdigest()

    def _persona_id(self, customer_id: str) -> str:
        return "per-" + self._keyed_hash("persona", customer_id)[:24]

    def personas(self) -> list[Persona]:
        """Demo persona picker: active customers behind opaque ids."""
        return [
            Persona(self._persona_id(c.customer_id), c.first_name, c.country, c.language)
            for c in self._customers.active_customers(self._settings.persona_limit)
        ]

    def _customer_for(self, persona_id: str) -> CustomerProfile | None:
        found = None
        for candidate in self._customers.active_customers(self._settings.persona_limit):
            if hmac.compare_digest(self._persona_id(candidate.customer_id), persona_id):
                found = candidate
        return found

    # -- login -------------------------------------------------------------

    def _check_lockout(self, customer_id: str, now: datetime) -> None:
        failures = self._store.failures_since(customer_id, now - self._settings.lockout_window)
        if len(failures) < self._settings.lockout_failures:
            return
        # Locked until enough failures fall out of the window.
        releasing = failures[len(failures) - self._settings.lockout_failures]
        retry_after = releasing + self._settings.lockout_window - now
        raise TooManyAttempts(max(1, int(retry_after.total_seconds())))

    def start_login(self, persona_id: str) -> ChallengeIssued:
        now = self._clock()
        customer = self._customer_for(persona_id) if persona_id.isascii() else None
        if customer is None:
            log.info("login_rejected reason=unknown_persona")
            raise InvalidCredentials(InvalidCredentials.code)
        self._check_lockout(customer.customer_id, now)
        # Housekeeping: nothing older than the lockout window can matter any more.
        self._store.purge_login_data(now - self._settings.lockout_window)
        challenge_id = self._new_id("chl")
        code = self._otp_factory()
        expires_at = now + self._settings.otp_ttl
        self._store.create_challenge(
            LoginChallenge(
                challenge_id=challenge_id,
                customer_id=customer.customer_id,
                otp_hash=self._keyed_hash("otp", f"{challenge_id}:{code}"),
                issued_at=now,
                expires_at=expires_at,
                max_attempts=self._settings.otp_max_attempts,
            )
        )
        log.info("login_challenge_created challenge=%s", challenge_id)
        return ChallengeIssued(
            challenge_id, expires_at, code if self._settings.expose_mock_otp else None
        )

    def verify_otp(self, challenge_id: str, code: str) -> LoginSuccess:
        now = self._clock()
        if not (challenge_id.isascii() and code.isascii()):
            log.info("login_rejected reason=malformed_input")
            raise InvalidCredentials(InvalidCredentials.code)
        challenge = self._store.get_challenge(challenge_id)
        # Hash even when the challenge is unknown, so both paths take similar time.
        presented = self._keyed_hash("otp", f"{challenge_id}:{code}")
        if challenge is None:
            log.info("login_rejected reason=unknown_challenge")
            raise InvalidCredentials(InvalidCredentials.code)
        # Lockout check, attempt and failure count in one transaction, before comparing: the
        # guess is counted as a failure up front and a success clears it below.
        with self._store.transaction():
            self._check_lockout(challenge.customer_id, now)
            if challenge.consumed_at is None and challenge.attempts >= challenge.max_attempts:
                log.info("login_rejected reason=challenge_exhausted challenge=%s", challenge_id)
                raise TooManyAttempts(0)
            if not self._store.reserve_attempt(challenge_id, now):
                log.info("login_rejected reason=challenge_closed challenge=%s", challenge_id)
                raise InvalidCredentials(InvalidCredentials.code)
            self._store.record_login_failure(challenge.customer_id, now)
        if not hmac.compare_digest(challenge.otp_hash, presented):
            log.info("login_failed challenge=%s", challenge_id)
            raise InvalidCredentials(InvalidCredentials.code)
        customer = self._customers.customer(challenge.customer_id)
        if customer is None or not customer.is_active:
            log.info("login_rejected reason=customer_unavailable challenge=%s", challenge_id)
            raise InvalidCredentials(InvalidCredentials.code)
        with self._store.transaction():
            if not self._store.consume_challenge(challenge_id, now):  # lost a race: single use
                raise InvalidCredentials(InvalidCredentials.code)
            session = Session(
                session_id=self._new_id("ses"),
                customer_id=customer.customer_id,
                issued_at=now,
                expires_at=now + self._settings.session_ttl,
                language=customer.language,
            )
            self._store.save_session(session)
            self._store.clear_failures(customer.customer_id)
        log.info("session_issued session=%s challenge=%s", session.session_id, challenge_id)
        return LoginSuccess(
            token=self._tokens.issue(session),
            expires_at=session.expires_at,
            first_name=customer.first_name,
            language=customer.language,
        )

    # -- sessions ----------------------------------------------------------

    def authenticate(self, token: str) -> Session:
        """The server-side session behind a token.

        ``Unauthorized`` for a bad, unknown or revoked token; ``SessionExpired`` after the TTL.
        """
        now = self._clock()
        session = self._store.get_session(self._tokens.session_id(token, now))
        if session is None:
            raise Unauthorized("invalid session token")
        if not session.is_active(now):
            raise SessionExpired("session expired")
        return session

    def logout(self, token: str) -> None:
        """Revoke the session of a valid token; anything else is a silent no-op."""
        now = self._clock()
        try:
            session_id = self._tokens.session_id(token, now)
        except (Unauthorized, SessionExpired):
            return
        if self._store.revoke_session(session_id, now):
            log.info("session_revoked session=%s", session_id)
