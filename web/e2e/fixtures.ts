import { test as base, expect, type Page } from '@playwright/test'

/**
 * Every test records what crossed the wire: each request (URL, headers, body and the decoded
 * session token claims) and each API response. After the test, nothing recorded may name a
 * customer: the UI never sends `customer_id` and the API never returns one (docs/rules/web.md).
 * The page must not log an error either: a Content Security Policy violation or a React error
 * fails the test. Expected HTTP failures (a wrong code, a revoked session) are left out, and a
 * spec that injects failures matches the ones it expects with `allowedConsoleErrors`.
 */
export const test = base.extend<{ wire: string[]; allowedConsoleErrors: RegExp | null }>({
  allowedConsoleErrors: [null, { option: true }],
  wire: [
    async ({ page, allowedConsoleErrors }, use) => {
      const wire: string[] = []
      const reading: Promise<void>[] = []
      const errors: string[] = []
      const expected = [/Failed to load resource: .* (401|429)/]
      if (allowedConsoleErrors !== null) expected.push(allowedConsoleErrors)
      page.on('pageerror', (error) => errors.push(error.message))
      page.on('console', (message) => {
        const text = message.text()
        if (message.type() === 'error' && !expected.some((pattern) => pattern.test(text))) {
          errors.push(text)
        }
      })
      page.on('request', (request) => {
        const token = request.headers().authorization?.replace(/^Bearer /, '')
        const claims = token?.split('.')[1]
        wire.push(
          [
            request.method(),
            request.url(),
            JSON.stringify(request.headers()),
            request.postData() ?? '',
            claims === undefined ? '' : Buffer.from(claims, 'base64url').toString('utf8'),
          ].join(' '),
        )
      })
      page.on('response', (response) => {
        if (!new URL(response.url()).pathname.startsWith('/api/')) return
        reading.push(
          response.text().then(
            (body) => void wire.push(`response ${response.url()} ${body}`),
            () => undefined,
          ),
        )
      })
      await use(wire)
      await Promise.all(reading)
      expect(wire.length).toBeGreaterThan(0)
      expect(wire.filter((line) => /customer_id|CUST-/i.test(line))).toEqual([])
      expect(errors).toEqual([])
    },
    { auto: true },
  ],
})

export { expect }

export const composer = (page: Page) => page.getByRole('textbox', { name: /Tu mensaje|Sua mensagem/ })
export const log = (page: Page) => page.getByRole('log')
export const confirmation = (page: Page) =>
  page.getByRole('region', { name: /Confirma antes de continuar|Confirme antes de continuar/ })

/** Persona login with the demo code, by mouse. */
export async function signIn(page: Page, firstName: string) {
  await page.goto('/')
  await page.getByRole('radio', { name: new RegExp(firstName) }).check()
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await page.getByRole('button', { name: 'Usar código' }).click()
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('heading', { name: new RegExp(firstName) })).toBeVisible()
}

/** Type a message and send it with Enter, then wait for the agent's answer. */
export async function say(page: Page, text: string) {
  const replies = log(page).locator('.msg--agent:not(.msg--typing)')
  const before = await replies.count()
  await composer(page).fill(text)
  await composer(page).press('Enter')
  await expect(replies).toHaveCount(before + 1)
}

/** The value shown next to a label in the confirmation panel. */
export function fact(page: Page, label: string) {
  return confirmation(page).locator('.fact').filter({ hasText: label }).locator('dd')
}
