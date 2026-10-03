"""Scripted user: answers from the FactSheet, default answer for unclassified questions."""

from __future__ import annotations

from typing import Any

import pytest

from bankagent.contracts.evaluation import EvalCase
from bankagent.eval.simulator import QuestionKind, ScriptedUser, classify_question

# What the proposed agent asks, as its templates render it (`bankagent.render.templates`).
CONFIRM_DISPUTE_ES = (
    "¿Confirmas crear un reclamo por movimiento no reconocido para este movimiento: comercio "
    "ELECTROMUNDO ONLINE, fecha 12/06/2026, canal sitio web, tarjeta terminada en 4821, importe "
    "2,450.00 MXN?"
)
CONFIRM_DISPUTE_PT = (
    "Você confirma a abertura de uma contestação por transação não reconhecida para esta "
    "transação: estabelecimento GAMESTORE DIGITAL, data 15/06/2026, canal site, cartão com final "
    "2208, valor 32.500,00 ARS?"
)
OFFER_BLOCK_ES = (
    "Creé el reclamo DSP-1 para el movimiento que confirmaste. También puedo bloquear la tarjeta "
    "para evitar nuevos cargos. ¿Confirmas bloquear la tarjeta terminada en 4821?"
)


def make_case(language: str = "es", **facts: Any) -> EvalCase:
    return EvalCase.model_validate(
        {
            "case_id": "dev-sim-001",
            "split": "dev",
            "category": "normal",
            "language": language,
            "dialect": "es-MX" if language == "es" else "pt-BR",
            "customer_id": "CUST-FX-001",
            "turns": [{"text": "no reconozco un cargo"}],
            "facts": facts,
            "expected_outcome": "automated_resolution",
            "acceptable_outcomes": ["automated_resolution"],
            "provenance": "team_authored",
            "author": "tests",
        }
    )


@pytest.mark.parametrize(
    ("agent_text", "kind"),
    [
        ("Encontré un cargo de AMAZON. ¿Reconoces este cargo?", QuestionKind.RECOGNIZE),
        ("Encontré un cargo. ¿Reconocés este cargo?", QuestionKind.RECOGNIZE),
        ("Você reconhece essa cobrança?", QuestionKind.RECOGNIZE),
        ("Voy a abrir una disputa. ¿Confirmas?", QuestionKind.CONFIRM),
        ("Vou abrir uma contestação. Você confirma?", QuestionKind.CONFIRM),
        ("¿Quieres que también bloqueemos tu tarjeta?", QuestionKind.CARD_BLOCK),
        ("¿Quieres hablar con un asesor?", QuestionKind.HUMAN_OFFER),
        ("¿Cuál de los dos cargos quieres reclamar?", QuestionKind.CLARIFY),
        ("¿Qué tal tu día?", QuestionKind.UNCLASSIFIED),
    ],
)
def test_classifies_agent_questions(agent_text: str, kind: QuestionKind) -> None:
    assert classify_question(agent_text) == kind


@pytest.mark.parametrize(
    ("agent_text", "kind"),
    [
        # A confirmation that names its action, although it mentions the "unrecognized" reason.
        (CONFIRM_DISPUTE_ES, QuestionKind.CONFIRM),
        (CONFIRM_DISPUTE_PT, QuestionKind.CONFIRM),
        (
            "¿Confirmas que quieres abrir una disputa por este cargo que no reconoces?",
            QuestionKind.CONFIRM,
        ),
        ("¿Autorizas abrir la disputa por este cargo no reconocido?", QuestionKind.CONFIRM),
        ("Posso prosseguir com a contestação da transação não reconhecida?", QuestionKind.CONFIRM),
        (OFFER_BLOCK_ES, QuestionKind.CONFIRM),
        ("¿Confirmas bloquear la tarjeta por el cargo no reconocido?", QuestionKind.CONFIRM),
        ("¿Confirmas levantar la aclaración por el cargo no reconocido?", QuestionKind.CONFIRM),
        ("¿Autorizas el contracargo de la compra no reconocida?", QuestionKind.CONFIRM),
        # "Confirm" with no action named is still about recognizing the charge.
        ("¿Puedes confirmar si reconoces este cargo?", QuestionKind.RECOGNIZE),
        ("Pode confirmar se você reconhece essa compra?", QuestionKind.RECOGNIZE),
        ("¿Reconocés el cobro o querés que lo reclamemos?", QuestionKind.RECOGNIZE),
    ],
)
def test_a_confirmation_that_names_its_action_is_not_a_recognition_question(
    agent_text: str, kind: QuestionKind
) -> None:
    assert classify_question(agent_text) == kind


@pytest.mark.parametrize(
    ("language", "confirmation", "yes"),
    [("es", CONFIRM_DISPUTE_ES, "Sí, confirmo."), ("pt", CONFIRM_DISPUTE_PT, "Sim, confirmo.")],
)
def test_the_agents_dispute_confirmation_gets_a_yes_not_the_recognition_answer(
    language: str, confirmation: str, yes: str
) -> None:
    user = ScriptedUser(make_case(language, recognizes_charge=False, confirms_actions=True))
    assert user.next_message(confirmation, 2) == yes
    assert [(e.kind, e.value) for e in user.events] == [(QuestionKind.CONFIRM, True)]


def test_the_block_confirmation_follows_wants_card_block() -> None:
    declines = ScriptedUser(make_case(confirms_actions=True, wants_card_block=False))
    assert declines.next_message(OFFER_BLOCK_ES, 3) == "No, no lo confirmo."
    accepts = ScriptedUser(make_case(confirms_actions=True, wants_card_block=True))
    assert accepts.next_message(OFFER_BLOCK_ES, 3) == "Sí, confirmo."


def test_statements_before_the_question_do_not_change_the_kind() -> None:
    text = "Como no reconoces el cargo, voy a abrir una disputa. ¿Confirmas?"
    assert classify_question(text) == QuestionKind.CONFIRM


def test_answers_from_the_fact_sheet() -> None:
    user = ScriptedUser(make_case(recognizes_charge=False, confirms_actions=True))
    assert user.next_message("¿Reconoces este cargo?", 1) == "No, no la reconozco."
    assert user.next_message("¿Confirmas?", 2) == "Sí, confirmo."
    assert [(e.kind, e.value) for e in user.events] == [
        (QuestionKind.RECOGNIZE, False),
        (QuestionKind.CONFIRM, True),
    ]


def test_confirmation_that_includes_an_unwanted_block_is_declined() -> None:
    user = ScriptedUser(make_case(confirms_actions=True, wants_card_block=False))
    answer = user.next_message("Voy a abrir una disputa y a bloquear tu tarjeta. ¿Confirmas?", 1)
    assert answer == "No, no lo confirmo."


def test_portuguese_answers() -> None:
    user = ScriptedUser(make_case("pt", recognizes_charge=True))
    assert user.next_message("Você reconhece essa cobrança?", 1) == "Sim, reconheço, fui eu."


def test_clarification_uses_the_matching_answer() -> None:
    user = ScriptedUser(make_case(clarification_answers={"cual": "El de 1,249 pesos"}))
    assert user.next_message("¿Cuál de los dos quieres reclamar?", 1) == "El de 1,249 pesos"


def test_unclassified_question_gets_the_default_answer_and_is_counted() -> None:
    user = ScriptedUser(make_case(clarification_answers={"default": "Solo quiero reclamar"}))
    assert user.next_message("¿Qué tal tu día?", 1) == "Solo quiero reclamar"
    user2 = ScriptedUser(make_case())
    assert user2.next_message("¿Qué tal tu día?", 1) == "No estoy seguro."
    assert user2.events[-1].kind == QuestionKind.UNCLASSIFIED


def test_reply_without_a_question_ends_the_conversation() -> None:
    user = ScriptedUser(make_case())
    assert user.next_message("Listo. Registré la disputa.", 1) is None


def test_scripted_turns_are_sent_before_answering_questions() -> None:
    case = make_case().model_copy(update={"turns": (*make_case().turns, *make_case().turns[:1])})
    user = ScriptedUser(case)
    assert user.next_message("¿Reconoces este cargo?", 1) == "no reconozco un cargo"
    assert user.events == []
