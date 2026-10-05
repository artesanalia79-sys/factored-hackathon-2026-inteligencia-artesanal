"""The scripted customer of the curated run: what it types and how a reply is read (T19).

The run can only show what the agent does if the keyword interpreter reads each scripted line
the way the script means it, for every country, currency and size of amount, and if a reply is
classified from the agent's own copy.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from bankagent.contracts.enums import (
    ActionType,
    ConversationState,
    DecisionType,
    DialogueAct,
    Intent,
    Language,
    Outcome,
    Specialty,
)
from bankagent.curated.cases import Charge, CuratedCase, Expected, Scenario
from bankagent.curated.run import Reply, classify, script, spoken_amount
from bankagent.interpret.keywords import interpret_text, is_explicit_yes, normalize
from bankagent.policy.schema import PolicyConfig
from bankagent.render.templates import (
    BLOCK_OFFER_COPY,
    INELIGIBLE_COPY,
    OPENING_QUESTION_COPY,
    render_block_declined,
    render_outcome,
    render_state,
)

AS_OF = date(2026, 6, 17)
PROCEED = Expected(DecisionType.PROCEED, ("DSP-ELIG-01",), block_offered=True)
# Invented names with the shapes merchant names have (several words, accents, "de", capitals),
# and amounts from cents to millions. No name or amount is taken from the organizer data.
MERCHANTS = [
    "Super Barato",
    "Restaurante La Buena Mesa",
    "Tienda Doña Inés",
    "Compañía Telefónica del Sur",
    "Servicios Urbanos",
    "Estación de Carga",
    "Óptica Mirada",
    "Cable Hogar",
    None,
]
AMOUNTS = [
    "0.05",
    "6.00",
    "20.00",
    "99.90",
    "312.47",
    "999.99",
    "1000.00",
    "1890.35",
    "21450.60",
    "98765.43",
    "1250300.75",
    "7654321.90",
]


def _charge(**changes: Any) -> Charge:
    base = Charge(
        transaction_id="TRX-9X8Y7Z",
        customer_id="CLI-OWNER",
        product_id="PRD-OWNER",
        country="CO",
        language=Language.ES,
        status="Approved",
        transaction_ts=datetime(2026, 6, 10, 15, 0),
        transaction_type="Purchase",
        amount=Decimal("85900.50"),
        currency="COP",
        merchant_name="Super Barato",
        card_last4="4001",
        card_status="Active",
        fraud_score=4.5,
        dq_flags=(),
        last_dispute_at=None,
        open_case=False,
        same_amount=1,
        position=1,
    )
    return replace(base, **changes)


def _case(scenario: Scenario, expected: Expected = PROCEED, **changes: Any) -> CuratedCase:
    charge = _charge(**changes)
    return CuratedCase(
        case_id=f"{scenario.value}-01",
        scenario=scenario,
        customer_id=(
            "CLI-SOMEONE" if scenario == Scenario.OTHER_CUSTOMERS_CHARGE else charge.customer_id
        ),
        language=Language.ES,
        charge=charge,
        expected=expected,
    )


@pytest.mark.parametrize(
    ("amount", "country", "written"),
    [
        ("1234.56", "AR", "1.234,56"),
        ("1234.56", "CO", "1.234,56"),
        ("1234.56", "MX", "1,234.56"),
        ("6.00", "CO", "6,00"),
        ("6.00", "MX", "6.00"),
        ("1250300.75", "CO", "1.250.300,75"),
        ("7654321.90", "MX", "7,654,321.90"),
        ("0.05", "AR", "0,05"),
    ],
)
def test_an_amount_is_written_as_its_customer_writes_it(
    amount: str, country: str, written: str
) -> None:
    assert spoken_amount(Decimal(amount), country) == written


@pytest.mark.parametrize("country", ["AR", "CO", "MX"])
@pytest.mark.parametrize("currency", ["USD", "PESOS"])
@pytest.mark.parametrize("kind", ["Purchase", "Withdrawal", "Payment"])
@pytest.mark.parametrize("merchant", MERCHANTS)
def test_the_opening_is_read_as_an_unrecognized_charge_of_that_exact_amount(
    policy: PolicyConfig, country: str, currency: str, kind: str, merchant: str | None
) -> None:
    code = "USD" if currency == "USD" else {"AR": "ARS", "CO": "COP", "MX": "MXN"}[country]
    for amount in AMOUNTS:
        case = _case(
            Scenario.RECOGNIZED,
            Expected(None),
            country=country,
            currency=code,
            transaction_type=kind,
            merchant_name=merchant,
            amount=Decimal(amount),
        )
        opening = script(case, policy, AS_OF)[0].steps[0]
        read = interpret_text(opening.says)
        assert read.intent == Intent.DISPUTE_UNRECOGNIZED, opening.says
        assert not read.injection_suspected
        assert read.slots.amount == Decimal(amount), opening.says
        # "pesos" names no currency; the amount alone finds the charge.
        assert read.slots.currency == ("USD" if code == "USD" else None), opening.says
        assert read.slots.merchant_query is None, opening.says
        assert read.slots.transaction_ref is None
        assert read.slots.card_last4 is None
        assert read.language == Language.ES


def test_a_customer_of_another_country_writes_like_the_default(policy: PolicyConfig) -> None:
    # The contract allows any country code; the script must not stop on one it has no words for.
    case = _case(Scenario.DISPUTE_BLOCK_DECLINED, country="BR", currency="USD")
    steps = script(case, policy, AS_OF)[0].steps
    assert steps[0].says == (
        "Hola, tengo un cargo de 85,900.50 dólares en Super Barato que no reconozco"
    )
    assert [step.says for step in steps[1:3]] == ["No fui yo", "Sí, por favor"]


def test_a_merchant_the_keyword_rules_know_is_read_with_the_amount(policy: PolicyConfig) -> None:
    case = _case(Scenario.RECOGNIZED, Expected(None), merchant_name="Cinépolis", country="MX")
    read = interpret_text(script(case, policy, AS_OF)[0].steps[0].says)
    assert read.slots.merchant_query == "CINEPOLIS"
    assert read.slots.amount == Decimal("85900.50")


@pytest.mark.parametrize("country", ["AR", "CO", "MX"])
def test_every_answer_is_read_the_way_the_script_means_it(
    policy: PolicyConfig, country: str
) -> None:
    code = {"AR": "ARS", "CO": "COP", "MX": "USD"}[country]
    declined, accepted = (
        script(_case(scenario, country=country, currency=code), policy, AS_OF)[0].steps
        for scenario in (Scenario.DISPUTE_BLOCK_DECLINED, Scenario.DISPUTE_BLOCK_ACCEPTED)
    )
    _, not_mine, yes, no_block = declined
    assert interpret_text(not_mine.says).dialogue_act in {
        DialogueAct.NOT_RECOGNIZE_CHARGE,
        DialogueAct.DENY,
    }
    # A write needs a reply in which every word confirms.
    for confirmation in (yes, accepted[3]):
        assert confirmation.confirms
        assert interpret_text(confirmation.says).dialogue_act == DialogueAct.AFFIRM
        assert is_explicit_yes(normalize(confirmation.says))
    assert interpret_text(no_block.says).dialogue_act == DialogueAct.DENY
    assert not no_block.confirms
    assert not not_mine.confirms

    recognized = script(
        _case(Scenario.RECOGNIZED, Expected(None), country=country, currency=code), policy, AS_OF
    )
    mine = recognized[0].steps[1]
    assert interpret_text(mine.says).dialogue_act == DialogueAct.RECOGNIZE_CHARGE


def test_the_scripts_expect_the_replies_of_each_scenario(policy: PolicyConfig) -> None:
    def kinds(
        scenario: Scenario, expected: Expected = PROCEED, **changes: Any
    ) -> list[list[Reply]]:
        conversations = script(_case(scenario, expected, **changes), policy, AS_OF)
        return [[step.expects for step in conversation.steps] for conversation in conversations]

    dispute = [Reply.RECOGNIZE, Reply.CONFIRM_DISPUTE, Reply.DISPUTED_OFFER_BLOCK]
    assert kinds(Scenario.DISPUTE_BLOCK_DECLINED) == [[*dispute, Reply.BLOCK_DECLINED]]
    assert kinds(Scenario.DISPUTE_BLOCK_ACCEPTED) == [[*dispute, Reply.BLOCKED]]
    no_offer = Expected(DecisionType.PROCEED, ("DSP-ELIG-01",), block_offered=False)
    assert kinds(Scenario.DISPUTE_CARD_NOT_ACTIVE, no_offer) == [
        [Reply.RECOGNIZE, Reply.CONFIRM_DISPUTE, Reply.DISPUTED]
    ]
    escalate = Expected(DecisionType.ESCALATE, ("DSP-ESC-01",), specialty=Specialty.FRAUD)
    assert kinds(Scenario.ESCALATE_FRAUD, escalate) == [[Reply.RECOGNIZE, Reply.ESCALATED]]
    refuse = Expected(DecisionType.INELIGIBLE, ("DSP-WIN-01",), explanation_key="x")
    assert kinds(Scenario.INELIGIBLE_WINDOW, refuse) == [[Reply.RECOGNIZE, Reply.INELIGIBLE]]
    assert kinds(Scenario.RECOGNIZED, Expected(None)) == [[Reply.RECOGNIZE, Reply.DEFLECTED]]
    assert kinds(Scenario.OTHER_CUSTOMERS_CHARGE, Expected(None)) == [
        [Reply.CLARIFY, Reply.CLARIFY, Reply.ABSTAINED]
    ]
    assert kinds(Scenario.CHOOSE_AMONG_MATCHES, escalate, position=2, same_amount=2) == [
        [Reply.CHOOSE, Reply.RECOGNIZE, Reply.ESCALATED]
    ]
    # The second session finds the dispute the first one filed.
    assert kinds(Scenario.ALREADY_DISPUTED, expect_all(policy)) == [
        [*dispute, Reply.BLOCK_DECLINED],
        [Reply.RECOGNIZE, Reply.INELIGIBLE],
    ]


def expect_all(policy: PolicyConfig) -> Expected:
    return Expected(
        DecisionType.PROCEED, tuple(rule.rule_id for rule in policy.rules), block_offered=True
    )


def test_the_second_session_of_a_disputed_charge_expects_the_open_case_rule(
    policy: PolicyConfig,
) -> None:
    first, second = script(_case(Scenario.ALREADY_DISPUTED, expect_all(policy)), policy, AS_OF)
    assert (first.disputes, first.blocks) == (True, False)
    assert (second.disputes, second.blocks) == (False, False)
    assert second.expected == Expected(
        DecisionType.INELIGIBLE, ("DSP-ELIG-02",), explanation_key="dispute.already_disputed"
    )


def test_what_the_report_shows_of_a_message_holds_no_value_of_the_row(
    policy: PolicyConfig,
) -> None:
    for scenario in Scenario:
        expected = Expected(None) if scenario in NO_POLICY else PROCEED
        for conversation in script(_case(scenario, expected, position=2), policy, AS_OF):
            for step in conversation.steps:
                for value in ("85.900,50", "85900", "Super Barato", "TRX-", "CLI-", "4001"):
                    assert value not in step.shown, (scenario, step.shown)
    opening = script(_case(Scenario.RECOGNIZED, Expected(None)), policy, AS_OF)[0].steps[0]
    assert opening.says == (
        "Buenas tardes, tengo un cargo de 85.900,50 pesos en Super Barato que no reconozco"
    )
    assert opening.shown == (
        "Buenas tardes, tengo un cargo de <importe> <moneda> en <comercio> que no reconozco"
    )


NO_POLICY = {Scenario.RECOGNIZED, Scenario.OTHER_CUSTOMERS_CHARGE}


def test_choosing_among_matches_names_no_merchant_and_answers_by_position(
    policy: PolicyConfig,
) -> None:
    for position, word in ((1, "El primero"), (2, "El segundo"), (3, "El tercero")):
        steps = script(
            _case(Scenario.CHOOSE_AMONG_MATCHES, position=position, same_amount=3), policy, AS_OF
        )[0].steps
        assert "Super Barato" not in steps[0].says
        assert steps[1].says == word


def _body(
    text: str, *, ended: bool, claimed: tuple[str, ...] = (), asks: str | None = None
) -> dict[str, Any]:
    return {
        "reply_text": text,
        "ended": ended,
        "claimed_actions": list(claimed),
        "language": "es",
        "confirmation": None if asks is None else {"action": asks, "card_last4": "4001"},
    }


SPANISH = Language.ES
DISPUTE, BLOCK, HANDOFF = (
    ActionType.CREATE_DISPUTE.value,
    ActionType.BLOCK_CARD.value,
    ActionType.CREATE_HANDOFF.value,
)


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (
            _body("Encontré este movimiento: x. ¿Reconoces este movimiento?", ended=False),
            Reply.RECOGNIZE,
        ),
        (
            _body("Encontré 2 movimientos que coinciden: 1) a; 2) b. ¿Cuál…?", ended=False),
            Reply.CHOOSE,
        ),
        (_body(render_state(ConversationState.CLARIFY, SPANISH), ended=False), Reply.CLARIFY),
        (_body("¿Confirmas crear un reclamo…?", ended=False, asks=DISPUTE), Reply.CONFIRM_DISPUTE),
        (
            _body("Creé el reclamo…", ended=False, claimed=(DISPUTE,), asks=BLOCK),
            Reply.DISPUTED_OFFER_BLOCK,
        ),
        (_body("Creé el reclamo…", ended=True, claimed=(DISPUTE,)), Reply.DISPUTED),
        (_body("Bloqueé la tarjeta…", ended=True, claimed=(BLOCK,)), Reply.BLOCKED),
        (_body("Registré tu solicitud…", ended=True, claimed=(HANDOFF,)), Reply.ESCALATED),
        (_body(render_block_declined(SPANISH), ended=True), Reply.BLOCK_DECLINED),
        (_body(render_outcome(Outcome.DEFLECTED_RECOGNIZED, SPANISH), ended=True), Reply.DEFLECTED),
        (_body(render_outcome(Outcome.ABSTAINED, SPANISH), ended=True), Reply.ABSTAINED),
        *[
            (_body(copy[SPANISH], ended=True), Reply.INELIGIBLE)
            for copy in INELIGIBLE_COPY.values()
        ],
    ],
)
def test_a_reply_is_read_from_its_fields_and_the_agents_own_copy(
    body: dict[str, Any], kind: Reply
) -> None:
    assert classify(body) == kind


@pytest.mark.parametrize(
    "body",
    [
        # Copy the script does not expect: a refusal, a re-authentication, a failure, the
        # opening question, the block offer with no dispute claimed.
        _body(render_outcome(Outcome.DENIED, SPANISH), ended=True),
        _body(render_outcome(Outcome.REAUTH_REQUIRED, SPANISH), ended=True),
        _body(render_outcome(Outcome.FAILED, SPANISH), ended=True),
        _body(render_outcome(Outcome.INCOMPLETE, SPANISH), ended=True),
        _body(OPENING_QUESTION_COPY[SPANISH], ended=False),
        _body(BLOCK_OFFER_COPY[SPANISH], ended=False, asks=BLOCK),
        # A claim where none belongs, two claims at once, a question that also ends.
        _body("¿Reconoces este movimiento?", ended=False, claimed=(DISPUTE,)),
        _body("¿Confirmas crear un reclamo…?", ended=False, claimed=(DISPUTE,), asks=DISPUTE),
        _body("x", ended=True, claimed=(DISPUTE, BLOCK)),
        _body("¿Confirmas…?", ended=True, claimed=(DISPUTE,), asks=BLOCK),
        _body(render_block_declined(SPANISH), ended=False),
        # The recognition question quoted inside another reply is not the question asked.
        _body("¿Reconoces este movimiento? Necesito un dato más.", ended=False),
        _body(render_outcome(Outcome.ABSTAINED, Language.PT), ended=True),
    ],
)
def test_anything_the_script_does_not_know_is_other(body: dict[str, Any]) -> None:
    assert classify(body) == Reply.OTHER
