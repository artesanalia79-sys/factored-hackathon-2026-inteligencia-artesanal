import { composer, expect, log, signIn, test } from './fixtures.ts'

// Failures are injected in the browser (page.route), so the real server stays healthy for the
// other specs. Valentina is used only here.
test.use({ allowedConsoleErrors: /status of 500|ERR_FAILED/ })

test('the persona list loads again after it failed', async ({ page }) => {
  let failing = true
  await page.route('**/api/auth/personas', (route) =>
    failing ? route.fulfill({ status: 500, body: 'unavailable' }) : route.continue(),
  )
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('No pudimos cargar los clientes de prueba.')
  failing = false
  await page.getByRole('button', { name: 'Reintentar' }).click()
  await expect(page.getByRole('radio', { name: /Valentina/ })).toBeVisible()
})

test('a persona the server no longer knows reloads the list', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('radio', { name: /Valentina/ }).check()
  await page.route(
    '**/api/auth/login',
    (route) => route.fulfill({ status: 401, json: { detail: { error: 'invalid_credentials' } } }),
    { times: 1 },
  )
  const reloaded = page.waitForRequest('**/api/auth/personas')
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await expect(page.getByRole('alert')).toContainText('Ese cliente ya no está disponible')
  await reloaded
  await expect(page.getByRole('radio', { name: /Valentina/ })).not.toBeChecked()
  await expect(page.getByRole('button', { name: 'Enviar código' })).toBeDisabled()
})

test('a locked login says how long to wait', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('radio', { name: /Valentina/ }).check()
  await page.route('**/api/auth/login', (route) =>
    route.fulfill({
      status: 429,
      headers: { 'Retry-After': '900' },
      json: { detail: { error: 'too_many_attempts', retry_after_seconds: 900 } },
    }),
  )
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await expect(page.getByRole('alert')).toContainText(
    'Demasiados intentos. Vuelve a intentarlo en 15 min.',
  )
})

test('a message that may not have arrived is given back, not resent', async ({ page }) => {
  await signIn(page, 'Valentina')
  const text = 'No reconozco el cargo de PEDIDOSYA de 15.200 pesos'
  await page.route('**/api/chat/turn', (route) => route.abort(), { times: 1 })
  await composer(page).fill(text)
  await composer(page).press('Enter')
  await expect(page.getByRole('alert')).toContainText('No pudimos confirmar si tu mensaje llegó')
  await expect(composer(page)).toHaveValue(text)
  // The customer decides to send it again; this time it reaches the agent.
  await composer(page).press('Enter')
  await expect(log(page)).toContainText('¿Reconoces este movimiento?')
  await expect(page.getByRole('alert')).toHaveCount(0)
})
