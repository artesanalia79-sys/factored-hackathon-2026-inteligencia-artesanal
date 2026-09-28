# Web UI rules

Applies to `web/**`. Owner: Jacobo (Tasks 14, 21, 22).

## Stack

- React + Vite + TypeScript (strict mode). Pin exact versions (`npm install --save-exact`) and
  commit `package-lock.json`. The production build is served by FastAPI from `web/dist/`.
- API types are generated from or checked against `docs/contracts/*.schema.json`. Do not redefine
  contract shapes by hand in the UI.

## Behavior

- The UI never sends `customer_id`. Identity is the session token issued by the backend.
- Show only what the backend returns as verified. Do not render optimistic "dispute created"
  states before the API confirms `verified=true`.
- Confirmation prompts must show the exact action and its arguments (merchant, amount, date, card
  last4) before the customer confirms.
- Customer-facing copy is Spanish or Portuguese and comes from the backend templates; UI chrome may
  be bilingual. Code and comments stay in English.

## Accessibility and quality

- Semantic HTML, labeled inputs, keyboard navigation, visible focus, sufficient contrast,
  `aria-live="polite"` for new assistant messages. Full WCAG validation needs manual testing.
- Keep a Playwright smoke test for the happy path (login → dispute → confirmation).
- No secrets or API keys in frontend code or `.env` files under `web/`.
