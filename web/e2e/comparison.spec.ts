import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { AxeBuilder } from '@axe-core/playwright'
import { expect, test } from './fixtures.ts'

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
  await expect(page.getByRole('heading', { name: 'Abre una ejecución para empezar' })).toBeVisible()
  const requests: string[] = []
  page.on('request', (request) => {
    if (request.method() !== 'GET') requests.push(request.url())
  })
  await page
    .getByLabel('Abrir comparison.json')
    .setInputFiles({
      name: 'comparison.json',
      mimeType: 'application/json',
      buffer: Buffer.from(artifact),
    })
  await expect(page.getByText('SIMULACIÓN · agentes con guion, no resultados reales')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Agente ingenuo' })).toContainText(
    'Eventos inseguros:',
  )
  await expect(page.getByRole('region', { name: 'Agente controlado' })).toContainText(
    'Ninguno detectado',
  )
  await page.getByLabel('Repetición', { exact: true }).selectOption('1')
  await page.getByRole('button', { name: 'Siguiente', exact: true }).click()
  await expect(page.getByRole('status')).toHaveText(/Turno 2 de/)
  await page.getByRole('button', { name: 'Anterior', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Anterior', exact: true })).toBeDisabled()
  await page.getByLabel('Caso', { exact: true }).selectOption('dev-injection-es-ar-001')
  await expect(page.getByRole('status')).toHaveText(/Turno 1 de/)
  expect(requests).toEqual([])
  await page.getByRole('link', { name: 'Volver al inicio' }).click()
  await expect(page.getByRole('link', { name: 'Comparar agentes' })).toBeVisible()
})

test('rejects malformed and duplicate files; handles missing counterparts and recovery', async ({
  page,
}) => {
  await page.goto('/compare')
  const input = page.getByLabel('Abrir comparison.json')
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
    await expect(page.getByRole('alert')).toContainText('No se pudo abrir')
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
  await expect(page.getByText('EJECUCIÓN REGISTRADA · consulta el proveedor y modelo')).toBeVisible()
  await expect(page.getByText('Sin ejecución para este caso y repetición')).toBeVisible()
  await expect(page.getByText('<img src=x onerror=alert(1)>', { exact: true })).toBeVisible()
  await expect(page.locator('.comparison img')).toHaveCount(0)
})

for (const colorScheme of ['light', 'dark'] as const) {
  test(`comparison is accessible on mobile (${colorScheme})`, async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 })
    await page.emulateMedia({ colorScheme })
    await page.goto('/compare')
    await page
      .getByLabel('Abrir comparison.json')
      .setInputFiles({
        name: 'comparison.json',
        mimeType: 'application/json',
        buffer: Buffer.from(artifact),
      })
    await expect(page.getByRole('region', { name: 'Agente controlado' })).toBeVisible()
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
