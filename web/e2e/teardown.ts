import { readdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

/** Remove this run's operational stores (synthetic data only) once the run is over. */
export default async function teardown() {
  const run = process.env.E2E_RUN
  if (run === undefined) return
  const prefix = `bankagent-e2e-${run}`
  for (const name of await readdir(tmpdir())) {
    if (name.startsWith(prefix)) await rm(join(tmpdir(), name), { force: true })
  }
}
