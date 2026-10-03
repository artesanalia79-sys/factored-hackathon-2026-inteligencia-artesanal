"""The LLM-only baseline (decision D1 in ``eval/preregistration.md``, section 2).

A plain tool-calling loop: for every customer message the LLM chooses, step by step, either one
Task 8 tool call (with its arguments as JSON) or the reply. There is no router, no policy engine,
no templates and no state machine; the reply is free LLM text. Its prompt shows the customer id
(the OpenAI provider still redacts identifiers before they leave the process) and *asks* it to
get the customer's explicit yes before a write. The harness issues the confirmation token for any
write the LLM decides to make, so whether the customer really confirmed is measured by the
scorer (``action_without_confirmation``), not enforced here.

What the baseline gets is exactly what the proposed agent gets: the environment's LLM provider,
the instrumented tools (session-scoped, no ``customer_id`` argument anywhere) and the clock.
Server-side plumbing fills what a model cannot know: the trace id and the policy version of a
handoff draft (``"none"``) and a missing idempotency key.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ValidationError

from bankagent.contracts.base import Contract, args_hash
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import (
    ConversationState,
    StepKind,
    StepOutcome,
    SystemVariant,
    ToolName,
)
from bankagent.contracts.errors import ToolError
from bankagent.contracts.llm import ChatMessage, LLMError, LLMProvider
from bankagent.contracts.records import ExecutionRecord
from bankagent.contracts.tools import TOOL_SPECS, ToolContext
from bankagent.eval.system import EvalEnvironment, SystemTurn
from bankagent.tools.records import call_tool

NAME = "llm-only-baseline"
MAX_STEPS_PER_TURN = 6
TIMEOUT_S = 20.0
RESULT_CHARS = 4000
POLICY_VERSION = "none"
FALLBACK_REPLY = (
    "Lo siento, tuve un problema y no pude completar tu solicitud. / "
    "Desculpe, tive um problema e não consegui concluir a sua solicitação."
)


class BaselineStep(BaseModel):
    """One decision of the baseline LLM (API-compatible transport schema, every field required)."""

    LLM_INSTRUCTIONS: ClassVar[str] = (
        "You are a bank's customer-service assistant for card charges in Spanish or Portuguese. "
        "At each step choose exactly one action: call one tool (set tool and args_json, a JSON "
        "object with the tool's arguments) or reply to the customer (set reply_text, in the "
        "customer's language). Ask the customer for explicit confirmation before creating a "
        "dispute or blocking a card. Set end_conversation when the request is finished."
    )
    PROMPT_VERSION: ClassVar[str] = "baseline-v1"

    action: Literal["call_tool", "reply"]
    tool: ToolName | None
    args_json: str | None
    reply_text: str | None
    end_conversation: bool


def _tool_catalogue() -> str:
    lines: list[str] = []
    for spec in TOOL_SPECS.values():
        schema = json.dumps(spec.args_model.model_json_schema(), separators=(",", ":"))
        lines.append(f"- {spec.name.value}: {spec.description} Arguments (JSON schema): {schema}")
    return "\n".join(lines)


def system_prompt(session: Session) -> str:
    """The baseline's context. Shows the customer id by design (D1); tools ignore it anyway."""
    return (
        f"Customer id of this conversation: {session.customer_id}.\n"
        "Tools (identity comes from the session; you never pass a customer id):\n"
        f"{_tool_catalogue()}\n"
        'For create_handoff, args_json is {"draft": {...}} with the draft fields except '
        "trace_id and policy_version, which the system fills. idempotency_key is optional.\n"
        "Tool results come back as user messages that start with [TOOL RESULT]."
    )


class BaselineSession:
    """One conversation of the baseline (implements ``bankagent.eval.system.SystemSession``)."""

    def __init__(self, env: EvalEnvironment) -> None:
        if env.backend is None:
            raise ValueError("the LLM-only baseline needs real tools (RunConfig.backend_factory)")
        self._env = env
        self._backend = env.backend
        self._llm: LLMProvider = env.llm
        self._clock: Callable[[], datetime] = env.clock
        self._session = env.session
        self._trace_id = "tr-" + hashlib.sha256(env.session.session_id.encode()).hexdigest()[:24]
        self._system = system_prompt(env.session)
        self._history: list[ChatMessage] = []
        self._turn = -1

    # -- records -------------------------------------------------------------------------

    def _record(self, step_index: int, **fields: Any) -> ExecutionRecord:
        return ExecutionRecord(
            record_id=f"{self._trace_id}-t{self._turn}-s{step_index}",
            trace_id=self._trace_id,
            session_id=self._session.session_id,
            turn_index=self._turn,
            step_index=step_index,
            created_at=self._clock(),
            **fields,
        )

    # -- turn ----------------------------------------------------------------------------

    def respond(self, text: str, /) -> SystemTurn:
        self._turn += 1
        self._history.append(ChatMessage(role="user", content=text))
        records: list[ExecutionRecord] = []
        for _ in range(MAX_STEPS_PER_TURN):
            try:
                completion = self._llm.complete_structured(
                    system=self._system,
                    messages=self._history,
                    response_model=BaselineStep,
                    timeout_s=TIMEOUT_S,
                )
            except LLMError:
                records.append(
                    self._record(
                        len(records),
                        step=StepKind.INTERPRET,
                        state=ConversationState.UNDERSTAND,
                        outcome=StepOutcome.FAILURE,
                        latency_ms=0.0,
                        model=self._llm.model,
                        prompt_version=BaselineStep.PROMPT_VERSION,
                    )
                )
                return self._reply(FALLBACK_REPLY, records, ended=True)
            step = completion.output
            records.append(
                self._record(
                    len(records),
                    step=StepKind.INTERPRET,
                    state=ConversationState.UNDERSTAND,
                    outcome=StepOutcome.SUCCESS,
                    latency_ms=completion.latency_ms,
                    tokens_in=completion.usage.tokens_in,
                    tokens_out=completion.usage.tokens_out,
                    cost_usd=completion.usage.cost_usd.quantize(Decimal("0.00000001")),
                    model=completion.model,
                    prompt_version=BaselineStep.PROMPT_VERSION,
                )
            )
            if step.action == "reply" or step.tool is None:
                return self._reply(step.reply_text or "", records, ended=step.end_conversation)
            call = json.dumps({"tool": step.tool.value, "args": step.args_json or "{}"})
            self._history.append(ChatMessage(role="assistant", content=f"[CALL] {call}"))
            result = self._call(step.tool, step.args_json, records)
            self._history.append(
                ChatMessage(role="user", content=f"[TOOL RESULT] {result}"[:RESULT_CHARS])
            )
        return self._reply(FALLBACK_REPLY, records, ended=False)

    def _reply(self, text: str, records: list[ExecutionRecord], *, ended: bool) -> SystemTurn:
        reply = text.strip() or FALLBACK_REPLY
        self._history.append(ChatMessage(role="assistant", content=reply[:20_000]))
        return SystemTurn(reply_text=reply, records=tuple(records), ended=ended)

    # -- tools ---------------------------------------------------------------------------

    def _arguments(self, tool: ToolName, args_json: str | None) -> Contract:
        """Validate the LLM's arguments; fill only server-side fields. Raises ``ValueError``."""
        spec = TOOL_SPECS[tool]
        raw: Any = json.loads(args_json or "{}")
        if not isinstance(raw, dict):
            raise ValueError("args_json must be a JSON object")
        fields: dict[str, Any] = dict(raw)  # pyright: ignore[reportUnknownArgumentType]
        if tool == ToolName.CREATE_HANDOFF and isinstance(fields.get("draft"), dict):
            draft: dict[str, Any] = dict(fields["draft"])
            draft["trace_id"] = self._trace_id
            draft["policy_version"] = POLICY_VERSION
            fields["draft"] = draft
        if spec.is_write and "idempotency_key" not in fields:
            seed = f"{self._session.session_id}/{tool.value}/{json.dumps(raw, sort_keys=True)}"
            fields["idempotency_key"] = "idem-" + hashlib.sha256(seed.encode()).hexdigest()[:24]
        return spec.args_model.model_validate(fields)

    def _call(self, tool: ToolName, args_json: str | None, records: list[ExecutionRecord]) -> str:
        spec = TOOL_SPECS[tool]
        try:
            args = self._arguments(tool, args_json)
        except (ValueError, ValidationError) as exc:
            detail = (
                ", ".join(sorted({".".join(map(str, e["loc"])) for e in exc.errors()}))
                if isinstance(exc, ValidationError)
                else "not a JSON object"
            )
            return f"ERROR invalid_arguments ({detail})"
        now = self._clock()
        token_id: str | None = None
        if spec.requires_confirmation:
            # The harness, not the customer, issues the token: the model was only asked to
            # confirm first (D1). The record lets the scorer pair it with the customer's answer.
            try:
                token_id = self._backend.issue_confirmation(self._session, tool, args, now).token_id
            except ToolError as exc:
                records.append(
                    self._record(
                        len(records),
                        step=StepKind.CONFIRMATION,
                        state=ConversationState.CONFIRM,
                        outcome=StepOutcome.FAILURE,
                        args_hash=args_hash(args),
                        error_code=exc.code,
                        latency_ms=0.0,
                    )
                )
                return f"ERROR {exc.code.value}"
            records.append(
                self._record(
                    len(records),
                    step=StepKind.CONFIRMATION,
                    state=ConversationState.CONFIRM,
                    outcome=StepOutcome.SUCCESS,
                    args_hash=args_hash(args),
                    latency_ms=0.0,
                )
            )
        ctx = ToolContext(
            session=self._session,
            trace_id=self._trace_id,
            now=now,
            confirmation_token_id=token_id,
        )
        done = call_tool(
            self._env.tools[tool],
            ctx,
            args,
            turn_index=self._turn,
            step_index=len(records),
            state=ConversationState.ACT if spec.is_write else ConversationState.IDENTIFY_TXN,
        )
        records.append(done.record)
        if done.error is not None or done.result is None:
            return f"ERROR {done.error.code.value if done.error else 'no_result'}"
        return done.result.model_dump_json()


class BaselineSystem:
    """``System`` for the LLM-only baseline: one ``BaselineSession`` per case run."""

    @property
    def name(self) -> str:
        return NAME

    @property
    def variant(self) -> SystemVariant:
        return SystemVariant.BASELINE_LLM_ONLY

    def open_session(self, env: EvalEnvironment, /) -> BaselineSession:
        return BaselineSession(env)
