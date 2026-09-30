"""Scripted user: answers from the FactSheet, default answer for unclassified questions."""

from __future__ import annotations

from typing import Any

import pytest

from bankagent.contracts.evaluation import EvalCase
from bankagent.eval.simulator import QuestionKind, ScriptedUser, classify_question


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
