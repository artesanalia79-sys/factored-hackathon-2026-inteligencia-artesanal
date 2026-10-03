#!/usr/bin/env bash
# Build the image and prove what docs/operations.md says about it (Task 15).
#
#   bash scripts/image_smoke.sh                   # needs Docker and python3
#   bash scripts/image_smoke.sh --plant-decoys    # CI: first plant files that must never get in
#
# It checks that:
#   - the build context holds only the allowlist of .dockerignore;
#   - the image is under 512 MB, runs as a non-root user that cannot write the code, holds
#     nothing but the checkout layout and the fixture bank, and that bank matches its hash;
#   - started with only the documented variables, Render's PORT and a 512 MB memory limit, it
#     answers /health and /ready and completes a dispute over HTTP that ends verified;
#   - the same flow again is refused (the demo wears out) and a new container is clean again
#     (the reset);
#   - SIGTERM stops it cleanly.
# It prints the image size and the memory after the dispute, also to the job summary in CI.
set -euo pipefail

cd "$(dirname "$0")/.."
IMAGE="${IMAGE:-bankagent:smoke}"
NAME="bankagent-smoke-$$"
HOST_PORT="${HOST_PORT:-18000}"
MEMORY_LIMIT="512m" # a Render free instance
SIZE_LIMIT_MB=512
URL="http://127.0.0.1:${HOST_PORT}"

fail() {
  echo "FAILED: $*" >&2
  docker logs "$NAME" 2>&1 | tail -40 >&2 || true
  exit 1
}
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

if [[ "${1:-}" == "--plant-decoys" ]]; then
  mkdir -p private data/raw eval/heldout .venv
  echo "DECOY=1" >.env
  echo decoy >private/decoy.txt
  echo decoy >data/raw/decoy.parquet
  echo decoy >data/decoy.duckdb
  echo decoy >eval/heldout/decoy.yaml
  echo decoy >.venv/decoy.txt
  echo decoy >src/bankagent/decoy.sqlite
  echo "DECOY=1" >src/.env
fi

echo "== build context: what .dockerignore lets through"
BASE_IMAGE="$(sed -n 's/^ARG PYTHON_BASE_IMAGE=//p' Dockerfile)"
[[ -n "$BASE_IMAGE" ]] || fail "no PYTHON_BASE_IMAGE in the Dockerfile"
printf 'FROM %s\nCOPY . /ctx\n' "$BASE_IMAGE" |
  docker build --quiet --file - --tag bankagent:context-audit . >/dev/null
CONTEXT="$(docker run --rm bankagent:context-audit sh -c 'cd /ctx && find . -type f | sort')"
OUTSIDE="$(grep -vE '^\./(pyproject\.toml|uv\.lock|src/|policy/|config/|tests/fixtures/bank/)' <<<"$CONTEXT" || true)"
[[ -z "$OUTSIDE" ]] || fail "outside the allowlist, in the build context:"$'\n'"$OUTSIDE"
FORBIDDEN="$(grep -E '(^|/)\.env(\.|$)|\.duckdb(\.wal)?$|\.sqlite3?$|\.parquet$|__pycache__|\.pyc$|decoy' <<<"$CONTEXT" || true)"
[[ -z "$FORBIDDEN" ]] || fail "forbidden files in the build context:"$'\n'"$FORBIDDEN"
echo "$(wc -l <<<"$CONTEXT") files, all inside the allowlist"

echo "== build"
docker build --tag "$IMAGE" .
SIZE_MB=$(($(docker image inspect --format '{{.Size}}' "$IMAGE") / 1000000))
((SIZE_MB < SIZE_LIMIT_MB)) || fail "the image is ${SIZE_MB} MB, the limit is ${SIZE_LIMIT_MB} MB"
echo "image size: ${SIZE_MB} MB"

echo "== image content"
IMAGE_UID="$(docker run --rm "$IMAGE" id -u)"
[[ "$IMAGE_UID" != "0" ]] || fail "the image runs as root"
TOP="$(docker run --rm "$IMAGE" sh -c 'ls -A /app | sort | tr "\n" " "')"
[[ "$TOP" == ".venv config data policy src tests " ]] || fail "unexpected entries in /app: $TOP"
DATA="$(docker run --rm "$IMAGE" sh -c 'find /app/data /app/tests -type f | sort')"
EXTRA="$(grep -vE '^/app/(data/fixtures/bank_fixture\.duckdb|tests/fixtures/bank/[a-z_]+\.(yaml|txt))$' <<<"$DATA" || true)"
[[ -z "$EXTRA" ]] || fail "unexpected data files in the image:"$'\n'"$EXTRA"
grep -q '^/app/data/fixtures/bank_fixture.duckdb$' <<<"$DATA" || fail "the fixture bank is missing"
LEAKS="$(docker run --rm "$IMAGE" sh -c "find / -xdev \( -name '.env' -o -name '.env.*' -o -name '*.parquet' -o -name '*.sqlite' -o -name 'decoy*' -o -name 'heldout' -o -name '.git' \) -not -path '/proc/*' 2>/dev/null" || true)"
[[ -z "$LEAKS" ]] || fail "files that must not be in the image:"$'\n'"$LEAKS"
WRITABLE="$(docker run --rm "$IMAGE" sh -c 'touch /app/src/probe /app/data/fixtures/probe 2>/dev/null && echo yes || echo no')"
[[ "$WRITABLE" == "no" ]] || fail "the runtime user can write the code or the fixture bank"
docker run --rm "$IMAGE" python -m bankagent.fixtures.builder --check
echo "uid ${IMAGE_UID}; /app holds only: ${TOP}"

start() {
  # Values are passed by name so they never appear in a process list. PORT as Render sets it.
  docker run --detach --name "$NAME" --memory "$MEMORY_LIMIT" --memory-swap "$MEMORY_LIMIT" \
    --publish "127.0.0.1:${HOST_PORT}:10000" --env PORT=10000 \
    --env APP_SECRET_KEY --env DEMO_ACCESS_CODE \
    --env AUTH_EXPOSE_MOCK_OTP=true --env LLM_PROVIDER=stub "$IMAGE" >/dev/null
}

echo "== run: documented variables only, ${MEMORY_LIMIT} memory, PORT=10000"
APP_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
DEMO_ACCESS_CODE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
export APP_SECRET_KEY DEMO_ACCESS_CODE
start
python3 scripts/smoke_dispute.py "$URL" --wait-ready 90 || fail "the dispute flow failed"

[[ "$(docker exec "$NAME" id -u)" != "0" ]] || fail "the service process runs as root"
PID1="$(docker exec "$NAME" sh -c "tr '\0' ' ' </proc/1/cmdline")"
[[ "$PID1" == *uvicorn* ]] || fail "PID 1 is not uvicorn: $PID1"
RSS_MB=$(($(docker exec "$NAME" sh -c "awk '/^VmRSS/ {print \$2}' /proc/1/status") / 1024))
PEAK_MB=$(($(docker exec "$NAME" sh -c "awk '/^VmHWM/ {print \$2}' /proc/1/status") / 1024))
CGROUP="$(docker stats --no-stream --format '{{.MemUsage}}' "$NAME")"
[[ "$(docker inspect --format '{{.State.OOMKilled}}' "$NAME")" == "false" ]] || fail "OOM killed"
echo "memory after the dispute: RSS ${RSS_MB} MB, peak ${PEAK_MB} MB, container ${CGROUP}"

echo "== the demo wears out, and a new container is clean (the reset)"
set +e
unset_code_run="$(DEMO_ACCESS_CODE='' python3 scripts/smoke_dispute.py "$URL" --wait-ready 10 2>&1)"
NO_CODE=$?
python3 scripts/smoke_dispute.py "$URL" --wait-ready 10 >/dev/null 2>&1
SECOND=$?
set -e
[[ $NO_CODE -eq 1 && "$unset_code_run" == *DEMO_ACCESS_CODE* ]] || fail "a login without the access code was not refused"
[[ $SECOND -eq 2 ]] || fail "a second run on the same container returned $SECOND, expected 2 (used up)"
docker stop --time 20 "$NAME" >/dev/null
EXIT_CODE="$(docker inspect --format '{{.State.ExitCode}}' "$NAME")"
[[ "$EXIT_CODE" == "0" ]] || fail "SIGTERM did not stop the service cleanly (exit $EXIT_CODE)"
docker rm "$NAME" >/dev/null
start
python3 scripts/smoke_dispute.py "$URL" --wait-ready 90 >/dev/null || fail "a new container was not clean"
echo "no code: refused; second run: used up; SIGTERM: exit 0; new container: verified dispute again"

SUMMARY="| Image size | RSS after the dispute | Peak RSS | Container memory (limit ${MEMORY_LIMIT}) | uid |
|---|---|---|---|---|
| ${SIZE_MB} MB | ${RSS_MB} MB | ${PEAK_MB} MB | ${CGROUP} | ${IMAGE_UID} |"
echo "$SUMMARY"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### Container image at \`${GITHUB_SHA:-local}\`"
    echo
    echo "$SUMMARY"
  } >>"$GITHUB_STEP_SUMMARY"
fi
echo "OK"
