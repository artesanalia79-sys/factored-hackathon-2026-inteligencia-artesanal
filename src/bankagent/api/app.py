"""Authenticated chat HTTP surface over one agent instance per session."""

from collections.abc import Callable, Sequence
from threading import RLock
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from bankagent.auth.http import SafeValidationRoute, build_auth_router, session_dependency
from bankagent.auth.service import AuthService
from bankagent.contracts.domain import Session
from bankagent.contracts.enums import ActionType
from bankagent.contracts.records import ExecutionRecord
from bankagent.orchestrator.agent import AgentTurnOutput


class TurnAgent(Protocol):
    def handle_turn(self, session: Session, text: str, /) -> AgentTurnOutput: ...


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1, max_length=2000)


class ChatTurnResponse(BaseModel):
    reply_text: str
    ended: bool
    claimed_actions: tuple[ActionType, ...]


def create_app(
    *,
    auth: AuthService,
    agent_factory: Callable[[], TurnAgent],
    record_sink: Callable[[Sequence[ExecutionRecord]], None] | None = None,
) -> FastAPI:
    """Build the API with an authenticated session and injected conversation factory."""
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

    return app
