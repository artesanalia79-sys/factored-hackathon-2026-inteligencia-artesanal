# web

Customer chat UI (T14): persona login with the demo code, a session-scoped list of recent
movements, the chat, and a confirmation panel for every write. React 19 + Vite 8 + TypeScript
(strict). Rules: `docs/rules/web.md`.

The production build (`web/dist/`, gitignored) is served by FastAPI at `/` on the same origin as
the API, with a strict Content Security Policy (`bankagent.api.app.WEB_SECURITY_HEADERS`); the
container image (T15) builds it in its `web` stage (`docs/operations.md`). Two reviewer pages sit
beside the chat: the bank-side console (T21) at `/console` and the T22 replay viewer at `/compare`.

## Run it

```bash
uv run poe web-install            # npm ci (exact pins, package-lock.json)
uv run poe fixtures               # the synthetic fixture bank the API reads
uv run poe web-build              # tsc -b && vite build -> web/dist

# One process: the API serves the build at http://localhost:8000
uv run poe serve
```

`poe serve` reads `.env` (`uv run poe init-env` creates it): `APP_SECRET_KEY`,
`AUTH_EXPOSE_MOCK_OTP=true` for the demo code, and `LLM_PROVIDER` (`stub` costs nothing; `openai`
and `compat` are paid or rate-limited model calls, `docs/adr/0002-llm-provider-and-budget.md`).

For UI work, run the API as above and `npm --prefix web run dev`: Vite serves the UI on :5173 with
hot reload and proxies `/api` and `/health` to :8000.

## Checks

| Command | What it checks |
|---|---|
| `uv run poe web-check` | `src/api/contracts.gen.ts` matches `docs/contracts/`, strict TypeScript, oxlint (React and jsx-a11y rules) |
| `uv run poe web-e2e` | Playwright against the real API serving `web/dist` (stub LLM, fixture bank, a fresh ops store and a random signing secret per run). First time: `npx --prefix web playwright install chromium` |
| `E2E_LLM_PROVIDER=openai uv run --env-file ../.env -- npx playwright test -g "happy path"` (in `web/`) | One spec on the real model; costs money (capped at 2 cents a run), so never the whole suite |

In PowerShell, from `web/`, run the live spec with:

```powershell
$env:E2E_LLM_PROVIDER = "openai"
uv run --env-file ../.env -- npx playwright test -g "happy path"
Remove-Item Env:E2E_LLM_PROVIDER
```

Playwright starts two API servers, each capped at 1 cent, for a 2-cent total maximum.

The Playwright suite (`e2e/`, 17 tests) covers the happy path (login, dispute, confirmation, a
second click on the card-block question as it appears), a keyboard-only run in Portuguese that
also blocks the card, a handoff, a wrong code, a revoked session, failures injected in the
browser (persona list down, a persona the server no longer knows, a locked login, a message lost
on the way, the reply to a confirmed dispute lost on the way, a sign-out the service never
answers), the access-code prompt (played by the browser, and against a second server started
with a real `DEMO_ACCESS_CODE`), axe WCAG 2.2 A/AA scans in light and dark mode, and a
phone-width layout, and session-scoped movements with click-to-chat. A fixture records every
request and API response of every test
and fails it if anything names a customer (`customer_id` or a customer id, also inside the
decoded session token) or if the page logs an error (a CSP violation included). CI runs all of it
in the `web` job.

## How it works

- **Contracts.** `npm run contracts` generates `src/api/contracts.gen.ts` from the JSON Schemas of
  `bankagent.contracts.api` in `docs/contracts/`. The UI never writes an API shape by hand. After
  changing a wire model: `uv run poe contracts`, then `npm --prefix web run contracts`.
- **Identity.** Login is persona + one-time code; the UI keeps only the opaque session token, in
  memory (a reload signs out). Every request body is typed by the contracts, which have no
  `customer_id`, and the server rejects unknown fields.
- **Movements.** `GET /api/chat/transactions` reads the 50 newest movements using the authenticated
  session's customer identity. The UI shows five first and can expand the list. Choosing one
  prepares a question with its exact transaction reference in the composer.
- **Access code.** A deployment with a shared access code (T15, `DEMO_ACCESS_CODE`) answers
  the first login with 403 `access_code_required`; the login screen then asks for the code
  (a password field, so a screen recording does not show it) and keeps it in memory for the
  tab, so signing in again does not ask twice.
- **Confirmation.** When a reply asks to confirm a write, the API sends `confirmation`
  (`ConfirmationView`): the action and the question's own display strings (merchant, amount,
  date, card ending, channel, reason), built from the same verified read as the question. The
  panel labels them and formats nothing. Its buttons answer in the chat with "Sí, confirmo" / "No"
  ("Sim, confirmo" / "Não"); the server issues the confirmation token at that yes for exactly the
  arguments shown. Nothing is shown as done before the reply arrives. A new question takes no
  answer for its first 600 ms: the card-block offer appears where the dispute question was, so
  the second click of a double click on "Confirmar" would otherwise confirm it unseen.
- **A reply that never arrives.** The UI cannot tell whether the message reached the agent, so
  it never resends it. A typed message goes back into the composer for the customer to decide;
  an answer to a confirmation does not, because the server may already be asking the next
  question, and the same "Sí" would answer that one. The customer is told to ask what happened
  first; the agent then repeats the question it is on (`docs/limitations.md`).
- **Verified actions.** A status under a reply ("Reclamo registrado y verificado", "Caso enviado a
  una persona del equipo") comes only from `claimed_actions`, which the API fills for writes
  verified by read-back.
- **Language.** Agent text comes from the backend templates. The chrome (labels, buttons) is in
  `src/i18n.ts` in Spanish and Portuguese. The chosen login language carries through the chat;
  every UI turn sends that preference to the agent. API clients that omit it keep automatic
  language detection. The two reviewer pages, `/compare` and `/console`, are in English instead,
  like every deliverable except the customer's own conversation; the conversations `/compare`
  replays keep their Spanish or Portuguese, marked as such for screen readers.
- **Demo scenarios.** `src/demo/scenarios.json` offers first messages per demo persona; picking
  one fills the composer. Openings have Spanish and Portuguese variants.
  `tests/orchestrator/test_api_acceptance.py` checks that each still reaches the question it
  expects on the fixture bank.
- **Accessibility.** Native controls (radios, labels, buttons), a `role="log"` live region for the
  conversation, focus moved to the confirmation panel when it appears and back to the composer,
  visible focus, WCAG AA contrast in both colour schemes, reduced motion respected. A manual pass
  with a screen reader has not been done (`docs/limitations.md`).

## Design

Calm retail-banking messenger: sage-tinted neutral canvas, charcoal ink as the action colour, a
turquoise accent for the brand mark and focus, green reserved for "verified". Geist and Geist
Mono (self-hosted, OFL), Phosphor icons. Tokens and the shape rule live at the top of
`src/styles.css`.

## Side-by-side comparison (T22)

The sign-in screen links to `/compare`, a local replay viewer for the naive and controlled
systems. Generate an example with `uv run poe eval-smoke` and open
`eval/runs/smoke/comparison.json`. Scripted runs are clearly labeled simulations.
See [the walkthrough and evidence boundaries](../docs/comparison.md).
