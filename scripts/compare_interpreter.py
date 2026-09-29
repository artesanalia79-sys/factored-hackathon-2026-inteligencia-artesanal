"""Compare the real interpreter with the keyword baseline on synthetic dev utterances.

Run with: uv run --env-file .env --group llm python scripts/compare_interpreter.py
Only labels, token counts and aggregate costs are printed. The run cap is 0.10 USD.
"""

from __future__ import annotations

from decimal import Decimal

from bankagent.contracts.decisions import InterpretationResult
from bankagent.contracts.enums import Intent
from bankagent.contracts.llm import ChatMessage
from bankagent.interpret.keywords import interpret_text
from bankagent.interpret.openai_provider import OpenAIProvider

CASES = (
    ("Me cobraron dos veces en Rappi", Intent.DISPUTE_DUPLICATE),
    ("Não reconheço essa compra no cartão", Intent.DISPUTE_UNRECOGNIZED),
    ("Mi pedido nunca llegó y aparece cobrado", Intent.DISPUTE_NOT_RECEIVED),
    ("Quiero bloquear mi tarjeta ahora", Intent.CARD_BLOCK),
    ("¿Cómo va mi reclamo?", Intent.DISPUTE_STATUS),
    ("Necesito hablar con una persona", Intent.HUMAN_REQUEST),
    ("El cargo de ayer no es mío, che", Intent.DISPUTE_UNRECOGNIZED),
    ("Me cobraram duas vezes pelo mesmo pedido", Intent.DISPUTE_DUPLICATE),
    ("Aparecen dos cargos idénticos por una sola compra", Intent.DISPUTE_DUPLICATE),
    ("Vejo duas cobranças iguais no extrato", Intent.DISPUTE_DUPLICATE),
    ("Nunca autoricé ese pago", Intent.DISPUTE_UNRECOGNIZED),
    ("Esse lançamento não fui eu", Intent.DISPUTE_UNRECOGNIZED),
    ("Pagué un producto que no me entregaron", Intent.DISPUTE_NOT_RECEIVED),
    ("Minha encomenda não chegou", Intent.DISPUTE_NOT_RECEIVED),
    ("¿Me pueden comunicar con un asesor?", Intent.HUMAN_REQUEST),
    ("Onde acompanho a contestação?", Intent.DISPUTE_STATUS),
)


def main() -> None:
    provider = OpenAIProvider(spend_limit_usd=Decimal("0.10"))
    baseline_hits = 0
    model_hits = 0
    for index, (utterance, expected) in enumerate(CASES, 1):
        baseline = interpret_text(utterance).intent
        completion = provider.complete_structured(
            system="Interpret one customer message.",
            messages=[ChatMessage(role="user", content=utterance)],
            response_model=InterpretationResult,
            timeout_s=20,
        )
        actual = completion.output.intent
        baseline_hits += baseline == expected
        model_hits += actual == expected
        print(f"{index}: expected={expected} baseline={baseline} openai={actual}")
    print(f"baseline={baseline_hits}/{len(CASES)} openai={model_hits}/{len(CASES)}")
    print(f"spent_usd={provider.spent_usd}")


if __name__ == "__main__":
    main()
