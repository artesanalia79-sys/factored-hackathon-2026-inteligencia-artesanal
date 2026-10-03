import { expect, log, say, test } from './fixtures.ts'

// This project's server runs with DEMO_ACCESS_CODE (T15): the gate is the real one.
test.use({ allowedConsoleErrors: /status of 403/ })

test('the real access-code gate refuses a login without the code, then lets the customer in', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('radio', { name: /Mariana/ }).check()
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await expect(page.getByRole('alert')).toContainText('Esta demo pide un código de acceso')
  const field = page.getByLabel('Código de acceso de la demo')
  await field.fill('not-the-demo-code')
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await expect(page.getByRole('alert')).toContainText('El código de acceso no es correcto.')

  await field.fill(process.env.E2E_ACCESS_CODE ?? '')
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await page.getByRole('button', { name: 'Usar código' }).click()
  await page.getByRole('button', { name: 'Entrar' }).click()
  await say(page, 'Tengo un cargo de 2,450 pesos en ELECTROMUNDO que no reconozco')
  await expect(log(page)).toContainText('¿Reconoces este movimiento?')
})
