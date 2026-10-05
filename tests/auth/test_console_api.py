"""The human-agent console (T21): gated by the shared access code, never by a customer session."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from bankagent.api.app import create_app
from bankagent.api.console import ConsoleDeps
from bankagent.auth.service import AuthService
from bankagent.auth.settings import AuthSettings, Secret
from bankagent.contracts.domain import CardBlockEvent, DisputeCase
from bankagent.contracts.enums import (
    ConversationState,
    DisputeReason,
    DisputeStatus,
    Intent,
    Language,
    Priority,
    Specialty,
    StepKind,
    StepOutcome,
    ToolName,
)
from bankagent.contracts.handoff import HandoffDraft, HandoffPacket, HandoffRouting
from bankagent.contracts.records import ExecutionRecord
from bankagent.orchestrator.agent import AgentTurnOutput
from bankagent.policy.schema import load_policy
from bankagent.store.console import HandoffConsole
from bankagent.store.ops import OpsStore

if TYPE_CHECKING:
    from .conftest import Clock, Directory, Otps

CODE = "jueces-" + "demo-2026"
NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


class _NoAgent:
    def handle_turn(self, *args: object, **kwargs: object) -> AgentTurnOutput:
        raise AssertionError("the console never drives the chat agent")


@pytest.fixture
def settings(secret: str) -> AuthSettings:
    return AuthSettings(secret=Secret(secret), expose_mock_otp=True, access_code=Secret(CODE))


def _seed(store: OpsStore) -> None:
    store.insert_dispute(
        "CUST-A",
        DisputeCase(
            dispute_id="dsp-1",
            transaction_id="txn-1",
            reason=DisputeReason.UNRECOGNIZED,
            status=DisputeStatus.SUBMITTED,
            created_at=NOW,
            amount=Decimal("100.00"),
            currency="MXN",
            idempotency_key="idem-1",
            policy_version="dispute-v1.1",
            rule_ids=(),
        ),
    )
    store.insert_card_block(
        "CUST-A",
        CardBlockEvent(
            block_id="blk-1",
            product_id="prod-1",
            card_last4="1234",
            blocked_at=NOW,
            reason="unrecognized charge",
            idempotency_key="idem-2",
        ),
    )
    draft = HandoffDraft(
        trace_id="tr-1",
        language=Language.ES,
        request="cliente no reconoce un cargo",
        intent=Intent.DISPUTE_UNRECOGNIZED,
        policy_version="dispute-v1.1",
        routing=HandoffRouting(
            specialty=Specialty.FRAUD, language=Language.ES, priority=Priority.HIGH
        ),
    )
    packet = HandoffPacket(
        **draft.model_dump(), handoff_id="hof-1", created_at=NOW, customer_id="CUST-A"
    )
    store.insert_handoff(packet, "idem-3")
    store.append_records(
        [
            ExecutionRecord(
                record_id="rec-1",
                trace_id="tr-1",
                turn_index=0,
                step_index=0,
                step=StepKind.TOOL_CALL,
                state=ConversationState.IDENTIFY_TXN,
                tool=ToolName.CREATE_HANDOFF,
                outcome=StepOutcome.SUCCESS,
                verified=True,
                latency_ms=5.0,
                created_at=NOW,
            )
        ]
    )


@pytest.fixture
def client(service: AuthService, store: OpsStore) -> TestClient:
    _seed(store)
    console = HandoffConsole(store.database)
    deps = ConsoleDeps(
        list_handoffs=console.list_handoffs,
        get_handoff=console.get_handoff,
        list_disputes=console.list_disputes,
        list_card_blocks=console.list_card_blocks,
        list_records=console.list_records,
        policy=load_policy(),
    )
    app = create_app(auth=service, agent_factory=_NoAgent, console=deps)
    return TestClient(app)


def test_no_header_and_wrong_code_are_both_refused(client: TestClient) -> None:
    for headers in ({}, {"X-Console-Access-Code": CODE + "x"}):
        response = client.get("/api/console/handoffs", headers=headers)
        assert response.status_code == 401
        assert CODE not in response.text


def test_the_right_code_opens_every_console_read(client: TestClient) -> None:
    headers = {"X-Console-Access-Code": CODE}
    handoffs = client.get("/api/console/handoffs", headers=headers)
    assert handoffs.status_code == 200
    body = handoffs.json()
    assert len(body) == 1
    assert body[0]["handoff"]["customer_id"] == "CUST-A"

    detail = client.get("/api/console/handoffs/hof-1", headers=headers)
    assert detail.status_code == 200
    assert [r["record_id"] for r in detail.json()["records"]] == ["rec-1"]

    disputes = client.get("/api/console/disputes", headers=headers)
    assert disputes.status_code == 200
    assert disputes.json()[0]["customer_id"] == "CUST-A"
    assert disputes.json()[0]["case"]["dispute_id"] == "dsp-1"

    blocks = client.get("/api/console/card-blocks", headers=headers)
    assert blocks.status_code == 200
    assert blocks.json()[0]["event"]["block_id"] == "blk-1"


def test_an_unknown_handoff_is_a_404_not_a_500(client: TestClient) -> None:
    response = client.get(
        "/api/console/handoffs/does-not-exist", headers={"X-Console-Access-Code": CODE}
    )
    assert response.status_code == 404


def test_a_customer_session_token_alone_does_not_open_the_console(
    client: TestClient, service: AuthService, persona_of: Callable[[str], str]
) -> None:
    """The console is gated by the shared code, never by being a signed-in customer (BOLA)."""
    challenge = service.start_login(persona_of("Mariana"), CODE)
    assert challenge.mock_otp is not None
    token = service.verify_otp(challenge.challenge_id, challenge.mock_otp).token
    response = client.get("/api/console/handoffs", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_no_code_configured_means_no_gate(
    secret: str, store: OpsStore, directory: Directory, clock: Clock, otps: Otps
) -> None:
    _seed(store)
    console = HandoffConsole(store.database)
    open_service = AuthService(
        settings=AuthSettings(secret=Secret(secret), expose_mock_otp=True),
        store=store,
        customers=directory,
        clock=clock,
        otp_factory=otps,
    )
    app = create_app(
        auth=open_service,
        agent_factory=_NoAgent,
        console=ConsoleDeps(
            list_handoffs=console.list_handoffs,
            get_handoff=console.get_handoff,
            list_disputes=console.list_disputes,
            list_card_blocks=console.list_card_blocks,
            list_records=console.list_records,
            policy=load_policy(),
        ),
    )
    response = TestClient(app).get("/api/console/handoffs")
    assert response.status_code == 200


def test_no_console_deps_means_the_path_does_not_exist_at_all(service: AuthService) -> None:
    app = create_app(auth=service, agent_factory=_NoAgent)
    response = TestClient(app).get("/api/console/handoffs", headers={"X-Console-Access-Code": CODE})
    assert response.status_code == 404
