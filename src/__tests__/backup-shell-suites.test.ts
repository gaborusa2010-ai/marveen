import { describe, it, expect } from 'vitest'
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'

// Two bash suites guard scripts/backup.sh, and until now nothing executed
// either of them: they were runnable by hand, which is the same as unrun.
// Both exist because the archive has twice looked healthy while missing the
// only copy of something -- the roxy raster exclusion silently dropping 321 kB
// of written material (2026-09-04), and the repo root being an allowlist that
// never named src/ or docs/ (2026-09-06, 44 files in no copy at all).
//
// A backup test that is not a CI gate is exactly the failure it is testing for.
const ROOT = join(__dirname, '..', '..')

const SUITES = [
  ['the roxy exclusion stays raster-only', 'backup-roxy-exclusion.test.sh'],
  ['the repo root is swept, not listed', 'backup-repo-root-sweep.test.sh'],
] as const

describe('backup.sh keeps its measured guarantees', () => {
  for (const [name, script] of SUITES) {
    it(name, () => {
      const res = spawnSync('bash', [join(ROOT, 'scripts', '__tests__', script)], {
        encoding: 'utf-8',
        timeout: 120_000,
      })
      if (res.status !== 0) {
        console.error(res.stdout)
        console.error(res.stderr)
      }
      expect(res.status).toBe(0)
    })
  }
})
