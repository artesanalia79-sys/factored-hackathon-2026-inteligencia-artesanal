"""Chat endpoint authentication, state isolation and input validation."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from bankagent.api.app import create_app
from bankagent.auth.service import AuthService
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import Language
from bankagent.orchestrator.agent import AgentTurnOutput


class FakeTurnAgent:
    def __init__(self) -> None:
        self.turns = 0
        self.preferred_languages: list[Language | None] = []

    def handle_turn(
        self, session: Session, text: str, /, preferred_language: Language | None = None
    ) -> AgentTurnOutput:
        self.turns += 1
        self.preferred_languages.append(preferred_language)
        return AgentTurnOutput(
            reply_text=f"turno {self.turns}",
            records=(),
            ended=self.turns == 2,
            language=preferred_language or Language.ES,
        )


def test_chat_turn_requires_authentication_and_reuses_agent_until_done(
    service: AuthService, do_login: Callable[..., str]
) -> None:
    instances: list[FakeTurnAgent] = []

    def factory() -> FakeTurnAgent:
        agent = FakeTurnAgent()
        instances.append(agent)
        return agent

    client = TestClient(create_app(auth=service, agent_factory=factory))
    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/api/chat/turn", json={"text": "hola"}).status_code == 401
    header = {"Authorization": f"Bearer {do_login()}"}
    first = client.post("/api/chat/turn", json={"text": "hola"}, headers=header)
    second = client.post("/api/chat/turn", json={"text": "sí"}, headers=header)
    third = client.post("/api/chat/turn", json={"text": "otra consulta"}, headers=header)
    assert first.json() == {
        "reply_text": "turno 1",
        "ended": False,
        "claimed_actions": [],
        "language": "es",
        "confirmation": None,
        "disputed_transaction_id": None,
        "blocked_product_id": None,
    }
    assert second.json() == {
        "reply_text": "turno 2",
        "ended": True,
        "claimed_actions": [],
        "language": "es",
        "confirmation": None,
        "disputed_transaction_id": None,
        "blocked_product_id": None,
    }
    assert third.json()["reply_text"] == "turno 1"
    assert len(instances) == 2
    assert "customer_id" not in first.text + second.text + third.text


def test_chat_rejects_customer_id_without_echoing_it(
    service: AuthService, do_login: Callable[..., str]
) -> None:
    client = TestClient(create_app(auth=service, agent_factory=FakeTurnAgent))
    header = {"Authorization": f"Bearer {do_login()}"}
    response = client.post(
        "/api/chat/turn",
        json={"text": "hola", "customer_id": "CUST-T7-001"},
        headers=header,
    )
    assert response.status_code == 422
    assert "CUST-T7-001" not in response.text


def test_chat_passes_the_selected_language_to_the_agent(
    service: AuthService, do_login: Callable[..., str]
) -> None:
    agent = FakeTurnAgent()
    client = TestClient(create_app(auth=service, agent_factory=lambda: agent))
    header = {"Authorization": f"Bearer {do_login()}"}
    response = client.post("/api/chat/turn", json={"text": "No", "language": "pt"}, headers=header)
    assert response.status_code == 200
    assert response.json()["language"] == "pt"
    assert agent.preferred_languages == [Language.PT]


def test_a_reply_cannot_leave_out_its_language() -> None:
    """The UI takes its language from the reply: a default would report Spanish by omission."""
    with pytest.raises(TypeError, match="language"):
        AgentTurnOutput(reply_text="Olá", records=(), ended=False)  # pyright: ignore[reportCallIssue]
