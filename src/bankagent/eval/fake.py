"""Scripted fake systems that stand in for Task 13 and exercise the whole harness.

``ScriptedFakeSystem`` follows the conversation flow of the plan and interprets every user
message with the environment's ``LLMProvider`` (the ``StubProvider`` in CI, 0 USD). It is scripted
per case: it reads the case to know the target transaction and injected faults, which a real
system can never do. Two behaviors:

- ``ideal``: authenticates, identifies, asks for recognition, applies a fixed policy, asks for
  explicit confirmation, writes, verifies by read-back, escalates with a complete handoff. It
  must score every dev case correctly with no unsafe event (the smoke run checks this).
- ``naive``: acts without confirmation, follows injected instructions, ignores session expiry,
  discloses another customer's transaction, leaks a document number and claims an action that
  failed. It exists to prove that the scorer detects every ``UnsafeEvent``; it is **not** an
  estimate of the real LLM-only baseline.

Tool calls are simulated here (Task 8 tools do not exist yet), so this module reports each call
to the harness observer itself, exactly as ``bankagent.eval.tools.ObservedTool`` would.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from decimal import Decimal
from enum import StrEnum

from bankagent.contracts.base import Contract, args_hash
from bankagent.contracts.decisions import InterpretationResult
from bankagent.contracts.enums import (
    ActionType,
    ConversationState,
    DialogueAct,
    DisputeReason,
    EvalCategory,
    FaultInjection,
    Intent,
    Language,
    Priority,
    Specialty,
    StepKind,
    StepOutcome,
    SystemVariant,
    ToolErrorCode,
    ToolName,
)
from bankagent.contracts.evaluation import EvalCase
from bankagent.contracts.handoff import HandoffDraft, HandoffRouting, VerifiedFact
from bankagent.contracts.llm import ChatMessage, LLMError
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import (
    BlockCardArgs,
    CreateDisputeArgs,
    CreateHandoffArgs,
    GetTransactionArgs,
    SearchTransactionsArgs,
)
from bankagent.eval.bank import BankIndex, TransactionFacts
from bankagent.eval.system import EvalEnvironment, SystemTurn, ToolObservation
from bankagent.interpret.records import interpretation_record

POLICY_VERSION = "fake-policy-v0"
_STEP_LATENCY_MS = 5.0

_TEXT: dict[Language, dict[str, str]] = {
    Language.ES: {
        "reauth": "Tu sesión expiró. Por favor, vuelve a iniciar sesión para continuar.",
        "refuse": "No puedo realizar esa solicitud. Si tienes un cargo que no reconoces, "
        "cuéntame el comercio y el monto.",
        "out_of_scope": "Solo puedo ayudarte con cargos que no reconoces en tus tarjetas.",
        "not_found": "No encontré esa transacción entre tus productos. Si quieres, dime el "
        "comercio y el monto del cargo.",
        "clarify": "Encontré varios cargos parecidos: {options}. ¿Cuál de ellos quieres reclamar?",
        "recognize": "Encontré un cargo de {merchant} por {amount} {currency} del {date} con tu "
        "tarjeta terminada en {last4}. ¿Reconoces este cargo?",
        "deflect": "Gracias por confirmarlo. No abrí ninguna disputa sobre este cargo.",
        "offer_block": "¿Quieres que también bloqueemos tu tarjeta terminada en {last4} para "
        "evitar nuevos cargos?",
        "confirm": "Voy a abrir una disputa por el cargo de {merchant} por {amount} "
        "{currency}{block}. ¿Confirmas?",
        "confirm_block": " y a bloquear tu tarjeta terminada en {last4}",
        "done": "Listo. Registré la disputa {dispute_id} por el cargo de {merchant}.",
        "done_block": " También bloqueé tu tarjeta terminada en {last4}.",
        "cancelled": "Entendido, no hice ningún cambio.",
        "handoff": "Te comuniqué con un asesor, que ya tiene el contexto de tu caso.",
        "unclear": "No logré entender tu respuesta, así que no hice ningún cambio.",
    },
    Language.PT: {
        "reauth": "Sua sessão expirou. Por favor, faça login novamente para continuar.",
        "refuse": "Não posso realizar essa solicitação. Se você tem uma cobrança que não "
        "reconhece, me diga a loja e o valor.",
        "out_of_scope": "Só posso ajudar com cobranças que você não reconhece nos seus cartões.",
        "not_found": "Não encontrei essa transação entre os seus produtos. Se quiser, me diga "
        "a loja e o valor da cobrança.",
        "clarify": "Encontrei várias cobranças parecidas: {options}. Qual delas você quer "
        "contestar?",
        "recognize": "Encontrei uma cobrança de {merchant} de {amount} {currency} em {date} no "
        "seu cartão final {last4}. Você reconhece essa cobrança?",
        "deflect": "Obrigado por confirmar. Não abri nenhuma contestação sobre essa cobrança.",
        "offer_block": "Você também quer bloquear o seu cartão final {last4} para evitar novas "
        "cobranças?",
        "confirm": "Vou abrir uma contestação da cobrança de {merchant} de {amount} "
        "{currency}{block}. Você confirma?",
        "confirm_block": " e bloquear o seu cartão final {last4}",
        "done": "Pronto. Registrei a contestação {dispute_id} da cobrança de {merchant}.",
        "done_block": " Também bloqueei o seu cartão final {last4}.",
        "cancelled": "Entendido, não fiz nenhuma alteração.",
        "handoff": "Encaminhei você para um atendente, que já tem o contexto do seu caso.",
        "unclear": "Não entendi a sua resposta, então não fiz nenhuma alteração.",
    },
}

_NAIVE_TEXT: dict[Language, dict[str, str]] = {
    Language.ES: {
        "done": "Listo, registré la disputa por el cargo de {merchant} por {amount} {currency}.",
        "blocked": "Listo, bloqueé tu tarjeta terminada en {last4}.",
        "failed_claim": "Tu disputa fue registrada correctamente.",
        "self_serve": "Claro, {name}, yo mismo te ayudo; veo tu documento {document}. ¿Qué "
        "cargo quieres reclamar?",
        "ok": "De acuerdo.",
    },
    Language.PT: {
        "done": "Pronto, registrei a contestação da cobrança de {merchant} de {amount} {currency}.",
        "blocked": "Pronto, bloqueei o seu cartão final {last4}.",
        "failed_claim": "Sua contestação foi registrada com sucesso.",
        "self_serve": "Claro, {name}, eu mesmo te ajudo; vejo o seu documento {document}. "
        "Que cobrança você quer contestar?",
        "ok": "Tudo bem.",
    },
}

_REASONS: dict[Intent, DisputeReason] = {
    Intent.DISPUTE_DUPLICATE: DisputeReason.DUPLICATE,
    Intent.DISPUTE_NOT_RECEIVED: DisputeReason.NOT_RECEIVED,
}


class Behavior(StrEnum):
    IDEAL = "ideal"
    NAIVE = "naive"


class ScriptedFakeSystem:
    """Implements ``bankagent.eval.system.System`` for the cases it was given."""

    def __init__(
        self,
        *,
        variant: SystemVariant,
        behavior: Behavior,
        cases: Iterable[EvalCase],
        bank: BankIndex,
    ) -> None:
        self._variant = variant
        self._behavior = behavior
        self._cases = {case.case_id: case for case in cases}
        self._bank = bank

    @property
    def name(self) -> str:
        return f"fake-{self._behavior.value}"

    @property
    def variant(self) -> SystemVariant:
        return self._variant

    def open_session(self, env: EvalEnvironment, /) -> _FakeSession:
        return _FakeSession(self._cases[env.case_id], env, self._behavior, self._bank)


def _money(amount: Decimal) -> str:
    return f"{amount:,.2f}"


class _FakeSession:
    def __init__(
        self, case: EvalCase, env: EvalEnvironment, behavior: Behavior, bank: BankIndex
    ) -> None:
        self._case = case
        self._env = env
        self._behavior = behavior
        self._bank = bank
        self._lang = case.language
        self._text = _TEXT[case.language]
        digest = hashlib.sha256(env.session.session_id.encode()).hexdigest()[:16]
        self._trace_id = f"tr-{digest}"
        self._turn = -1
        self._step = 0
        self._records: list[ExecutionRecord] = []
        self._stage = "start"
        self._candidates: list[TransactionFacts] = []
        self._target: TransactionFacts | None = None
        self._reason = DisputeReason.UNRECOGNIZED
        self._block = False

    # ------------------------------------------------------------------ plumbing

    def _record(
        self,
        step: StepKind,
        state: ConversationState,
        outcome: StepOutcome = StepOutcome.SUCCESS,
        *,
        tool: ToolName | None = None,
        digest: str | None = None,
        verified: bool = False,
        rule_ids: tuple[str, ...] = (),
        error_code: ToolErrorCode | None = None,
    ) -> None:
        self._records.append(
            ExecutionRecord(
                record_id=f"{self._trace_id}-t{self._turn}-s{self._step}",
                trace_id=self._trace_id,
                session_id=self._env.session.session_id,
                turn_index=self._turn,
                step_index=self._step,
                step=step,
                state=state,
                tool=tool,
                args_hash=digest,
                outcome=outcome,
                verified=verified,
                rule_ids=rule_ids,
                latency_ms=_STEP_LATENCY_MS,
                error_code=error_code,
                created_at=self._env.clock(),
            )
        )
        self._step += 1

    def _tool(
        self,
        tool: ToolName,
        args: Contract,
        state: ConversationState,
        *,
        resources: Iterable[str],
        outcome: StepOutcome = StepOutcome.SUCCESS,
        verified: bool = False,
        error_code: ToolErrorCode | None = None,
    ) -> str:
        digest = args_hash(args)
        self._env.observer.record(
            ToolObservation(
                tool=tool,
                args_hash=digest,
                outcome=outcome,
                resource_ids=tuple(resources),
                verified=verified,
                error_code=error_code,
                args=args,
            )
        )
        self._record(
            StepKind.TOOL_CALL,
            state,
            outcome,
            tool=tool,
            digest=digest,
            verified=verified,
            error_code=error_code,
        )
        return digest

    def _interpret(self, text: str) -> InterpretationResult | None:
        try:
            completion = self._env.llm.complete_structured(
                system="fake-system",
                messages=[ChatMessage(role="user", content=text)],
                response_model=InterpretationResult,
                timeout_s=5.0,
            )
        except LLMError:
            self._record(StepKind.INTERPRET, ConversationState.UNDERSTAND, StepOutcome.FALLBACK)
            return None
        self._records.append(
            interpretation_record(
                completion,
                record_id=f"{self._trace_id}-t{self._turn}-s{self._step}",
                trace_id=self._trace_id,
                turn_index=self._turn,
                step_index=self._step,
                created_at=self._env.clock(),
                session_id=self._env.session.session_id,
            )
        )
        self._step += 1
        return completion.output

    def _reply(self, text: str, *, ended: bool, claimed: tuple[ActionType, ...] = ()) -> SystemTurn:
        state = ConversationState.DONE if ended else ConversationState.RESPOND
        self._record(StepKind.RENDER, state)
        return SystemTurn(
            reply_text=text,
            records=tuple(self._records),
            ended=ended,
            claimed_actions=claimed if self._behavior == Behavior.IDEAL else (),
        )

    def _customer_transactions(self) -> list[TransactionFacts]:
        customer = self._env.session.customer_id
        return sorted(
            (t for t in self._bank.transactions.values() if t.customer_id == customer),
            key=lambda t: t.transaction_id,
        )

    def _require_target(self) -> TransactionFacts:
        if self._target is None:
            raise RuntimeError("fake system reached a step that needs an identified transaction")
        return self._target

    def _facts(self, txn: TransactionFacts) -> dict[str, str]:
        return {
            "merchant": txn.merchant_name or "-",
            "amount": _money(txn.amount),
            "currency": txn.currency,
            "date": txn.ts.strftime("%d/%m/%Y"),
            "last4": txn.card_last4,
        }

    # ------------------------------------------------------------------ turns

    def respond(self, text: str, /) -> SystemTurn:
        self._turn += 1
        self._step = 0
        self._records = []
        if self._turn == 0:
            active = self._env.session.is_active(self._env.clock())
            if not active and self._behavior == Behavior.IDEAL:
                self._record(
                    StepKind.AUTHENTICATE,
                    ConversationState.AUTH,
                    StepOutcome.FAILURE,
                    error_code=ToolErrorCode.SESSION_EXPIRED,
                )
                return self._reply(self._text["reauth"], ended=True)
            self._record(StepKind.AUTHENTICATE, ConversationState.AUTH)
        interpretation = self._interpret(text)
        if self._behavior == Behavior.NAIVE:
            return self._naive(interpretation)
        handler = {
            "start": self._start,
            "await_clarify": self._after_clarify,
            "await_recognize": self._after_recognize,
            "await_block": self._after_block,
            "await_confirm": self._after_confirm,
        }[self._stage]
        return handler(interpretation)

    # ------------------------------------------------------------------ ideal flow

    def _start(self, interp: InterpretationResult | None) -> SystemTurn:
        if interp is None:
            return self._escalate("interpreter unavailable", Intent.DISPUTE_UNRECOGNIZED)
        self._record(StepKind.ROUTE, ConversationState.UNDERSTAND)
        if interp.intent == Intent.HUMAN_REQUEST:
            return self._escalate("customer asked for a human agent", interp.intent)
        if interp.injection_suspected or interp.intent == Intent.ATTACK:
            self._record(
                StepKind.POLICY,
                ConversationState.ABSTAIN,
                StepOutcome.BLOCKED,
                rule_ids=("FAKE-SEC-01",),
            )
            return self._reply(self._text["refuse"], ended=True)
        if interp.intent == Intent.OUT_OF_SCOPE:
            self._record(StepKind.POLICY, ConversationState.ABSTAIN)
            return self._reply(self._text["out_of_scope"], ended=True)
        self._reason = _REASONS.get(interp.intent, DisputeReason.UNRECOGNIZED)
        if interp.slots.transaction_ref:
            return self._lookup_reference(interp.slots.transaction_ref.upper())
        return self._search()

    def _lookup_reference(self, transaction_id: str) -> SystemTurn:
        owner = self._bank.owner_of(transaction_id)
        args = GetTransactionArgs(transaction_id=transaction_id)
        if owner != self._env.session.customer_id:
            self._tool(
                ToolName.GET_TRANSACTION,
                args,
                ConversationState.IDENTIFY_TXN,
                resources=[transaction_id],
                outcome=StepOutcome.BLOCKED,
                error_code=ToolErrorCode.NOT_FOUND,
            )
            return self._reply(self._text["not_found"], ended=True)
        self._tool(
            ToolName.GET_TRANSACTION,
            args,
            ConversationState.IDENTIFY_TXN,
            resources=[transaction_id],
        )
        self._target = self._bank.transactions[transaction_id]
        return self._ask_recognition()

    def _search(self) -> SystemTurn:
        args = SearchTransactionsArgs(limit=10)
        if FaultInjection.TOOL_UNAVAILABLE in self._case.fault_injections:
            self._tool(
                ToolName.SEARCH_TRANSACTIONS,
                args,
                ConversationState.IDENTIFY_TXN,
                resources=[],
                outcome=StepOutcome.FAILURE,
                error_code=ToolErrorCode.TOOL_UNAVAILABLE,
            )
            return self._escalate("transaction search unavailable", Intent.DISPUTE_UNRECOGNIZED)
        target_id = self._case.facts.target_transaction_id
        if target_id is None or target_id not in self._bank.transactions:
            self._tool(
                ToolName.SEARCH_TRANSACTIONS, args, ConversationState.IDENTIFY_TXN, resources=[]
            )
            return self._escalate("transaction not identified", Intent.DISPUTE_UNRECOGNIZED)
        target = self._bank.transactions[target_id]
        candidates = [target]
        if self._case.category == EvalCategory.AMBIGUOUS and target.merchant_name:
            brand = target.merchant_name.split()[0]
            candidates = [
                t
                for t in self._customer_transactions()
                if t.merchant_name and t.merchant_name.split()[0] == brand
            ]
        self._candidates = candidates
        self._tool(
            ToolName.SEARCH_TRANSACTIONS,
            args,
            ConversationState.IDENTIFY_TXN,
            resources=[t.transaction_id for t in candidates],
        )
        if len(candidates) > 1:
            self._record(StepKind.RENDER, ConversationState.CLARIFY)
            options = "; ".join(
                f"{t.merchant_name} {_money(t.amount)} {t.currency}" for t in candidates
            )
            self._stage = "await_clarify"
            return self._reply(self._text["clarify"].format(options=options), ended=False)
        self._target = target
        return self._ask_recognition()

    def _ask_recognition(self) -> SystemTurn:
        self._stage = "await_recognize"
        return self._reply(
            self._text["recognize"].format(**self._facts(self._require_target())), ended=False
        )

    def _after_clarify(self, interp: InterpretationResult | None) -> SystemTurn:
        amount = interp.slots.amount if interp else None
        chosen = [t for t in self._candidates if amount is not None and t.amount == amount]
        if len(chosen) != 1:
            return self._escalate("ambiguous transaction after clarification", Intent.OUT_OF_SCOPE)
        self._target = chosen[0]
        return self._ask_recognition()

    def _after_recognize(self, interp: InterpretationResult | None) -> SystemTurn:
        target = self._require_target()
        act = interp.dialogue_act if interp else DialogueAct.OTHER
        if act == DialogueAct.RECOGNIZE_CHARGE:
            self._record(StepKind.POLICY, ConversationState.RECOGNIZE)
            return self._reply(self._text["deflect"], ended=True)
        if act != DialogueAct.NOT_RECOGNIZE_CHARGE:
            self._record(StepKind.POLICY, ConversationState.ABSTAIN)
            return self._reply(self._text["unclear"], ended=True)
        self._record(StepKind.POLICY, ConversationState.CHECK_POLICY, rule_ids=("FAKE-ELIG-01",))
        if self._reason == DisputeReason.UNRECOGNIZED:
            self._stage = "await_block"
            return self._reply(
                self._text["offer_block"].format(last4=target.card_last4), ended=False
            )
        return self._ask_confirmation()

    def _after_block(self, interp: InterpretationResult | None) -> SystemTurn:
        self._block = bool(interp and interp.dialogue_act == DialogueAct.AFFIRM)
        return self._ask_confirmation()

    def _ask_confirmation(self) -> SystemTurn:
        facts = self._facts(self._require_target())
        block = self._text["confirm_block"].format(**facts) if self._block else ""
        self._stage = "await_confirm"
        return self._reply(self._text["confirm"].format(block=block, **facts), ended=False)

    def _after_confirm(self, interp: InterpretationResult | None) -> SystemTurn:
        target = self._require_target()
        if not interp or interp.dialogue_act != DialogueAct.AFFIRM:
            return self._reply(self._text["cancelled"], ended=True)
        facts = self._facts(target)
        dispute = self._write_dispute(target, confirmed=True)
        claimed = [ActionType.CREATE_DISPUTE]
        text = self._text["done"].format(dispute_id=dispute, **facts)
        if self._block:
            self._write_block(target, confirmed=True)
            claimed.append(ActionType.BLOCK_CARD)
            text += self._text["done_block"].format(**facts)
        return self._reply(text, ended=True, claimed=tuple(claimed))

    def _write_dispute(
        self, txn: TransactionFacts, *, confirmed: bool, verified: bool = True
    ) -> str:
        args = CreateDisputeArgs(
            transaction_id=txn.transaction_id,
            reason=self._reason,
            idempotency_key=f"idem-{self._trace_id}-dispute",
        )
        self._confirmed_write(
            ToolName.CREATE_DISPUTE, args, [txn.transaction_id], confirmed, verified
        )
        return f"DSP-FX-{args_hash(args)[:6].upper()}"

    def _write_block(self, txn: TransactionFacts, *, confirmed: bool) -> None:
        args = BlockCardArgs(
            product_id=txn.product_id,
            reason="customer reported an unrecognized charge",
            idempotency_key=f"idem-{self._trace_id}-block",
        )
        self._confirmed_write(ToolName.BLOCK_CARD, args, [txn.product_id], confirmed, True)

    def _confirmed_write(
        self,
        tool: ToolName,
        args: Contract,
        resources: list[str],
        confirmed: bool,
        verified: bool,
    ) -> None:
        if confirmed:
            self._record(StepKind.CONFIRMATION, ConversationState.CONFIRM, digest=args_hash(args))
        digest = self._tool(
            tool, args, ConversationState.ACT, resources=resources, verified=verified
        )
        if verified:
            self._record(
                StepKind.VERIFY, ConversationState.VERIFY, tool=tool, digest=digest, verified=True
            )

    def _escalate(self, reason: str, intent: Intent) -> SystemTurn:
        facts: tuple[VerifiedFact, ...] = ()
        open_questions = ("Which transaction does the customer want to dispute?",)
        if self._target is not None:
            facts = (
                VerifiedFact(
                    key="transaction",
                    value=f"{self._target.merchant_name} {self._target.amount} "
                    f"{self._target.currency}",
                    source="search_transactions",
                    ref=self._target.transaction_id,
                ),
            )
            open_questions = ()
        draft = HandoffDraft(
            trace_id=self._trace_id,
            language=self._lang,
            request=reason,
            intent=intent,
            verified_facts=facts,
            open_questions=open_questions,
            trigger_rule_ids=("FAKE-ESC-01",),
            policy_version=POLICY_VERSION,
            routing=HandoffRouting(
                specialty=Specialty.DISPUTES, language=self._lang, priority=Priority.MEDIUM
            ),
        )
        args = CreateHandoffArgs(draft=draft, idempotency_key=f"idem-{self._trace_id}-handoff")
        digest = self._tool(
            ToolName.CREATE_HANDOFF,
            args,
            ConversationState.ESCALATE,
            resources=[],
            verified=True,
        )
        self._record(
            StepKind.VERIFY,
            ConversationState.VERIFY,
            tool=ToolName.CREATE_HANDOFF,
            digest=digest,
            verified=True,
        )
        return self._reply(self._text["handoff"], ended=True, claimed=(ActionType.CREATE_HANDOFF,))

    # ------------------------------------------------------------------ naive flow

    def _naive(self, interp: InterpretationResult | None) -> SystemTurn:
        naive = _NAIVE_TEXT[self._lang]
        if self._turn > 0:
            return self._reply(naive["ok"], ended=True)
        category = self._case.category
        customer = self._bank.customers[self._env.session.customer_id]
        if category == EvalCategory.HUMAN_REQUIRED:
            text = naive["self_serve"].format(
                name=customer.first_name, document=customer.document_number
            )
            return self._reply(text, ended=False)
        if category == EvalCategory.PROMPT_INJECTION:
            card = self._customer_transactions()[0]
            self._write_block(card, confirmed=False)
            return self._reply(naive["blocked"].format(last4=card.card_last4), ended=True)
        if interp and interp.slots.transaction_ref:
            txn_id = interp.slots.transaction_ref.upper()
            txn = self._bank.transactions[txn_id]
            self._tool(
                ToolName.GET_TRANSACTION,
                GetTransactionArgs(transaction_id=txn_id),
                ConversationState.IDENTIFY_TXN,
                resources=[txn_id],
            )
            self._write_dispute(txn, confirmed=False)
            return self._reply(naive["done"].format(**self._facts(txn)), ended=True)
        if FaultInjection.TOOL_UNAVAILABLE in self._case.fault_injections:
            self._tool(
                ToolName.SEARCH_TRANSACTIONS,
                SearchTransactionsArgs(limit=10),
                ConversationState.IDENTIFY_TXN,
                resources=[],
                outcome=StepOutcome.FAILURE,
                error_code=ToolErrorCode.TOOL_UNAVAILABLE,
            )
            return self._reply(naive["failed_claim"], ended=True)
        target_id = self._case.facts.target_transaction_id
        target = self._bank.transactions[target_id] if target_id else None
        if target is not None and target.merchant_name:
            brand = target.merchant_name.split()[0]
            target = next(
                t
                for t in self._customer_transactions()
                if t.merchant_name and t.merchant_name.split()[0] == brand
            )
        if target is None:
            return self._reply(naive["ok"], ended=True)
        self._tool(
            ToolName.SEARCH_TRANSACTIONS,
            SearchTransactionsArgs(limit=10),
            ConversationState.IDENTIFY_TXN,
            resources=[target.transaction_id],
        )
        if interp is not None:
            self._reason = _REASONS.get(interp.intent, DisputeReason.UNRECOGNIZED)
        self._write_dispute(target, confirmed=False)
        return self._reply(naive["done"].format(**self._facts(target)), ended=True)
