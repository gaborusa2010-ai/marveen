// DIARYSPLIT901 (2026-09-04). Two mechanisms wrote something called "napi
// naplo" into two different tables, and the endpoint everyone measures with
// could only see one of them.
//
// The nightly digest saved into `memories` with a `[Napi naplo <date>]` prefix.
// Every agent, the dashboard, and /api/daily-log/dates use `daily_logs`. So
// /api/daily-log/dates reported ZERO diary entries for marveen on 2026-09-03,
// an agent concluded "the manual fix did not last a day", and I passed that on
// as confirmed. The entry existed -- 1500+ characters, written at 23:01, in the
// other table. Measured that night: 13 digests in `memories` (all marveen, all
// around 23:00, since 08-22) against 117 rows in `daily_logs`.
//
// This is a SOURCE contract test on purpose. runDailyDigest calls out to a
// sub-agent, so exercising it end-to-end would mean running a model to assert a
// storage decision. What has to stay true is narrower and permanent: the digest
// writes to daily_logs, and nothing re-introduces a second diary under the same
// name.
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = join(import.meta.dirname, '..', '..')
const MEMORY = readFileSync(join(ROOT, 'src/memory.ts'), 'utf-8')

// The digest body: from where it is produced to where the function returns.
const DIGEST = MEMORY.slice(MEMORY.indexOf('const digest = text.trim()'))

describe('the nightly digest files into the same table agents write to', () => {
  it('appends to daily_logs', () => {
    expect(MEMORY).toContain('appendDailyLog')
    expect(DIGEST).toContain('appendDailyLog(MAIN_AGENT_ID, digest)')
  })

  it('does NOT write the digest into the memories table as well', () => {
    // Writing to both would be worse than the original bug: the recall index
    // reads daily_logs AND memories, so every night would appear twice.
    expect(DIGEST).not.toContain('saveMemory(')
  })

  it('no longer stamps a diary entry with the "[Napi naplo ...]" prefix', () => {
    // That prefix was the marker of the wrong-table write. If it comes back,
    // something is filing diary entries into memories again -- and the endpoint
    // will go blind to them again in exactly the same way.
    expect(MEMORY).not.toContain('[Napi naplo ${today}]')
  })

  it('files under the main agent id, not a chat id', () => {
    // daily_logs is keyed by agent, memories by chat. Passing a chat id here
    // would file the fleet's diary under an id no dashboard view looks up.
    expect(MEMORY).toContain("import { MAIN_AGENT_ID } from './config.js'")
  })
})
