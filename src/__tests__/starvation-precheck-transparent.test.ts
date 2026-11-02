import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { initDatabase, getDb, appendTaskRun, countConsecutiveSkips } from '../db.js'

// #423 (f3c1c7dd): kvota-szam-frissites skips on purpose in its preCheck while
// its state file is fresh. Those quiet rounds were counted as starvation and
// paged a false alert at the 40th. A 'skipped-precheck' row is now transparent:
// it neither counts nor resets the busy/quota streak around it.

// Recent timestamps: appendTaskRun prunes rows older than its TTL on insert.
const BASE = Date.now() - 600_000

beforeAll(() => { initDatabase(':memory:') })
beforeEach(() => { getDb().prepare('DELETE FROM task_runs').run() })

function seq(statuses: string[]): void {
  let ts = BASE
  for (const st of statuses) {
    const id = appendTaskRun('t', 'a', st, st === 'fired' ? null : 'r-' + st)
    getDb().prepare('UPDATE task_runs SET ts = ? WHERE id = ?').run(ts, id)
    ts += 1000
  }
}

describe('countConsecutiveSkips treats skipped-precheck as transparent', () => {
  it('forty quiet preCheck rounds after a run are not starvation', () => {
    seq(['fired', ...Array(40).fill('skipped-precheck')])
    const r = countConsecutiveSkips('t', 'a')
    expect(r.skips).toBe(0)
    expect(r.lastFiredTs).toBe(BASE)
  })
  it('busy skips on both sides of a quiet round still add up', () => {
    seq(['fired', 'skipped', 'skipped-precheck', 'skipped', 'skipped'])
    expect(countConsecutiveSkips('t', 'a').skips).toBe(3)
  })
})

// #243 (dc80b854): a 'missed' row (catch-up too stale to run) is NOT a run.
describe('countConsecutiveSkips counts missed as a skip', () => {
  it('missed does not reset the streak or become the last fire', () => {
    seq(['fired', 'missed', 'skipped', 'skipped'])
    const r = countConsecutiveSkips('t', 'a')
    expect(r.skips).toBe(3)
    expect(r.lastFiredTs).toBe(BASE)
    expect(r.reasons).toContain('missed')
  })
})

// #243 review: the long-silence threshold must not treat a healthy
// preCheck-gated task, or a brand-new one, as silent for 8+ days.
describe('countConsecutiveSkips aliveRefTs', () => {
  it('a quiet preCheck round is proof of life', () => {
    seq(['fired', 'skipped', 'skipped', 'skipped-precheck', 'skipped-precheck'])
    const r = countConsecutiveSkips('t', 'a')
    expect(r.lastFiredTs).toBe(BASE)
    expect(r.aliveRefTs).toBe(BASE + 4000)
  })
  it('no run in the window: the oldest row, not infinity', () => {
    seq(['skipped', 'skipped'])
    const r = countConsecutiveSkips('t', 'a')
    expect(r.lastFiredTs).toBeNull()
    expect(r.aliveRefTs).toBe(BASE)
  })
  it('empty history: null', () => {
    expect(countConsecutiveSkips('t', 'a').aliveRefTs).toBeNull()
  })
})
