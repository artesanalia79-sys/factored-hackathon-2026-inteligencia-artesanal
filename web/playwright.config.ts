import { randomBytes } from 'node:crypto'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

// The real API serves the production build (run `npm run build` first) with the stub LLM on the
// synthetic fixture bank (`uv run poe fixtures`). Each run gets a fresh operational store, because
// a persona's second dispute escalates as a repeat disputer (docs/limitations.md). The signing
// secret is random per run and never written anywhere. A second server runs with a shared access
// code (T15, `DEMO_ACCESS_CODE`) for the specs that test the real gate. The stores are removed
// after the run, or after the next one on Windows (e2e/teardown.ts).
const PORT = 8765
const GATED_PORT = 8766
process.env.E2E_RUN ??= `${process.pid}-${Date.now()}`
const RUN = process.env.E2E_RUN
// `stub` unless asked: E2E_LLM_PROVIDER=openai runs the servers on the real model, which costs
// money, so pick one spec with -g and pass OPENAI_API_KEY in the environment (for example
// `uv run --env-file ../.env -- npx playwright test -g "happy path"`). Two servers are capped
// independently at 1 cent each, for a 2-cent maximum across the run.
const LLM_PROVIDER = process.env.E2E_LLM_PROVIDER ?? 'stub'
// Workers evaluate this file again: set once in the main process, inherited by the workers.
process.env.E2E_ACCESS_CODE ??= randomBytes(12).toString('base64url')

function server(port: number, accessCode: string) {
  return {
    command: `uv run uvicorn bankagent.api.wiring:create_default_app --factory --host 127.0.0.1 --port ${port} --workers 1`,
    url: `http://127.0.0.1:${port}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      APP_SECRET_KEY: randomBytes(32).toString('base64url'),
      AUTH_EXPOSE_MOCK_OTP: 'true',
      DATA_MODE: 'synthetic',
      DEMO_ACCESS_CODE: accessCode,
      LLM_PROVIDER,
      LLM_SPEND_LIMIT_USD: '0.01',
      OPS_DB_PATH: join(tmpdir(), `bankagent-e2e-${RUN}-${port}.sqlite`),
      WEB_DIST_DIR: 'web/dist',
    },
  }
}

export default defineConfig({
  testDir: './e2e',
  globalTeardown: './e2e/teardown.ts',
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      testIgnore: /\.gated\.spec\.ts$/,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${PORT}` },
    },
    {
      name: 'gated',
      testMatch: /\.gated\.spec\.ts$/,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${GATED_PORT}` },
    },
  ],
  webServer: [server(PORT, ''), server(GATED_PORT, process.env.E2E_ACCESS_CODE)],
})
