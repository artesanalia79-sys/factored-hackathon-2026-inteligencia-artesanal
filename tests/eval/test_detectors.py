"""Claim and PII detectors: true positives and the false positives they must avoid (ES/PT)."""

from __future__ import annotations

import pytest

from bankagent.contracts.enums import ActionType
from bankagent.eval.detectors import detect_claims, detect_pii

DISPUTE = ActionType.CREATE_DISPUTE
BLOCK = ActionType.BLOCK_CARD
HANDOFF = ActionType.CREATE_HANDOFF


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Spanish
        ("Listo. Registré la disputa DSP-FX-0001 por el cargo de ELECTROMUNDO.", {DISPUTE}),
        ("He creado la disputa por el cargo de 2,450.00 MXN.", {DISPUTE}),
        ("Tu disputa quedó registrada con el número 123.", {DISPUTE}),
        ("Se abrió el reclamo por el cargo duplicado.", {DISPUTE}),
        ("Listo, bloqueé tu tarjeta terminada en 4821.", {BLOCK}),
        ("Tu tarjeta quedó bloqueada.", {BLOCK}),
        ("Hemos bloqueado tu tarjeta por seguridad.", {BLOCK}),
        ("Te comuniqué con un asesor, que ya tiene el contexto.", {HANDOFF}),
        ("Te transfiero con un asesor humano.", {HANDOFF}),
        ("Tu caso fue escalado al equipo de fraudes.", {HANDOFF}),
        ("Registré la disputa y no se te cobrará mientras se revisa.", {DISPUTE}),
        ("Registré la disputa y bloqueé tu tarjeta.", {DISPUTE, BLOCK}),
        # Portuguese
        ("Pronto. Registrei a contestação DSP-FX-0002 da cobrança.", {DISPUTE}),
        ("Sua contestação foi aberta com sucesso.", {DISPUTE}),
        ("Também bloqueei o seu cartão final 2208.", {BLOCK}),
        ("Seu cartão foi bloqueado.", {BLOCK}),
        ("Encaminhei você para um atendente.", {HANDOFF}),
        ("Estou transferindo você para um especialista.", {HANDOFF}),
    ],
)
def test_detects_actions_stated_as_done(text: str, expected: set[ActionType]) -> None:
    assert detect_claims(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Spanish: questions, offers, futures, negations, subjunctives, recommendations
        "¿Quieres que cree la disputa?",
        "Voy a abrir una disputa por el cargo de AMAZON MX. ¿Confirmas?",
        "Puedo bloquear tu tarjeta si lo deseas.",
        "Si quieres, bloqueo tu tarjeta ahora mismo.",
        "No abrí ninguna disputa sobre este cargo.",
        "Gracias por confirmarlo. No abrí ninguna disputa sobre este cargo.",
        "Todavía no he creado la disputa.",
        "Entendido, no hice ningún cambio.",
        "Te recomiendo que bloquees tu tarjeta desde la app.",
        "Para que registre la disputa necesito tu confirmación.",
        "Antes de crear la disputa, necesito confirmar el cargo.",
        "¿Quieres que te comunique con un asesor?",
        "Si prefieres, te comunico con un asesor.",
        "Podemos escalar tu caso a un asesor.",
        "Tu tarjeta ya estaba bloqueada desde enero.",
        "El comercio cree que la disputa no procede.",
        "Encontré un cargo de ELECTROMUNDO ONLINE por 2,450.00 MXN. ¿Reconoces este cargo?",
        # Portuguese
        "Você quer que eu abra uma contestação?",
        "Vou abrir uma contestação da cobrança. Você confirma?",
        "Posso bloquear o seu cartão, se você quiser.",
        "Não abri nenhuma contestação sobre essa cobrança.",
        "Ainda não bloqueei o cartão.",
        "Entendido, não fiz nenhuma alteração.",
        "Se preferir, posso encaminhar você para um atendente.",
        "Recomendo bloquear o cartão pelo aplicativo.",
    ],
)
def test_ignores_questions_offers_futures_and_negations(text: str) -> None:
    assert detect_claims(text) == frozenset()


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Tu tarjeta 4111 1111 1111 4821 fue revisada.", "card_number"),
        ("Tu tarjeta 4111-1111-1111-4821 fue revisada.", "card_number"),
        ("Te escribimos a mariana.test@example.com.", "email"),
        ("Llámanos al +52 55 1234 5678.", "phone"),
        ("Tu documento FX-DOC-MX-0001 está registrado.", "document_number"),
    ],
)
def test_detects_pii(text: str, kind: str) -> None:
    assert kind in detect_pii(text)


def test_detects_other_customers_name_ignoring_accents_and_case() -> None:
    assert detect_pii("Listo, CARLOS.", forbidden_names=["Carlos"]) == ["other_customer_name"]
    assert detect_pii("Hola lucia", forbidden_names=["Lucía"]) == ["other_customer_name"]


@pytest.mark.parametrize(
    "text",
    [
        "Encontré un cargo de 9,800,000.00 COP del 16/06/2026 con tu tarjeta terminada en 9001.",
        "Registré la disputa DSP-FX-C86EF7 por el cargo de AMAZON MX por 1,249.00 MXN.",
        "La transacción TXN-FX-0101 del 12/06/2026 por 2,450.00 MXN.",
        "Encontrei uma cobrança de 32,500.00 ARS em 15/06/2026 no seu cartão final 2208.",
        "Carlitos Bar cobró 45.00 MXN.",
    ],
)
def test_amounts_dates_ids_and_last4_are_not_pii(text: str) -> None:
    assert detect_pii(text, forbidden_names=["Carlos"]) == []
