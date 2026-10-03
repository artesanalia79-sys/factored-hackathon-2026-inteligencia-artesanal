"""The API serves the built web UI (T14) next to its routes, with the UI's security headers."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bankagent.api.app import WEB_SECURITY_HEADERS, create_app
from bankagent.auth.service import AuthService
from bankagent.contracts.domain import Session
from bankagent.orchestrator.agent import AgentTurnOutput


class EchoAgent:
    def handle_turn(self, session: Session, text: str, /) -> AgentTurnOutput:
        return AgentTurnOutput(reply_text=text, records=(), ended=False)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    built = tmp_path / "dist"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("<!doctype html><title>UI</title>", encoding="utf-8")
    (built / "assets" / "app.js").write_text("export {};\n", encoding="utf-8")
    return built


def _client(service: AuthService, web_dist: Path | None) -> TestClient:
    return TestClient(create_app(auth=service, agent_factory=EchoAgent, web_dist=web_dist))


def test_built_ui_is_served_with_its_security_headers(service: AuthService, dist: Path) -> None:
    client = _client(service, dist)
    for path in ("/", "/assets/app.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        for header, value in WEB_SECURITY_HEADERS.items():
            assert response.headers[header] == value, (path, header)
    assert "<title>UI</title>" in client.get("/").text
    assert "frame-ancestors 'none'" in WEB_SECURITY_HEADERS["Content-Security-Policy"]


def test_api_routes_keep_precedence_over_the_ui(service: AuthService, dist: Path) -> None:
    client = _client(service, dist)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/auth/personas").status_code == 200
    assert client.post("/api/chat/turn", json={"text": "hola"}).status_code == 401
    # The API docs load their assets from a CDN: the UI's policy must not reach them.
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "Content-Security-Policy" not in docs.headers


@pytest.mark.parametrize("build", ["missing", "without_index"])
def test_without_a_build_only_the_api_is_served(
    service: AuthService, tmp_path: Path, build: str
) -> None:
    web_dist = tmp_path / "dist"
    if build == "without_index":
        web_dist.mkdir()
    client = _client(service, web_dist)
    assert client.get("/").status_code == 404
    assert client.get("/health").json() == {"status": "ok"}
    assert _client(service, None).get("/").status_code == 404


def test_readiness_is_not_shadowed_by_the_ui(service: AuthService, dist: Path) -> None:
    """The UI is mounted at / last: `/ready` (T15) still answers for the platform's checks."""
    app = create_app(
        auth=service, agent_factory=EchoAgent, web_dist=dist, readiness={"probe": lambda: None}
    )
    client = TestClient(app)
    assert client.get("/ready").json() == {"status": "ready"}
    assert "<title>UI</title>" in client.get("/").text
