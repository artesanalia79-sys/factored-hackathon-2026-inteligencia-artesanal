// Unlike every other spec, this one does not import `test`/`expect` from `./fixtures.ts`: that
// fixture fails any test whose network traffic contains `customer_id` or `CUST-` (the customer
// chat must never see either). The console (T21) is the bank side, where seeing both across
// customers is the whole point, so it is exempt from that check by using Playwright's own `test`
// instead. The helpers it reuses (`signIn`, `say`, `composer`, `log`, `ownText`) are plain
// functions and carry no such restriction.
import { AxeBuilder } from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { composer, log, ownText, say, signIn, SPANISH } from './fixtures.ts'

test('a handoff a customer caused shows up on the bank side, with its trace and rules', async ({
  page,
}) => {
  // Reuses Carlos's own escalation flow (chat.spec.ts): a second run just adds a second handoff,
  // since handoffs carry no per-transaction uniqueness the way disputes and card blocks do.
  await signIn(page, 'Carlos')
  await page.getByRole('button', { name: /Cargo de alto riesgo/ }).click()
  await composer(page).press('Enter')
  await expect(log(page)).toContainText('¿Reconoces este movimiento?')
  await say(page, 'No fui yo')
  await expect(log(page)).toContainText(/revisión humana con referencia HND-/)
  const reference = (await log(page).textContent()) ?? ''
  const handoffId = reference.match(/HND-[A-Za-z0-9-]+/)?.[0]
  expect(handoffId).toBeTruthy()

  await page.goto('/console')
  await expect(page.getByRole('heading', { name: /What the bank sees/ })).toBeVisible()
  // English, not the customer's language: the page, the document and the tab title.
  await expect(page.locator('main')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page).toHaveTitle('Bank-side console · Verdict')
  await expect(page.locator('.console__credit')).toHaveText(/^Verdict is a prototype by Inteligencia Artesanal/)
  const handoffsTab = page.getByRole('button', { name: /^Escalated/ })
  await expect(handoffsTab).toBeVisible()
  // The customer's name is shown next to their id, and the case has a date/time. chat.spec.ts
  // gives Carlos an escalation too, in the same shared server, so more than one row can match.
  await expect(page.getByText('Carlos · CUST-FX-006').first()).toBeVisible()
  // With an English month ("5 Oct 2026, 14:03 UTC"); Spanish writes it in lower case ("5 oct").
  await expect(
    page.getByText(/\d{1,2} [A-Z][a-z]{2,3} \d{4}, \d{2}:\d{2} UTC/).first(),
  ).toBeVisible()
  // No Spanish anywhere else in what the page says, customers' names aside.
  expect(await ownText(page, 'strong, .console__meta')).not.toMatch(SPANISH)

  await page.getByRole('button', { name: new RegExp(`CUST-FX-006`) }).first().click()
  await expect(page.getByRole('heading', { name: new RegExp(handoffId!) })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Rules that triggered the case' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Execution trace' })).toBeVisible()
  await expect(page.locator('.console__steps li').first()).toBeVisible()
  // The rule that decided is tagged, and each step says whether it was verified.
  await expect(page.locator('.console__rule-tag').first()).toHaveText('Applied in this case')
  await expect(page.locator('.console__steps li').first()).toContainText(/Verified|Not verified/)
  expect(await ownText(page, 'strong, .console__meta')).not.toMatch(SPANISH)

  // The other two tabs render without error, whatever other specs left behind.
  await page.getByRole('button', { name: /^Disputes/ }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: /^Blocked cards/ }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('link', { name: 'Verdict, back to home' }).click()
  await expect(page.getByRole('link', { name: 'Lado del banco' })).toBeVisible()
})

test('a wrong or missing access code is refused, not a silent empty page', async ({ page }) => {
  await page.goto('/console')
  // The ungated server (port 8765) accepts no header at all here; the point of this test is the
  // UI's handling of the 401 shape. console.gated.spec.ts runs the real gate on its server.
  await page.route('**/api/console/**', (route) =>
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: { error: 'access_code_required' } }),
    }),
  )
  await page.getByRole('button', { name: 'Refresh' }).click()
  await expect(page.getByRole('alert')).toContainText('Incorrect or missing access code.')
})

test('a dispute and a card block read in English, dates included', async ({ page }) => {
  // Fixed rows (invented values), whatever the other specs left on this shared server.
  const customer = { customer_id: 'CUST-FX-990', customer_name: 'Valentina' }
  const dispute = {
    ...customer,
    case: {
      dispute_id: 'DSP-E2E-0001',
      reason: 'unrecognized',
      status: 'submitted',
      amount: '250.00',
      currency: 'COP',
      created_at: '2026-10-04T08:05:00Z',
      sla_due_date: '2026-11-03',
    },
    rule_explanations: [
      { rule_id: 'DSP-ACT-01', description: 'The rule that decided.', decisive: true },
      { rule_id: 'DSP-ELIG-01', description: 'A rule that was checked.', decisive: false },
    ],
  }
  const block = {
    ...customer,
    event: {
      block_id: 'BLK-E2E-0001',
      card_last4: '4321',
      reason: 'Customer did not recognize a disputed charge',
      blocked_at: '2026-10-04T08:07:00Z',
    },
  }
  await page.route('**/api/console/disputes', (route) => route.fulfill({ json: [dispute] }))
  await page.route('**/api/console/card-blocks', (route) => route.fulfill({ json: [block] }))
  await page.goto('/console')

  await page.getByRole('button', { name: 'Disputes (1)' }).click()
  const row = page.locator('.console__row')
  await expect(row).toContainText('unrecognized · submitted · 250.00 COP · 4 Oct 2026, 08:05 UTC')
  await expect(row).toContainText('SLA due date: 2026-11-03')
  await expect(row.locator('.console__rule-tag')).toHaveText('Applied in this case')
  expect(await ownText(page, 'strong, .console__meta')).not.toMatch(SPANISH)
  await page.getByRole('button', { name: 'Blocked cards (1)' }).click()
  await expect(row).toContainText(
    'Card •••• 4321 · Customer did not recognize a disputed charge · 4 Oct 2026, 08:07 UTC',
  )
  expect(await ownText(page, 'strong, .console__meta')).not.toMatch(SPANISH)
})

test('while its code loads, the console says so in English', async ({ page }) => {
  // The page's own code arrives in a chunk of its own: hold it to see what shows meanwhile.
  let release = () => {}
  const held = new Promise<void>((resolve) => (release = resolve))
  await page.route('**/assets/ConsoleScreen-*.js', async (route) => {
    await held
    await route.continue()
  })
  await page.goto('/console', { waitUntil: 'commit' })
  await expect(page.getByRole('status')).toHaveText('Loading the console…')
  await expect(page.getByRole('status')).toHaveAttribute('lang', 'en')
  release()
  await expect(page.getByRole('heading', { name: /What the bank sees/ })).toBeVisible()
})

for (const colorScheme of ['light', 'dark'] as const) {
  test(`the console is accessible on a phone (${colorScheme})`, async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 })
    await page.emulateMedia({ colorScheme })
    await page.goto('/console')
    await expect(page.getByRole('button', { name: 'Refresh' })).toBeEnabled()
    // Each tab, with whatever cases the other specs left on this shared server.
    for (const tab of [/^Escalated/, /^Disputes/, /^Blocked cards/]) {
      await page.getByRole('button', { name: tab }).click()
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        ),
      ).toBeLessThanOrEqual(0)
      const scan = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze()
      expect(scan.violations).toEqual([])
    }
  })
}
