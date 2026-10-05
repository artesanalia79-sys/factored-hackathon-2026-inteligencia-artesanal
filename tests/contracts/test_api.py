"""HTTP wire contracts of the customer API: what the web UI may send and what it receives."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from bankagent.contracts.api import (
    ChatTurnRequest,
    ChatTurnResponse,
    ConfirmationView,
    LoginRequest,
    LoginResponse,
    PersonaResponse,
    SessionResponse,
    VerifyRequest,
    VerifyResponse,
)
from bankagent.contracts.enums import ActionType, Language

DISPUTE: dict[str, Any] = {
    "action": "create_dispute",
    "card_last4": "4821",
    "reason": "movimiento no reconocido",
    "merchant": "ELECTROMUNDO ONLINE",
    "date": "12/06/2026",
    "channel": "sitio web",
    "amount": "2,450.00 MXN",
}


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (LoginRequest, {"persona_id": "p-1"}),
        (VerifyRequest, {"challenge_id": "c-1", "code": "123456"}),
        (ChatTurnRequest, {"text": "hola"}),
    ],
)
def test_requests_reject_a_customer_id(model: type[BaseModel], body: dict[str, str]) -> None:
    model.model_validate(body)
    with pytest.raises(ValidationError):
        model.model_validate({**body, "customer_id": "CUST-FX-001"})


@pytest.mark.parametrize(
    "model",
    [
        PersonaResponse,
        LoginResponse,
        VerifyResponse,
        SessionResponse,
        ChatTurnResponse,
        ConfirmationView,
    ],
)
def test_no_response_has_a_customer_id_field(model: type[BaseModel]) -> None:
    assert "customer_id" not in model.model_fields


def test_dispute_confirmation_needs_the_whole_charge() -> None:
    assert ConfirmationView.model_validate(DISPUTE).action == ActionType.CREATE_DISPUTE
    for field in ("reason", "merchant", "date", "channel", "amount"):
        with pytest.raises(ValidationError, match="whole charge"):
            ConfirmationView.model_validate({**DISPUTE, field: None})


def test_block_confirmation_shows_the_card_ending_only() -> None:
    view = ConfirmationView(action=ActionType.BLOCK_CARD, card_last4="4821")
    assert (view.merchant, view.amount) == (None, None)
    with pytest.raises(ValidationError, match="card ending only"):
        ConfirmationView.model_validate({**DISPUTE, "action": "block_card"})


def test_a_handoff_is_never_asked_for_confirmation() -> None:
    with pytest.raises(ValidationError, match="confirmed write"):
        ConfirmationView(action=ActionType.CREATE_HANDOFF, card_last4="4821")


def test_confirmation_card_ending_is_four_digits() -> None:
    with pytest.raises(ValidationError):
        ConfirmationView(action=ActionType.BLOCK_CARD, card_last4="4821 9999")


def test_chat_response_defaults_to_no_confirmation() -> None:
    response = ChatTurnResponse(
        reply_text="Hola", ended=False, claimed_actions=(), language=Language.PT
    )
    assert response.confirmation is None
    assert response.model_dump(mode="json")["language"] == "pt"


def test_a_reply_marks_a_transaction_or_card_only_with_its_verified_claim() -> None:
    """The UI tags the Transactions panel from these ids: never one without its claimed write."""
    base: dict[str, object] = {"reply_text": "Hecho", "ended": True, "language": "es"}
    ChatTurnResponse.model_validate(
        {**base, "claimed_actions": ["create_dispute"], "disputed_transaction_id": "TXN-1"}
    )
    ChatTurnResponse.model_validate(
        {**base, "claimed_actions": ["block_card"], "blocked_product_id": "CARD-1"}
    )
    for claimed, field in (
        ([], "disputed_transaction_id"),
        (["block_card"], "disputed_transaction_id"),
        ([], "blocked_product_id"),
        (["create_dispute"], "blocked_product_id"),
    ):
        with pytest.raises(ValidationError):
            ChatTurnResponse.model_validate({**base, "claimed_actions": claimed, field: "ID-1"})


def test_chat_request_language_is_optional_and_bounded_to_supported_languages() -> None:
    assert ChatTurnRequest(text="No").language is None
    assert ChatTurnRequest(text="No", language=Language.PT).language == Language.PT
    with pytest.raises(ValidationError):
        ChatTurnRequest.model_validate({"text": "No", "language": "en"})


def test_the_demo_access_code_is_optional_and_bounded() -> None:
    assert LoginRequest(persona_id="p-1").access_code is None
    assert LoginRequest(persona_id="p-1", access_code="shared-demo-code").access_code
    for bad in ("", "x" * 129):
        with pytest.raises(ValidationError):
            LoginRequest(persona_id="p-1", access_code=bad)
