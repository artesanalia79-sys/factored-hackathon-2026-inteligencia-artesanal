"""Auth over HTTP: the client never sends or receives `customer_id`; bad tokens get 401."""

from collections.abc import Callable
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from bankagent.auth.http import build_auth_router, session_dependency
from bankagent.auth.service import AuthService
from bankagent.contracts.domain import Session


@pytest.fixture
def client(service: AuthService) -> TestClient:
    app = FastAPI()
    app.include_router(build_auth_router(service))
    current_session = session_dependency(service)

    @app.get("/api/protected")
    def protected(session: Annotated[Session, Depends(current_session)]) -> dict[str, str]:
        # A protected endpoint sees the server-side identity without the client sending it.
        return {"served_for_session": session.session_id}

    return TestClient(app)


def _login(client: TestClient, first_name: str = "Mariana") -> dict[str, Any]:
    personas = client.get("/api/auth/personas").json()
    persona = next(p for p in personas if p["first_name"] == first_name)
    challenge = client.post("/api/auth/login", json={"persona_id": persona["persona_id"]}).json()
    verified = client.post(
        "/api/auth/verify",
        json={"challenge_id": challenge["challenge_id"], "code": challenge["mock_otp"]},
    )
    assert verified.status_code == 200
    return verified.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_full_login_flow_never_exposes_customer_id(client: TestClient) -> None:
    personas = client.get("/api/auth/personas")
    assert personas.status_code == 200
    body = _login(client, "Rafael")
    assert body["token_type"] == "bearer"
    assert (body["first_name"], body["language"]) == ("Rafael", "pt")
    session = client.get("/api/auth/session", headers=_bearer(body["token"]))
    assert session.status_code == 200
    assert set(session.json()) == {"expires_at", "language"}
    for response in (personas, session):
        assert "CUST-T7" not in response.text
    assert "CUST-T7" not in str(body)
    assert client.get("/api/protected", headers=_bearer(body["token"])).status_code == 200


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/auth/login", {"persona_id": "per-x", "customer_id": "CUST-T7-001"}),
        ("/api/auth/login", {"customer_id": "CUST-T7-001"}),
        ("/api/auth/verify", {"challenge_id": "c", "code": "1", "customer_id": "CUST-T7-001"}),
    ],
)
def test_customer_id_from_the_client_is_rejected(
    client: TestClient, path: str, body: dict[str, str]
) -> None:
    assert client.post(path, json=body).status_code == 422


def test_unknown_persona_and_wrong_code_get_the_same_401(client: TestClient) -> None:
    unknown = client.post("/api/auth/login", json={"persona_id": "per-unknown"})
    personas = client.get("/api/auth/personas").json()
    challenge = client.post(
        "/api/auth/login", json={"persona_id": personas[0]["persona_id"]}
    ).json()
    wrong = client.post(
        "/api/auth/verify", json={"challenge_id": challenge["challenge_id"], "code": "999999"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json() == {"detail": {"error": "invalid_credentials"}}


def test_too_many_wrong_codes_get_429_with_retry_after(client: TestClient) -> None:
    personas = client.get("/api/auth/personas").json()
    challenge = client.post(
        "/api/auth/login", json={"persona_id": personas[0]["persona_id"]}
    ).json()
    payload = {"challenge_id": challenge["challenge_id"], "code": "999999"}
    for _ in range(3):
        assert client.post("/api/auth/verify", json=payload).status_code == 401
    locked = client.post(
        "/api/auth/verify",
        json={"challenge_id": challenge["challenge_id"], "code": challenge["mock_otp"]},
    )
    assert locked.status_code == 429
    assert locked.headers["Retry-After"] == "0"
    assert locked.json()["detail"]["error"] == "too_many_attempts"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic abc"}],
)
def test_protected_endpoints_need_a_valid_bearer_token(
    client: TestClient, headers: dict[str, str]
) -> None:
    for path in ("/api/auth/session", "/api/protected"):
        response = client.get(path, headers=headers)
        assert response.status_code == 401
        assert response.json() == {"detail": {"error": "unauthorized"}}


def test_tampered_token_is_rejected(client: TestClient) -> None:
    token = _login(client)["token"]
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}{'AA' if signature[-2:] != 'AA' else 'BB'}"
    assert client.get("/api/protected", headers=_bearer(tampered)).status_code == 401


def test_expired_session_is_reported_as_such(client: TestClient, clock: Any) -> None:
    token = _login(client)["token"]
    clock.advance(minutes=15)
    response = client.get("/api/protected", headers=_bearer(token))
    assert response.status_code == 401
    assert response.json() == {"detail": {"error": "session_expired"}}


def test_logout_revokes_the_token(client: TestClient) -> None:
    token = _login(client)["token"]
    assert client.post("/api/auth/logout", headers=_bearer(token)).status_code == 204
    assert client.get("/api/protected", headers=_bearer(token)).status_code == 401
    assert client.post("/api/auth/logout").status_code == 204  # no token: still fine


def test_do_login_fixture_matches_http(client: TestClient, do_login: Callable[..., str]) -> None:
    # A token minted through the service works over HTTP: one source of truth for sessions.
    assert client.get("/api/protected", headers=_bearer(do_login())).status_code == 200
