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


# A real persona of the shared `directory` fixture (tests/auth/conftest.py), so customer_name
# resolution has something real to find; "CUST-A" stays unknown to it on purpose (below).
MARIANA_ID = "CUST-T7-001"


def _seed(store: OpsStore, customer_id: str = MARIANA_ID) -> None:
    store.insert_dispute(
        customer_id,
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
            # DSP-ACT-01 (card_blockable, an action rule) is the decisive one; DSP-ELIG-01
            # (approved_status, eligibility) was checked and passed, nothing case-specific.
            rule_ids=("DSP-ACT-01", "DSP-ELIG-01"),
        ),
    )
    store.insert_card_block(
        customer_id,
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
        **draft.model_dump(), handoff_id="hof-1", created_at=NOW, customer_id=customer_id
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


def _seed_more(store: OpsStore) -> None:
    """A second case of each kind, for another customer, created a minute later."""
    later = NOW.replace(minute=1)
    store.insert_dispute(
        "CUST-B",
        DisputeCase(
            dispute_id="dsp-2",
            transaction_id="txn-2",
            reason=DisputeReason.UNRECOGNIZED,
            status=DisputeStatus.SUBMITTED,
            created_at=later,
            amount=Decimal("5.00"),
            currency="MXN",
            idempotency_key="idem-4",
            policy_version="dispute-v1.1",
            # Stored in this order on purpose: the decisive rule must still come first.
            rule_ids=("DSP-ELIG-01", "DSP-ACT-01"),
        ),
    )
    store.insert_card_block(
        "CUST-B",
        CardBlockEvent(
            block_id="blk-2",
            product_id="prod-2",
            card_last4="1234",
            blocked_at=later,
            reason="unrecognized charge",
            idempotency_key="idem-5",
        ),
    )
    draft = HandoffDraft(
        trace_id="tr-2",
        language=Language.ES,
        request="cargo de alto riesgo",
        intent=Intent.DISPUTE_UNRECOGNIZED,
        trigger_rule_ids=("DSP-ESC-01",),
        policy_version="dispute-v1.1",
        routing=HandoffRouting(
            specialty=Specialty.FRAUD, language=Language.ES, priority=Priority.HIGH
        ),
    )
    store.insert_handoff(
        HandoffPacket(
            **draft.model_dump(), handoff_id="hof-2", created_at=later, customer_id="CUST-B"
        ),
        "idem-6",
    )


def _name_lookup(directory: Directory) -> Callable[[str], str | None]:
    def lookup(customer_id: str) -> str | None:
        profile = directory.customer(customer_id)
        return None if profile is None else profile.first_name

    return lookup


@pytest.fixture
def client(service: AuthService, store: OpsStore, directory: Directory) -> TestClient:
    _seed(store)
    console = HandoffConsole(store.database)
    deps = ConsoleDeps(
        list_handoffs=console.list_handoffs,
        get_handoff=console.get_handoff,
        list_disputes=console.list_disputes,
        list_card_blocks=console.list_card_blocks,
        list_records=console.list_records,
        customer_name=_name_lookup(directory),
        policy=load_policy(),
    )
    app = create_app(auth=service, agent_factory=_NoAgent, console=deps)
    return TestClient(app)


# Every console read, each behind its own `Depends(require_access)`: one missing is a leak.
EVERY_READ = (
    "/api/console/handoffs",
    "/api/console/handoffs/hof-1",
    "/api/console/disputes",
    "/api/console/card-blocks",
)


@pytest.mark.parametrize("path", EVERY_READ)
def test_no_header_and_wrong_code_are_both_refused(client: TestClient, path: str) -> None:
    for headers in ({}, {"X-Console-Access-Code": CODE + "x"}, {"X-Console-Access-Code": ""}):
        response = client.get(path, headers=headers)
        assert response.status_code == 401
        assert response.json() == {"detail": {"error": "access_code_required"}}
        assert CODE not in response.text


@pytest.mark.parametrize("path", EVERY_READ)
def test_every_read_is_never_stored_by_a_cache(client: TestClient, path: str) -> None:
    response = client.get(path, headers={"X-Console-Access-Code": CODE})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/console/handoffs", "/api/console/disputes"])
@pytest.mark.parametrize("limit", ["0", "-1", "201", str(10**20)])
def test_a_limit_outside_1_to_200_is_a_422_not_every_row_or_a_500(
    client: TestClient, path: str, limit: str
) -> None:
    """SQLite reads LIMIT -1 as no limit, and an int past 64 bits made the read raise (500)."""
    response = client.get(path, params={"limit": limit}, headers={"X-Console-Access-Code": CODE})
    assert response.status_code == 422


def test_a_limit_inside_the_bounds_is_applied(client: TestClient, store: OpsStore) -> None:
    _seed_more(store)
    headers = {"X-Console-Access-Code": CODE}
    for path in ("/api/console/handoffs", "/api/console/disputes", "/api/console/card-blocks"):
        assert len(client.get(path, headers=headers).json()) == 2
        assert len(client.get(path, params={"limit": 1}, headers=headers).json()) == 1
        assert len(client.get(path, params={"limit": 200}, headers=headers).json()) == 2


def test_the_right_code_opens_every_console_read(client: TestClient) -> None:
    headers = {"X-Console-Access-Code": CODE}
    handoffs = client.get("/api/console/handoffs", headers=headers)
    assert handoffs.status_code == 200
    body = handoffs.json()
    assert len(body) == 1
    assert body[0]["handoff"]["customer_id"] == MARIANA_ID
    assert body[0]["customer_name"] == "Mariana"

    detail = client.get("/api/console/handoffs/hof-1", headers=headers)
    assert detail.status_code == 200
    assert [r["record_id"] for r in detail.json()["records"]] == ["rec-1"]

    disputes = client.get("/api/console/disputes", headers=headers)
    assert disputes.status_code == 200
    dispute = disputes.json()[0]
    assert dispute["customer_id"] == MARIANA_ID
    assert dispute["customer_name"] == "Mariana"
    assert dispute["case"]["dispute_id"] == "dsp-1"
    # Every checked rule is explained in full, decisive (action-kind) ones sorted first.
    rules = dispute["rule_explanations"]
    assert [(r["rule_id"], r["decisive"]) for r in rules] == [
        ("DSP-ACT-01", True),
        ("DSP-ELIG-01", False),
    ]
    assert all(r["description"] for r in rules)

    blocks = client.get("/api/console/card-blocks", headers=headers)
    assert blocks.status_code == 200
    assert blocks.json()[0]["customer_name"] == "Mariana"
    assert blocks.json()[0]["event"]["block_id"] == "blk-1"


def test_rules_are_explained_decisive_first_whatever_the_stored_order(
    client: TestClient, store: OpsStore
) -> None:
    _seed_more(store)
    headers = {"X-Console-Access-Code": CODE}
    policy = {rule.rule_id: rule.description for rule in load_policy().rules}
    newest = client.get("/api/console/disputes", headers=headers).json()[0]
    assert newest["case"]["dispute_id"] == "dsp-2"
    assert [(r["rule_id"], r["decisive"]) for r in newest["rule_explanations"]] == [
        ("DSP-ACT-01", True),
        ("DSP-ELIG-01", False),
    ]
    # A handoff's trigger rules are its decisive ones, each explained from the policy file.
    handoff = client.get("/api/console/handoffs", headers=headers).json()[0]
    assert handoff["handoff"]["handoff_id"] == "hof-2"
    assert handoff["rule_explanations"] == [
        {"rule_id": "DSP-ESC-01", "description": policy["DSP-ESC-01"], "decisive": True}
    ]


def test_an_id_outside_the_directory_gets_no_name_not_an_error(
    service: AuthService, store: OpsStore, directory: Directory
) -> None:
    _seed(store, customer_id="CUST-UNKNOWN-TO-DIRECTORY")
    console = HandoffConsole(store.database)
    app = create_app(
        auth=service,
        agent_factory=_NoAgent,
        console=ConsoleDeps(
            list_handoffs=console.list_handoffs,
            get_handoff=console.get_handoff,
            list_disputes=console.list_disputes,
            list_card_blocks=console.list_card_blocks,
            list_records=console.list_records,
            customer_name=_name_lookup(directory),
            policy=load_policy(),
        ),
    )
    response = TestClient(app).get("/api/console/handoffs", headers={"X-Console-Access-Code": CODE})
    assert response.status_code == 200
    assert response.json()[0]["customer_name"] is None


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
    for path in EVERY_READ:
        response = client.get(path, headers={"Authorization": f"Bearer {token}"})
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
            customer_name=_name_lookup(directory),
            policy=load_policy(),
        ),
    )
    response = TestClient(app).get("/api/console/handoffs")
    assert response.status_code == 200


def test_no_console_deps_means_the_path_does_not_exist_at_all(service: AuthService) -> None:
    app = create_app(auth=service, agent_factory=_NoAgent)
    response = TestClient(app).get("/api/console/handoffs", headers={"X-Console-Access-Code": CODE})
    assert response.status_code == 404
