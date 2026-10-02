"""HTTP surface of authentication: a FastAPI router and the session dependency.

``build_auth_router(service)`` is mounted by the app (T13); ``session_dependency(service)``
gives every protected endpoint its server-side ``Session`` from the ``Authorization: Bearer``
header. Request models forbid unknown fields, so a body carrying ``customer_id`` is rejected
with 422 before it reaches any logic. No response ever contains ``customer_id``.

Validation errors go through ``SafeValidationRoute``: FastAPI's default handler echoes the
rejected input back, which would repeat a submitted code or id and fails with a 500 on input
that cannot be encoded (a lone surrogate). Ours returns only where and what kind of error.

No ``from __future__ import annotations`` here: FastAPI must resolve the dependencies that
are closed over inside ``build_auth_router`` when it reads the endpoint signatures.
"""

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from bankagent.auth.service import AuthService, InvalidCredentials, TooManyAttempts
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import Language
from bankagent.contracts.errors import SessionExpired, Unauthorized

_bearer = HTTPBearer(auto_error=False)


class SafeValidationRoute(APIRoute):
    """Route whose 422 responses never echo the client's input. Reusable by other routers."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                detail = [
                    {"loc": [str(part) for part in error["loc"]], "type": error["type"]}
                    for error in exc.errors()
                ]
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": detail}
                )

        return safe_handler


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LoginRequest(_Request):
    persona_id: str = Field(min_length=1, max_length=64)


class VerifyRequest(_Request):
    challenge_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=12)


class PersonaResponse(BaseModel):
    persona_id: str
    first_name: str
    country: str
    language: Language | None


class LoginResponse(BaseModel):
    challenge_id: str
    expires_at: datetime
    delivery: str = "mock"
    mock_otp: str | None = None


class VerifyResponse(BaseModel):
    token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a credential
    expires_at: datetime
    first_name: str
    language: Language | None


class SessionResponse(BaseModel):
    expires_at: datetime
    language: Language | None


def _unauthorized(code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": code},
        headers={"WWW-Authenticate": "Bearer"},
    )


def session_dependency(service: AuthService) -> Callable[..., Session]:
    """FastAPI dependency: the authenticated server-side session, or 401."""

    def current_session(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Session:
        if credentials is None:
            raise _unauthorized("unauthorized")
        try:
            return service.authenticate(credentials.credentials)
        except SessionExpired:
            raise _unauthorized("session_expired") from None
        except Unauthorized:
            raise _unauthorized("unauthorized") from None

    return current_session


def build_auth_router(service: AuthService) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"], route_class=SafeValidationRoute)
    current_session = session_dependency(service)

    @router.get("/personas")
    def personas() -> list[PersonaResponse]:
        return [
            PersonaResponse(
                persona_id=p.persona_id,
                first_name=p.first_name,
                country=p.country,
                language=p.language,
            )
            for p in service.personas()
        ]

    @router.post("/login")
    def login(body: LoginRequest) -> LoginResponse:
        try:
            issued = service.start_login(body.persona_id)
        except TooManyAttempts as exc:
            raise _too_many(exc) from None
        except InvalidCredentials:
            raise _unauthorized(InvalidCredentials.code) from None
        return LoginResponse(
            challenge_id=issued.challenge_id, expires_at=issued.expires_at, mock_otp=issued.mock_otp
        )

    @router.post("/verify")
    def verify(body: VerifyRequest) -> VerifyResponse:
        try:
            result = service.verify_otp(body.challenge_id, body.code)
        except TooManyAttempts as exc:
            raise _too_many(exc) from None
        except InvalidCredentials:
            raise _unauthorized(InvalidCredentials.code) from None
        return VerifyResponse(
            token=result.token,
            expires_at=result.expires_at,
            first_name=result.first_name,
            language=result.language,
        )

    @router.get("/session")
    def session(current: Annotated[Session, Depends(current_session)]) -> SessionResponse:
        return SessionResponse(expires_at=current.expires_at, language=current.language)

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Response:
        if credentials is not None:
            service.logout(credentials.credentials)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def _too_many(exc: TooManyAttempts) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"error": exc.code, "retry_after_seconds": exc.retry_after_seconds},
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )
