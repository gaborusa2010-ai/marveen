// Contract tests for the governance-file audit trail (AUTONOMYVAK901).
//
// The security review found that store/autonomy-config.json -- the file that
// defines what every agent is allowed to do on its own -- could be rewritten on
// disk without leaving a single audit row. Two independent reasons, and the
// second is the one that makes the obvious fix a no-op:
//
//   1. The filename is on the store-watcher's SYSTEM_FILES denylist.
//   2. The watcher logs CREATIONS only. `change` events return early, and a
//      rewrite of an existing path is dropped by the knownFiles check. So even
//      off the denylist, an EDIT would still have gone unrecorded.
//
// These tests pin the behaviour that matters: an edit is logged as 'modify', a
// touch that changes nothing is not logged, and ordinary store files are not
// dragged into the audit by the new branch.

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { writeFileSync, mkdirSync, rmSync, utimesSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { initDatabase, getRecentStoreFileEvents } from '../db.js'
import { STORE_DIR } from '../config.js'
import { startStoreWatcher, stopStoreWatcher, setStoreWriteActor } from '../store-watcher.js'

const GOV = join(STORE_DIR, 'autonomy-config.json')
const PLAIN = join(STORE_DIR, 'watcher-plain-probe.json')

function write(path: string, body: unknown): void {
  writeFileSync(path, JSON.stringify(body))
}

/** fs.watch is asynchronous: poll until the predicate holds or time runs out. */
async function waitFor(fn: () => boolean, ms = 3000): Promise<boolean> {
  const deadline = Date.now() + ms
  while (Date.now() < deadline) {
    if (fn()) return true
    await new Promise((r) => setTimeout(r, 25))
  }
  return fn()
}

function eventsFor(rel: string): Array<{ event_type: string; agent: string | null }> {
  return getRecentStoreFileEvents(200)
    .filter((e) => e.rel_path === rel)
    .map((e) => ({ event_type: e.event_type, agent: e.agent }))
}

describe('store-watcher: governance file audit', () => {
  beforeEach(async () => {
    mkdirSync(STORE_DIR, { recursive: true })
    // The file must already exist when the watcher starts: that is the real
    // situation, and it is what makes the first edit a modification.
    write(GOV, { categories: [{ key: 'kanban_archive_done', level: 3 }] })
    rmSync(PLAIN, { force: true })
    initDatabase(':memory:')
    startStoreWatcher()
    // Give the watcher a moment to attach before the first write.
    await new Promise((r) => setTimeout(r, 100))
  })

  afterEach(() => {
    stopStoreWatcher()
    rmSync(PLAIN, { force: true })
  })

  it('logs a direct edit of autonomy-config.json as a modification', async () => {
    write(GOV, { categories: [{ key: 'kanban_archive_done', level: 1 }] })

    const seen = await waitFor(() => eventsFor('autonomy-config.json').length > 0)
    expect(seen).toBe(true)

    const rows = eventsFor('autonomy-config.json')
    expect(rows).toHaveLength(1)
    expect(rows[0].event_type).toBe('modify')
  })

  it('stores a null agent for a direct disk write (the unattributed case)', async () => {
    write(GOV, { categories: [{ key: 'egress_allowlist', level: 2 }] })

    await waitFor(() => eventsFor('autonomy-config.json').length > 0)
    expect(eventsFor('autonomy-config.json')[0].agent).toBeNull()
  })

  it('attributes the change when a route handler set the actor slot', async () => {
    setStoreWriteActor('marveen')
    write(GOV, { categories: [{ key: 'egress_allowlist', level: 3 }] })

    await waitFor(() => eventsFor('autonomy-config.json').length > 0)
    expect(eventsFor('autonomy-config.json')[0].agent).toBe('marveen')
  })

  it('does NOT log a touch that leaves the content identical', async () => {
    const when = new Date()
    utimesSync(GOV, when, when)

    // Nothing should appear. Give the watcher the same budget a real event gets.
    await new Promise((r) => setTimeout(r, 600))
    expect(eventsFor('autonomy-config.json')).toHaveLength(0)
  })

  it('logs each distinct edit once, and repeated identical writes only once', async () => {
    write(GOV, { categories: [{ key: 'a', level: 1 }] })
    await waitFor(() => eventsFor('autonomy-config.json').length >= 1)

    // Same bytes again: fs fires, content hash is unchanged -> no second row.
    write(GOV, { categories: [{ key: 'a', level: 1 }] })
    await new Promise((r) => setTimeout(r, 400))
    expect(eventsFor('autonomy-config.json')).toHaveLength(1)

    // Genuinely different bytes -> a second row.
    write(GOV, { categories: [{ key: 'a', level: 2 }] })
    const second = await waitFor(() => eventsFor('autonomy-config.json').length >= 2)
    expect(second).toBe(true)
    expect(eventsFor('autonomy-config.json')).toHaveLength(2)
  })

  it('still ignores an ordinary system file, and still logs a plain new file', async () => {
    // A denylisted runtime file must stay out of the audit.
    write(join(STORE_DIR, 'schedule-last-run.json'), { t: Date.now() })
    // A normal agent-created file must still be logged as a creation.
    write(PLAIN, { probe: true })

    const seen = await waitFor(() => eventsFor('watcher-plain-probe.json').length > 0)
    expect(seen).toBe(true)
    expect(eventsFor('watcher-plain-probe.json')[0].event_type).toBe('create')
    expect(eventsFor('schedule-last-run.json')).toHaveLength(0)
  })

  it('the governance file is still on the denylist (the fix is the branch, not the list)', async () => {
    // Guard against a future "simplification" that just drops the name from
    // SYSTEM_FILES: that alone would not log edits, only creations.
    expect(existsSync(GOV)).toBe(true)
    write(GOV, { categories: [{ key: 'z', level: 1 }] })
    await waitFor(() => eventsFor('autonomy-config.json').length > 0)
    expect(eventsFor('autonomy-config.json')[0].event_type).toBe('modify')
  })
})
