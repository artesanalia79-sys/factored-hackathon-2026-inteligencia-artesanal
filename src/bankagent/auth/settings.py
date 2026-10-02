"""Authentication settings. The signing secret comes from ``APP_SECRET_KEY`` and is never
printed: it is wrapped in ``Secret``, whose ``repr`` and ``str`` are masked.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

MIN_SECRET_BYTES = 32


class AuthConfigError(RuntimeError):
    """Authentication is misconfigured; the service must not start."""


class Secret:
    """A secret value that cannot leak through logs, reprs or f-strings."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> bytes:
        return self._value.encode("utf-8")

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class AuthSettings:
    secret: Secret
    session_ttl: timedelta = timedelta(minutes=15)
    otp_ttl: timedelta = timedelta(minutes=5)
    otp_max_attempts: int = 3
    # A customer with `lockout_failures` wrong codes inside `lockout_window` cannot start or
    # finish a login until the window passes: new challenges do not reset the count.
    lockout_failures: int = 5
    lockout_window: timedelta = timedelta(minutes=15)
    # Demo only: there is no SMS channel, so the mock OTP is returned to the caller.
    expose_mock_otp: bool = True
    persona_limit: int = 50
    issuer: str = field(default="bankagent")

    def __post_init__(self) -> None:
        if len(self.secret.reveal()) < MIN_SECRET_BYTES:
            raise AuthConfigError(
                f"APP_SECRET_KEY must be at least {MIN_SECRET_BYTES} bytes; "
                "run `uv run poe init-env` to generate one"
            )
        if self.otp_max_attempts < 1 or self.lockout_failures < self.otp_max_attempts:
            raise AuthConfigError("lockout_failures must be >= otp_max_attempts >= 1")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthSettings:
        env = os.environ if environ is None else environ
        value = env.get("APP_SECRET_KEY", "")
        if not value:
            raise AuthConfigError("APP_SECRET_KEY is not set; run `uv run poe init-env`")
        return cls(secret=Secret(value))
