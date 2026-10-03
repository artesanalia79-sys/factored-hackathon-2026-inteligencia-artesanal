"""The HTTP smoke script against a real server: it is the evidence for the image and for staging,
so it must pass on a fresh service and say so plainly when the demo state is used up."""

from __future__ import annotations

import importlib.util
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import uvicorn

from bankagent.api.wiring import create_default_app
from bankagent.fixtures.builder import build

ROOT = Path(__file__).resolve().parents[2]
SECRET = "test-only-" + "".join(chr(97 + index % 26) for index in range(40))
CODE = "jueces-" + "demo-2026"


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load("smoke_dispute", "scripts/smoke_dispute.py")


@pytest.fixture(scope="module")
def bank(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("t15-smoke") / "bank_fixture.duckdb"
    _, problems = build(out=path)
    assert not problems
    return path


def _serve(
    bank: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, access_code: str | None
) -> Iterator[str]:
    monkeypatch.setenv("SERVING_DB_PATH", str(bank))
    monkeypatch.setenv("OPS_DB_PATH", str(tmp_path / "ops.sqlite"))
    monkeypatch.setenv("APP_SECRET_KEY", SECRET)
    monkeypatch.setenv("DATA_MODE", "synthetic")
    monkeypatch.setenv("AUTH_EXPOSE_MOCK_OTP", "true")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    if access_code is None:
        monkeypatch.delenv("DEMO_ACCESS_CODE", raising=False)
    else:
        monkeypatch.setenv("DEMO_ACCESS_CODE", access_code)
    config = uvicorn.Config(create_default_app(), host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        assert time.monotonic() < deadline, "the test server did not start"
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def open_service(bank: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    yield from _serve(bank, tmp_path, monkeypatch, access_code=None)


@pytest.fixture
def gated_service(bank: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    yield from _serve(bank, tmp_path, monkeypatch, access_code=CODE)


def test_a_fresh_service_ends_in_a_verified_dispute_and_a_second_run_says_used_up(
    open_service: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert smoke.main([open_service, "--wait-ready", "20"]) == smoke.OK
    first = capsys.readouterr().out
    assert "GET /ready -> 200" in first
    assert "OK: verified dispute DSP-" in first
    # One dispute per transaction is permanent: the same flow again is refused, not repeated.
    assert smoke.main([open_service, "--wait-ready", "20"]) == smoke.USED_UP
    assert "USED UP" in capsys.readouterr().out


def test_a_gated_service_needs_the_code_from_the_environment(
    gated_service: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The server already read its own copy; this is the caller's environment.
    monkeypatch.delenv("DEMO_ACCESS_CODE")
    assert smoke.main([gated_service, "--wait-ready", "20"]) == smoke.FAILED
    assert "DEMO_ACCESS_CODE" in capsys.readouterr().out
    monkeypatch.setenv("DEMO_ACCESS_CODE", CODE)
    assert smoke.main([gated_service, "--wait-ready", "20"]) == smoke.OK
    output = capsys.readouterr().out
    assert "OK: verified dispute DSP-" in output
    assert CODE not in output


def test_an_unreachable_service_fails_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert smoke.main(["http://127.0.0.1:9", "--wait-ready", "0", "--timeout", "2"]) == smoke.FAILED
    assert "FAILED: /ready did not answer 200" in capsys.readouterr().out
