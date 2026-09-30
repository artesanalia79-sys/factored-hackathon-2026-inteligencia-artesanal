"""Customer copy. The interpreter never supplies text to these functions."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from bankagent.contracts.base import args_hash
from bankagent.contracts.domain import TransactionView
from bankagent.contracts.enums import (
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
    ConversationState.CLARIFY: {
        Language.ES: "Necesito un dato más para continuar. ¿Puedes darme más detalles?",
        Language.PT: "Preciso de mais uma informação para continuar. Você pode dar mais detalhes?",
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
    Channel.POS: {Language.ES: "comercio", Language.PT: "estabelecimento"},
    Channel.TRANSFER: {Language.ES: "transferencia", Language.PT: "transferência"},
}

COUNTRY_ZONES = {
    "AR": ZoneInfo("America/Argentina/Buenos_Aires"),
    "CO": ZoneInfo("America/Bogota"),
    "MX": ZoneInfo("America/Mexico_City"),
}


def _clean(value: str) -> str:
    """Keep bank-sourced names on one line and short enough for customer copy."""
    cleaned = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value).split()
    text = " ".join(cleaned)
    return text[:59] + "…" if len(text) > 60 else text


def _transaction_facts(txn: TransactionView, language: Language) -> str:
    if not txn.merchant_name or not txn.card_last4 or not _clean(txn.merchant_name):
        raise UnverifiedRenderError("transaction display requires merchant and card facts")
    zone = COUNTRY_ZONES.get(txn.transaction_country)
    if zone is None:
        raise UnverifiedRenderError("transaction country has no display timezone")
    date = txn.transaction_ts.astimezone(zone).strftime("%d/%m/%Y")
    amount = f"{txn.amount:,.2f}"
    if txn.transaction_country in {"AR", "CO"}:
        amount = amount.replace(",", "_").replace(".", ",").replace("_", ".")
    channel = CHANNELS[txn.channel][language]
    if language == Language.ES:
        return (
            f"comercio {_clean(txn.merchant_name)}, fecha {date}, canal {channel}, "
            f"tarjeta terminada en {txn.card_last4}, importe {amount} {txn.currency}"
        )
    return (
        f"estabelecimento {_clean(txn.merchant_name)}, data {date}, canal {channel}, "
        f"cartão com final {txn.card_last4}, valor {amount} {txn.currency}"
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


def render_recognition(
    language: Language,
    args: GetTransactionArgs,
    result: GetTransactionResult,
    record: ExecutionRecord,
) -> str:
    """Ask a neutral recognition question using an authenticated transaction read."""
    _require_verified(record, ToolName.GET_TRANSACTION, expected_args_hash=args_hash(args))
    txn = result.transaction
    if txn.transaction_id != args.transaction_id or not txn.merchant_name or not txn.card_last4:
        raise UnverifiedRenderError("recognition requires matching merchant and card facts")
    facts = _transaction_facts(txn, language)
    if language == Language.ES:
        return f"Encontré este movimiento: {facts}. ¿Reconoces este movimiento?"
    return f"Encontrei esta transação: {facts}. Você reconhece esta transação?"


def render_confirmation(
    language: Language,
    args: CreateDisputeArgs | BlockCardArgs,
    read_args: GetTransactionArgs | ListCardsArgs,
    read_result: GetTransactionResult | ListCardsResult,
    record: ExecutionRecord,
) -> str:
    """Show recognizable facts from a matching authenticated read before confirmation."""
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
        facts = _transaction_facts(read_result.transaction, language)
        reason = REASONS[args.reason][language]
        if language == Language.ES:
            return f"¿Confirmas crear un reclamo por {reason} para este movimiento: {facts}?"
        return (
            f"Você confirma a abertura de uma contestação por {reason} "
            f"para esta transação: {facts}?"
        )
    if not isinstance(read_args, ListCardsArgs) or not isinstance(read_result, ListCardsResult):
        raise UnverifiedRenderError("card confirmation requires a card read")
    _require_verified(record, ToolName.LIST_CARDS, expected_args_hash=args_hash(read_args))
    card = next((item for item in read_result.cards if item.product_id == args.product_id), None)
    if card is None:
        raise UnverifiedRenderError("card read does not match pending block")
    if language == Language.ES:
        return f"¿Confirmas bloquear la tarjeta terminada en {card.card_last4}?"
    return f"Você confirma o bloqueio do cartão com final {card.card_last4}?"


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
            return (
                f"Creé el reclamo {dispute.dispute_id} "
                f"para la transacción {dispute.transaction_id}."
            )
        return (
            f"El reclamo {dispute.dispute_id} está registrado "
            f"para la transacción {dispute.transaction_id}."
        )
    if result.created:
        return f"Abri a contestação {dispute.dispute_id} para a transação {dispute.transaction_id}."
    return (
        f"A contestação {dispute.dispute_id} está registrada "
        f"para a transação {dispute.transaction_id}."
    )


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
