import { composer, expect, log, signIn, test } from './fixtures.ts'

test('the signed-in customer sees only their movements and can ask about one', async ({ page }) => {
  await signIn(page, 'Mariana')
  const list = page.getByRole('region', { name: 'Tus movimientos recientes' })
  await expect(list.getByText('ELECTROMUNDO ONLINE')).toBeVisible()
  await expect(list.getByText('GAMESTORE DIGITAL')).toHaveCount(0)
  await expect(list.getByText('Ref. TXN-FX-0101')).toBeVisible()
  await list.getByRole('button', { name: /Preguntar por este movimiento: ELECTROMUNDO ONLINE/ }).click()
  await expect(composer(page)).toHaveValue(
    /transacción TXN-FX-0101 en ELECTROMUNDO ONLINE, por .*MXN.* del /,
  )
  await composer(page).press('Enter')
  await expect(log(page).locator('.msg--customer')).toContainText('TXN-FX-0101 en ELECTROMUNDO ONLINE')
  await expect(log(page).locator('.msg--agent')).toContainText('¿Reconoces este movimiento?')

  await page.getByRole('button', { name: 'Cerrar sesión' }).click()
  await page.getByRole('button', { name: 'Português' }).click()
  await page.getByRole('radio', { name: /Rafael/ }).check()
  await page.getByRole('button', { name: 'Solicitar código' }).click()
  await page.getByRole('button', { name: 'Usar código' }).click()
  await page.getByRole('button', { name: 'Entrar' }).click()
  const rafaelList = page.getByRole('region', { name: 'Suas transações recentes' })
  await expect(rafaelList.getByText('GAMESTORE DIGITAL')).toBeVisible()
  await expect(rafaelList.getByText('ELECTROMUNDO ONLINE')).toHaveCount(0)
  await rafaelList.getByRole('button', { name: /Perguntar sobre esta transação: GAMESTORE DIGITAL/ }).click()
  await expect(composer(page)).toHaveValue(
    /transação TXN-FX-0401 em GAMESTORE DIGITAL, no valor de .*ARS.* de /,
  )
})

test('an amount is shown and asked about with its cents, in a currency Intl prints without', async ({ page }) => {
  // Colombian pesos: Intl's default is no decimals, which rounds 85,900.50 to "85.901 COP". The
  // question would then name an amount the customer never paid and the agent cannot find.
  await signIn(page, 'Andrés')
  const list = page.getByRole('region', { name: 'Tus movimientos recientes' })
  await expect(list.getByText('RAPPI*RESTAURANTE').first()).toBeVisible()
  const amounts = await list.locator('.transactions__amount').allTextContents()
  expect(amounts.length).toBeGreaterThan(1)
  for (const amount of amounts) expect(amount).toMatch(/^\d{1,3}(\.\d{3})*,\d{2}\sCOP$/)
  expect(amounts[0]).toMatch(/^85\.900,00\sCOP$/)

  await list.getByRole('button', { name: /Preguntar por este movimiento: RAPPI/ }).first().click()
  await expect(composer(page)).toHaveValue(/ en RAPPI\*RESTAURANTE, por 85\.900,00\sCOP, del /)
  await composer(page).press('Enter')
  await expect(log(page).locator('.msg--agent')).toContainText('importe 85.900,00 COP')
})
