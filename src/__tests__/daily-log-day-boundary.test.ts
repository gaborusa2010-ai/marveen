// DAYSPLIT901 (Cody, 2026-09-01, from the day's diff review).
//
// The WRITER stamped the entry with the LOCAL calendar day and explained why in
// a comment; the READER computed a UTC day of its own. In BST that means between
// 00:00 and 01:00 a plain GET answers 200 with the PREVIOUS day, while entries
// written in that hour are already filed under the new one -- Anorak's shift.
// The fix is one exported helper both sides call, so this is a source contract
// test: it fails the moment either side grows its own day arithmetic again.
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = join(import.meta.dirname, '..', '..')
const ROUTE = readFileSync(join(ROOT, 'src/web/routes/daily-log.ts'), 'utf-8')
const DB = readFileSync(join(ROOT, 'src/db.ts'), 'utf-8')

describe('the daily log reader and writer agree on what "today" is', () => {
  it('exports ONE local-day helper and uses it on both sides', () => {
    expect(DB).toContain('export function localCalendarDay()')
    expect(DB).toContain('const today = localCalendarDay()')
    expect(ROUTE).toContain('localCalendarDay()')
  })

  it('neither side computes a UTC day of its own', () => {
    // `toISOString().split` is the exact shape that produced the bug.
    expect(ROUTE).not.toContain("toISOString().split('T')[0]")
  })

  it('the helper is timezone-anchored, not machine-locale dependent', () => {
    expect(DB).toContain("toLocaleDateString('en-CA', { timeZone: APP_TZ })")
  })
})
