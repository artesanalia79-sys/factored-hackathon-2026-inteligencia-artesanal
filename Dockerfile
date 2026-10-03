# Dispute intake API (Task 15). Operations: docs/operations.md.
#
# One process, one worker: conversations live in process memory (docs/limitations.md), so a
# second worker or instance would break them. Never add workers here.
#
# The image keeps the layout of a source checkout under /app (src/, policy/, config/,
# tests/fixtures/bank) and imports the package from src/: the code finds the policy, the
# pricing table and the fixture YAML relative to the repository root, so a wheel installed
# into site-packages would not find them.
#
# Render passes every service environment variable to this build as a build argument. The
# only ARG declared here is the base image, so no secret can end up in a layer.

ARG PYTHON_BASE_IMAGE=python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

FROM ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 AS uv

# ---------------------------------------------------------------------------
# build: locked dependencies, bytecode, and the synthetic fixture bank
# ---------------------------------------------------------------------------
FROM ${PYTHON_BASE_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1
WORKDIR /app

# Dependencies first, so a code change does not reinstall them. Only what runs: the main
# dependencies, `api`, and `llm` (the wiring imports both providers). No `dev`, no `data`.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-default-groups --group api --group llm --no-install-project

COPY src ./src
COPY policy ./policy
COPY config ./config
COPY tests/fixtures/bank ./tests/fixtures/bank
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src

# The only data in the image: the fixture bank, checked against its committed hash and the
# serving contract, then built at the default path (data/fixtures/). The runtime user cannot
# write bytecode next to the code, so it is compiled here.
RUN python -m bankagent.fixtures.builder --check \
 && python -m bankagent.fixtures.builder \
 && python -m compileall -q src

# TODO(T14, Jacobo): add the web build stage (a pinned node image running `npm ci` and
# `npm run build` in web/) and copy web/dist into the runtime stage; allow web/ in
# .dockerignore. Steps in docs/operations.md, "Adding the web UI to the image".

# ---------------------------------------------------------------------------
# runtime: no uv, no build tools, non-root
# ---------------------------------------------------------------------------
FROM ${PYTHON_BASE_IMAGE} AS runtime

# A fixed non-root uid. The ops store directory is the only path it can write.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin app \
 && install -d -o app -g app -m 0700 /var/lib/bankagent

WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY --from=build /app/src ./src
COPY --from=build /app/policy ./policy
COPY --from=build /app/config ./config
COPY --from=build /app/tests/fixtures/bank ./tests/fixtures/bank
COPY --from=build /app/data/fixtures ./data/fixtures

# Not secrets, and fixed on purpose: this image only ever serves the synthetic bank. On a
# platform with an ephemeral disk (Render free) the ops store is new on every start, which is
# how the demo is reset. APP_SECRET_KEY and the rest come from the platform.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_MODE=synthetic \
    SERVING_DB_PATH=/app/data/fixtures/bank_fixture.duckdb \
    OPS_DB_PATH=/var/lib/bankagent/ops.sqlite \
    PORT=8000

USER 10001:10001
EXPOSE 8000

# A shell only to read $PORT (Render sets it); `exec` leaves uvicorn as PID 1 so SIGTERM
# stops it cleanly.
CMD ["sh", "-c", "exec uvicorn bankagent.api.wiring:create_default_app --factory --host 0.0.0.0 --port \"$PORT\" --workers 1"]
