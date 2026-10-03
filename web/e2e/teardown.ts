import { readdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const PREFIX = 'bankagent-e2e-'

/**
 * Remove the operational stores (synthetic data only) once the run is over. Playwright runs this
 * before it stops the API servers, which still hold this run's files: Linux and macOS remove an
 * open file, Windows refuses (EBUSY). There, this run's files stay until the next run's teardown,
 * which removes every store it can, the earlier runs' included; one still open is skipped.
 */
export default async function teardown() {
  const run = process.env.E2E_RUN
  if (run === undefined) return
  // Elsewhere, only this run's: removing a file another run still uses would not fail there.
  const prefix = process.platform === 'win32' ? PREFIX : `${PREFIX}${run}`
  for (const name of await readdir(tmpdir())) {
    if (!name.startsWith(prefix)) continue
    try {
      await rm(join(tmpdir(), name), { force: true })
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code
      if (code !== 'EBUSY' && code !== 'EPERM') throw error
    }
  }
}
