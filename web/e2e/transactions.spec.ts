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
