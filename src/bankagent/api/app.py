"""Authenticated chat HTTP surface over one agent instance per session."""

import logging
from collections.abc import Callable, Mapping, Sequence
from threading import RLock
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from bankagent.auth.http import SafeValidationRoute, build_auth_router, session_dependency
from bankagent.auth.service import AuthService
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import ActionType
from bankagent.contracts.records import ExecutionRecord
from bankagent.orchestrator.agent import AgentTurnOutput

log = logging.getLogger("bankagent.api")


class TurnAgent(Protocol):
    def handle_turn(self, session: Session, text: str, /) -> AgentTurnOutput: ...


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1, max_length=2000)


class ChatTurnResponse(BaseModel):
    reply_text: str
    ended: bool
    claimed_actions: tuple[ActionType, ...]


def _answers(name: str, probe: Callable[[], object]) -> bool:
    try:
        probe()
    except Exception as exc:
        # The type only: an exception message can carry a file path or a row.
        log.warning("readiness_failed dependency=%s error=%s", name, type(exc).__name__)
        return False
    return True


def create_app(
    *,
    auth: AuthService,
    agent_factory: Callable[[], TurnAgent],
    record_sink: Callable[[Sequence[ExecutionRecord]], None] | None = None,
    readiness: Mapping[str, Callable[[], object]] | None = None,
) -> FastAPI:
    """Build the API with an authenticated session and injected conversation factory.

    ``readiness`` maps a dependency name to a probe that raises when it is unreachable. `/ready`
    exists only when probes are given, so an app built without them cannot report itself ready.
    """
    app = FastAPI(title="Bank dispute intake")
    app.include_router(build_auth_router(auth))
    router = APIRouter(prefix="/api/chat", tags=["chat"], route_class=SafeValidationRoute)
    current_session = session_dependency(auth)
    agents: dict[str, TurnAgent] = {}
    lock = RLock()

    @router.post("/turn")
    def turn(
        body: ChatTurnRequest,
        session: Annotated[Session, Depends(current_session)],
    ) -> ChatTurnResponse:
        # A single lock makes concurrent turns for one session deterministic.
        with lock:
            agent = agents.get(session.session_id)
            if agent is None:
                agent = agent_factory()
                agents[session.session_id] = agent
            output = agent.handle_turn(session, body.text)
            if record_sink is not None:
                record_sink(output.records)
            if output.ended:
                agents.pop(session.session_id, None)
        return ChatTurnResponse(
            reply_text=output.reply_text,
            ended=output.ended,
            claimed_actions=output.claimed_actions,
        )

    app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if readiness:
        probes = dict(readiness)

        @app.get("/ready")
        def ready() -> JSONResponse:
            # Names only, never a path or an exception text: this endpoint is public.
            failed = [name for name, probe in probes.items() if not _answers(name, probe)]
            if failed:
                return JSONResponse(
                    status_code=503, content={"status": "not_ready", "failed": failed}
                )
            return JSONResponse(content={"status": "ready"})

    return app
