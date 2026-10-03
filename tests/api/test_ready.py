"""`/ready` on the production wiring: 200 only while the serving DB and the ops store answer.

A failure names the dependency and nothing else: the endpoint is public and an exception text
carries the file path.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bankagent.api import wiring
from bankagent.api.app import create_app
from bankagent.auth.service import AuthService
from bankagent.auth.settings import AuthSettings, Secret
from bankagent.contracts.domain import Session
from bankagent.fixtures.builder import build
from bankagent.orchestrator.agent import AgentTurnOutput
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDB

SECRET = "test-only-" + "".join(chr(97 + index % 26) for index in range(40))


@pytest.fixture(scope="module")
def bank(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("t15-ready") / "bank_fixture.duckdb"
    _, problems = build(out=path)
    assert not problems
    return path


@dataclass
class Served:
    client: TestClient
    serving_path: Path
    store: OpsStore


@pytest.fixture
def served(bank: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Served]:
    """The default app on its own copy of the fixture bank, with the ops store it opened."""
    serving_path = tmp_path / "bank_fixture.duckdb"
    shutil.copy(bank, serving_path)
    opened: list[OpsStore] = []

    class RecordingStore(OpsStore):
        def __init__(self, source: Path | str) -> None:
            super().__init__(source)
            opened.append(self)

    monkeypatch.setattr(wiring, "OpsStore", RecordingStore)
    monkeypatch.setenv("SERVING_DB_PATH", str(serving_path))
    monkeypatch.setenv("OPS_DB_PATH", str(tmp_path / "ops.sqlite"))
    monkeypatch.setenv("APP_SECRET_KEY", SECRET)
    monkeypatch.setenv("DATA_MODE", "synthetic")
    monkeypatch.setenv("AUTH_EXPOSE_MOCK_OTP", "true")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    client = TestClient(wiring.create_default_app())
    (store,) = opened
    yield Served(client, serving_path, store)
    store.close()


def _leaks_nothing(text: str, tmp_path: Path) -> bool:
    lowered = text.lower()
    hidden = (str(tmp_path).lower(), tmp_path.name.lower(), "bank_fixture", "ops.sqlite")
    return not any(part in lowered for part in hidden) and "error" not in lowered


def test_ready_while_both_stores_answer(served: Served) -> None:
    response = served.client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_a_missing_serving_db_is_named_and_nothing_else(
    served: Served, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    served.serving_path.unlink()
    with caplog.at_level(logging.WARNING, logger="bankagent.api"):
        response = served.client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "failed": ["serving_db"]}
    assert _leaks_nothing(response.text, tmp_path)
    # The operator's log names the dependency and the error type, not the path either.
    assert "dependency=serving_db" in caplog.text
    assert str(tmp_path) not in caplog.text


def test_an_unusable_ops_store_is_named_and_nothing_else(served: Served, tmp_path: Path) -> None:
    served.store.close()
    response = served.client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "failed": ["ops_store"]}
    assert _leaks_nothing(response.text, tmp_path)


def test_both_failures_are_listed(served: Served, tmp_path: Path) -> None:
    served.serving_path.unlink()
    served.store.close()
    response = served.client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "failed": ["serving_db", "ops_store"]}
    assert _leaks_nothing(response.text, tmp_path)
    # Liveness is a different question: the process is up, so the platform must not kill it.
    assert served.client.get("/health").json() == {"status": "ok"}


class _NoAgent:
    def handle_turn(self, session: Session, text: str, /) -> AgentTurnOutput:
        raise AssertionError("not used")


def test_ready_exists_only_for_an_app_built_with_probes(bank: Path, tmp_path: Path) -> None:
    with OpsStore(tmp_path / "ops.sqlite") as store:
        auth = AuthService(
            settings=AuthSettings(secret=Secret(SECRET)),
            store=store,
            customers=ServingDB(bank),
            clock=lambda: datetime(2026, 6, 17, 12, tzinfo=UTC),
        )
        probed = create_app(auth=auth, agent_factory=_NoAgent, readiness={"ops_store": bool})
        assert TestClient(probed).get("/ready").json() == {"status": "ready"}
        # Without probes there is nothing to check, so the app must not answer "ready".
        bare = TestClient(create_app(auth=auth, agent_factory=_NoAgent))
        assert bare.get("/health").status_code == 200
        assert bare.get("/ready").status_code == 404
