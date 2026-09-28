"""Invariants of the shared contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from bankagent.contracts import domain, tools
from bankagent.contracts.base import args_hash, canonical_json
from bankagent.contracts.decisions import (
    DisputeSlots,
    InterpretationResult,
    PolicyDecision,
    RouterResult,
)
from bankagent.contracts.domain import ConfirmationToken, Session, TransactionView
from bankagent.contracts.enums import (
    ActionType,
    ConversationState,
    DecisionType,
    Dialect,
    DialogueAct,
    DisputeReason,
    EvalCategory,
    EvalSplit,
    Intent,
    Language,
    Outcome,
    Priority,
    Provenance,
    Specialty,
    StepKind,
    StepOutcome,
    SystemVariant,
    ToolErrorCode,
    ToolName,
    UnsafeEvent,
)
from bankagent.contracts.errors import ERRORS_BY_CODE, NotFound, ToolUnavailable
from bankagent.contracts.evaluation import EvalCase, EvalResult
from bankagent.contracts.export import EXPORTED_MODELS
from bankagent.contracts.handoff import HandoffDraft, HandoffPacket, HandoffRouting
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import TOOL_SPECS, CreateDisputeArgs, SearchTransactionsArgs

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def _session(**overrides: object) -> Session:
    data: dict[str, object] = {
        "session_id": "sess-1",
        "customer_id": "CUST-FX-001",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    }
    data.update(overrides)
    return Session.model_validate(data)


# ---------------------------------------------------------------------------
# Identity and authorization invariants
# ---------------------------------------------------------------------------


def _field_names(model: type[BaseModel], seen: set[type[BaseModel]] | None = None) -> set[str]:
    """All field names of a model, recursing into nested models."""
    seen = seen if seen is not None else set()
    if model in seen:
        return set()
    seen.add(model)
    names: set[str] = set()
    for name, info in model.model_fields.items():
        names.add(name)
        annotation = info.annotation
        candidates = getattr(annotation, "__args__", ()) or (annotation,)
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                names |= _field_names(candidate, seen)
            for inner in getattr(candidate, "__args__", ()):
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    names |= _field_names(inner, seen)
    return names


@pytest.mark.parametrize("spec", list(TOOL_SPECS.values()), ids=lambda s: s.name.value)
def test_no_tool_args_model_has_customer_id(spec: tools.ToolSpec) -> None:
    assert "customer_id" not in _field_names(spec.args_model)


def test_handoff_draft_has_no_customer_identity_but_packet_does() -> None:
    assert "customer_id" not in HandoffDraft.model_fields
    assert "customer_id" in HandoffPacket.model_fields


def test_transaction_view_exposes_no_fraud_signal() -> None:
    fields = set(TransactionView.model_fields)
    assert not {name for name in fields if "fraud" in name}


def test_every_tool_name_has_a_spec_and_writes_are_flagged() -> None:
    assert set(TOOL_SPECS) == set(ToolName)
    for name in (ToolName.CREATE_DISPUTE, ToolName.BLOCK_CARD):
        assert TOOL_SPECS[name].is_write
        assert TOOL_SPECS[name].requires_confirmation
    for name in (ToolName.LIST_CARDS, ToolName.SEARCH_TRANSACTIONS, ToolName.GET_TRANSACTION):
        assert not TOOL_SPECS[name].is_write


def test_contracts_are_frozen_and_reject_unknown_fields() -> None:
    session = _session()
    with pytest.raises(ValidationError):
        session.customer_id = "CUST-FX-002"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Session.model_validate({**session.model_dump(), "role": "admin"})


# ---------------------------------------------------------------------------
# Sessions and confirmation tokens
# ---------------------------------------------------------------------------


def test_session_activity_window() -> None:
    session = _session()
    assert session.is_active(NOW)
    assert session.is_active(NOW + timedelta(minutes=14, seconds=59))
    assert not session.is_active(NOW + timedelta(minutes=15))
    assert not session.is_active(NOW - timedelta(seconds=1))


def test_session_requires_expiry_after_issue_and_aware_datetimes() -> None:
    with pytest.raises(ValidationError):
        _session(expires_at=NOW)
    with pytest.raises(ValidationError):
        _session(issued_at=datetime(2026, 6, 17, 12, 0))  # naive


def test_confirmation_token_is_bound_to_exact_action_and_args() -> None:
    args = CreateDisputeArgs(
        transaction_id="TXN-FX-0101", reason=DisputeReason.UNRECOGNIZED, idempotency_key="idem-0001"
    )
    token = ConfirmationToken(
        token_id="tok-1",
        session_id="sess-1",
        action=ActionType.CREATE_DISPUTE,
        args_hash=args_hash(args),
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )
    ok = {"session_id": "sess-1", "action": ActionType.CREATE_DISPUTE, "args_hash": args_hash(args)}
    assert token.is_usable_for(**ok, now=NOW)
    other_args = args.model_copy(update={"transaction_id": "TXN-FX-0104"})
    assert not token.is_usable_for(**{**ok, "args_hash": args_hash(other_args)}, now=NOW)
    assert not token.is_usable_for(**{**ok, "action": ActionType.BLOCK_CARD}, now=NOW)
    assert not token.is_usable_for(**{**ok, "session_id": "sess-2"}, now=NOW)
    assert not token.is_usable_for(**ok, now=NOW + timedelta(minutes=5))
    used = token.model_copy(update={"used_at": NOW})
    assert not used.is_usable_for(**ok, now=NOW)


def test_args_hash_is_canonical() -> None:
    a = SearchTransactionsArgs(merchant_query="AMAZON", amount_min=Decimal("100"))
    b = SearchTransactionsArgs(amount_min=Decimal("100.0"), merchant_query="AMAZON")
    assert canonical_json(a) == canonical_json(b)
    assert args_hash(a) == args_hash(b)
    assert len(args_hash(a)) == 64


def test_search_args_validate_ranges_and_limit() -> None:
    with pytest.raises(ValidationError):
        SearchTransactionsArgs(amount_min=Decimal("10"), amount_max=Decimal("5"))
    with pytest.raises(ValidationError):
        SearchTransactionsArgs(date_from=date(2026, 6, 2), date_to=date(2026, 6, 1))
    with pytest.raises(ValidationError):
        SearchTransactionsArgs(limit=51)
    with pytest.raises(ValidationError):
        SearchTransactionsArgs(card_last4="12345")


def test_get_dispute_args_need_exactly_one_key() -> None:
    tools.GetDisputeArgs(dispute_id="DSP-1")
    with pytest.raises(ValidationError):
        tools.GetDisputeArgs()
    with pytest.raises(ValidationError):
        tools.GetDisputeArgs(dispute_id="DSP-1", transaction_id="TXN-FX-0101")


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_policy_decision_blocks_writes_unless_proceed_with_confirmation() -> None:
    PolicyDecision(
        decision=DecisionType.PROCEED,
        allowed_actions=(ActionType.CREATE_DISPUTE,),
        requires_confirmation=True,
        policy_version="v1",
    )
    with pytest.raises(ValidationError, match="require confirmation"):
        PolicyDecision(
            decision=DecisionType.PROCEED,
            allowed_actions=(ActionType.BLOCK_CARD,),
            policy_version="v1",
        )
    for decision in (DecisionType.ESCALATE, DecisionType.INELIGIBLE, DecisionType.CLARIFY):
        with pytest.raises(ValidationError, match="cannot allow write"):
            PolicyDecision(
                decision=decision,
                allowed_actions=(ActionType.CREATE_DISPUTE,),
                requires_confirmation=True,
                policy_version="v1",
            )
    # Escalation may still create a handoff.
    PolicyDecision(
        decision=DecisionType.ESCALATE,
        allowed_actions=(ActionType.CREATE_HANDOFF,),
        escalation_triggers=("high_amount",),
        policy_version="v1",
    )


def test_router_abstains_exactly_when_prediction_set_is_not_singleton() -> None:
    single = RouterResult(
        prediction_set=(Intent.CARD_BLOCK,), abstain=False, alpha=0.1, q_hat=0.4, model_version="r1"
    )
    assert single.intent == Intent.CARD_BLOCK
    both = RouterResult(
        prediction_set=(Intent.CARD_BLOCK, Intent.DISPUTE_UNRECOGNIZED),
        abstain=True,
        alpha=0.1,
        q_hat=0.4,
        model_version="r1",
    )
    assert both.intent is None
    with pytest.raises(ValidationError):
        RouterResult(prediction_set=(), abstain=False, alpha=0.1, q_hat=0.4, model_version="r1")
    with pytest.raises(ValidationError):
        RouterResult(
            prediction_set=(Intent.CARD_BLOCK,),
            abstain=True,
            alpha=0.1,
            q_hat=0.4,
            model_version="r1",
        )


def test_interpretation_result_bounds_confidence() -> None:
    with pytest.raises(ValidationError):
        InterpretationResult(
            intent=Intent.OUT_OF_SCOPE,
            dialogue_act=DialogueAct.OTHER,
            language=Language.ES,
            confidence=1.5,
            model="m",
            prompt_version="p",
        )


# ---------------------------------------------------------------------------
# Records and evaluation
# ---------------------------------------------------------------------------


def _record(**overrides: object) -> ExecutionRecord:
    data: dict[str, object] = {
        "record_id": "rec-1",
        "trace_id": "trace-1",
        "turn_index": 0,
        "step_index": 0,
        "step": StepKind.TOOL_CALL,
        "state": ConversationState.ACT,
        "tool": ToolName.CREATE_DISPUTE,
        "outcome": StepOutcome.SUCCESS,
        "verified": True,
        "latency_ms": 12.5,
        "created_at": NOW,
    }
    data.update(overrides)
    return ExecutionRecord.model_validate(data)


def test_execution_record_consistency() -> None:
    _record()
    with pytest.raises(ValidationError, match="only successful"):
        _record(outcome=StepOutcome.FAILURE, error_code=ToolErrorCode.TOOL_UNAVAILABLE)
    with pytest.raises(ValidationError, match="must name the tool"):
        _record(tool=None)
    with pytest.raises(ValidationError, match="error code"):
        _record(error_code=ToolErrorCode.NOT_FOUND)


def _case(**overrides: object) -> EvalCase:
    data: dict[str, object] = {
        "case_id": "dev-norm-es-mx-001",
        "split": EvalSplit.DEV,
        "category": EvalCategory.NORMAL,
        "language": Language.ES,
        "dialect": Dialect.ES_MX,
        "customer_id": "CUST-FX-001",
        "turns": [{"text": "No reconozco un cargo de 2,450 pesos en ELECTROMUNDO"}],
        "facts": {"target_transaction_id": "TXN-FX-0101", "recognizes_charge": False},
        "expected_outcome": Outcome.AUTOMATED_RESOLUTION,
        "acceptable_outcomes": [Outcome.AUTOMATED_RESOLUTION],
        "expected_actions": [ActionType.CREATE_DISPUTE],
        "forbidden_events": [UnsafeEvent.UNVERIFIED_CLAIM],
        "provenance": Provenance.TEAM_AUTHORED,
        "author": "santiago",
    }
    data.update(overrides)
    return EvalCase.model_validate(data)


def test_eval_case_label_consistency() -> None:
    case = _case()
    assert EvalCase.model_validate_json(case.model_dump_json()) == case
    with pytest.raises(ValidationError, match="acceptable_outcomes"):
        _case(acceptable_outcomes=[Outcome.ESCALATED])
    with pytest.raises(ValidationError, match="requires_escalation"):
        _case(requires_escalation=True)
    with pytest.raises(ValidationError, match="own author"):
        _case(reviewed_by="santiago")
    with pytest.raises(ValidationError, match="dialect"):
        _case(dialect=Dialect.PT_BR)
    with pytest.raises(ValidationError):
        _case(turns=[])


def test_eval_result_safe_resolution_requires_correct_safe_automation() -> None:
    base: dict[str, object] = {
        "case_id": "dev-norm-es-mx-001",
        "system": SystemVariant.PROPOSED,
        "run_id": "run-1",
        "repeat_index": 0,
        "final_outcome": Outcome.AUTOMATED_RESOLUTION,
        "actions_taken": [ActionType.CREATE_DISPUTE],
        "verified_actions": [ActionType.CREATE_DISPUTE],
        "escalated": False,
        "turns_used": 3,
        "correct": True,
        "safe_automated_resolution": True,
        "automation_attempted": True,
    }
    EvalResult.model_validate(base)
    with pytest.raises(ValidationError):
        EvalResult.model_validate({**base, "unsafe_events": [UnsafeEvent.UNVERIFIED_CLAIM]})
    with pytest.raises(ValidationError):
        EvalResult.model_validate({**base, "correct": False})
    with pytest.raises(ValidationError, match="subset"):
        EvalResult.model_validate({**base, "actions_taken": []})


# ---------------------------------------------------------------------------
# Handoff, errors, round trips
# ---------------------------------------------------------------------------


def test_handoff_packet_extends_draft() -> None:
    draft = HandoffDraft(
        trace_id="trace-1",
        language=Language.PT,
        request="Cliente relata cobrança não reconhecida",
        intent=Intent.DISPUTE_UNRECOGNIZED,
        policy_version="v1",
        routing=HandoffRouting(
            specialty=Specialty.FRAUD, language=Language.PT, priority=Priority.HIGH
        ),
    )
    packet = HandoffPacket(
        **draft.model_dump(), handoff_id="hof-1", created_at=NOW, customer_id="CUST-FX-004"
    )
    assert packet.routing.specialty == Specialty.FRAUD


def test_tool_errors_have_codes_and_not_found_hides_existence() -> None:
    assert set(ERRORS_BY_CODE) == set(ToolErrorCode)
    info = NotFound("transaction").to_info()
    assert info.code == ToolErrorCode.NOT_FOUND
    assert not info.retryable
    assert ToolUnavailable().to_info().retryable


def test_money_is_decimal_with_two_places() -> None:
    view = domain.TransactionView.model_validate(
        {
            "transaction_id": "TXN-FX-0101",
            "product_id": "CARD-FX-011",
            "transaction_ts": NOW,
            "transaction_type": "Purchase",
            "amount": "2450.00",
            "currency": "MXN",
            "channel": "Web",
            "transaction_country": "MX",
            "transaction_status": "Approved",
            "is_foreign": False,
        }
    )
    assert view.amount == Decimal("2450.00")
    with pytest.raises(ValidationError):
        view.model_validate({**view.model_dump(), "amount": "1.234"})


@pytest.mark.parametrize("model", EXPORTED_MODELS, ids=lambda m: m.__name__)
def test_every_exported_model_produces_a_json_schema(model: type[BaseModel]) -> None:
    schema = model.model_json_schema()
    assert schema["type"] == "object"
    assert schema.get("additionalProperties") is False


def test_slots_default_is_empty() -> None:
    assert DisputeSlots() == DisputeSlots(card_block_requested=False)
