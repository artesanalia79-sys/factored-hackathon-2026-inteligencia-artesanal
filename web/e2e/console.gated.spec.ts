// The bank side behind the real access-code gate: this project's server runs with
// DEMO_ACCESS_CODE (T15), the same code the customer login asks for. Playwright's own `test`, as
// in console.spec.ts: the console is the one surface where customer ids are the point.
import { expect, test } from '@playwright/test'

test('the console refuses every read without the code, then opens with it', async ({ page }) => {
  const refused: number[] = []
  page.on('response', (response) => {
    if (response.url().includes('/api/console/')) refused.push(response.status())
  })
  await page.goto('/console')
  await expect(page.getByRole('alert')).toContainText('Código de acceso incorrecto o faltante.')
  await expect(page.getByRole('button', { name: /^Escalados \(0\)/ })).toBeVisible()
  expect(refused.length).toBeGreaterThan(0)
  expect(refused.every((code) => code === 401)).toBe(true)

  const field = page.getByLabel('Código de acceso (solo si el servidor lo pide)')
  await field.fill('not-the-demo-code')
  await page.getByRole('button', { name: 'Actualizar' }).click()
  await expect(page.getByRole('alert')).toContainText('Código de acceso incorrecto o faltante.')

  refused.length = 0
  await field.fill(process.env.E2E_ACCESS_CODE ?? '')
  await page.getByRole('button', { name: 'Actualizar' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^Escalados/ })).toBeEnabled()
  expect(refused.length).toBeGreaterThan(0)
  expect(refused.every((code) => code === 200)).toBe(true)
})
