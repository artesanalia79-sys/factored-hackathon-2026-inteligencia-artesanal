# Operations

How the public staging service is built, deployed, reset and rolled back (Task 15), and what
its logs may contain (Task 20, "Logs and personal data").

## What runs

- One container image (`Dockerfile`): the API only, one process, one worker. Conversations
  live in process memory under one lock, so a second worker or instance would break them
  (`docs/limitations.md`, "Conversation"). Never add workers.
- One Render web service on the free plan, `bankagent-staging`, described by `render.yaml`:
  <https://bankagent-staging.onrender.com>.
- Data: the synthetic fixture bank, built from `tests/fixtures/bank` while the image is built
  and checked against its committed hash. No organizer data is in the image or the service.
- State: the ops store, a SQLite file on the container's disk. The disk is ephemeral: the file
  is new after every restart, deploy or wake-up. That is how the demo is reset (see below).

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness: the process answers. |
| `GET /ready` | Readiness: 200 `{"status": "ready"}` when the serving DB and the ops store both answer; otherwise 503 with the names that failed (`serving_db`, `ops_store`) and nothing else. Render's health check uses it. |
| `GET /` | The customer chat UI (Task 14), the production build in `web/dist`, with a Content Security Policy that keeps it to its own origin. Mounted after every route, so it never shadows `/ready` or the API. |
| `GET /docs` | OpenAPI page, a manual client for the API. |
| `/api/auth/*`, `POST /api/chat/turn` | Login and chat. |

## Environment variables

Names only. No value of a secret is in the repository, in an image layer or in this file.

| Variable | Set by | Meaning |
|---|---|---|
| `APP_SECRET_KEY` | Render generates it (`generateValue`) | Signs session tokens. At least 32 bytes and 12 distinct characters, or the service refuses to start. Nobody needs to read it. |
| `DATA_MODE` | `render.yaml` and the image, `synthetic` | The service also checks what the serving DB itself records. |
| `AUTH_EXPOSE_MOCK_OTP` | `render.yaml`, `true` | The demo login returns the one-time code, because there is no SMS channel. Refused unless the data is synthetic. |
| `DEMO_ACCESS_CODE` | Render dashboard | Shared code every login must present (at least 8 ASCII characters). Empty means no gate. Set it before a paid model is on. |
| `LLM_PROVIDER` | Render dashboard | `stub` (keyword rules, 0 USD), `openai` or `compat`. Empty means `stub`. |
| `LLM_MODEL` | Render dashboard | Empty means `gpt-6-luna`. |
| `LLM_SPEND_LIMIT_USD` | Render dashboard | Spend cap of one process. Empty means 0.10. When it is reached the agent keeps working on the keyword interpreter. |
| `OPENAI_API_KEY` | Render dashboard | Only for `LLM_PROVIDER=openai`. |
| `SERVING_DB_PATH`, `OPS_DB_PATH` | The image | Do not set them on Render. |
| `PORT` | Render (10000) | The image defaults to 8000. |

`LLM_PROVIDER=compat` needs `LLM_BASE_URL` and `LLM_API_KEY` (`.env.example`); they are not
declared in `render.yaml` and must be added there with `sync: false` before it is used.

## First deploy (once)

Done on 2026-10-03 (see "Staging record"). To recreate the service:

1. In GitHub, as the owner of the repository's account, install the Render GitHub App
   (`https://github.com/apps/render/installations/new`). Under "Repository access" choose
   "Only select repositories", pick this repository and save: an installed app with no
   repository selected sees nothing.
2. In Render, connect that GitHub account: the **GitHub** button under "Configure your Git
   provider", with the browser logged in to GitHub as the account that owns the repository.
   Until both steps are done Render answers "No repositories found".
3. **New > Blueprint** (the button in the top bar; the onboarding list of service types has
   no Blueprint entry), **Connect** this repository, give the Blueprint a name and keep the
   branch `main`.
4. Render lists the variables marked `sync: false` and asks for their values. For a first
   deploy without the model: `LLM_PROVIDER` = `stub` and a `DEMO_ACCESS_CODE` of your choice.
5. **Deploy Blueprint**. The deploy goes live when `/ready` answers 200 (52 seconds the
   first time).
6. Run the check in "Verify a deployment".

The Render CLI (`https://render.com/docs/cli`) covers the rest of this file from a terminal:
`render login`, then `render workspace set`. `render services` prints the service id
(`srv-...`) that the commands below need.

## Deploys

- `autoDeployTrigger: checksPass`: Render deploys a commit of the linked branch only after
  every CI check on it passed. The CI job `image` builds this image and runs a dispute against
  it, so a commit that breaks the container is never deployed.
- At the freeze tag (Task 26): set **Settings > Auto-Deploy** to **Off** and deploy the tagged
  commit, so a later merge cannot restart or change the demo:
  `render deploys create <service-id> --commit <sha> --wait`.
- Which commit is live: `render deploys list <service-id> -o json`, or the service's
  **Events** page.
- A changed environment variable applies from the next deploy or restart, and that restart
  also resets the demo.

## Reset the demo

Why: a persona wears out. One dispute per transaction is permanent, and a persona's second
dispute within 90 days is escalated to a person (`DSP-ESC-02`). After a few demos the happy
path is gone until the ops store is emptied.

How: restart the service. The new container has an empty ops store.

- `render restart <service-id>`, or in the dashboard **Manual Deploy > Restart service**.
- It also happens by itself: a free instance sleeps after 15 minutes without traffic and
  wakes up clean.

What a reset loses: every dispute, card block, handoff, session and execution record, the
conversations in progress, and the spend counter of `LLM_SPEND_LIMIT_USD`. There is no reset
endpoint: a public one would let anyone wipe someone else's demo.

Locally (no container): stop the server and delete the file at `OPS_DB_PATH`.

## Turning the real model on

1. Set `DEMO_ACCESS_CODE` first. Without it anyone who finds the URL can make paid calls.
2. Set `OPENAI_API_KEY`, then `LLM_PROVIDER` = `openai` and, on purpose, `LLM_SPEND_LIMIT_USD`.
3. The key owner sets a monthly budget on the OpenAI project. The cap of
   `LLM_SPEND_LIMIT_USD` is per process and starts again at every restart, so on an instance
   that sleeps and wakes it does not bound the total: the project budget does.
4. Run "Verify a deployment", then reset the demo.

To go back: `LLM_PROVIDER` = `stub`.

## Verify a deployment

```
uv run poe smoke https://<service>.onrender.com
```

It waits for `/ready`, logs in as a synthetic persona and runs one full dispute
(`scripts/smoke_dispute.py`). Exit 0 and `OK: verified dispute DSP-...` means the service
created the dispute and read it back. Exit 2 (`USED UP`) means that persona's charge was
already disputed: reset the demo and run it again. If the service has an access code, put the
same value in `DEMO_ACCESS_CODE` in your local `.env`; the script never prints it.

The run leaves one dispute behind, so reset the demo afterwards.

## Logs and personal data

The service writes to stdout and stderr only (Render's **Logs** page, `render logs`). Every
log record of the process is redacted when it is created (`src/bankagent/obs/redaction.py`,
installed by the first line of `create_default_app`): the message, its arguments, the
exception text and the stack. A `Logger.makeRecord` wrapper redacts `extra` fields that Python
attaches after creating the record. Both cover every logger and handler, uvicorn's included.

What writes a log line:

| Logger | Content | In the output |
|---|---|---|
| `uvicorn.access` | method, path with its query string, status | yes |
| `uvicorn.error` | start, stop, and the traceback of a request that failed | yes |
| `bankagent.api` | `readiness_failed` with the dependency name and the exception type | yes (warning) |
| `bankagent.auth` | login events with opaque ids (`chl-…`, `ses-…`), never a code, token or customer id | no: INFO lines are not emitted, see `docs/limitations.md` |
| `openai`, `httpx` | request URL at INFO, request body at DEBUG (`OPENAI_LOG=debug`) | no, unless the level is lowered |

Redacted, replaced by a marker that names the kind:

| Marker | What |
|---|---|
| `[REDACTED:card]` | 13 to 19 digits standing alone, with spaces or hyphens or neither |
| `[REDACTED:email]` | email addresses |
| `[REDACTED:phone]` | phone numbers of 10 to 14 digits, with or without `+`, spaces, hyphens |
| `[REDACTED:document]` | `FX-DOC-…` (the fixture bank), a CPF or CURP as written, `document_number=…` |
| `[REDACTED:identity]` | `CUST-…`, `customer_id`, `is_fraud`, `fraud_score` and their values |
| `[REDACTED:secret]` | the value after `otp`, `otp_code`, `mock_otp`, `access_code`, `token`, `api_key`, `password`, `secret` and their variants (`key=value`, `key: value`, JSON, query string); `Bearer …`; session tokens (JWT); `sk-…` and `AKIA…` keys |
| `[REDACTED:ip]` | IP literals in messages and query strings, and the client address of every `uvicorn.access` line |

A URL-encoded query string is decoded through at most three layers, so `%40`, `%20` and
double-encoded values do not hide an email or a card number. A record that cannot be redacted is
replaced by `log record withheld: redaction failed`.

Left as they are, because they are what a failure is debugged with: record and trace ids
(`DSP-…`, `HND-…`, `trace-…`, `rec-…`, `chl-…`, `ses-…`), rule ids, error codes
(`code=session_expired`), status codes, paths, amounts, dates, token counts and cost.

The client address is redacted on purpose: the project treats an IP address as personal data
(`FORBIDDEN_COLUMNS` in `src/bankagent/contracts/serving.py`), and nothing in this file needs
it. The cost: the access log cannot tell one client from another. Requests are still tied
together by the session id in the application's own lines.

The mock OTP of the demo login is returned in the response body on purpose
(`AUTH_EXPOSE_MOCK_OTP`); it is never written to a log.

How to check:

- `uv run pytest tests/obs -q`. `tests/obs/test_service_logs.py` starts a real uvicorn the way
  the image does, sends a request with a card number, an email, an OTP and a session token in
  the query string and in the message, makes the turn fail, and reads what uvicorn wrote.
- On a running service: `curl "https://<service>/health?mail=a.b%40example.com"`, then look
  for the line in the logs. It must read `[REDACTED:ip] - "GET /health?mail=[REDACTED:email]
  HTTP/1.1" 200 OK`.

Not checked on Render itself: the test above runs locally and in CI, not against the service.

## Roll back

- Dashboard: **Deploys**, pick the last good deploy, **Rollback**. Render turns auto-deploy
  off when you do this; turn it back on in **Settings** once `main` is fixed.
- Or deploy a known good commit: `render deploys create <service-id> --commit <sha> --wait`.
- A bad environment variable: correct it in **Environment** and restart the service.

A rollback restarts the service, so it also resets the demo.

## Building and testing the image

Nobody needs Docker locally. The CI job `image` runs `scripts/image_smoke.sh` on every pull
request and on every push to `main`, and writes the image size and the memory to the job
summary. With Docker installed the same script runs with `uv run poe image-smoke`.

It fails unless all of this holds:

- the build context contains only what `.dockerignore` allows (`pyproject.toml`, `uv.lock`,
  `src/`, `policy/`, `config/`, `tests/fixtures/bank/`), even with a `.env`, `private/`,
  `data/`, `eval/heldout/` and `.venv/` planted next to them;
- the image is under 512 MB, runs as uid 10001, cannot write its code or the fixture bank,
  and `/app` holds nothing but the checkout layout and the fixture bank, whose hash matches;
- started with only the variables above, `PORT=10000` and a 512 MB memory limit, it answers
  `/health` and `/ready`, refuses a login without the access code, and completes a dispute
  that ends verified;
- the same dispute again is refused, and a new container accepts it again (the reset);
- `SIGTERM` stops it with exit code 0.

## Measured

From the CI job `image` on `ubuntu-latest`, commit `ee51b86`, 2026-10-03, with the `stub`
interpreter and a 512 MB limit on the container
([run 37134461186](https://github.com/artesanalia79-sys/factored-hackathon-2026-inteligencia-artesanal/actions/runs/37134461186)).
Every later run writes the same table to its job summary.

| What | Value | How |
|---|---|---|
| Image size | 228 MB | `docker image inspect` |
| Memory of the server process after one full dispute | 112 MB resident, 118 MB peak | `VmRSS` and `VmHWM` of PID 1 |
| Memory charged to the container at that moment | 69 MiB of 512 MiB | `docker stats` |
| Build context | 108 files, all inside the allowlist | listing of a `COPY .` image |

Not measured: memory with a real model answering (the OpenAI client is imported in both
cases, but it was never called in the container), and memory on Render itself.

## Staging record

`https://bankagent-staging.onrender.com`, Render free plan, region `virginia`, branch `main`,
one instance, health check on `/ready`, auto-deploy after CI checks. All times UTC,
2026-10-03. Deployed commits, read with `render deploys list`: `546ec7b` (the merge of pull
request #56) until 17:06, then `bf20735`.

| Time | What | Result |
|---|---|---|
| 16:56:01 to 16:56:53 | First deploy, from the Blueprint | Live in 52 s |
| 16:58 | `GET /health`, `GET /ready` | 200 `{"status":"ready"}` |
| 16:58 | `POST /api/auth/login` without the access code | 403 `access_code_required` |
| 16:58:13 | `uv run poe smoke` with the access code | `OK: verified dispute DSP-68EA194969CC12E4`; 0.1 to 0.9 s per turn |
| Before 16:58:29 | The same flow again, same instance | Exit 2, used up |
| 16:58:29 | `render restart` | Accepted |
| 16:58:41 | The same flow | Still used up: the old instance answers until the new one is ready |
| 16:58:55 | The same flow | `OK: verified dispute DSP-3EE4469C10522E95`: a new instance with an empty store, 26 s after the restart |
| 16:59:10 | `render restart` | Leaves the demo unused |
| 17:06:10 to 17:06:55 | Automatic deploy of `bf20735` (pull request #57) | Created 3 s after the CI checks of that commit finished green; live in 45 s |
| 17:21:59 to 17:22:32 | Manual deploy after `LLM_PROVIDER` was set to `openai` in the dashboard | Live |
| 17:23:07 | `uv run poe smoke` | `OK: verified dispute DSP-56C60A26866E6AFD`; 2.0 to 6.4 s per turn |
| 17:24 | Three openings sent to a local server on the keyword interpreter and to the service | The model is interpreting, see below; 2.1 to 2.7 s per turn |
| 17:24:30 | `render restart` | Leaves the demo unused; `/ready` 200 at 17:25:11 |

That the model reads the messages, and not the keyword fallback, was checked by behaviour,
because the service does not say which one answered. Two openings the keyword rules cannot
read get the clarification question from a local server on `LLM_PROVIDER=stub` and the
right charge from the service: the amount in words ("fueron dos mil cuatrocientos
cincuenta") and informal spelling ("me salio algo de electromundo q no es mio, como 2450
varos"). A third opening is read by both. The first deploy ran on `stub` (0.1 to 0.9 s per
turn).

Not verified on the service: what these calls cost (the spend counter is not exposed; see
the OpenAI dashboard), the memory the instance uses (Render dashboard, **Metrics**), and
the wake-up after 15 idle minutes.

## What to expect from the free plan

- The first request after 15 minutes without traffic takes about a minute.
- 512 MB of memory and 0.1 CPU.
- Render may restart a free instance at any time. A restart ends the conversations in
  progress and resets the demo.
- One instance only, which is what this service needs.

## The web UI in the image (Task 14)

- `.dockerignore` lets `web/` in and keeps `web/node_modules`, `web/dist` and the Playwright
  outputs out: the image builds the UI itself.
- `Dockerfile`, stage `web`: a Node image pinned by tag and digest runs `npm ci` and
  `npm run build:app` (the shipped code's types, then Vite; the Playwright specs are checked by
  the CI job `web`, so a type error in a test cannot block a deploy). Only `web/dist` is copied
  into the runtime stage, at `/app/web/dist`, where `WEB_DIST_DIR` points by default. The UI
  adds under 1 MB: the image measured 228 MB locally both with and without it (227 MB in CI).
- `scripts/image_smoke.sh` allows `web/` in the build context, plants `web/node_modules` and
  `web/dist` decoys that must stay out, expects `/app/web` to hold only `dist`, and checks that
  `GET /` answers the UI with its Content Security Policy.
- Caching: `index.html` and the other files at the root are sent with `Cache-Control:
  no-cache` (revalidated on every load, so a redeploy is picked up at once); the hashed files
  under `/assets/` are `immutable`.
- With `DEMO_ACCESS_CODE` set, the login screen asks for the code after the first 403
  `access_code_required` and keeps it in memory for the tab (a password field, so a screen
  recording of the demo does not show it).
