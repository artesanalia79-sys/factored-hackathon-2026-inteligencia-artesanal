"""Issue the single-use confirmation token a confirmed write needs.

The orchestrator calls this at CONFIRM, once the customer has said yes to one exact action.
The token is bound to the session, the action and the hash of the exact arguments; the write
tool spends it in the same transaction as the write (see `bankagent.tools.writes`).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from bankagent.contracts.base import Contract, args_hash
from bankagent.contracts.domain import ConfirmationToken, Session
from bankagent.contracts.enums import ToolName
from bankagent.contracts.errors import InvalidArguments, SessionExpired
from bankagent.contracts.tools import TOOL_SPECS
from bankagent.store.ops import OpsStore

# Long enough to issue the token and run the write in the turn where the customer confirms,
# short enough that a token left behind is useless a few minutes later.
DEFAULT_TOKEN_TTL = timedelta(minutes=5)


def issue_confirmation_token(
    store: OpsStore,
    *,
    session: Session,
    tool: ToolName,
    args: Contract,
    now: datetime,
    ttl: timedelta = DEFAULT_TOKEN_TTL,
) -> ConfirmationToken:
    """Store and return a token for exactly ``tool(args)`` in this session.

    ``store`` must be the store the tools were built on, or another ``OpsStore`` on the same
    file; a token saved anywhere else does not exist for the write tool. Pass its ``token_id``
    as ``ToolContext.confirmation_token_id``; it never needs to leave the server, and it never
    outlives the session.

    Raises ``SessionExpired`` for an inactive session and ``InvalidArguments`` for a tool that
    takes no confirmation or for arguments of another tool.
    """
    spec = TOOL_SPECS[tool]
    if not session.is_active(now):
        raise SessionExpired("session expired")
    if not spec.requires_confirmation or spec.action is None:
        raise InvalidArguments("this tool takes no confirmation token")
    if not isinstance(args, spec.args_model):
        raise InvalidArguments("arguments do not match the tool")
    if ttl <= timedelta(0):
        raise InvalidArguments("the token lifetime must be positive")
    token = ConfirmationToken(
        token_id=secrets.token_urlsafe(24),
        session_id=session.session_id,
        action=spec.action,
        args_hash=args_hash(args),
        issued_at=now,
        expires_at=min(now + ttl, session.expires_at),
    )
    store.save_confirmation_token(token)
    return token
