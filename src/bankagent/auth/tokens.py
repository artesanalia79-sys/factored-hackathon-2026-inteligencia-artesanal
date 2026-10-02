"""Signed session tokens (JWT, HS256).

The token is only a signed pointer to a server-side ``Session``: it carries the session id and
its validity window, never ``customer_id`` or any profile data. Whoever decodes it learns
nothing about the customer, and the server still looks the session up before trusting it.

Time comes from the caller (the injected clock), not from the JWT library, so expiry is
testable and consistent with ``Session.is_active``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import jwt

from bankagent.auth.settings import Secret
from bankagent.contracts.domain import Session
from bankagent.contracts.errors import SessionExpired, Unauthorized

ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ("iss", "sid", "iat", "exp")


class SessionTokens:
    def __init__(self, secret: Secret, issuer: str) -> None:
        self._secret = secret
        self._issuer = issuer

    def issue(self, session: Session) -> str:
        claims = {
            "iss": self._issuer,
            "sid": session.session_id,
            "iat": int(session.issued_at.timestamp()),
            "exp": int(session.expires_at.timestamp()),
        }
        return jwt.encode(claims, self._secret.reveal(), algorithm=ALGORITHM)

    def session_id(self, token: str, now: datetime) -> str:
        """The session id of a valid token.

        Raises ``Unauthorized`` for a malformed, tampered or wrongly signed token (the message
        never says which) and ``SessionExpired`` when the signature is good but the window passed.
        """
        try:
            claims = jwt.decode(
                token,
                self._secret.reveal(),
                algorithms=[ALGORITHM],
                issuer=self._issuer,
                options={
                    "require": list(_REQUIRED_CLAIMS),
                    # Checked below against the injected clock.
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError:
            raise Unauthorized("invalid session token") from None
        sid, issued, expires = claims["sid"], claims["iat"], claims["exp"]
        if not isinstance(sid, str) or not sid or not _is_number(issued) or not _is_number(expires):
            raise Unauthorized("invalid session token")
        moment = now.astimezone(UTC).timestamp()
        if moment < issued:
            raise Unauthorized("invalid session token")
        if moment >= expires:
            raise SessionExpired("session expired")
        return sid


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
