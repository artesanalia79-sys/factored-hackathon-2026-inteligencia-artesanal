"""Log redaction through the real service (T20): the production wiring under TestClient, and
under a real uvicorn started the way the image starts it (import string, ``--factory``).

The service itself never logs a customer message, so the agent of these tests does what a
careless change would do: it logs the message it receives and fails with it in the exception.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from bankagent.api import wiring
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import Language
from bankagent.fixtures.builder import build
from bankagent.orchestrator.agent import AgentTurnOutput
from bankagent.store.ops import OpsStore

SECRET = "test-only-" + "".join(chr(97 + index % 26) for index in range(40))
CARD = "4111111111111111"
CARD_SPACED = "4111 1111 1111 1111"
EMAIL = "ana.perez@example.com"
MESSAGE = f"mi tarjeta es {CARD_SPACED}, o {CARD}, y mi correo es {EMAIL}"
QUERY = "mail=ana.perez%40example.com&card=4111%201111%201111%201111&raw=" + CARD
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
TIMEOUT_S = 20.0

log = logging.getLogger("bankagent.orchestrator.careless")


class CarelessAgent:
    def handle_turn(
        self, session: Session, text: str, /, preferred_language: Language | None = None
    ) -> AgentTurnOutput:
        log.info("turn session=%s text=%s", session.session_id, text)
        log.warning("turn %s", {"text": text})
        try:
            raise ValueError(f"cannot interpret {text}")
        except ValueError:
            log.exception("turn_failed")
        raise RuntimeError(f"cannot handle {text}")


@pytest.fixture(scope="module")
def bank(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("t20-logs") / "bank_fixture.duckdb"
    _, problems = build(out=path)
    assert not problems
    return path


@pytest.fixture
def production_env(bank: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The environment of the default app, with an agent that logs and raises the message."""
    opened: list[OpsStore] = []

    class RecordingStore(OpsStore):
        def __init__(self, source: Path | str) -> None:
            super().__init__(source)
            opened.append(self)

    monkeypatch.setattr(wiring, "OpsStore", RecordingStore)
    monkeypatch.setattr(wiring, "create_agent", lambda **_: CarelessAgent())
    monkeypatch.setenv("SERVING_DB_PATH", str(bank))
    monkeypatch.setenv("OPS_DB_PATH", str(tmp_path / "ops.sqlite"))
    monkeypatch.setenv("APP_SECRET_KEY", SECRET)
    monkeypatch.setenv("DATA_MODE", "synthetic")
    monkeypatch.setenv("AUTH_EXPOSE_MOCK_OTP", "true")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    yield
    for store in opened:
        store.close()


@pytest.fixture
def uvicorn_loggers_restored() -> Iterator[None]:
    """uvicorn configures its loggers when a server is built; leave them as they were."""
    saved = {
        name: (list(logger.handlers), logger.level, logger.propagate)
        for name in UVICORN_LOGGERS
        for logger in (logging.getLogger(name),)
    }
    yield
    for name, (handlers, level, propagate) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


def _login(client: Any) -> tuple[str, str]:
    """(session token, the mock OTP that was used) of a real login."""
    persona = client.get("/api/auth/personas").json()[0]
    challenge = client.post("/api/auth/login", json={"persona_id": persona["persona_id"]}).json()
    otp = challenge["mock_otp"]
    verified = client.post(
        "/api/auth/verify", json={"challenge_id": challenge["challenge_id"], "code": otp}
    )
    assert verified.status_code == 200
    return verified.json()["token"], otp


def _no_trace(text: str) -> bool:
    return CARD not in text and CARD_SPACED not in text and EMAIL not in text


def test_a_message_with_a_card_and_an_email_leaves_no_trace_in_the_logs(
    production_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client = TestClient(wiring.create_default_app(), raise_server_exceptions=False)
        token, _ = _login(client)
        response = client.post(
            "/api/chat/turn?" + QUERY,
            json={"text": MESSAGE},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 500
    careless = [record for record in caplog.records if record.name == log.name]
    # The lines were written, with the session id operations need and without the message.
    assert len(careless) == 3
    assert "turn session=ses-" in careless[0].getMessage()
    assert "[REDACTED:card]" in caplog.text
    assert "[REDACTED:email]" in caplog.text
    assert "ValueError: cannot interpret" in caplog.text
    assert _no_trace(caplog.text)
    assert token not in caplog.text
    for record in caplog.records:
        assert _no_trace(record.getMessage())
        assert _no_trace(record.exc_text or "")


def test_uvicorn_access_and_error_logs_are_redacted(
    production_env: None,
    uvicorn_loggers_restored: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # As in the image: uvicorn configures its logging first, then imports and calls the factory.
    # Port 0 is a free port chosen by the system.
    config = uvicorn.Config(
        "bankagent.api.wiring:create_default_app",
        factory=True,
        host="127.0.0.1",
        port=0,
        log_level="info",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + TIMEOUT_S
        while not server.started:
            assert thread.is_alive(), "the test server stopped before it started"
            assert time.monotonic() < deadline, "the test server did not start"
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=TIMEOUT_S) as client:
            token, otp = _login(client)
            response = client.post(
                f"/api/chat/turn?{QUERY}&otp={otp}&token={token}",
                json={"text": MESSAGE},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 500
    finally:
        server.should_exit = True
        thread.join(timeout=TIMEOUT_S)
    assert not thread.is_alive(), "the test server did not stop"

    captured = capsys.readouterr()
    output = captured.out + captured.err
    access = [line for line in output.splitlines() if '"POST /api/chat/turn' in line]
    assert len(access) == 1, output
    # uvicorn's own formatter wrote the line: the five arguments survived the redaction.
    assert access[0].startswith("INFO:     [REDACTED:ip] - ")
    assert access[0].endswith("500 Internal Server Error")
    assert "[REDACTED:email]" in access[0]
    assert "[REDACTED:card]" in access[0]
    assert "otp=[REDACTED:secret]" in access[0]
    assert "token=[REDACTED:secret]" in access[0]
    # uvicorn.error wrote the traceback of the failed turn.
    assert "Exception in ASGI application" in output
    assert "RuntimeError: cannot handle mi tarjeta es [REDACTED:card]" in output
    assert _no_trace(output)
    assert "example.com" not in output
    assert "4111" not in output
    assert token not in output
    assert f"otp={otp}" not in output
    assert all("127.0.0.1" not in line for line in output.splitlines() if "/api/" in line)
