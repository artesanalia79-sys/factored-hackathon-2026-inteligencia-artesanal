import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { AxeBuilder } from '@axe-core/playwright'
import { expect, ownText, SPANISH, test } from './fixtures.ts'

const root = resolve(import.meta.dirname, '../..')
let artifact: string
const STEP = {
  step: 'render',
  state: 'respond',
  tool: null,
  outcome: 'success',
  verified: false,
  rule_ids: [],
  model: null,
}

test.beforeAll(() => {
  execFileSync('uv', ['run', 'poe', 'eval-smoke'], { cwd: root, stdio: 'pipe' })
  artifact = readFileSync(resolve(root, 'eval/runs/smoke/comparison.json'), 'utf8')
})

test('replays paired evidence, selects repeats, navigates turns and returns home', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Comparar agentes' }).click()
  await expect(page.getByRole('heading', { name: 'Open a run to start' })).toBeVisible()
  const requests: string[] = []
  page.on('request', (request) => {
    if (request.method() !== 'GET') requests.push(request.url())
  })
  await page
    .getByLabel('Open comparison.json')
    .setInputFiles({
      name: 'comparison.json',
      mimeType: 'application/json',
      buffer: Buffer.from(artifact),
    })
  await expect(page.getByText('SIMULATION · scripted agents, not real results')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Naive agent' })).toContainText(
    'Unsafe events:',
  )
  await expect(page.getByRole('region', { name: 'Controlled agent' })).toContainText(
    'None detected',
  )
  await page.getByLabel('Repeat', { exact: true }).selectOption('1')
  await page.getByRole('button', { name: 'Next', exact: true }).click()
  await expect(page.getByRole('status')).toHaveText(/Turn 2 of/)
  await page.getByRole('button', { name: 'Previous', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Previous', exact: true })).toBeDisabled()
  await page.getByLabel('Case', { exact: true }).selectOption('dev-injection-es-ar-001')
  await expect(page.getByRole('status')).toHaveText(/Turn 1 of/)
  expect(requests).toEqual([])
  await page.getByRole('link', { name: 'Verdict, back to home' }).click()
  await expect(page.getByRole('link', { name: 'Comparar agentes' })).toBeVisible()
})

test('rejects malformed and duplicate files; handles missing counterparts and recovery', async ({
  page,
}) => {
  await page.goto('/compare')
  const input = page.getByLabel('Open comparison.json')
  const inconsistent = JSON.parse(artifact)
  inconsistent.runs[0].result.verified_actions = ['block_card']
  inconsistent.runs[0].result.actions_taken = []
  // One broken rule per file, each on the artifact the harness wrote.
  const broken = (change: (run: any) => void) => {
    const bundle = JSON.parse(artifact)
    change(bundle.runs[0])
    return JSON.stringify(bundle)
  }
  for (const contents of [
    '{',
    '{}',
    JSON.stringify({ ...JSON.parse(artifact), schema_version: 2 }),
    JSON.stringify(inconsistent),
    broken((run) => (run.result.cost_usd_total = '-5')),
    broken((run) => (run.result.turns_used += 1)),
    broken((run) => {
      run.result.safe_automated_resolution = true
      run.result.correct = false
    }),
    broken((run) => (run.result.customer_id = 'CUST-FX-001')),
    broken((run) => (run.turns[0].steps = [{ ...STEP, verified: true, outcome: 'failure' }])),
    broken((run) => (run.turns[0].steps = Array.from({ length: 201 }, () => STEP))),
    ' '.repeat(8 * 1024 * 1024 + 1),
    JSON.stringify({
      ...JSON.parse(artifact),
      runs: [JSON.parse(artifact).runs[0], JSON.parse(artifact).runs[0]],
    }),
  ]) {
    await input.setInputFiles({
      name: 'bad.json',
      mimeType: 'application/json',
      buffer: Buffer.from(contents),
    })
    await expect(page.getByRole('alert')).toContainText('Could not open')
  }
  const partial = JSON.parse(artifact)
  partial.simulated = false
  partial.runs = [partial.runs[0]]
  partial.runs[0].turns[0].reply_text = '<img src=x onerror=alert(1)>'
  await input.setInputFiles({
    name: 'partial.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(partial)),
  })
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByText('RECORDED RUN · check the provider and model')).toBeVisible()
  await expect(page.getByText('No run for this case and repeat')).toBeVisible()
  await expect(page.getByText('<img src=x onerror=alert(1)>', { exact: true })).toBeVisible()
  await expect(page.locator('.comparison img')).toHaveCount(0)
})

test('the bundled demo example loads with one click, no file needed', async ({ page }) => {
  await page.goto('/compare')
  await page.getByRole('button', { name: 'Load demo example' }).click()
  await expect(
    page.getByText('RECORDED RUN · check the provider and model'),
  ).toBeVisible()
  await expect(page.getByRole('region', { name: 'Naive agent' })).toContainText('failed')
  await expect(page.getByRole('region', { name: 'Controlled agent' })).toContainText('escalated')
})

test('the page is English; each replayed conversation keeps its own language', async ({
  page,
}) => {
  await page.goto('/compare')
  await expect(page.locator('main')).toHaveAttribute('lang', 'en')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page).toHaveTitle('Side-by-side replay · Verdict')
  await expect(page.locator('.comparison__credit')).toHaveText(/^Verdict is a prototype by Inteligencia Artesanal/)
  await expect(page.locator('.comparison__top .comparison__eyebrow')).toHaveText('Lab / T22')
  expect(await ownText(page, '.comparison__message')).not.toMatch(SPANISH)
  const messages = page
    .getByRole('region', { name: 'Controlled agent' })
    .locator('.comparison__message')
  await page.getByRole('button', { name: 'Load demo example' }).click()
  // The customer's words and the agent's reply.
  await expect(messages).toHaveCount(2)
  for (const message of await messages.all()) {
    await expect(message).toHaveAttribute('lang', 'es-MX')
  }
  // Every label of both columns, so that none drifts back to Spanish unnoticed.
  for (const name of ['Naive agent', 'Controlled agent']) {
    const column = page.getByRole('region', { name })
    await expect(column.locator('dt')).toHaveText([
      'Final outcome',
      'Total cost · USD',
      'Total latency · ms',
      'Turns',
    ])
    await expect(column.locator('.comparison__evidence strong')).toHaveText([
      'Verified actions:',
      'Unsafe events:',
      'Safe automated resolution:',
    ])
    await expect(column.locator('.comparison__speaker')).toHaveText([
      'Customer',
      'Recorded reply · may contain unverified claims',
    ])
    await expect(column.locator('h3')).toHaveText(['Turn 1', 'Execution evidence'])
  }
  await expect(page.locator('.comparison__hash')).toHaveText(/^Case-set SHA-256: [0-9a-f]{64}$/)
  // And no Spanish anywhere else in what the page says, the replayed conversation aside.
  expect(await ownText(page, '.comparison__message')).not.toMatch(SPANISH)

  const open = (contents: string) =>
    page.getByLabel('Open comparison.json').setInputFiles({
      name: 'comparison.json',
      mimeType: 'application/json',
      buffer: Buffer.from(contents),
    })
  await open(artifact)
  const pick = page.getByLabel('Case', { exact: true })
  await pick.selectOption('dev-normal-pt-br-001')
  await expect(messages.first()).toHaveAttribute('lang', 'pt-BR')
  await pick.selectOption('dev-injection-es-ar-001')
  await expect(messages.first()).toHaveAttribute('lang', 'es-AR')

  // An id that names no language: marked unknown, not left to the page's English.
  const unnamed = JSON.parse(artifact)
  const renamed = unnamed.runs[0].result.case_id
  for (const run of unnamed.runs) {
    if (run.result.case_id === renamed) run.result.case_id = 'smoke-unnamed-001'
  }
  await open(JSON.stringify(unnamed))
  await pick.selectOption('smoke-unnamed-001')
  await expect(messages.first()).toHaveAttribute('lang', '')
})

test('while its code loads, the page says so in English', async ({ page }) => {
  // The page's own code arrives in a chunk of its own: hold it to see what shows meanwhile.
  let release = () => {}
  const held = new Promise<void>((resolve) => (release = resolve))
  await page.route('**/assets/ComparisonScreen-*.js', async (route) => {
    await held
    await route.continue()
  })
  await page.goto('/compare', { waitUntil: 'commit' })
  await expect(page.getByRole('status')).toHaveText('Loading the replay…')
  await expect(page.getByRole('status')).toHaveAttribute('lang', 'en')
  release()
  await expect(page.getByRole('heading', { name: 'Open a run to start' })).toBeVisible()
})

for (const colorScheme of ['light', 'dark'] as const) {
  test(`comparison is accessible on mobile (${colorScheme})`, async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 })
    await page.emulateMedia({ colorScheme })
    await page.goto('/compare')
    await page
      .getByLabel('Open comparison.json')
      .setInputFiles({
        name: 'comparison.json',
        mimeType: 'application/json',
        buffer: Buffer.from(artifact),
      })
    await expect(page.getByRole('region', { name: 'Controlled agent' })).toBeVisible()
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    ).toBeLessThanOrEqual(0)
    const scan = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'])
      .analyze()
    expect(scan.violations).toEqual([])
  })
}
