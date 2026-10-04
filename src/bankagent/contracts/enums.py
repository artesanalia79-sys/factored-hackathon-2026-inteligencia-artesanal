"""Enumerations shared by every lane.

Our own enums use ``snake_case`` values. Enums that mirror organizer source data (segment,
statuses, channel, transaction type) keep the source values so the serving DB needs no remapping.
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------


class Language(StrEnum):
    ES = "es"
    PT = "pt"


class Dialect(StrEnum):
    ES_MX = "es-MX"
    ES_CO = "es-CO"
    ES_AR = "es-AR"
    ES_NEUTRAL = "es-neutral"
    PT_BR = "pt-BR"
    PT_OTHER = "pt-other"


# ---------------------------------------------------------------------------
# Bank domain (mirrors source data values)
# ---------------------------------------------------------------------------


class Country(StrEnum):
    """Countries where LATAM Bank operates (customer residence)."""

    MX = "MX"
    CO = "CO"
    AR = "AR"


class Segment(StrEnum):
    PREMIUM = "Premium"
    PLUS = "Plus"
    BASIC = "Basic"
    STUDENT = "Student"


class CustomerStatus(StrEnum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    SUSPENDED = "Suspended"
    CLOSED = "Closed"


class ProductStatus(StrEnum):
    ACTIVE = "Active"
    BLOCKED = "Blocked"
    CLOSED = "Closed"
    SUSPENDED = "Suspended"


class CardType(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"


class TransactionType(StrEnum):
    DEPOSIT = "Deposit"
    WITHDRAWAL = "Withdrawal"
    TRANSFER = "Transfer"
    PAYMENT = "Payment"
    PURCHASE = "Purchase"
    ADJUSTMENT = "Adjustment"


class TransactionStatus(StrEnum):
    APPROVED = "Approved"
    DECLINED = "Declined"
    PENDING = "Pending"
    REVERSED = "Reversed"


class Channel(StrEnum):
    ATM = "ATM"
    BRANCH = "Branch"
    WEB = "Web"
    APP = "App"
    POS = "POS"
    TRANSFER = "Transfer"


class DataMode(StrEnum):
    SYNTHETIC = "synthetic"
    CURATED = "curated"


# ---------------------------------------------------------------------------
# Conversation and decisions
# ---------------------------------------------------------------------------


class Intent(StrEnum):
    DISPUTE_UNRECOGNIZED = "dispute_unrecognized"
    DISPUTE_DUPLICATE = "dispute_duplicate"
    DISPUTE_NOT_RECEIVED = "dispute_not_received"
    CARD_BLOCK = "card_block"
    DISPUTE_STATUS = "dispute_status"
    HUMAN_REQUEST = "human_request"
    OUT_OF_SCOPE = "out_of_scope"
    ATTACK = "attack"


DISPUTE_INTENTS: frozenset[Intent] = frozenset(
    {Intent.DISPUTE_UNRECOGNIZED, Intent.DISPUTE_DUPLICATE, Intent.DISPUTE_NOT_RECEIVED}
)


class DialogueAct(StrEnum):
    NEW_REQUEST = "new_request"
    PROVIDE_INFO = "provide_info"
    AFFIRM = "affirm"
    DENY = "deny"
    RECOGNIZE_CHARGE = "recognize_charge"
    NOT_RECOGNIZE_CHARGE = "not_recognize_charge"
    REQUEST_HUMAN = "request_human"
    OTHER = "other"


class DisputeReason(StrEnum):
    UNRECOGNIZED = "unrecognized"
    DUPLICATE = "duplicate"
    NOT_RECEIVED = "not_received"


class DisputeStatus(StrEnum):
    SUBMITTED = "submitted"
    ESCALATED_FOR_REVIEW = "escalated_for_review"


class ActionType(StrEnum):
    CREATE_DISPUTE = "create_dispute"
    BLOCK_CARD = "block_card"
    CREATE_HANDOFF = "create_handoff"


CONFIRMED_WRITE_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.CREATE_DISPUTE, ActionType.BLOCK_CARD}
)
"""Actions that change the customer's products and always need explicit confirmation."""


class DecisionType(StrEnum):
    PROCEED = "proceed"
    CLARIFY = "clarify"
    ABSTAIN = "abstain"
    ESCALATE = "escalate"
    INELIGIBLE = "ineligible"


class ConversationState(StrEnum):
    AUTH = "auth"
    UNDERSTAND = "understand"
    IDENTIFY_TXN = "identify_txn"
    RECOGNIZE = "recognize"
    CHECK_POLICY = "check_policy"
    CONFIRM = "confirm"
    ACT = "act"
    VERIFY = "verify"
    RESPOND = "respond"
    CLARIFY = "clarify"
    ABSTAIN = "abstain"
    ESCALATE = "escalate"
    DONE = "done"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Specialty(StrEnum):
    DISPUTES = "disputes"
    FRAUD = "fraud"
    CARDS = "cards"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# Tools and observability
# ---------------------------------------------------------------------------


class ToolName(StrEnum):
    LIST_CARDS = "list_cards"
    SEARCH_TRANSACTIONS = "search_transactions"
    GET_TRANSACTION = "get_transaction"
    GET_DISPUTE = "get_dispute"
    CREATE_DISPUTE = "create_dispute"
    BLOCK_CARD = "block_card"
    CREATE_HANDOFF = "create_handoff"


class ToolErrorCode(StrEnum):
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    CONFIRMATION_REQUIRED = "confirmation_required"
    TOOL_UNAVAILABLE = "tool_unavailable"
    SESSION_EXPIRED = "session_expired"
    INVALID_ARGUMENTS = "invalid_arguments"


class StepKind(StrEnum):
    """What an ExecutionRecord step did."""

    AUTHENTICATE = "authenticate"
    ROUTE = "route"
    INTERPRET = "interpret"
    TOOL_CALL = "tool_call"
    POLICY = "policy"
    CONFIRMATION = "confirmation"
    VERIFY = "verify"
    RENDER = "render"
    HANDOFF = "handoff"


class StepOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    FALLBACK = "fallback"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class Outcome(StrEnum):
    AUTOMATED_RESOLUTION = "automated_resolution"
    DEFLECTED_RECOGNIZED = "deflected_recognized"
    ABSTAINED = "abstained"
    ESCALATED = "escalated"
    DENIED = "denied"
    REAUTH_REQUIRED = "reauth_required"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class EvalCategory(StrEnum):
    NORMAL = "normal"
    RECOGNIZED_AFTER_EVIDENCE = "recognized_after_evidence"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    HUMAN_REQUIRED = "human_required"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    PROMPT_INJECTION = "prompt_injection"
    EXPIRED_SESSION = "expired_session"
    TOOL_FAILURE = "tool_failure"
    MISSING_OR_INCORRECT_DATA = "missing_or_incorrect_data"
    MULTILINGUAL_AMBIGUITY = "multilingual_ambiguity"


class EvalSplit(StrEnum):
    DEV = "dev"
    HELDOUT = "heldout"
    PILOT = "pilot"
    REDTEAM = "redteam"


class Provenance(StrEnum):
    TEAM_AUTHORED = "team_authored"
    CROSS_AUTHORED = "cross_authored"
    LLM_GENERATED_REVIEWED = "llm_generated_reviewed"
    PILOT = "pilot"
    REDTEAM_GENERATED = "redteam_generated"


class FaultInjection(StrEnum):
    LLM_TIMEOUT = "llm_timeout"
    LLM_MALFORMED_OUTPUT = "llm_malformed_output"
    LLM_UNAVAILABLE = "llm_unavailable"
    TOOL_UNAVAILABLE = "tool_unavailable"
    SESSION_EXPIRED = "session_expired"
    READBACK_MISMATCH = "readback_mismatch"


class ScriptedAnswer(StrEnum):
    """A yes or a no the evaluation's scripted user gives (`bankagent.eval.simulator`)."""

    RECOGNIZE_YES = "recognize_yes"
    RECOGNIZE_NO = "recognize_no"
    CONFIRM_YES = "confirm_yes"
    CONFIRM_NO = "confirm_no"
    BLOCK_YES = "block_yes"
    BLOCK_NO = "block_no"
    HUMAN_YES = "human_yes"
    HUMAN_NO = "human_no"


class UnsafeEvent(StrEnum):
    CROSS_CUSTOMER_DISCLOSURE = "cross_customer_disclosure"
    UNAUTHORIZED_ACTION = "unauthorized_action"
    ACTION_WITHOUT_CONFIRMATION = "action_without_confirmation"
    UNVERIFIED_CLAIM = "unverified_claim"
    ACTION_ON_ATTACK = "action_on_attack"
    PII_LEAK = "pii_leak"
    POLICY_VIOLATION = "policy_violation"
    MATERIALLY_INCORRECT_OUTCOME = "materially_incorrect_outcome"


class SystemVariant(StrEnum):
    BASELINE_LLM_ONLY = "baseline_llm_only"
    PROPOSED = "proposed"
