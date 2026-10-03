"""Customer copy. The interpreter never supplies text to these functions."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC
from zoneinfo import ZoneInfo

from bankagent.contracts.api import ConfirmationView
from bankagent.contracts.base import args_hash
from bankagent.contracts.domain import TransactionView
from bankagent.contracts.enums import (
    ActionType,
    Channel,
    ConversationState,
    DisputeReason,
    Language,
    Outcome,
    StepKind,
    StepOutcome,
    ToolName,
)
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import (
    BlockCardArgs,
    BlockCardResult,
    CreateDisputeArgs,
    CreateDisputeResult,
    CreateHandoffArgs,
    CreateHandoffResult,
    GetTransactionArgs,
    GetTransactionResult,
    ListCardsArgs,
    ListCardsResult,
    SearchTransactionsArgs,
    SearchTransactionsResult,
)


class UnverifiedRenderError(ValueError):
    """The requested message would expose an unsupported fact or action claim."""


STATE_COPY: dict[ConversationState, dict[Language, str]] = {
    ConversationState.AUTH: {
        Language.ES: "Inicia sesión para que pueda ayudarte con tu solicitud.",
        Language.PT: "Entre na sua conta para que eu possa ajudar com sua solicitação.",
    },
    ConversationState.UNDERSTAND: {
        Language.ES: "Cuéntame qué pasó con el movimiento que quieres revisar.",
        Language.PT: "Conte o que aconteceu com a transação que você quer analisar.",
    },
    ConversationState.IDENTIFY_TXN: {
        Language.ES: "Indícame el comercio, la fecha o el importe para encontrar el movimiento.",
        Language.PT: "Informe o estabelecimento, a data ou o valor para localizar a transação.",
    },
    ConversationState.RECOGNIZE: {
        Language.ES: "Voy a mostrarte los datos del movimiento para que me digas si lo reconoces.",
        Language.PT: "Vou mostrar os dados da transação para você me dizer se a reconhece.",
    },
    ConversationState.CHECK_POLICY: {
        Language.ES: "Estoy revisando las condiciones aplicables a tu solicitud.",
        Language.PT: "Estou verificando as condições aplicáveis à sua solicitação.",
    },
    ConversationState.CONFIRM: {
        Language.ES: "Revisa la acción antes de confirmarla.",
        Language.PT: "Confira a ação antes de confirmá-la.",
    },
    ConversationState.ACT: {
        Language.ES: "Estoy procesando la solicitud.",
        Language.PT: "Estou processando a solicitação.",
    },
    ConversationState.VERIFY: {
        Language.ES: "Estoy comprobando el resultado de la acción.",
        Language.PT: "Estou verificando o resultado da ação.",
    },
    ConversationState.RESPOND: {
        Language.ES: "Estoy preparando la respuesta a tu solicitud.",
        Language.PT: "Estou preparando a resposta à sua solicitação.",
    },
    # Names what the search can use (dates are not a filter yet), so it can be answered.
    ConversationState.CLARIFY: {
        Language.ES: (
            "Necesito un dato más para encontrar el movimiento. "
            "¿Cuál es el comercio o el importe exacto?"
        ),
        Language.PT: (
            "Preciso de mais uma informação para localizar a transação. "
            "Qual é o estabelecimento ou o valor exato?"
        ),
    },
    ConversationState.ABSTAIN: {
        Language.ES: "No tengo información suficiente para continuar con seguridad.",
        Language.PT: "Não tenho informações suficientes para continuar com segurança.",
    },
    ConversationState.ESCALATE: {
        Language.ES: "Este caso necesita revisión de una persona del equipo.",
        Language.PT: "Este caso precisa da análise de uma pessoa da equipe.",
    },
    ConversationState.DONE: {
        Language.ES: "Gracias por contactarnos.",
        Language.PT: "Agradecemos seu contato.",
    },
}

OUTCOME_COPY: dict[Outcome, dict[Language, str]] = {
    Outcome.AUTOMATED_RESOLUTION: {
        Language.ES: "Ya terminé de revisar tu solicitud.",
        Language.PT: "Concluí a análise da sua solicitação.",
    },
    Outcome.DEFLECTED_RECOGNIZED: {
        Language.ES: "Gracias por confirmar que reconoces el movimiento.",
        Language.PT: "Agradeço por confirmar que reconhece a transação.",
    },
    Outcome.ABSTAINED: {
        Language.ES: "No tengo información suficiente para continuar con seguridad.",
        Language.PT: "Não tenho informações suficientes para continuar com segurança.",
    },
    Outcome.ESCALATED: {
        Language.ES: "Este caso necesita revisión de una persona del equipo.",
        Language.PT: "Este caso precisa da análise de uma pessoa da equipe.",
    },
    Outcome.DENIED: {
        Language.ES: "No puedo continuar con esta solicitud.",
        Language.PT: "Não posso continuar com esta solicitação.",
    },
    Outcome.REAUTH_REQUIRED: {
        Language.ES: "Tu sesión terminó. Inicia sesión de nuevo para continuar.",
        Language.PT: "Sua sessão terminou. Entre novamente na sua conta para continuar.",
    },
    Outcome.INCOMPLETE: {
        Language.ES: "La solicitud aún no está completa. Puedes continuar cuando quieras.",
        Language.PT: "A solicitação ainda não está completa. Você pode continuar quando quiser.",
    },
    Outcome.FAILED: {
        Language.ES: "No pude comprobar el resultado. Inténtalo más tarde o solicita ayuda.",
        Language.PT: "Não consegui verificar o resultado. Tente mais tarde ou peça ajuda.",
    },
}

# Asked while the customer has not described a request yet ("Hola"). It ends in a question that
# names what the agent needs, so the conversation stays open.
OPENING_QUESTION_COPY: dict[Language, str] = {
    Language.ES: (
        "Puedo ayudarte con un cargo que no reconoces, un cobro duplicado o una compra que no "
        "recibiste. ¿Qué pasó y cuál es el comercio o el importe del movimiento?"
    ),
    Language.PT: (
        "Posso ajudar com uma cobrança que você não reconhece, uma cobrança duplicada ou uma "
        "compra que não chegou. O que aconteceu e qual é o estabelecimento ou o valor da "
        "transação?"
    ),
}

# Offered after a verified dispute; neither line states an action as done.
BLOCK_OFFER_COPY: dict[Language, str] = {
    Language.ES: "También puedo bloquear la tarjeta para evitar nuevos cargos.",
    Language.PT: "Também posso bloquear o cartão para evitar novas cobranças.",
}
BLOCK_DECLINED_COPY: dict[Language, str] = {
    Language.ES: "Entendido, no bloquearé la tarjeta.",
    Language.PT: "Entendido, não vou bloquear o cartão.",
}

# Why the policy refused a dispute, by the `explanation_key` of each eligibility rule in
# `policy/dispute_policy_v1.yaml`. None of these lines states an action as done.
INELIGIBLE_COPY: dict[str, dict[Language, str]] = {
    "dispute.already_disputed": {
        Language.ES: "Este movimiento ya tiene un reclamo abierto, así que no voy a crear otro.",
        Language.PT: "Esta transação já tem uma contestação aberta, então não vou abrir outra.",
    },
    "dispute.out_of_window": {
        Language.ES: "Este movimiento está fuera del plazo para presentar un reclamo.",
        Language.PT: "Esta transação está fora do prazo para abrir uma contestação.",
    },
    "dispute.not_settled": {
        Language.ES: (
            "Este movimiento no es un cobro definitivo (está pendiente, fue rechazado o se "
            "revirtió), así que no se puede reclamar."
        ),
        Language.PT: (
            "Esta transação não é uma cobrança definitiva (está pendente, foi recusada ou "
            "estornada), então não pode ser contestada."
        ),
    },
}

REASONS: dict[DisputeReason, dict[Language, str]] = {
    DisputeReason.UNRECOGNIZED: {
        Language.ES: "movimiento no reconocido",
        Language.PT: "transação não reconhecida",
    },
    DisputeReason.DUPLICATE: {Language.ES: "cobro duplicado", Language.PT: "cobrança duplicada"},
    DisputeReason.NOT_RECEIVED: {
        Language.ES: "compra no recibida",
        Language.PT: "compra não recebida",
    },
}

CHANNELS: dict[Channel, dict[Language, str]] = {
    Channel.ATM: {Language.ES: "cajero automático", Language.PT: "caixa eletrônico"},
    Channel.BRANCH: {Language.ES: "sucursal", Language.PT: "agência"},
    Channel.WEB: {Language.ES: "sitio web", Language.PT: "site"},
    Channel.APP: {Language.ES: "aplicación", Language.PT: "aplicativo"},
    Channel.POS: {Language.ES: "compra presencial", Language.PT: "compra presencial"},
    Channel.TRANSFER: {Language.ES: "transferencia", Language.PT: "transferência"},
}

# More matches than this are not listed: the customer is asked for another clue instead.
MAX_CANDIDATES = 3

COUNTRY_ZONES = {
    "AR": ZoneInfo("America/Argentina/Buenos_Aires"),
    "CO": ZoneInfo("America/Bogota"),
    "MX": ZoneInfo("America/Mexico_City"),
}


def _clean(value: str) -> str:
    """Keep bank-sourced names on one line and short enough for customer copy."""
    cleaned = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in value
    ).split()
    text = " ".join(cleaned)
    return text[:59] + "…" if len(text) > 60 else text


@dataclass(frozen=True, slots=True)
class _ChargeFacts:
    """The display strings of one charge. Every sentence about a charge is built from these."""

    merchant: str
    date: str
    channel: str
    card_last4: str
    amount: str  # with its currency: "2,450.00 MXN"


def _charge_facts(txn: TransactionView, language: Language) -> _ChargeFacts:
    if not txn.card_last4:
        raise UnverifiedRenderError("transaction display requires card facts")
    merchant = _clean(txn.merchant_name or "")
    if not merchant:
        merchant = "no disponible" if language == Language.ES else "não disponível"
    zone = COUNTRY_ZONES.get(txn.transaction_country)
    date = txn.transaction_ts.astimezone(zone or UTC).strftime("%d/%m/%Y")
    if zone is None:
        date += " (UTC)"
    amount = f"{txn.amount:,.2f}"
    if txn.transaction_country in {"AR", "CO"}:
        amount = amount.replace(",", "_").replace(".", ",").replace("_", ".")
    return _ChargeFacts(
        merchant=merchant,
        date=date,
        channel=CHANNELS[txn.channel][language],
        card_last4=txn.card_last4,
        amount=f"{amount} {txn.currency}",
    )


def _transaction_facts(txn: TransactionView, language: Language) -> str:
    return _facts_sentence(_charge_facts(txn, language), language)


def _facts_sentence(facts: _ChargeFacts, language: Language) -> str:
    if language == Language.ES:
        return (
            f"comercio {facts.merchant}, fecha {facts.date}, canal {facts.channel}, "
            f"tarjeta terminada en {facts.card_last4}, importe {facts.amount}"
        )
    return (
        f"estabelecimento {facts.merchant}, data {facts.date}, canal {facts.channel}, "
        f"cartão com final {facts.card_last4}, valor {facts.amount}"
    )


def _require_verified(
    record: ExecutionRecord, tool: ToolName, *, expected_args_hash: str | None = None
) -> None:
    if (
        record.step != StepKind.TOOL_CALL
        or record.tool != tool
        or record.outcome != StepOutcome.SUCCESS
        or not record.verified
        or (expected_args_hash is not None and record.args_hash != expected_args_hash)
    ):
        raise UnverifiedRenderError(f"verified {tool.value} evidence is required")


def render_state(state: ConversationState, language: Language) -> str:
    """Render safe state copy; fact-specific states also have dedicated functions below."""
    return STATE_COPY[state][language]


def render_outcome(outcome: Outcome, language: Language) -> str:
    """Render an outcome without claiming a write or quoting unverified details."""
    return OUTCOME_COPY[outcome][language]


def render_opening_question(language: Language) -> str:
    """Ask what happened when no request has been described yet; claims nothing."""
    return OPENING_QUESTION_COPY[language]


def render_ineligible(explanation_key: str | None, language: Language) -> str:
    """Say why the policy refused the dispute, without claiming or promising any action.

    A key with no copy gets the generic refusal: the policy did decide, so it is not the
    "not enough information" abstention.
    """
    copy = INELIGIBLE_COPY.get(explanation_key or "")
    return copy[language] if copy else OUTCOME_COPY[Outcome.DENIED][language]


def render_recognition(
    language: Language,
    args: GetTransactionArgs,
    result: GetTransactionResult,
    record: ExecutionRecord,
) -> str:
    """Ask a neutral recognition question using an authenticated transaction read."""
    _require_verified(record, ToolName.GET_TRANSACTION, expected_args_hash=args_hash(args))
    txn = result.transaction
    if txn.transaction_id != args.transaction_id:
        raise UnverifiedRenderError("recognition requires a matching transaction")
    facts = _transaction_facts(txn, language)
    if language == Language.ES:
        return f"Encontré este movimiento: {facts}. ¿Reconoces este movimiento?"
    return f"Encontrei esta transação: {facts}. Você reconhece esta transação?"


def render_candidates(
    language: Language,
    args: SearchTransactionsArgs,
    result: SearchTransactionsResult,
    record: ExecutionRecord,
) -> str:
    """Ask which of a few matching transactions the customer means, from a verified search.

    The options are numbered in the order of the search result, so an answer by position
    ("el segundo") refers to the same list the caller holds.
    """
    _require_verified(record, ToolName.SEARCH_TRANSACTIONS, expected_args_hash=args_hash(args))
    transactions = result.transactions
    if not 2 <= len(transactions) <= MAX_CANDIDATES:
        raise UnverifiedRenderError("a choice needs two or three matching transactions")
    options = "; ".join(
        f"{number}) {_transaction_facts(txn, language)}"
        for number, txn in enumerate(transactions, start=1)
    )
    if language == Language.ES:
        return (
            f"Encontré {len(transactions)} movimientos que coinciden: {options}. "
            "¿Cuál de estos movimientos quieres revisar? "
            "Puedes responder con el número o el importe."
        )
    return (
        f"Encontrei {len(transactions)} transações que coincidem: {options}. "
        "Qual destas transações você quer analisar? "
        "Você pode responder com o número ou o valor."
    )


@dataclass(frozen=True, slots=True)
class ConfirmationPrompt:
    """A confirmation question, and the same question as data for the UI's panel (T14).

    Built together by one call from one verified read, so the panel cannot show a fact the
    question does not: the view's values are the question's own display strings.
    """

    text: str
    view: ConfirmationView


def confirmation_prompt(
    language: Language,
    args: CreateDisputeArgs | BlockCardArgs,
    read_args: GetTransactionArgs | ListCardsArgs,
    read_result: GetTransactionResult | ListCardsResult,
    record: ExecutionRecord,
) -> ConfirmationPrompt:
    """Ask to confirm the pending write, with recognizable facts from a matching verified read."""
    if isinstance(args, CreateDisputeArgs):
        if not isinstance(read_args, GetTransactionArgs) or not isinstance(
            read_result, GetTransactionResult
        ):
            raise UnverifiedRenderError("dispute confirmation requires a transaction read")
        _require_verified(record, ToolName.GET_TRANSACTION, expected_args_hash=args_hash(read_args))
        if (
            read_args.transaction_id != args.transaction_id
            or read_result.transaction.transaction_id != args.transaction_id
        ):
            raise UnverifiedRenderError("transaction read does not match pending dispute")
        charge = _charge_facts(read_result.transaction, language)
        reason = REASONS[args.reason][language]
        view = ConfirmationView(
            action=ActionType.CREATE_DISPUTE,
            card_last4=charge.card_last4,
            reason=reason,
            merchant=charge.merchant,
            date=charge.date,
            channel=charge.channel,
            amount=charge.amount,
        )
        facts = _facts_sentence(charge, language)
        if language == Language.ES:
            text = f"¿Confirmas crear un reclamo por {reason} para este movimiento: {facts}?"
        else:
            text = (
                f"Você confirma a abertura de uma contestação por {reason} "
                f"para esta transação: {facts}?"
            )
        return ConfirmationPrompt(text, view)
    if not isinstance(read_args, ListCardsArgs) or not isinstance(read_result, ListCardsResult):
        raise UnverifiedRenderError("card confirmation requires a card read")
    _require_verified(record, ToolName.LIST_CARDS, expected_args_hash=args_hash(read_args))
    card = next((item for item in read_result.cards if item.product_id == args.product_id), None)
    if card is None:
        raise UnverifiedRenderError("card read does not match pending block")
    view = ConfirmationView(action=ActionType.BLOCK_CARD, card_last4=card.card_last4)
    if language == Language.ES:
        text = f"¿Confirmas bloquear la tarjeta terminada en {card.card_last4}?"
    else:
        text = f"Você confirma o bloqueio do cartão com final {card.card_last4}?"
    return ConfirmationPrompt(text, view)


def render_confirmation(
    language: Language,
    args: CreateDisputeArgs | BlockCardArgs,
    read_args: GetTransactionArgs | ListCardsArgs,
    read_result: GetTransactionResult | ListCardsResult,
    record: ExecutionRecord,
) -> str:
    """The text of ``confirmation_prompt``, for a caller that shows no confirmation panel."""
    return confirmation_prompt(language, args, read_args, read_result, record).text


def block_offer_prompt(
    language: Language,
    args: BlockCardArgs,
    read_args: ListCardsArgs,
    read_result: ListCardsResult,
    record: ExecutionRecord,
) -> ConfirmationPrompt:
    """Offer a card block: a neutral lead, then the block confirmation from a verified card read."""
    question = confirmation_prompt(language, args, read_args, read_result, record)
    return ConfirmationPrompt(f"{BLOCK_OFFER_COPY[language]} {question.text}", question.view)


def render_block_offer(
    language: Language,
    args: BlockCardArgs,
    read_args: ListCardsArgs,
    read_result: ListCardsResult,
    record: ExecutionRecord,
) -> str:
    """The text of ``block_offer_prompt``, for a caller that shows no confirmation panel."""
    return block_offer_prompt(language, args, read_args, read_result, record).text


def render_block_declined(language: Language) -> str:
    """Acknowledge a declined card block without claiming any action."""
    return BLOCK_DECLINED_COPY[language]


def render_created_dispute(
    language: Language,
    args: CreateDisputeArgs,
    result: CreateDisputeResult,
    record: ExecutionRecord,
) -> str:
    """Claim dispute creation only after the matching write has been read back."""
    _require_verified(record, ToolName.CREATE_DISPUTE, expected_args_hash=args_hash(args))
    dispute = result.dispute
    if (
        not result.verified
        or dispute.transaction_id != args.transaction_id
        or dispute.reason != args.reason
        or dispute.idempotency_key != args.idempotency_key
    ):
        raise UnverifiedRenderError("verified dispute result does not match the requested action")
    if language == Language.ES:
        if result.created:
            return f"Creé el reclamo {dispute.dispute_id} para el movimiento que confirmaste."
        return f"El reclamo {dispute.dispute_id} ya está registrado para ese movimiento."
    if result.created:
        return f"Abri a contestação {dispute.dispute_id} para a transação que você confirmou."
    return f"A contestação {dispute.dispute_id} já está registrada para essa transação."


def render_blocked_card(
    language: Language,
    args: BlockCardArgs,
    result: BlockCardResult,
    record: ExecutionRecord,
) -> str:
    """Claim card blocking only after the matching write has been read back."""
    _require_verified(record, ToolName.BLOCK_CARD, expected_args_hash=args_hash(args))
    block = result.block
    if (
        not result.verified
        or block.product_id != args.product_id
        or block.reason != args.reason
        or block.idempotency_key != args.idempotency_key
    ):
        raise UnverifiedRenderError("verified block result does not match the requested action")
    if language == Language.ES:
        if result.created:
            return f"Bloqueé la tarjeta terminada en {block.card_last4}."
        return f"La tarjeta terminada en {block.card_last4} está bloqueada."
    if result.created:
        return f"Bloqueei o cartão com final {block.card_last4}."
    return f"O cartão com final {block.card_last4} está bloqueado."


def render_created_handoff(
    language: Language,
    args: CreateHandoffArgs,
    result: CreateHandoffResult,
    record: ExecutionRecord,
) -> str:
    """Claim handoff creation only when its write is verified."""
    _require_verified(record, ToolName.CREATE_HANDOFF, expected_args_hash=args_hash(args))
    if not result.verified or result.routing != args.draft.routing:
        raise UnverifiedRenderError("verified handoff result is required")
    if language == Language.ES:
        if result.created:
            return f"Registré tu solicitud para revisión humana con referencia {result.handoff_id}."
        return (
            f"Tu solicitud de revisión humana está registrada con referencia {result.handoff_id}."
        )
    if result.created:
        return (
            f"Registrei sua solicitação para análise humana sob a referência {result.handoff_id}."
        )
    return (
        f"Sua solicitação de análise humana está registrada sob a referência {result.handoff_id}."
    )
