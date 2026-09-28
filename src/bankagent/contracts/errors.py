"""Typed tool errors. The orchestrator maps each code to a state transition.

Tools raise ``NotFound`` both when a record does not exist and when it belongs to another
customer, so an attacker cannot learn whether an id exists (no existence disclosure).
``Unauthorized`` is reserved for session or role failures.
"""

from __future__ import annotations

from typing import ClassVar

from bankagent.contracts.base import Contract
from bankagent.contracts.enums import ToolErrorCode


class ToolErrorInfo(Contract):
    """Serializable form of a tool error for records, APIs and traces."""

    code: ToolErrorCode
    message: str
    retryable: bool


class ToolError(Exception):
    code: ClassVar[ToolErrorCode]
    retryable: ClassVar[bool] = False

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code.value)
        self.message = message or self.code.value

    def to_info(self) -> ToolErrorInfo:
        return ToolErrorInfo(code=self.code, message=self.message, retryable=self.retryable)


class Unauthorized(ToolError):
    code = ToolErrorCode.UNAUTHORIZED


class NotFound(ToolError):
    code = ToolErrorCode.NOT_FOUND


class ConfirmationRequired(ToolError):
    code = ToolErrorCode.CONFIRMATION_REQUIRED


class ToolUnavailable(ToolError):
    code = ToolErrorCode.TOOL_UNAVAILABLE
    retryable = True


class SessionExpired(ToolError):
    code = ToolErrorCode.SESSION_EXPIRED


class InvalidArguments(ToolError):
    code = ToolErrorCode.INVALID_ARGUMENTS


ERRORS_BY_CODE: dict[ToolErrorCode, type[ToolError]] = {
    cls.code: cls
    for cls in (
        Unauthorized,
        NotFound,
        ConfirmationRequired,
        ToolUnavailable,
        SessionExpired,
        InvalidArguments,
    )
}
