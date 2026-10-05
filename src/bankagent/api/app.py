"""Authenticated chat HTTP surface over one agent instance per session, plus the built web UI."""

import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePath
from threading import RLock
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.routing import BaseRoute, Match, Mount
from starlette.types import Scope

from bankagent.api.console import ConsoleDeps, build_console_router
from bankagent.auth.http import SafeValidationRoute, build_auth_router, session_dependency
from bankagent.auth.service import AuthService
from bankagent.contracts.api import ChatTurnRequest, ChatTurnResponse
from bankagent.contracts.domain import Session, TransactionView
from bankagent.contracts.enums import Language
from bankagent.contracts.records import ExecutionRecord
from bankagent.orchestrator.agent import AgentTurnOutput

log = logging.getLogger("bankagent.api")

# Sent with every file of the web UI: it loads nothing from another origin, runs no inline
# script and cannot be framed. Only the UI gets them: FastAPI's /docs loads its assets from a CDN.
WEB_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
# Vite names every file under assets/ after a hash of its content, so a browser may keep it for
# good. Everything else, index.html first, is revalidated on each load: after a redeploy the page
# must name the new assets, not old ones that no longer exist (a blank page until a reload).
IMMUTABLE_ASSET = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


class TurnAgent(Protocol):
    def handle_turn(
        self, session: Session, text: str, /, preferred_language: Language | None = None
    ) -> AgentTurnOutput: ...


class WebFiles(StaticFiles):
    """The built UI (``web/dist``), served with ``WEB_SECURITY_HEADERS`` and its cache policy."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if path in {"compare", "compare/", "console", "console/"}:
            path = "index.html"
        response = await super().get_response(path, scope)
        response.headers.update(WEB_SECURITY_HEADERS)
        # ``path`` uses the OS separator; a 304 gets the same policy as the file it stands for.
        hashed = PurePath(path).parts[:1] == ("assets",)
        response.headers["Cache-Control"] = IMMUTABLE_ASSET if hashed else REVALIDATE
        return response


class WebMount(Mount):
    """The UI at ``/``, minus every path an API route owns with another method.

    A mount at ``/`` matches every path, and the router prefers any full match to a method
    mismatch, so ``GET /api/chat/turn`` would get the UI's 404. Stepping aside lets the route
    answer 405 with ``Allow``.
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        routes: Sequence[BaseRoute] = getattr(scope.get("router"), "routes", ())
        if any(route is not self and route.matches(scope)[0] == Match.PARTIAL for route in routes):
            return Match.NONE, {}
        return super().matches(scope)


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
    transaction_reader: Callable[[str], Sequence[TransactionView]] | None = None,
    record_sink: Callable[[Sequence[ExecutionRecord]], None] | None = None,
    readiness: Mapping[str, Callable[[], object]] | None = None,
    web_dist: Path | None = None,
    console: ConsoleDeps | None = None,
) -> FastAPI:
    """Build the API with an authenticated session and injected conversation factory.

    ``readiness`` maps a dependency name to a probe that raises when it is unreachable. `/ready`
    exists only when probes are given, so an app built without them cannot report itself ready.
    ``web_dist`` is the UI's production build. It is served at ``/`` when it holds an
    ``index.html``; every API route, `/health` and `/ready` keep precedence because they are
    registered before it, and a wrong method on them gets 405 (``WebMount``).
    ``console`` mounts the read-only human-agent console (T21) at ``/api/console/*`` when given;
    an app built without it never answers those paths at all, not even with 401.
    """
    app = FastAPI(title="Bank dispute intake")
    app.include_router(build_auth_router(auth))
    if console is not None:
        app.include_router(build_console_router(auth=auth, deps=console))
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
            output = agent.handle_turn(session, body.text, body.language)
            if record_sink is not None:
                record_sink(output.records)
            if output.ended:
                agents.pop(session.session_id, None)
        return ChatTurnResponse(
            reply_text=output.reply_text,
            ended=output.ended,
            claimed_actions=output.claimed_actions,
            language=output.language,
            confirmation=output.confirmation,
        )

    if transaction_reader is not None:

        @router.get("/transactions")
        def transactions(
            session: Annotated[Session, Depends(current_session)],
        ) -> list[TransactionView]:
            """Recent movements belonging to the authenticated customer only."""
            return list(transaction_reader(session.customer_id))

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

    # Last: a mount at / would answer every path registered after it.
    if web_dist is not None and (web_dist / "index.html").is_file():
        app.router.routes.append(
            WebMount("/", app=WebFiles(directory=web_dist, html=True), name="web")
        )

    return app
