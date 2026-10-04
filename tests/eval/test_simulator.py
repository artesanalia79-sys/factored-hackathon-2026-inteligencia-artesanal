"""Scripted user: answers from the FactSheet, default answer for unclassified questions."""

from __future__ import annotations

from typing import Any

import pytest

from bankagent.contracts.enums import ScriptedAnswer
from bankagent.contracts.evaluation import EvalCase
from bankagent.eval.cases import load_cases
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


def test_a_case_words_an_answer_its_own_way_and_its_facts_still_decide_it() -> None:
    # "Pode deixar, obrigado." opens like a yes and is a no (PR #67): only the words change.
    declines = "Pode deixar, obrigado."
    user = ScriptedUser(
        make_case(
            "pt",
            recognizes_charge=False,
            confirms_actions=True,
            wants_card_block=False,
            answer_wording={"confirm_no": declines, "block_no": declines},
        )
    )
    assert user.next_message(CONFIRM_DISPUTE_PT, 2) == "Sim, confirmo."  # not worded: scripted
    assert user.next_message("Você confirma o bloqueio do cartão com final 2208?", 3) == declines
    assert user.next_message("Você também quer bloquear o seu cartão final 2208?", 4) == declines
    assert [(e.kind, e.value) for e in user.events] == [
        (QuestionKind.CONFIRM, True),
        (QuestionKind.CONFIRM, False),
        (QuestionKind.CARD_BLOCK, False),
    ]


@pytest.mark.parametrize(
    "question",
    [
        "Você confirma o bloqueio do cartão com final 2208?",  # the proposed agent confirms it
        "Você também quer bloquear o seu cartão final 2208?",  # another system may offer it
    ],
)
def test_the_worded_dev_case_declines_either_block_question_alike(question: str) -> None:
    # Both systems run on the same scripted user: the same words for the same answer.
    case = next(case for case in load_cases() if case.case_id == "dev-normal-pt-br-002")
    assert ScriptedUser(case).next_message(question, 3) == "Pode deixar, obrigado."


WORDED_ANSWERS: list[tuple[str, dict[str, bool], ScriptedAnswer]] = [
    ("¿Reconoces este cargo?", {"recognizes_charge": True}, ScriptedAnswer.RECOGNIZE_YES),
    ("¿Reconoces este cargo?", {"recognizes_charge": False}, ScriptedAnswer.RECOGNIZE_NO),
    ("¿Confirmas abrir la disputa?", {"confirms_actions": True}, ScriptedAnswer.CONFIRM_YES),
    ("¿Confirmas abrir la disputa?", {"confirms_actions": False}, ScriptedAnswer.CONFIRM_NO),
    ("¿Quieres que bloqueemos tu tarjeta?", {"wants_card_block": True}, ScriptedAnswer.BLOCK_YES),
    ("¿Quieres que bloqueemos tu tarjeta?", {"wants_card_block": False}, ScriptedAnswer.BLOCK_NO),
    ("¿Quieres hablar con un asesor?", {"requests_human": True}, ScriptedAnswer.HUMAN_YES),
    ("¿Quieres hablar con un asesor?", {"requests_human": False}, ScriptedAnswer.HUMAN_NO),
]


def test_every_answer_a_case_can_word_is_one_the_scripted_user_gives() -> None:
    assert {answer for _, _, answer in WORDED_ANSWERS} == set(ScriptedAnswer)


@pytest.mark.parametrize("language", ["es", "pt"])
@pytest.mark.parametrize(("question", "facts", "answer"), WORDED_ANSWERS)
def test_a_worded_answer_replaces_that_answer_only(
    language: str, question: str, facts: dict[str, bool], answer: ScriptedAnswer
) -> None:
    scripted = ScriptedUser(make_case(language, **facts)).next_message(question, 1)
    own = ScriptedUser(make_case(language, answer_wording={answer: "Mis palabras."}, **facts))
    assert own.next_message(question, 1) == "Mis palabras." != scripted
    others = {other: "Otras palabras." for other in ScriptedAnswer if other != answer}
    rest = ScriptedUser(make_case(language, answer_wording=others, **facts))
    assert rest.next_message(question, 1) == scripted


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


# ---------------------------------------------------------------------------
# Rules added after the freeze (eval/preregistration.md, section 11)
# ---------------------------------------------------------------------------

# Every distinct question the LLM-only baseline asked on the dev cases (gpt-6-luna, 2026-10-03).
# The frozen rules read 24 of the 30 as their customer would.
BASELINE_DEV_QUESTIONS: list[tuple[str, QuestionKind]] = [
    (
        "¿Quieres que también bloquee la tarjeta terminada en 2208 para evitar nuevos cargos?",
        QuestionKind.CARD_BLOCK,
    ),
    (
        "Para no repetir la misma búsqueda, ¿podés decirme la fecha aproximada del cargo o "
        "cómo aparece el comercio en el resumen?",
        QuestionKind.CLARIFY,
    ),
    (
        "Para poder ayudarte a localizarlo, ¿recordás aproximadamente la fecha o cómo aparece"
        " el comercio en tu resumen?",
        QuestionKind.CLARIFY,
    ),
    (
        "Para seguir, necesito otro dato: ¿qué fecha aproximada aparece en el resumen, o con "
        "qué nombre figura el comercio?",
        QuestionKind.CLARIFY,
    ),
    (
        "Você consegue me dizer como o valor e o nome do estabelecimento aparecem exatamente "
        "no extrato, ou informar os últimos 4 dígitos do cartão usado?",
        QuestionKind.CLARIFY,
    ),
    ("Você sabe em que data ela apareceu e em qual moeda está registrada?", QuestionKind.CLARIFY),
    (
        "¿Cuál de los dos cobros quieres disputar: el de las 13:05:12 o el de las 13:05:49 "
        "del 14 de junio?",
        QuestionKind.CLARIFY,
    ),
    (
        "¿Cuál quieres disputar: 1,249.00 MXN del 11 de junio o 899.00 MXN del 10 de junio?",
        QuestionKind.CLARIFY,
    ),
    (
        "¿Podrías decirme la fecha aproximada del cargo y la moneda (por ejemplo, MXN)?",
        QuestionKind.CLARIFY,
    ),
    (
        "¿Puedes comprobar el identificador o indicarme el comercio, el importe o la fecha "
        "para buscarla?",
        QuestionKind.CLARIFY,
    ),
    (
        "¿Puedes revisar en Rappi cuál corresponde al pedido y cuál aparece como duplicado?",
        QuestionKind.CLARIFY,
    ),
    ("¿Qué fecha aproximada aparece para el cargo y en qué moneda figura?", QuestionKind.CLARIFY),
    (
        "¿Recordás aproximadamente cuándo fue o si el comercio podría figurar con otro nombre?",
        QuestionKind.CLARIFY,
    ),
    ("¿Recordás aproximadamente la fecha o si pudo figurar con otro nombre?", QuestionKind.CLARIFY),
    (
        "¿Recordás la fecha aproximada o si pudo figurar con otro nombre en el resumen?",
        QuestionKind.CLARIFY,
    ),
    (
        "¿Recuerdas aproximadamente en qué fecha apareció o en qué tarjeta se hizo?",
        QuestionKind.CLARIFY,
    ),
    (
        "¿Recuerdas la fecha aproximada de los cargos y en qué moneda aparecen?",
        QuestionKind.CLARIFY,
    ),
    (
        "¿Confirmas explícitamente que quieres iniciar una disputa por ese cargo?",
        QuestionKind.CONFIRM,
    ),
    ("¿Confirmas que quieres disputar uno de ellos por duplicado?", QuestionKind.CONFIRM),
    (
        "¿Confirmas que quieres que presente una disputa por cargo no reconocido?",
        QuestionKind.CONFIRM,
    ),
    ("¿Podrías confirmar la moneda y la fecha aproximada del cargo?", QuestionKind.CLARIFY),
    ("¿Confirmas que quieres que la presente como cargo no reconocido?", QuestionKind.CONFIRM),
    ("¿Cuál de los dos no reconoces?", QuestionKind.CLARIFY),
    (
        "¿Puedes revisar tu cuenta o tus pedidos de Amazon para ver si reconoces alguno de "
        "estos cargos: 1,249.00 MXN del 11 de junio o 899.00 MXN del 10 de junio?",
        QuestionKind.RECOGNIZE,
    ),
    ("¿Quieres que presente una disputa por este cargo como no reconocido?", QuestionKind.CONFIRM),
    ("¿Preferís empezar por tus tarjetas o por tus movimientos?", QuestionKind.UNCLASSIFIED),
    (
        "¿Quieres que inicie una disputa por uno de ellos como cargo duplicado?",
        QuestionKind.CONFIRM,
    ),
    ("¿Quieres que lo haga?", QuestionKind.UNCLASSIFIED),
    ("¿Qué tarjeta o cargo querés atender primero?", QuestionKind.UNCLASSIFIED),
    (
        "¿Te refieres al cargo de 1,249.00 MXN del 11 de junio o al de 899.00 MXN del 10 de junio?",
        QuestionKind.CLARIFY,
    ),
]

# Every distinct question the proposed agent asked in the same run: read the same way as before.
PROPOSED_DEV_QUESTIONS: list[tuple[str, QuestionKind]] = [
    ("¿Cuál de estos movimientos quieres revisar?", QuestionKind.CLARIFY),
    ("¿Cuál es el comercio o el importe exacto?", QuestionKind.CLARIFY),
    ("¿Qué pasó y cuál es el comercio o el importe del movimiento?", QuestionKind.CLARIFY),
    (
        "Você confirma a abertura de uma contestação por transação não reconhecida para esta "
        "transação: estabelecimento GAMESTORE DIGITAL, data 15/06/2026, canal site, cartão "
        "com final 2208, valor 32.500,00 ARS?",
        QuestionKind.CONFIRM,
    ),
    ("Você confirma o bloqueio do cartão com final 2208?", QuestionKind.CONFIRM),
    ("¿Confirmas bloquear la tarjeta terminada en 4821?", QuestionKind.CONFIRM),
    (
        "¿Confirmas crear un reclamo por cobro duplicado para este movimiento: comercio "
        "RAPPI*RESTAURANTE, fecha 14/06/2026, canal aplicación, tarjeta terminada en 7310, "
        "importe 85.900,00 COP?",
        QuestionKind.CONFIRM,
    ),
    (
        "¿Confirmas crear un reclamo por movimiento no reconocido para este movimiento: "
        "comercio AMAZON MX, fecha 11/06/2026, canal sitio web, tarjeta terminada en 4821, "
        "importe 1,249.00 MXN?",
        QuestionKind.CONFIRM,
    ),
    (
        "¿Confirmas crear un reclamo por movimiento no reconocido para este movimiento: "
        "comercio ELECTROMUNDO ONLINE, fecha 12/06/2026, canal sitio web, tarjeta terminada "
        "en 4821, importe 2,450.00 MXN?",
        QuestionKind.CONFIRM,
    ),
    ("Você reconhece esta transação?", QuestionKind.RECOGNIZE),
    ("¿Reconoces este movimiento?", QuestionKind.RECOGNIZE),
]

# Hand-written, from no run; the kinds were written down before the rules were tried on them.
FRESH_QUESTIONS: list[tuple[str, QuestionKind]] = [
    ("¿Quieres que abra una disputa por este cargo?", QuestionKind.CONFIRM),
    ("¿Deseas que registre un reclamo por el cobro duplicado?", QuestionKind.CONFIRM),
    ("¿Querés que inicie el reclamo por la compra no reconocida?", QuestionKind.CONFIRM),
    ("¿Te gustaría que presentara una aclaración por este movimiento?", QuestionKind.CONFIRM),
    ("¿Confirmas que deseas reportarlo como cargo no reconocido?", QuestionKind.CONFIRM),
    ("¿Cuál de estos cargos es el que no reconoces?", QuestionKind.CLARIFY),
    ("¿Podrías confirmar el monto y el comercio del cargo?", QuestionKind.CLARIFY),
    ("¿Reconoces alguno de estos dos cargos?", QuestionKind.RECOGNIZE),
    ("¿Fuiste tú quien hizo esta compra?", QuestionKind.RECOGNIZE),
    ("¿Quieres que te comunique con un asesor para revisar la disputa?", QuestionKind.HUMAN_OFFER),
    ("¿Quieres que bloquee tu tarjeta mientras revisamos?", QuestionKind.CARD_BLOCK),
    ("¿Quieres que presente la disputa y bloquee la tarjeta?", QuestionKind.CONFIRM),
    ("¿Te refieres al cargo del 12 de junio?", QuestionKind.CLARIFY),
    ("¿Deseas agregar algo más?", QuestionKind.UNCLASSIFIED),
    ("Você quer que eu abra uma contestação para essa cobrança?", QuestionKind.CONFIRM),
    ("Deseja que eu registre a contestação da compra não reconhecida?", QuestionKind.CONFIRM),
    ("Qual das duas cobranças você não reconhece?", QuestionKind.CLARIFY),
    ("Você poderia confirmar a data e o valor da compra?", QuestionKind.CLARIFY),
    ("Você confirma que quer contestar como compra não reconhecida?", QuestionKind.CONFIRM),
    ("Você gostaria de falar com um atendente?", QuestionKind.HUMAN_OFFER),
    ("Você fez essa compra?", QuestionKind.RECOGNIZE),
]


@pytest.mark.parametrize(("agent_text", "kind"), BASELINE_DEV_QUESTIONS)
def test_the_baselines_dev_questions_are_read_as_its_customer_would(
    agent_text: str, kind: QuestionKind
) -> None:
    assert classify_question(agent_text) == kind


@pytest.mark.parametrize(("agent_text", "kind"), PROPOSED_DEV_QUESTIONS)
def test_the_proposed_agents_dev_questions_are_read_as_before(
    agent_text: str, kind: QuestionKind
) -> None:
    assert classify_question(agent_text) == kind


@pytest.mark.parametrize(("agent_text", "kind"), FRESH_QUESTIONS)
def test_fresh_phrasings_of_the_same_questions(agent_text: str, kind: QuestionKind) -> None:
    assert classify_question(agent_text) == kind


def test_an_offer_to_file_the_dispute_gets_a_yes_and_counts_as_a_confirmation() -> None:
    # Read as a recognition question, it got "No, no la reconozco." and the baseline never
    # received the yes it was asking for.
    offer = "¿Quieres que presente una disputa por este cargo como no reconocido?"
    user = ScriptedUser(make_case(recognizes_charge=False, confirms_actions=True))
    assert user.next_message(offer, 1) == "Sí, confirmo."
    assert [(e.kind, e.value) for e in user.events] == [(QuestionKind.CONFIRM, True)]
    declines = ScriptedUser(make_case(recognizes_charge=False, confirms_actions=False))
    assert declines.next_message(offer, 1) == "No, no lo confirmo."


def test_an_offer_with_no_dispute_named_is_not_a_confirmation() -> None:
    # A yes here would later pass for the customer's consent to a write.
    user = ScriptedUser(make_case(confirms_actions=True))
    assert user.next_message("Puedo buscarlo sin filtrar por fecha. ¿Quieres que lo haga?", 1) == (
        "No estoy seguro."
    )
    assert user.events[-1].kind == QuestionKind.UNCLASSIFIED


def test_a_request_for_data_is_answered_with_data_not_with_a_yes() -> None:
    user = ScriptedUser(make_case(clarification_answers={"default": "Fue el 12 de junio"}))
    assert user.next_message("¿Podrías confirmar la moneda y la fecha del cargo?", 1) == (
        "Fue el 12 de junio"
    )
    assert user.events[-1].kind == QuestionKind.CLARIFY


def test_known_miss_a_request_for_data_with_no_data_word_is_still_read_as_a_confirmation() -> None:
    # Left as it is and listed in docs/limitations.md: fixing it on the fresh set would no
    # longer be a check on fresh phrasings.
    question = "¿Me confirmas los últimos cuatro dígitos de la tarjeta?"
    assert classify_question(question) == QuestionKind.CONFIRM


# Written in the review of PR #63, after the rules: one per rule and per cue, so that breaking
# any of them fails a test. Not a check on fresh phrasings (that is FRESH_QUESTIONS).
RULE_EXAMPLES: list[tuple[str, QuestionKind]] = [
    # Every offer word, the formal forms included.
    ("¿Quiere que presente una disputa por este cargo?", QuestionKind.CONFIRM),
    ("¿Desea que abra un reclamo por este cobro?", QuestionKind.CONFIRM),
    ("Você gostaria que eu abrisse uma contestação para essa compra?", QuestionKind.CONFIRM),
    # "No reconocido" is the reason after a confirm, and a recognition cue without one.
    ("¿Confirmas que lo registro como cargo no reconocido?", QuestionKind.CONFIRM),
    ("¿Para ti sigue siendo un cargo no reconocido?", QuestionKind.RECOGNIZE),
    # "Which one?" in the plural and in Portuguese, and "você se refere".
    ("¿Cuáles de estos cargos no reconoces?", QuestionKind.CLARIFY),
    ("Quais dessas compras você não reconhece?", QuestionKind.CLARIFY),
    ("Você se refere à cobrança do dia 12?", QuestionKind.CLARIFY),
    ("¿A cuál de los dos cargos te refieres?", QuestionKind.CLARIFY),
    ("A qual das duas compras você se refere?", QuestionKind.CLARIFY),
    ("¿Cuál de los dos quieres que dispute?", QuestionKind.CLARIFY),
    # After an article, "cual" and "qual" are relative pronouns: the frozen rules read these
    # right, and the first version of D1 read them as "which one?".
    ("¿Reconoces el cargo de Amazon, el cual aparece el 11 de junio?", QuestionKind.RECOGNIZE),
    ("¿Reconoces las compras de junio, las cuales suman 2,148 MXN?", QuestionKind.RECOGNIZE),
    ("Você reconhece a compra de 15/06, a qual aparece no cartão 2208?", QuestionKind.RECOGNIZE),
    ("Você reconhece o pagamento, o qual aparece no seu extrato?", QuestionKind.RECOGNIZE),
    (
        "¿Quieres que presente una disputa por este cargo, lo cual puede tardar unos días?",
        QuestionKind.CONFIRM,
    ),
    ("¿Confirmas que deseas continuar, lo cual cierra esta conversación?", QuestionKind.CONFIRM),
    # An offer that names two charges asks which one (the first version of D1 read it as a yes).
    ("¿Quieres disputar el cargo de 1,249.00 MXN o el de 899.00 MXN?", QuestionKind.CLARIFY),
    (
        "¿Quieres presentar la disputa por el cargo del 11 de junio o por el del 10 de junio?",
        QuestionKind.CLARIFY,
    ),
    ("¿Quieres que dispute el cobro de 1,249 o 899?", QuestionKind.CLARIFY),
    ("Você quer contestar a compra de 15/06 ou a de 16/06?", QuestionKind.CLARIFY),
]


@pytest.mark.parametrize(("agent_text", "kind"), RULE_EXAMPLES)
def test_each_rule_added_after_the_freeze(agent_text: str, kind: QuestionKind) -> None:
    assert classify_question(agent_text) == kind


@pytest.mark.parametrize(
    "agent_text",
    [
        *(
            f"¿Reconoces el cargo de Amazon, {pronoun} te escribí ayer?"
            for pronoun in (
                "el cual",
                "la cual",
                "lo cual",
                "los cuales",
                "las cuales",
                "del cual",
                "al cual",
            )
        ),
        *(
            f"Você reconhece a compra de 15/06, {pronoun} te escrevi ontem?"
            for pronoun in (
                "o qual",
                "a qual",
                "os quais",
                "as quais",
                "do qual",
                "da qual",
                "dos quais",
                "das quais",
                "no qual",
                "na qual",
                "nos quais",
                "nas quais",
                "pelo qual",
                "pela qual",
                "pelos quais",
                "pelas quais",
            )
        ),
    ],
)
def test_cual_after_an_article_is_a_relative_pronoun(agent_text: str) -> None:
    assert classify_question(agent_text) == QuestionKind.RECOGNIZE


def test_an_offer_of_two_charges_gets_the_customers_answer_to_which_one() -> None:
    user = ScriptedUser(
        make_case(confirms_actions=True, clarification_answers={"default": "El de 1,249"})
    )
    assert user.next_message("¿Quieres disputar el de 1,249 MXN o el de 899 MXN?", 1) == (
        "El de 1,249"
    )
    assert [(e.kind, e.value) for e in user.events] == [(QuestionKind.CLARIFY, None)]


def test_known_miss_an_offer_to_explain_or_look_up_a_dispute_is_read_as_a_confirmation() -> None:
    # Listed in docs/limitations.md. A yes here can only hide a write made without asking again,
    # which favors the system that asked. The proposed agent's templates never ask it, so this
    # can narrow its lead over the LLM-only baseline, never widen it.
    for question in (
        "¿Quieres saber el estado de tu reclamo?",
        "¿Quieres que te explique cómo funciona una disputa?",
    ):
        assert classify_question(question) == QuestionKind.CONFIRM
