import type { Route } from '@playwright/test'
import { composer, confirmation, expect, fact, log, say, signIn, test } from './fixtures.ts'

// Failures are injected in the browser (page.route), so the real server stays healthy for the
// other specs. Valentina is used only here; so is Sofía's dispute (elsewhere she only signs in).
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
  // The message follows the language toggle.
  await page.getByRole('button', { name: 'Português' }).click()
  await expect(page.getByRole('alert')).toContainText('Muitas tentativas. Tente de novo em 15 min.')
})

test('an answer that may not have arrived is not given back to be sent again', async ({
  page,
}) => {
  await signIn(page, 'Sofía')
  await say(page, 'Me aparece un cargo de PAYPAL SPOTIFYMX de 129 pesos y no sé qué es')
  await say(page, 'No')
  await expect(confirmation(page)).toContainText('Crear un reclamo')
  // The server reads the yes and creates the dispute, but its reply never reaches the browser.
  await page.route(
    '**/api/chat/turn',
    async (route) => {
      await route.fetch()
      await route.abort()
    },
    { times: 1 },
  )
  await confirmation(page).getByRole('button', { name: 'Confirmar' }).click()
  await expect(page.getByRole('alert')).toContainText('No pudimos confirmar si tu respuesta llegó')
  // The server now asks about the card block. A "Sí, confirmo" in the composer would answer
  // that question, which the customer never saw, with a single Enter.
  await expect(composer(page)).toHaveValue('')
  await expect(confirmation(page)).toHaveCount(0)

  // Asking what happened shows the question the server is on, before anything else is written.
  await composer(page).fill('¿En qué quedó mi solicitud?')
  await composer(page).press('Enter')
  await expect(confirmation(page)).toContainText('Bloquear la tarjeta')
  await expect(fact(page, 'Tarjeta terminada en')).toHaveText('3344')
  // A panel answer leaves a half-written message alone.
  await composer(page).fill('Una nota a medio escribir')
  await confirmation(page).getByRole('button', { name: 'Cancelar' }).click()
  await expect(log(page)).toContainText('Entendido, no bloquearé la tarjeta.')
  await expect(composer(page)).toHaveValue('Una nota a medio escribir')
  await expect(page.getByText('Bloqueo de tarjeta verificado')).toHaveCount(0)
})

test('signing out leaves at once, even when the service does not answer', async ({ page }) => {
  await signIn(page, 'Valentina')
  let held: Route | undefined
  await page.route('**/api/auth/logout', (route) => {
    held = route
  })
  const revoked = page.waitForRequest('**/api/auth/logout')
  await page.getByRole('button', { name: 'Cerrar sesión' }).click()
  await expect(page.getByRole('radio', { name: /Valentina/ })).toBeVisible()
  await revoked
  await held?.abort()
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
