import { composer, confirmation, expect, fact, log, say, signIn, test } from './fixtures.ts'

// Each test uses its own persona: one API process serves the whole run, and a persona's second
// dispute escalates as a repeat disputer.

test('happy path: a dispute is created only after confirming its exact facts', async ({ page }) => {
  await signIn(page, 'Mariana')
  await page.getByRole('button', { name: /Cargo no reconocido/ }).click()
  await expect(composer(page)).toHaveValue(
    'Tengo un cargo de 2,450 pesos en ELECTROMUNDO que no reconozco',
  )
  await composer(page).press('Enter')
  await expect(log(page)).toContainText('¿Reconoces este movimiento?')
  await expect(confirmation(page)).toHaveCount(0)

  await say(page, 'No')
  await expect(confirmation(page)).toBeVisible()
  await expect(confirmation(page)).toContainText('Crear un reclamo')
  await expect(fact(page, 'Comercio')).toHaveText('ELECTROMUNDO ONLINE')
  await expect(fact(page, 'Importe')).toHaveText('2,450.00 MXN')
  await expect(fact(page, 'Fecha')).toHaveText('12/06/2026')
  await expect(fact(page, 'Tarjeta')).toContainText('4821')
  await expect(fact(page, 'Canal')).toHaveText('sitio web')
  await expect(fact(page, 'Motivo')).toHaveText('movimiento no reconocido')
  await expect(page.getByText('Reclamo registrado y verificado')).toHaveCount(0)

  await confirmation(page).getByRole('button', { name: 'Confirmar' }).click()
  await expect(log(page)).toContainText(/Creé el reclamo DSP-/)
  await expect(page.getByText('Reclamo registrado y verificado')).toBeVisible()

  // The card block is offered next, as its own confirmation.
  await expect(confirmation(page)).toContainText('Bloquear la tarjeta')
  await expect(fact(page, 'Tarjeta')).toContainText('4821')
  await expect(fact(page, 'Comercio')).toHaveCount(0)
  await confirmation(page).getByRole('button', { name: 'Cancelar' }).click()
  await expect(log(page)).toContainText('Entendido, no bloquearé la tarjeta.')
  await expect(log(page)).toContainText('Conversación finalizada')
  await expect(confirmation(page)).toHaveCount(0)
  await expect(page.getByText('Bloqueo de tarjeta verificado')).toHaveCount(0)
})

test('keyboard only, in Portuguese: sign in, dispute and block the card', async ({ page }) => {
  await page.goto('/')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Español' })).toBeFocused()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: /Analise uma cobrança/ })).toBeVisible()

  await page.keyboard.press('Tab')
  const rafael = page.getByRole('radio', { name: /Rafael/ })
  for (let step = 0; step < 10 && !(await rafael.isChecked()); step += 1) {
    await page.keyboard.press('ArrowDown')
  }
  await expect(rafael).toBeChecked()
  await expect(rafael).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Enviar código' })).toBeFocused()
  await page.keyboard.press('Enter')

  await expect(page.getByRole('heading', { name: 'Código de acesso de Rafael' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Usar código' })).toBeFocused()
  await page.keyboard.press('Enter')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('textbox', { name: 'Código de 6 dígitos' })).toBeFocused()
  await expect(page.getByRole('textbox', { name: 'Código de 6 dígitos' })).toHaveValue(/^\d{6}$/)
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Entrar' })).toBeFocused()
  await page.keyboard.press('Enter')

  await expect(composer(page)).toBeFocused()
  await page.keyboard.type('Não reconheço uma compra de 32.500 pesos na GAMESTORE DIGITAL')
  await page.keyboard.press('Enter')
  await expect(log(page)).toContainText('Você reconhece esta transação?')
  await page.keyboard.type('Não')
  await page.keyboard.press('Enter')

  // The question takes the focus: its name and facts are read out, then Tab reaches the answers.
  await expect(confirmation(page)).toBeFocused()
  await expect(fact(page, 'Estabelecimento')).toHaveText('GAMESTORE DIGITAL')
  await expect(fact(page, 'Valor')).toHaveText('32.500,00 ARS')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Cancelar' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Confirmar' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Contestação registrada e verificada')).toBeVisible()

  await expect(confirmation(page)).toBeFocused()
  await expect(confirmation(page)).toContainText('Bloquear o cartão')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Confirmar' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(log(page)).toContainText('Bloqueei o cartão com final 2208.')
  await expect(page.getByText('Bloqueio do cartão verificado')).toBeVisible()
  await expect(log(page)).toContainText('Conversa encerrada')
  await expect(composer(page)).toBeFocused()
})

test('a case that needs a person ends in a visible handoff', async ({ page }) => {
  await signIn(page, 'Carlos')
  await page.getByRole('button', { name: /Cargo de alto riesgo/ }).click()
  await composer(page).press('Enter')
  await expect(log(page)).toContainText('¿Reconoces este movimiento?')
  await say(page, 'No fui yo')
  await expect(log(page)).toContainText(/revisión humana con referencia HND-/)
  await expect(page.getByText('Caso enviado a una persona del equipo')).toBeVisible()
  await expect(log(page)).toContainText('Conversación finalizada')
  await expect(confirmation(page)).toHaveCount(0)
  // The demo offers another try once the conversation is over.
  await expect(page.getByRole('heading', { name: 'Prueba otra consulta' })).toBeVisible()
})

test('a wrong code is refused, then the right one signs in', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('radio', { name: /Sofía/ }).check()
  await page.getByRole('button', { name: 'Enviar código' }).click()
  const code = page.getByRole('textbox', { name: 'Código de 6 dígitos' })
  const demo = await page.locator('.democode__value').textContent()
  await code.fill(demo === '000000' ? '111111' : '000000')
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('alert')).toContainText('El código no es válido o ya venció')
  await expect(code).toHaveAttribute('aria-invalid', 'true')
  await page.getByRole('button', { name: 'Usar código' }).click()
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('heading', { name: 'Hola, Sofía' })).toBeVisible()
})

test('an ended session asks to sign in again and keeps the conversation', async ({ page }) => {
  await signIn(page, 'Lucía')
  const turn = page.waitForRequest('**/api/chat/turn')
  await say(page, 'Hola')
  const authorization = (await turn).headers().authorization ?? ''
  expect(authorization).toMatch(/^Bearer /)
  // Revoke the session behind the UI's back, as an expiry would.
  const logout = await page.request.post('/api/auth/logout', { headers: { authorization } })
  expect(logout.status()).toBe(204)

  await composer(page).fill('No me llegó una compra de 45.999 pesos en MERCADOLIBRE')
  await composer(page).press('Enter')
  await expect(page.getByRole('alert')).toContainText('Tu sesión terminó')
  await expect(log(page)).toContainText('No me llegó una compra')
  await expect(composer(page)).toHaveCount(0)
  await page.getByRole('button', { name: 'Entrar de nuevo' }).click()
  await expect(page.getByText('Tu sesión terminó. Entra de nuevo para continuar.')).toBeVisible()
  await expect(page.getByRole('radio', { name: /Lucía/ })).toBeVisible()
})
