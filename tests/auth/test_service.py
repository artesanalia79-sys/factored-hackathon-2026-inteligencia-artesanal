"""Login flow: OTP attempt limits, lockout, single use, server-side sessions, clean logs.

Fixtures (`service`, `otps`, `clock`, `directory`, `persona_of`, `do_login`) are in conftest.py.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest

from bankagent.auth.service import AuthService, InvalidCredentials, TooManyAttempts
from bankagent.auth.settings import AuthSettings
from bankagent.contracts.enums import Language
from bankagent.contracts.errors import SessionExpired, Unauthorized
from bankagent.store.ops import OpsStore

PersonaOf = Callable[[str], str]
DoLogin = Callable[..., str]
WRONG = "999999"


def _fail(service: AuthService, challenge_id: str, times: int) -> None:
    for _ in range(times):
        with pytest.raises(InvalidCredentials):
            service.verify_otp(challenge_id, WRONG)


def test_personas_are_active_customers_behind_opaque_ids(service: AuthService) -> None:
    personas = service.personas()
    assert [p.first_name for p in personas] == ["Mariana", "Rafael"]  # the closed one is out
    assert personas[1].language == Language.PT
    for persona in personas:
        assert persona.persona_id.startswith("per-")
        assert "CUST" not in persona.persona_id


def test_successful_login_opens_a_server_side_session(
    service: AuthService, otps: Any, store: OpsStore, persona_of: PersonaOf
) -> None:
    challenge = service.start_login(persona_of("Rafael"))
    assert challenge.mock_otp == otps.issued[-1]  # demo delivery
    result = service.verify_otp(challenge.challenge_id, otps.issued[-1])
    assert (result.first_name, result.language) == ("Rafael", Language.PT)
    session = service.authenticate(result.token)
    assert session.customer_id == "CUST-T7-002"
    assert session.language == Language.PT
    assert store.get_session(session.session_id) == session
    assert "CUST-T7-002" not in result.token


def test_mock_otp_can_be_hidden(
    settings: AuthSettings,
    store: OpsStore,
    directory: Any,
    clock: Any,
    otps: Any,
    persona_of: PersonaOf,
) -> None:
    hidden = AuthService(
        settings=dataclasses.replace(settings, expose_mock_otp=False),
        store=store,
        customers=directory,
        clock=clock,
        otp_factory=otps,
    )
    assert hidden.start_login(persona_of("Mariana")).mock_otp is None


def test_customer_id_is_not_a_login_handle(service: AuthService) -> None:
    for handle in ("CUST-T7-001", "per-unknown", ""):
        with pytest.raises(InvalidCredentials):
            service.start_login(handle)


def test_wrong_codes_are_limited_per_challenge(
    service: AuthService, otps: Any, persona_of: PersonaOf
) -> None:
    challenge = service.start_login(persona_of("Mariana"))
    _fail(service, challenge.challenge_id, 3)
    # Exhausted: even the right code is refused now.
    with pytest.raises(TooManyAttempts) as locked:
        service.verify_otp(challenge.challenge_id, otps.issued[-1])
    assert locked.value.retry_after_seconds == 0


def test_new_challenges_do_not_reset_the_customer_lockout(
    service: AuthService, otps: Any, clock: Any, persona_of: PersonaOf, do_login: DoLogin
) -> None:
    mariana = persona_of("Mariana")
    first = service.start_login(mariana)
    _fail(service, first.challenge_id, 3)
    second = service.start_login(mariana)
    _fail(service, second.challenge_id, 2)
    # 5 failures inside the window: locked for start and for verify, right code included.
    with pytest.raises(TooManyAttempts) as locked:
        service.start_login(mariana)
    assert 0 < locked.value.retry_after_seconds <= 15 * 60
    with pytest.raises(TooManyAttempts):
        service.verify_otp(second.challenge_id, otps.issued[-1])
    # Another customer is unaffected.
    assert do_login("Rafael")
    # After the window the customer can log in again.
    clock.advance(minutes=15, seconds=1)
    assert do_login("Mariana")


def test_success_clears_earlier_failures(
    service: AuthService, otps: Any, persona_of: PersonaOf, do_login: DoLogin
) -> None:
    for _ in range(2):
        challenge = service.start_login(persona_of("Mariana"))
        _fail(service, challenge.challenge_id, 2)
        service.verify_otp(challenge.challenge_id, otps.issued[-1])
    # 4 failures in total, but each success reset the count: no lockout.
    assert do_login("Mariana")


def test_challenge_is_single_use(service: AuthService, otps: Any, persona_of: PersonaOf) -> None:
    challenge = service.start_login(persona_of("Mariana"))
    service.verify_otp(challenge.challenge_id, otps.issued[-1])
    with pytest.raises(InvalidCredentials):
        service.verify_otp(challenge.challenge_id, otps.issued[-1])


def test_expired_and_unknown_challenges_are_rejected(
    service: AuthService, otps: Any, clock: Any, persona_of: PersonaOf
) -> None:
    challenge = service.start_login(persona_of("Mariana"))
    clock.advance(minutes=5)
    with pytest.raises(InvalidCredentials):
        service.verify_otp(challenge.challenge_id, otps.issued[-1])
    with pytest.raises(InvalidCredentials):
        service.verify_otp("chl-unknown", "000001")


def test_code_of_another_challenge_does_not_work(
    service: AuthService, otps: Any, persona_of: PersonaOf
) -> None:
    first = service.start_login(persona_of("Mariana"))
    first_code = otps.issued[-1]
    second = service.start_login(persona_of("Rafael"))
    with pytest.raises(InvalidCredentials):
        service.verify_otp(second.challenge_id, first_code)
    assert service.verify_otp(first.challenge_id, first_code)


def test_customer_closed_after_the_challenge_cannot_finish_login(
    service: AuthService, otps: Any, directory: Any, persona_of: PersonaOf
) -> None:
    challenge = service.start_login(persona_of("Mariana"))
    mariana = directory.profiles["CUST-T7-001"]
    directory.profiles["CUST-T7-001"] = dataclasses.replace(mariana, customer_status="Closed")
    with pytest.raises(InvalidCredentials):
        service.verify_otp(challenge.challenge_id, otps.issued[-1])


def test_session_expires_after_its_ttl(service: AuthService, clock: Any, do_login: DoLogin) -> None:
    token = do_login()
    clock.advance(minutes=14, seconds=59)
    assert service.authenticate(token)
    clock.advance(seconds=1)
    with pytest.raises(SessionExpired):
        service.authenticate(token)


def test_logout_revokes_the_session(service: AuthService, do_login: DoLogin) -> None:
    token = do_login()
    service.logout(token)
    with pytest.raises(Unauthorized):
        service.authenticate(token)
    service.logout(token)  # idempotent
    service.logout("garbage")  # silent


def test_valid_signature_without_a_stored_session_is_rejected(
    settings: AuthSettings, clock: Any, directory: Any, do_login: DoLogin
) -> None:
    token = do_login()
    # Same secret, different (empty) store: the signature is fine, the session does not exist.
    with OpsStore(":memory:") as empty:
        other = AuthService(settings=settings, store=empty, customers=directory, clock=clock)
        with pytest.raises(Unauthorized):
            other.authenticate(token)


def test_logs_carry_no_secret_token_otp_name_or_customer_id(
    service: AuthService,
    otps: Any,
    secret: str,
    persona_of: PersonaOf,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    mariana = persona_of("Mariana")
    challenge = service.start_login(mariana)
    _fail(service, challenge.challenge_id, 1)
    token = service.verify_otp(challenge.challenge_id, otps.issued[-1]).token
    service.authenticate(token)
    service.logout(token)
    with pytest.raises(InvalidCredentials):
        service.start_login("per-unknown")
    text = caplog.text
    assert "login_challenge_created" in text
    assert "session_issued" in text
    for forbidden in (secret, token, otps.issued[-1], WRONG, "Mariana", "CUST-T7-001", mariana):
        assert forbidden not in text


@pytest.mark.parametrize("hostile", ["per-ñandú", "１２３４５６", "\ud800", "per-\x00"])
def test_non_ascii_input_is_rejected_cleanly(
    service: AuthService, persona_of: PersonaOf, hostile: str
) -> None:
    # Regression: non-ASCII used to raise TypeError / UnicodeEncodeError (HTTP 500).
    challenge = service.start_login(persona_of("Mariana"))
    for call in (
        lambda: service.start_login(hostile),
        lambda: service.verify_otp(hostile, "000001"),
        lambda: service.verify_otp(challenge.challenge_id, hostile),
    ):
        with pytest.raises(InvalidCredentials):
            call()


def test_concurrent_guesses_across_challenges_cannot_beat_the_lockout(
    service: AuthService, persona_of: PersonaOf
) -> None:
    # Regression: the lockout check and the failure count were separate steps, so guesses
    # fired in parallel over many challenges were all evaluated before the lockout applied.
    challenges = [service.start_login(persona_of("Mariana")).challenge_id for _ in range(30)]
    evaluated = 0
    lock = threading.Lock()

    def guess(challenge_id: str) -> None:
        nonlocal evaluated
        try:
            service.verify_otp(challenge_id, WRONG)
        except InvalidCredentials:
            with lock:
                evaluated += 1
        except TooManyAttempts:
            pass

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(guess, challenges * 3))
    assert evaluated == 5  # exactly `lockout_failures`, never more


def test_old_challenges_and_failures_are_purged(
    service: AuthService, store: OpsStore, clock: Any, persona_of: PersonaOf
) -> None:
    for _ in range(3):
        _fail(service, service.start_login(persona_of("Mariana")).challenge_id, 1)
    assert store.count("login_challenges") == 3
    clock.advance(minutes=21)  # past the 5 min OTP TTL plus the 15 min lockout window
    service.start_login(persona_of("Rafael"))
    assert store.count("login_challenges") == 1
    assert store.failures_since("CUST-T7-001", clock.now - timedelta(days=1)) == []
