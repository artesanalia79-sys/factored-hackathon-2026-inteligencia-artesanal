"""Session tokens: tampered, expired, wrongly signed and malformed tokens are rejected."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from bankagent.auth.settings import AuthConfigError, AuthSettings, Secret
from bankagent.auth.tokens import SessionTokens
from bankagent.contracts.domain import Session
from bankagent.contracts.errors import SessionExpired, Unauthorized

SECRET = "token-secret-" + "s" * 60
NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
SESSION = Session(
    session_id="ses-abc",
    customer_id="CUST-T7-001",
    issued_at=NOW,
    expires_at=NOW + timedelta(minutes=15),
)


@pytest.fixture
def tokens() -> SessionTokens:
    return SessionTokens(Secret(SECRET), "bankagent")


def _forge(claims: dict[str, object], key: str = SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, key, algorithm=algorithm)


def _claims(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "iss": "bankagent",
        "sid": "ses-abc",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=15)).timestamp()),
    }
    return {k: v for k, v in {**base, **changes}.items() if v is not None}


def test_valid_token_returns_the_session_id(tokens: SessionTokens) -> None:
    token = tokens.issue(SESSION)
    assert tokens.session_id(token, NOW + timedelta(minutes=14, seconds=59)) == "ses-abc"


def test_token_carries_no_customer_identity(tokens: SessionTokens) -> None:
    payload = tokens.issue(SESSION).split(".")[1]
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert set(decoded) == {"iss", "sid", "iat", "exp"}
    assert "CUST-T7-001" not in json.dumps(decoded)


def test_expired_token_is_rejected_as_session_expired(tokens: SessionTokens) -> None:
    token = tokens.issue(SESSION)
    with pytest.raises(SessionExpired):
        tokens.session_id(token, NOW + timedelta(minutes=15))


def test_token_from_the_future_is_rejected(tokens: SessionTokens) -> None:
    with pytest.raises(Unauthorized):
        tokens.session_id(tokens.issue(SESSION), NOW - timedelta(seconds=5))


def test_tampered_payload_is_rejected(tokens: SessionTokens) -> None:
    header, _, signature = tokens.issue(SESSION).split(".")
    forged = base64.urlsafe_b64encode(json.dumps(_claims(sid="ses-victim")).encode()).rstrip(b"=")
    with pytest.raises(Unauthorized):
        tokens.session_id(f"{header}.{forged.decode()}.{signature}", NOW)


def test_tampered_signature_is_rejected(tokens: SessionTokens) -> None:
    token = tokens.issue(SESSION)
    flipped = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(Unauthorized):
        tokens.session_id(flipped, NOW)


def test_wrong_signing_key_is_rejected(tokens: SessionTokens) -> None:
    with pytest.raises(Unauthorized):
        tokens.session_id(_forge(_claims(), key="another-secret-" + "x" * 40), NOW)


def test_unsigned_and_other_algorithms_are_rejected(tokens: SessionTokens) -> None:
    unsigned = jwt.encode(_claims(), key="", algorithm="none")
    with pytest.raises(Unauthorized):
        tokens.session_id(unsigned, NOW)
    with pytest.raises(Unauthorized):
        tokens.session_id(_forge(_claims(), algorithm="HS512"), NOW)


@pytest.mark.parametrize(
    "claims",
    [
        _claims(sid=None),
        _claims(exp=None),
        _claims(iat=None),
        _claims(iss=None),
        _claims(iss="someone-else"),
        _claims(sid=""),
        _claims(sid=123),
    ],
)
def test_missing_or_wrong_claims_are_rejected(
    tokens: SessionTokens, claims: dict[str, object]
) -> None:
    with pytest.raises(Unauthorized):
        tokens.session_id(_forge(claims), NOW)


@pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b.c", "a.b"])
def test_garbage_is_rejected(tokens: SessionTokens, garbage: str) -> None:
    with pytest.raises(Unauthorized):
        tokens.session_id(garbage, NOW)


def test_error_message_does_not_say_why(tokens: SessionTokens) -> None:
    with pytest.raises(Unauthorized) as wrong_key:
        tokens.session_id(_forge(_claims(), key="another-secret-" + "x" * 40), NOW)
    with pytest.raises(Unauthorized) as garbage:
        tokens.session_id("not-a-token", NOW)
    assert wrong_key.value.message == garbage.value.message


def test_secret_is_masked_and_must_be_long_enough() -> None:
    secret = Secret(SECRET)
    assert SECRET not in repr(secret)
    assert SECRET not in f"{secret}"
    assert SECRET not in repr(AuthSettings(secret=secret))
    with pytest.raises(AuthConfigError, match="at least"):
        AuthSettings(secret=Secret("short"))
    with pytest.raises(AuthConfigError, match="not set"):
        AuthSettings.from_env({})
    assert AuthSettings.from_env({"APP_SECRET_KEY": SECRET}).secret.reveal() == SECRET.encode()
