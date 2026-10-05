import { composer, confirmation, expect, fact, log, say, signIn, test } from './fixtures.ts'

// Each test uses its own persona: one API process serves the whole run, and a persona's second
// dispute escalates as a repeat disputer.

test('happy path: a dispute is created only after confirming its exact facts', async ({ page }) => {
  await signIn(page, 'Mariana')
  // The limit comes from the contract (ChatTurnRequest), not from a number copied by hand.
  await expect(composer(page)).toHaveAttribute('maxlength', '2000')
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
  await expect(fact(page, 'Tarjeta terminada en')).toHaveText('4821')
  await expect(fact(page, 'Canal')).toHaveText('sitio web')
  await expect(fact(page, 'Motivo')).toHaveText('movimiento no reconocido')
  await expect(page.getByText('Reclamo registrado y verificado')).toHaveCount(0)

  // The card block is offered next, in the place of the dispute question, so the second click
  // of a double click on "Confirmar" lands on the new "Confirmar" as it appears. The page plays
  // that click the moment the new question is in the DOM, whatever the test runner's timing.
  await page.evaluate(() => {
    const observer = new MutationObserver(() => {
      const panel = document.querySelector('.confirm')
      if (panel === null || !panel.textContent?.includes('Bloquear la tarjeta')) return
      observer.disconnect()
      const buttons = Array.from(panel.querySelectorAll('button'))
      const button = buttons.find((candidate) => candidate.textContent === 'Confirmar')
      document.body.dataset.secondClick = button?.getAttribute('aria-disabled') ?? 'missing'
      button?.click()
    })
    observer.observe(document.body, { childList: true, subtree: true })
  })
  const answers = log(page).locator('.msg--customer')
  const before = await answers.count()
  await confirmation(page).getByRole('button', { name: 'Confirmar' }).click()
  await expect(log(page)).toContainText(/Creé el reclamo DSP-/)
  await expect(page.getByText('Reclamo registrado y verificado')).toBeVisible()
  // Tagged in the Transactions panel from the server's own disputed_transaction_id, not parsed
  // out of the reply text.
  await expect(
    page.getByRole('region', { name: 'Tus movimientos recientes' }).getByText('Reclamo en curso'),
  ).toBeVisible()
  await expect(confirmation(page)).toContainText('Bloquear la tarjeta')
  // The click came while the question was not yet taking answers: no "Sí" sent, nothing blocked.
  await expect(page.locator('body')).toHaveAttribute('data-second-click', 'true')
  expect(await answers.count()).toBe(before + 1)
  await expect(confirmation(page)).toContainText('Bloquear la tarjeta')
  await expect(fact(page, 'Tarjeta terminada en')).toHaveText('4821')
  await expect(fact(page, 'Comercio')).toHaveCount(0)
  await confirmation(page).getByRole('button', { name: 'Cancelar' }).click()
  await expect(log(page)).toContainText('Entendido, no bloquearé la tarjeta.')
  await expect(log(page)).toContainText('Conversación finalizada')
  await expect(confirmation(page)).toHaveCount(0)
  await expect(page.getByText('Bloqueo de tarjeta verificado')).toHaveCount(0)
  // A declined block marks no card: only a verified block_card names one.
  await expect(
    page.getByRole('region', { name: 'Tus movimientos recientes' }).getByText('Tarjeta bloqueada'),
  ).toHaveCount(0)
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
  await expect(page.getByRole('button', { name: 'Solicitar código' })).toBeFocused()
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
  // A new question takes answers only after a moment (a person reads it first).
  await expect(page.getByRole('button', { name: 'Confirmar' })).toBeEnabled()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Contestação registrada e verificada')).toBeVisible()

  await expect(confirmation(page)).toBeFocused()
  await expect(confirmation(page)).toContainText('Bloquear o cartão')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Confirmar' })).toBeFocused()
  await expect(page.getByRole('button', { name: 'Confirmar' })).toBeEnabled()
  await page.keyboard.press('Enter')
  await expect(log(page)).toContainText('Bloqueei o cartão com final 2208.')
  await expect(page.getByText('Bloqueio do cartão verificado')).toBeVisible()
  // The customer can see it, not just read it once in the log: every movement on that card is
  // now tagged, and only those, for the rest of this session.
  await expect(page.getByText('Cartão bloqueado').first()).toBeVisible()
  const rows = page.locator('.transactions__row')
  await expect(rows.filter({ hasText: 'Cartão bloqueado' })).toHaveCount(
    await rows.filter({ hasText: 'final 2208' }).count(),
  )
  await expect(log(page)).toContainText('Conversa encerrada')
  await expect(composer(page)).toBeFocused()
})

test('Portuguese selection carries through login and a Spanish-profile demo', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Português' }).click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'pt-BR')
  await expect(page.locator('meta[name="description"]')).toHaveAttribute(
    'content',
    /Assistente de contestações de cartão/,
  )
  await expect(page.getByRole('heading', { name: 'Analise uma cobrança do seu cartão' })).toBeVisible()
  await expect(page.getByText('Escolha um cliente de teste')).toBeVisible()
  await expect(page.getByRole('radio', { name: /Andrés.*Espanhol/ })).toBeVisible()
  await page.getByRole('radio', { name: /Andrés/ }).check()
  await page.getByRole('button', { name: 'Solicitar código' }).click()
  await expect(page.getByRole('heading', { name: 'Código de acesso de Andrés' })).toBeVisible()
  await expect(page.getByText('Nesta demo não há SMS: o código aparece aqui embaixo.')).toBeVisible()
  await page.getByRole('button', { name: 'Usar código' }).click()
  await page.getByRole('button', { name: 'Entrar' }).click()

  await expect(page.getByRole('heading', { name: 'Olá, Andrés' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Cobrança duplicada/ })).toBeVisible()
  await page.getByRole('button', { name: /Cobrança duplicada/ }).click()
  await composer(page).press('Enter')
  await expect(log(page)).toContainText('Você reconhece esta transação?')
  await say(page, 'Não')
  await expect(confirmation(page)).toContainText('Abrir uma contestação')
  await expect(fact(page, 'Estabelecimento')).toContainText('RAPPI')
})

test('the chosen language controls an ambiguous first message', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Português' }).click()
  await page.getByRole('radio', { name: /Valentina/ }).check()
  await page.getByRole('button', { name: 'Solicitar código' }).click()
  await page.getByRole('button', { name: 'Usar código' }).click()
  await page.getByRole('button', { name: 'Entrar' }).click()

  await say(page, 'Hola')
  await expect(log(page)).toContainText('Posso ajudar com uma cobrança')
  await expect(page.locator('html')).toHaveAttribute('lang', 'pt-BR')
})

test('Spanish selection offers Rafael transactions in Spanish', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('radio', { name: /Rafael/ }).check()
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await page.getByRole('button', { name: 'Usar código' }).click()
  await page.getByRole('button', { name: 'Entrar' }).click()

  await expect(page.getByRole('heading', { name: 'Hola, Rafael' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Compra no reconocida/ })).toBeVisible()
  await page.getByRole('button', { name: /Compra no reconocida/ }).click()
  await composer(page).press('Enter')
  await expect(log(page)).toContainText('¿Reconoces este movimiento?')
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
  // The notice follows the language toggle.
  await page.getByRole('button', { name: 'Português' }).click()
  await expect(page.getByText('Sua sessão terminou. Entre de novo para continuar.')).toBeVisible()
})
