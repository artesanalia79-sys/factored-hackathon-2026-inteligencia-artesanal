"""The shared demo access code: with it configured no login starts without it, and a refused
caller leaves no trace it could use against a persona."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bankagent.auth.http import build_auth_router
from bankagent.auth.service import AccessCodeRequired, AuthService, InvalidCredentials
from bankagent.auth.settings import AuthConfigError, AuthSettings, Secret
from bankagent.store.ops import OpsStore

if TYPE_CHECKING:
    from .conftest import Clock, Directory, Otps

CODE = "jueces-" + "demo-2026"
REFUSED = {"detail": {"error": "access_code_required"}}


@pytest.fixture
def settings(secret: str) -> AuthSettings:
    return AuthSettings(secret=Secret(secret), expose_mock_otp=True, access_code=Secret(CODE))


@pytest.fixture
def client(service: AuthService) -> TestClient:
    app = FastAPI()
    app.include_router(build_auth_router(service))
    return TestClient(app)


def test_login_without_the_right_code_is_refused_and_writes_nothing(
    client: TestClient, store: OpsStore, persona_of: Callable[[str], str]
) -> None:
    persona = persona_of("Mariana")
    for body in (
        {"persona_id": persona},
        {"persona_id": persona, "access_code": "otro-codigo-1"},
        {"persona_id": persona, "access_code": CODE + "x"},
        {"persona_id": persona, "access_code": CODE[:-1]},
        {"persona_id": persona, "access_code": "contraseña-ñandú"},
    ):
        response = client.post("/api/auth/login", json=body)
        assert response.status_code == 403
        assert response.json() == REFUSED
        assert CODE not in response.text
    assert store.count("login_challenges") == 0
    assert store.count("login_failures") == 0


def test_login_with_the_code_runs_the_normal_flow(
    client: TestClient, persona_of: Callable[[str], str]
) -> None:
    challenge = client.post(
        "/api/auth/login", json={"persona_id": persona_of("Rafael"), "access_code": CODE}
    )
    assert challenge.status_code == 200
    verified = client.post(
        "/api/auth/verify",
        json={
            "challenge_id": challenge.json()["challenge_id"],
            "code": challenge.json()["mock_otp"],
        },
    )
    assert verified.status_code == 200
    token = verified.json()["token"]
    session = client.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert session.status_code == 200
    for response in (challenge, verified, session):
        assert CODE not in response.text


def test_the_code_is_checked_before_the_persona(service: AuthService) -> None:
    # An unknown persona with no code learns nothing about personas.
    with pytest.raises(AccessCodeRequired):
        service.start_login("per-unknown")
    with pytest.raises(InvalidCredentials):
        service.start_login("per-unknown", CODE)


def test_wrong_codes_cannot_lock_a_persona(
    service: AuthService, store: OpsStore, persona_of: Callable[[str], str]
) -> None:
    persona = persona_of("Mariana")
    for _ in range(10):  # twice the lockout threshold
        with pytest.raises(AccessCodeRequired):
            service.start_login(persona, "otro-codigo-1")
    assert store.count("login_failures") == 0
    assert service.start_login(persona, CODE).mock_otp


def test_no_code_configured_means_no_gate(
    secret: str, store: OpsStore, directory: Directory, clock: Clock, otps: Otps
) -> None:
    open_service = AuthService(
        settings=AuthSettings(secret=Secret(secret), expose_mock_otp=True),
        store=store,
        customers=directory,
        clock=clock,
        otp_factory=otps,
    )
    persona = open_service.personas()[0].persona_id
    assert open_service.start_login(persona).mock_otp
    # A client that sends one anyway (a UI with the field filled in) is not punished for it.
    assert open_service.start_login(persona, "cualquier-cosa").mock_otp


def test_settings_read_the_code_from_the_environment(secret: str) -> None:
    env = {"APP_SECRET_KEY": secret}
    assert AuthSettings.from_env(env).access_code is None
    assert AuthSettings.from_env({**env, "DEMO_ACCESS_CODE": "   "}).access_code is None
    configured = AuthSettings.from_env({**env, "DEMO_ACCESS_CODE": f" {CODE}\n"})
    assert configured.access_code is not None
    assert configured.access_code.reveal() == CODE.encode()
    assert CODE not in repr(configured)


@pytest.mark.parametrize("weak", ["corto", "1234567", "contraseña-ñandú"])
def test_a_weak_code_is_refused_at_startup_without_echoing_it(secret: str, weak: str) -> None:
    with pytest.raises(AuthConfigError, match="DEMO_ACCESS_CODE") as caught:
        AuthSettings.from_env({"APP_SECRET_KEY": secret, "DEMO_ACCESS_CODE": weak})
    assert weak not in str(caught.value)
