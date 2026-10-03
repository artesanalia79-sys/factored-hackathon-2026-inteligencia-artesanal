"""Run one full dispute over HTTP against a running service and check every step.

Usage:
    uv run poe smoke http://127.0.0.1:8000
    uv run poe smoke https://<service>.onrender.com
    python3 scripts/smoke_dispute.py http://127.0.0.1:8000     # standard library only

The flow is FX-001 of ``tests/orchestrator/test_api_acceptance.py`` on the synthetic fixture
bank: Mariana does not recognize a charge, confirms the dispute and declines the card block.
A dispute counts only when the service claims it (``claimed_actions``), which it may do only
for a write it read back.

If the service asks for the shared access code, it is read from ``DEMO_ACCESS_CODE`` in the
environment (``poe smoke`` loads ``.env``). It is never taken as an argument and never printed,
and neither are the mock OTP and the session token.

Exit codes: 0 the dispute was created and verified; 1 a step did not behave; 2 the demo state
is used up (this persona already disputed the charge): reset the demo, ``docs/operations.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

PERSONA = "Mariana"
OPENING = "Hola, tengo un cargo de 2,450 pesos en ELECTROMUNDO que no reconozco"
NOT_MINE = "No fui yo"
CONFIRM = "Sí, confirmo"
NO_BLOCK = "No, no la bloquees."
DISPUTE_ID = re.compile(r"\bDSP-[A-Z0-9]+\b")
OK, FAILED, USED_UP = 0, 1, 2


class StepFailed(Exception):
    """A step did not behave as the acceptance flow says."""


class DemoUsedUp(Exception):
    """The persona's charge is already disputed or the persona now escalates."""


class Client:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise SystemExit("the base URL must start with http:// or https://")
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self.token: str | None = None

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self._base + path, data=data, method=method)  # noqa: S310
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token is not None:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            # The scheme is checked in __init__: http or https only.
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return response.status, _json(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _json(error.read())


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def _say(message: str) -> None:
    print(message, flush=True)


def wait_until_ready(client: Client, seconds: float) -> None:
    """Poll `/ready`: a container still starting or a free instance waking up needs a moment."""
    deadline = time.monotonic() + seconds
    last = "no answer"
    while True:
        try:
            status, body = client.call("GET", "/ready")
            if status == 200:
                _say(f"GET /ready -> 200 {body}")
                return
            last = f"{status} {body}"
        except OSError as error:
            last = type(error).__name__
        if time.monotonic() >= deadline:
            raise StepFailed(f"/ready did not answer 200 within {seconds:.0f} s (last: {last})")
        time.sleep(2)


def login(client: Client) -> None:
    status, personas = client.call("GET", "/api/auth/personas")
    if status != 200 or not isinstance(personas, list):
        raise StepFailed(f"GET /api/auth/personas -> {status}")
    persona = next((p for p in personas if p.get("first_name") == PERSONA), None)
    if persona is None:
        raise StepFailed(f"persona {PERSONA} is not served: is this the synthetic fixture bank?")
    body: dict[str, Any] = {"persona_id": persona["persona_id"]}
    access_code = os.environ.get("DEMO_ACCESS_CODE", "").strip()
    if access_code:
        body["access_code"] = access_code
    status, challenge = client.call("POST", "/api/auth/login", body)
    if status == 403:
        raise StepFailed(
            "the service asks for the shared access code: set DEMO_ACCESS_CODE in the "
            "environment (or in .env for `poe smoke`)"
        )
    if status != 200:
        raise StepFailed(f"POST /api/auth/login -> {status}")
    if not challenge.get("mock_otp"):
        raise StepFailed("the login returned no mock OTP: AUTH_EXPOSE_MOCK_OTP is not on")
    status, verified = client.call(
        "POST",
        "/api/auth/verify",
        {"challenge_id": challenge["challenge_id"], "code": challenge["mock_otp"]},
    )
    if status != 200:
        raise StepFailed(f"POST /api/auth/verify -> {status}")
    client.token = verified["token"]
    _say(f"logged in as {verified['first_name']} (session until {verified['expires_at']})")


def turn(client: Client, text: str) -> dict[str, Any]:
    started = time.monotonic()
    status, body = client.call("POST", "/api/chat/turn", {"text": text})
    if status != 200:
        raise StepFailed(f"POST /api/chat/turn -> {status}")
    _say(f"> {text}")
    _say(f"  [{time.monotonic() - started:.1f} s] {body['reply_text']}")
    return body


def run(client: Client, wait_ready: float) -> str:
    """The whole flow. Returns the dispute id the service reported."""
    wait_until_ready(client, wait_ready)
    status, health = client.call("GET", "/health")
    if status != 200 or health != {"status": "ok"}:
        raise StepFailed(f"GET /health -> {status} {health}")
    _say(f"GET /health -> 200 {health}")
    login(client)

    found = turn(client, OPENING)
    if "¿Reconoces este movimiento?" not in found["reply_text"]:
        raise StepFailed("the charge was not found or the recognition question was not asked")
    asked = turn(client, NOT_MINE)
    if (
        asked["claimed_actions"] == ["create_handoff"]
        or "ya tiene un reclamo" in (asked["reply_text"])
    ):
        raise DemoUsedUp
    if "¿Confirmas crear un reclamo" not in asked["reply_text"] or asked["claimed_actions"]:
        raise StepFailed("the confirmation question was not asked before any action")
    created = turn(client, CONFIRM)
    if created["claimed_actions"] != ["create_dispute"]:
        raise StepFailed(f"no verified dispute: claimed_actions={created['claimed_actions']}")
    dispute = DISPUTE_ID.search(created["reply_text"])
    if dispute is None:
        raise StepFailed("the reply claims a dispute but shows no dispute id")
    declined = turn(client, NO_BLOCK)
    if not declined["ended"] or declined["claimed_actions"]:
        raise StepFailed("declining the card block did not end the conversation without a write")
    return dispute.group()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One full dispute over HTTP, checked step by step")
    parser.add_argument("base_url", help="for example http://127.0.0.1:8000")
    parser.add_argument("--wait-ready", type=float, default=120.0, metavar="SECONDS")
    parser.add_argument("--timeout", type=float, default=90.0, metavar="SECONDS")
    args = parser.parse_args(argv)

    client = Client(args.base_url, args.timeout)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        dispute_id = run(client, args.wait_ready)
    except DemoUsedUp:
        _say(
            "USED UP: this persona already disputed the charge, so the service refused or "
            "escalated. Reset the demo (docs/operations.md) and run again."
        )
        return USED_UP
    except (StepFailed, OSError, KeyError, TypeError) as error:
        detail = str(error) if isinstance(error, StepFailed) else type(error).__name__
        _say(f"FAILED: {detail}")
        return FAILED
    _say(f"OK: verified dispute {dispute_id} on {args.base_url} at {now}")
    return OK


if __name__ == "__main__":
    sys.exit(main())
