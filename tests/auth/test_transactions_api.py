"""The movements endpoint uses the authenticated session for its customer scope."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import NoReturn

from fastapi.testclient import TestClient

from bankagent.api.app import create_app
from bankagent.auth.service import AuthService
from bankagent.contracts.domain import TransactionView
from bankagent.contracts.enums import Channel, TransactionStatus, TransactionType


def _transaction(reference: str) -> TransactionView:
    return TransactionView(
        transaction_id=reference,
        product_id="CARD-001",
        card_last4="1234",
        transaction_ts=datetime(2026, 6, 12, tzinfo=UTC),
        transaction_type=TransactionType.PURCHASE,
        amount=Decimal("25.00"),
        currency="MXN",
        channel=Channel.WEB,
        merchant_name="TEST MERCHANT",
        transaction_country="MX",
        transaction_status=TransactionStatus.APPROVED,
        is_foreign=False,
    )


def test_movements_are_scoped_to_the_authenticated_customer(
    service: AuthService, do_login: Callable[..., str]
) -> None:
    seen: list[str] = []

    def read(customer_id: str) -> list[TransactionView]:
        seen.append(customer_id)
        return [_transaction("TXN-MARIANA" if customer_id == "CUST-T7-001" else "TXN-RAFAEL")]

    def unused_agent() -> NoReturn:
        raise AssertionError("the movements endpoint must not create a chat agent")

    client = TestClient(
        create_app(auth=service, agent_factory=unused_agent, transaction_reader=read)
    )
    assert client.get("/api/chat/transactions").status_code == 401

    mariana = client.get(
        "/api/chat/transactions", headers={"Authorization": f"Bearer {do_login('Mariana')}"}
    )
    rafael = client.get(
        "/api/chat/transactions", headers={"Authorization": f"Bearer {do_login('Rafael')}"}
    )
    assert mariana.status_code == rafael.status_code == 200
    assert [row["transaction_id"] for row in mariana.json()] == ["TXN-MARIANA"]
    assert [row["transaction_id"] for row in rafael.json()] == ["TXN-RAFAEL"]
    assert seen == ["CUST-T7-001", "CUST-T7-002"]
    assert "customer_id" not in mariana.text + rafael.text
