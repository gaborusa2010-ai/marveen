// heredoc-scan.mjs against the shared case file (card #729).
//
// The JS scanner is the twin of scripts/hooks/heredoc_scan.py (destructive gate);
// scripts/__tests__/heredoc-scan.test.py checks the Python side against the SAME
// file, so the two cannot drift apart silently. Regenerate the expected values
// from Python with `python3 scripts/__tests__/heredoc-scan.test.py --write`.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
// @ts-expect-error -- plain .mjs module, no types
import { find } from '../../scripts/hooks/lib/heredoc-scan.mjs'

type Case = { cmd: string; heredocs: unknown[]; certain: boolean }
const cases: Case[] = JSON.parse(
  readFileSync(join(__dirname, '..', '..', 'scripts', '__tests__', 'heredoc-scan-cases.json'), 'utf-8'),
)

describe('heredoc scanner: JS reads every case exactly like the Python twin', () => {
  it('has the full case file', () => {
    expect(cases.length).toBeGreaterThanOrEqual(300)
  })

  it('matches every expected result', () => {
    const bad = cases.filter((c) => JSON.stringify(find(c.cmd)) !== JSON.stringify({ heredocs: c.heredocs, certain: c.certain }))
    expect(bad.map((c) => c.cmd)).toEqual([])
  })
})
