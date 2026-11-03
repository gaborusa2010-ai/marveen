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
import { find, findAll } from '../../scripts/hooks/lib/heredoc-scan.mjs'

type HereString = { start: number; tailStart: number; lineEnd: number; text: string; dynamic: boolean }
type Case = { cmd: string; heredocs: unknown[]; certain: boolean; herestrings: HereString[]; certainAll: boolean }
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

  // #777: findAll adds the here-strings; the heredocs in it are find()'s, unchanged.
  it('reads every here-string like the Python find_all', () => {
    const pick = (r: Record<string, unknown>): HereString => ({
      start: r.start as number, tailStart: r.tailStart as number, lineEnd: r.lineEnd as number,
      text: r.text as string, dynamic: r.dynamic as boolean,
    })
    const bad = cases.filter((c) => {
      const all = findAll(c.cmd)
      const hs = all.records.filter((r: { kind: string }) => r.kind === 'herestring').map(pick)
      // a heredoc record carries no `kind` in JS (find()'s shape is unchanged); `kind ?? 'heredoc'` is the convention
      const hd = all.records.filter((r: { kind?: string }) => (r.kind ?? 'heredoc') === 'heredoc')
      return JSON.stringify(hs) !== JSON.stringify(c.herestrings) || all.certain !== c.certainAll ||
        JSON.stringify(hd) !== JSON.stringify(c.heredocs)
    })
    expect(bad.map((c) => c.cmd)).toEqual([])
    expect(cases.filter((c) => c.herestrings.length > 0).length).toBeGreaterThanOrEqual(30)
  })
})
