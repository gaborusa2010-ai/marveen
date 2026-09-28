import { describe, it, expect } from 'vitest'
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'

// Card 93d5d846: tool_call_log carried 691 rows and not one success=0. The
// diagnosis that survived measurement is not a mis-read field -- a Bash call
// that exits non-zero never reaches PostToolUse at all, so the table only ever
// saw wins. A column that cannot take the value 0 looks like a check and checks
// nothing.
//
// The harness does have an event for those calls (PostToolUseFailure, verified
// present in CC 2.1.263 and observed firing on a bare `exit 7`), and the hook
// now listens on it. The python suite guards BOTH halves: the verdict logic,
// and the registration -- because on 2026-09-06 the logic was not what was
// missing, the listener was. This wrapper makes that suite a CI gate.
const ROOT = join(__dirname, '..', '..')

describe('a failed tool call is recorded, not dropped', () => {
  it('the python suite passes (failure verdict, interrupt tagging, hook registration)', () => {
    const res = spawnSync('python3', [join(ROOT, 'scripts', '__tests__', 'tool-log-failure-capture.test.py')], {
      encoding: 'utf-8',
      timeout: 120_000,
    })
    if (res.status !== 0) {
      // Surface the python output so a failure names the exact case.
      console.error(res.stdout)
      console.error(res.stderr)
    }
    expect(res.status).toBe(0)
  })
})
