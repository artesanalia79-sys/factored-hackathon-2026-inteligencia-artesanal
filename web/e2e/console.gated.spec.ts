// The bank side behind the real access-code gate: this project's server runs with
// DEMO_ACCESS_CODE (T15), the same code the customer login asks for. Playwright's own `test`, as
// in console.spec.ts: the console is the one surface where customer ids are the point.
import { expect, test } from '@playwright/test'

test('the console refuses every read without the code, then opens with it', async ({ page }) => {
  const code = process.env.E2E_ACCESS_CODE ?? ''
  // Each read with the header it was sent with: checked by code, not by arrival order.
  const seen: { code: string | undefined; status: number }[] = []
  page.on('response', (response) => {
    if (!response.url().includes('/api/console/')) return
    seen.push({
      code: response.request().headers()['x-console-access-code'],
      status: response.status(),
    })
  })
  // Named "Loading…" while its reads are in flight, so this waits for them to settle.
  const idle = page.getByRole('button', { name: 'Update' })

  await page.goto('/console')
  await expect(page.getByRole('alert')).toContainText('Incorrect or missing access code.')
  await expect(idle).toBeEnabled()

  const field = page.getByLabel('Access code (only if the server asks for it)')
  await field.fill('not-the-demo-code')
  await idle.click()
  await expect(idle).toBeEnabled()
  await expect(page.getByRole('alert')).toContainText('Incorrect or missing access code.')

  await field.fill(code)
  await idle.click()
  await expect(idle).toBeEnabled()
  await expect(page.getByRole('alert')).toHaveCount(0)

  const right = seen.filter((read) => read.code === code)
  const other = seen.filter((read) => read.code !== code)
  expect(right.length).toBeGreaterThanOrEqual(3)
  expect(other.length).toBeGreaterThanOrEqual(6) // no header on load, then the wrong code
  expect(right.every((read) => read.status === 200)).toBe(true)
  expect(other.every((read) => read.status === 401)).toBe(true)
})
