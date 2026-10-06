import { AxeBuilder } from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { confirmation, expect, say, signIn, test } from './fixtures.ts'

// Automated WCAG 2.2 A/AA checks catch part of the problems only; a manual pass with a screen
// reader is still needed (docs/rules/web.md).
async function violations(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  return result.violations.map((violation) => ({
    id: violation.id,
    nodes: violation.nodes.map((node) => node.target.join(' ')),
  }))
}

for (const colorScheme of ['light', 'dark'] as const) {
  test(`no WCAG A/AA violation on any screen (${colorScheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' })
    await page.goto('/')
    await expect(page.getByRole('radio', { name: /Andrés/ })).toBeVisible()
    expect(await violations(page)).toEqual([])

    await page.getByRole('radio', { name: /Andrés/ }).check()
    await page.getByRole('button', { name: 'Enviar código' }).click()
    await expect(page.getByRole('button', { name: 'Usar código' })).toBeVisible()
    expect(await violations(page)).toEqual([])

    await page.getByRole('button', { name: 'Usar código' }).click()
    await page.getByRole('button', { name: 'Entrar' }).click()
    await expect(page.getByRole('heading', { name: 'Hola, Andrés' })).toBeVisible()
    expect(await violations(page)).toEqual([])

    if (colorScheme === 'light') {
      // One persona reaches the confirmation panel; the dark pass checks the screens above.
      await say(page, 'Me cobraron dos veces 85.900 pesos en Rappi')
      await say(page, 'Sí')
      await expect(confirmation(page)).toBeVisible()
      expect(await violations(page)).toEqual([])
    }
  })
}

test('the chat fits a phone screen without sideways scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 })
  await signIn(page, 'Diego')
  await say(page, 'No reconozco un cargo de UBER EATS por 560 pesos')
  await say(page, 'No')
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)
  expect(await violations(page)).toEqual([])
})

test('the top bars keep the product name at 320 px without sideways scrolling', async ({ page }) => {
  // WCAG 2.2 SC 1.4.10 (Reflow) is checked at 320 CSS px; the longest persona name is the worst case.
  await page.setViewportSize({ width: 320, height: 640 })
  const overflow = () =>
    page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  await page.goto('/')
  await page.getByRole('button', { name: 'Português' }).click()
  await expect(page.locator('.topbar .brand__name')).toBeVisible()
  expect(await overflow()).toBeLessThanOrEqual(0)
  await page.getByRole('button', { name: 'Español' }).click()
  await signIn(page, 'Valentina')
  await expect(page.locator('.topbar .brand__name')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cerrar sesión' })).toBeVisible()
  expect(await overflow()).toBeLessThanOrEqual(0)
})
