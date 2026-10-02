"""What every tool shares: the session check, the argument check and error translation.

A tool never logs and never puts a value into an error: messages are fixed strings and the
cause chained to a ``ToolUnavailable`` is a `ToolFailure` that names what failed, never the
original exception (a database or validation error can quote a row or a parameter). So an id,
an amount or a customer text cannot reach a log through a traceback. Failures of either
database, and stored records that no longer fit their contract, become ``ToolUnavailable``
(retryable); nothing is swallowed.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

import duckdb
from pydantic import ValidationError

from bankagent.contracts.base import Contract, args_hash
from bankagent.contracts.enums import ToolName
from bankagent.contracts.errors import (
    ConfirmationRequired,
    InvalidArguments,
    SessionExpired,
    ToolError,
    ToolUnavailable,
)
from bankagent.contracts.tools import TOOL_SPECS, ToolContext, ToolSpec
from bankagent.store.ops import OpsStore
from bankagent.store.serving import ServingDataError, ServingDB
from bankagent.store.sqlite import OpsStoreError

INFRASTRUCTURE_ERRORS: tuple[type[Exception], ...] = (
    duckdb.Error,
    sqlite3.Error,
    OpsStoreError,
    ServingDataError,
)


class ToolFailure(RuntimeError):
    """Sanitized cause of a ``ToolUnavailable``: the kind of failure, without any value."""


def _sanitized(error: Exception) -> ToolFailure:
    kind = f"{type(error).__module__}.{type(error).__qualname__}"
    if isinstance(error, ValidationError):
        fields = sorted({".".join(str(part) for part in e["loc"]) for e in error.errors()})
        return ToolFailure(f"{kind}: {error.title} ({', '.join(fields)})")
    if isinstance(error, ServingDataError):  # already names fields only
        return ToolFailure(f"{kind}: {error}")
    return ToolFailure(kind)


def new_id(prefix: str) -> str:
    """Random record id, e.g. ``DSP-3F9A61C2D4E8B705``."""
    return f"{prefix}-{secrets.token_hex(8).upper()}"


@dataclass(frozen=True, slots=True)
class ToolDeps:
    """What the tools are built on. One instance is shared by the seven tools."""

    serving: ServingDB
    store: OpsStore
    new_id: Callable[[str], str]
    # Confirmed writes are refused without a policy decision in the context. Only the
    # evaluation harness turns this off (its LLM-only baseline has no policy by design).
    require_policy: bool


class BaseTool[ArgsT: Contract, ResultT: Contract]:
    """Implements the ``Tool`` protocol; subclasses set ``name`` and write ``_run``."""

    name: ClassVar[ToolName]

    def __init__(self, deps: ToolDeps) -> None:
        self._deps = deps

    @property
    def spec(self) -> ToolSpec:
        return TOOL_SPECS[self.name]

    def run(self, ctx: ToolContext, args: ArgsT, /) -> ResultT:
        if not ctx.session.is_active(ctx.now):
            raise SessionExpired("session expired")
        if not isinstance(args, self.spec.args_model):
            raise InvalidArguments("arguments do not match the tool")
        try:
            return self._run(ctx, args)
        except ToolError:
            raise
        except (*INFRASTRUCTURE_ERRORS, ValidationError) as exc:
            failure = _sanitized(exc)
        # Raised outside the handler so the original exception is not kept as context.
        raise ToolUnavailable(f"{self.name.value} is temporarily unavailable") from failure

    def _run(self, ctx: ToolContext, args: ArgsT) -> ResultT:
        raise NotImplementedError

    # -- confirmed writes ----------------------------------------------------

    def _require_allowed_by_policy(self, ctx: ToolContext) -> None:
        """Second lock behind the orchestrator: the decision in the context must allow it."""
        if ctx.policy is None:
            if self._deps.require_policy:
                raise InvalidArguments("a policy decision is required for this action")
            return
        if self.spec.action not in ctx.policy.allowed_actions:
            raise InvalidArguments("the policy decision does not allow this action")

    def _require_token(self, ctx: ToolContext) -> str:
        if ctx.confirmation_token_id is None:
            raise ConfirmationRequired("explicit confirmation is required")
        return ctx.confirmation_token_id

    def _consume_token(self, ctx: ToolContext, args: ArgsT, token_id: str) -> None:
        """Spend the token for exactly this call. Run it inside the write's transaction.

        A foreign, mismatched, expired or already used token is the same refusal as none.
        """
        action = self.spec.action
        if action is None or not self._deps.store.consume_confirmation_token(
            token_id,
            session_id=ctx.session.session_id,
            action=action,
            args_hash=args_hash(args),
            now=ctx.now,
        ):
            raise ConfirmationRequired("explicit confirmation is required")

    @staticmethod
    def _read_back[T](read: Callable[[], T | None], expected: T) -> bool:
        """True only when the stored record equals what the tool reports.

        The write is already committed, so a failing read is "not verified", never an error.
        """
        try:
            return read() == expected
        except (*INFRASTRUCTURE_ERRORS, ValidationError):
            return False
