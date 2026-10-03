import { expect, test } from './fixtures.ts'

// The access-code gate belongs to the deployment (T15, `DEMO_ACCESS_CODE`). Here the browser
// plays it: logins without the right code get 403 access_code_required, and the one with it
// reaches the real server.
test.use({ allowedConsoleErrors: /status of 403/ })

const SHARED_CODE = 'shared-demo-code'

test('a demo with an access code asks for it once and remembers it', async ({ page }) => {
  const presented: (string | undefined)[] = []
  await page.route('**/api/auth/login', async (route) => {
    const body = route.request().postDataJSON() as { access_code?: string }
    presented.push(body.access_code)
    if (body.access_code !== SHARED_CODE) {
      await route.fulfill({ status: 403, json: { detail: { error: 'access_code_required' } } })
      return
    }
    await route.continue()
  })

  await page.goto('/')
  await expect(page.getByLabel('Código de acceso de la demo')).toHaveCount(0)
  await page.getByRole('radio', { name: /Sofía/ }).check()
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await expect(page.getByRole('alert')).toContainText('Esta demo pide un código de acceso')
  const field = page.getByLabel('Código de acceso de la demo')
  await expect(field).toBeFocused()
  await expect(field).toHaveAttribute('type', 'password')

  await field.fill('wrong-code-1')
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await expect(page.getByRole('alert')).toContainText('El código de acceso no es correcto.')
  await expect(field).toHaveAttribute('aria-invalid', 'true')

  await field.fill(SHARED_CODE)
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await page.getByRole('button', { name: 'Usar código' }).click()
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('heading', { name: 'Hola, Sofía' })).toBeVisible()
  expect(presented).toEqual([undefined, 'wrong-code-1', SHARED_CODE])

  // Signing in again in the same tab does not ask a second time.
  await page.getByRole('button', { name: 'Cerrar sesión' }).click()
  await page.getByRole('radio', { name: /Sofía/ }).check()
  await page.getByRole('button', { name: 'Enviar código' }).click()
  await expect(page.getByRole('button', { name: 'Usar código' })).toBeVisible()
  expect(presented.at(-1)).toBe(SHARED_CODE)
})
