// Unlike every other spec, this one does not import `test`/`expect` from `./fixtures.ts`: that
// fixture fails any test whose network traffic contains `customer_id` or `CUST-` (the customer
// chat must never see either). The console (T21) is the bank side, where seeing both across
// customers is the whole point, so it is exempt from that check by using Playwright's own `test`
// instead. The customer-chat helpers it reuses (`signIn`, `say`, `composer`, `log`) are plain
// functions and carry no such restriction.
import { expect, test } from '@playwright/test'
import { composer, log, say, signIn } from './fixtures.ts'

test('a handoff a customer caused shows up on the bank side, with its trace and rules', async ({
  page,
}) => {
  // Reuses Carlos's own escalation flow (chat.spec.ts): a second run just adds a second handoff,
  // since handoffs carry no per-transaction uniqueness the way disputes and card blocks do.
  await signIn(page, 'Carlos')
  await page.getByRole('button', { name: /Cargo de alto riesgo/ }).click()
  await composer(page).press('Enter')
  await expect(log(page)).toContainText('¿Reconoces este movimiento?')
  await say(page, 'No fui yo')
  await expect(log(page)).toContainText(/revisión humana con referencia HND-/)
  const reference = (await log(page).textContent()) ?? ''
  const handoffId = reference.match(/HND-[A-Za-z0-9-]+/)?.[0]
  expect(handoffId).toBeTruthy()

  await page.goto('/console')
  await expect(page.getByRole('heading', { name: /Lo que ve el banco/ })).toBeVisible()
  const handoffsTab = page.getByRole('button', { name: /^Escalados/ })
  await expect(handoffsTab).toBeVisible()
  await expect(page.getByText('CUST-FX-006').first()).toBeVisible()

  await page.getByRole('button', { name: new RegExp(`CUST-FX-006`) }).first().click()
  await expect(page.getByRole('heading', { name: new RegExp(handoffId!) })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Reglas que dispararon el caso' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Rastro de ejecución' })).toBeVisible()
  await expect(page.locator('.console__steps li').first()).toBeVisible()

  // The other two tabs render without error, whatever other specs left behind.
  await page.getByRole('button', { name: /^Disputas/ }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: /^Tarjetas bloqueadas/ }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('link', { name: 'Volver al inicio' }).click()
  await expect(page.getByRole('link', { name: 'Lado del banco' })).toBeVisible()
})

test('a wrong or missing access code is refused, not a silent empty page', async ({ page }) => {
  await page.goto('/console')
  // The ungated server (port 8765) accepts no header at all here; the point of this test is the
  // UI's handling of the 401 shape, which the gated project exercises for real against its server.
  await page.route('**/api/console/**', (route) =>
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: { error: 'access_code_required' } }),
    }),
  )
  await page.getByRole('button', { name: 'Actualizar' }).click()
  await expect(page.getByRole('alert')).toContainText('Código de acceso incorrecto o faltante.')
})
