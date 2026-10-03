"""Authentication settings. The signing secret comes from ``APP_SECRET_KEY`` and is never
printed: it is wrapped in ``Secret``, whose ``repr`` and ``str`` are masked.

Environment (see ``.env.example``):

- ``APP_SECRET_KEY``: at least 32 bytes and not obviously low-entropy. The supported way to
  create one is ``uv run poe init-env``.
- ``AUTH_EXPOSE_MOCK_OTP``: ``true`` returns the mock OTP in the login response (there is no SMS
  channel in the demo). Off unless set. It is refused unless the data is synthetic
  (``DATA_MODE`` and the serving DB's own metadata): with the public persona list it would
  let anyone log in as a customer from organizer data.
- ``DEMO_ACCESS_CODE``: a shared code every login must present. Unset means no gate. It keeps
  anonymous traffic off a public demo, where each chat turn can be a paid model call.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

MIN_SECRET_BYTES = 32
MIN_SECRET_DISTINCT = 12
MIN_ACCESS_CODE_CHARS = 8
SYNTHETIC = "synthetic"
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


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
    # Demo only: there is no SMS channel, so the mock OTP can be returned to the caller.
    # Off by default; never allowed on curated (organizer-derived) data.
    expose_mock_otp: bool = False
    # Demo only: a code shared with the people invited to try the service. None = no gate.
    access_code: Secret | None = None
    data_mode: str = SYNTHETIC
    persona_limit: int = 50
    issuer: str = field(default="bankagent")

    def __post_init__(self) -> None:
        if self.access_code is not None:
            code = self.access_code.reveal()
            if len(code) < MIN_ACCESS_CODE_CHARS or not code.isascii():
                raise AuthConfigError(
                    f"DEMO_ACCESS_CODE must be at least {MIN_ACCESS_CODE_CHARS} ASCII characters"
                )
        value = self.secret.reveal()
        if len(value) < MIN_SECRET_BYTES:
            raise AuthConfigError(
                f"APP_SECRET_KEY must be at least {MIN_SECRET_BYTES} bytes; "
                "run `uv run poe init-env` to generate one"
            )
        if len(set(value)) < MIN_SECRET_DISTINCT:
            raise AuthConfigError(
                "APP_SECRET_KEY looks low-entropy (too few distinct characters); "
                "run `uv run poe init-env` to generate one"
            )
        self.require_safe_for(self.data_mode)
        if self.otp_max_attempts < 1 or self.lockout_failures < self.otp_max_attempts:
            raise AuthConfigError("lockout_failures must be >= otp_max_attempts >= 1")

    def require_safe_for(self, data_mode: str) -> None:
        """Allow the exposed mock OTP only on synthetic data (fail closed on anything else).

        Called with the declared ``DATA_MODE`` and again with what the serving DB records.
        """
        if self.expose_mock_otp and data_mode != SYNTHETIC:
            raise AuthConfigError(
                "AUTH_EXPOSE_MOCK_OTP is only allowed with synthetic data "
                f"(data mode is '{data_mode}'): anyone could log in as an organizer-derived "
                "customer. Unset it or use the synthetic fixture bank."
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthSettings:
        env = os.environ if environ is None else environ
        value = env.get("APP_SECRET_KEY", "")
        if not value:
            raise AuthConfigError("APP_SECRET_KEY is not set; run `uv run poe init-env`")
        access_code = env.get("DEMO_ACCESS_CODE", "").strip()
        return cls(
            secret=Secret(value),
            expose_mock_otp=_flag(env, "AUTH_EXPOSE_MOCK_OTP"),
            access_code=Secret(access_code) if access_code else None,
            data_mode=env.get("DATA_MODE", "").strip().lower() or SYNTHETIC,
        )


def _flag(env: Mapping[str, str], key: str) -> bool:
    raw = env.get(key, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise AuthConfigError(f"{key} must be true or false")
