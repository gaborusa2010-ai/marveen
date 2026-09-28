/**
 * Backlog closure must not notify. Measured 2026-09-01.
 *
 * Closing a message creates a reverse 'pending' row so the delegator learns the
 * result without polling. Correct for a live task; self-defeating for a backlog
 * row. Real numbers from 37 closures: the fleet's unclosed rows rose 405 -> 425,
 * one agent was woken ten times in the same second, and every receipt then sat
 * unclosed itself. The cleanup produced more mess than it removed, so nobody
 * could safely clean up -- which is why the rows had piled up for days.
 *
 * The cutoff is the one the context-restart gate already uses to call dispatched
 * work stale, so "live" means the same thing in both places.
 */
import { describe, it, expect } from 'vitest'
import { shouldNotifyDelegator } from '../web/routes/messages.js'
import { MAIN_AGENT_ID } from '../config.js'
import { DEFAULT_STALE_CUTOFF_MS } from '../context-restart-gate.js'

const NOW = 1_788_000_000_000
const epochAgo = (ms: number) => Math.floor((NOW - ms) / 1000)

describe('shouldNotifyDelegator staleness rule', () => {
  it('still notifies for a freshly finished delegated task', () => {
    expect(shouldNotifyDelegator(MAIN_AGENT_ID, 'dex', 'csinald meg', epochAgo(60_000), NOW)).toBe(true)
  })

  it('stays silent for a row older than the staleness window', () => {
    expect(shouldNotifyDelegator(MAIN_AGENT_ID, 'dex', 'csinald meg', epochAgo(DEFAULT_STALE_CUTOFF_MS + 1000), NOW))
      .toBe(false)
  })

  it('notifies right up to the boundary, so "live" keeps its meaning', () => {
    // Exactly at the cutoff is still live: the gate treats > cutoff as stale.
    expect(shouldNotifyDelegator(MAIN_AGENT_ID, 'dex', 'csinald meg', epochAgo(DEFAULT_STALE_CUTOFF_MS), NOW))
      .toBe(true)
  })

  it('omitting the timestamp keeps the old behaviour', () => {
    // Callers that cannot supply created_at must not silently lose their notification.
    expect(shouldNotifyDelegator(MAIN_AGENT_ID, 'dex', 'csinald meg')).toBe(true)
  })

  it('the three original rules still hold, and outrank recency', () => {
    const fresh = epochAgo(60_000)
    expect(shouldNotifyDelegator(MAIN_AGENT_ID, MAIN_AGENT_ID, 'sajat magam', fresh, NOW)).toBe(false)
    expect(shouldNotifyDelegator('', 'dex', 'valami', fresh, NOW)).toBe(false)
    expect(shouldNotifyDelegator(MAIN_AGENT_ID, 'dex', '[Eredmény] msg_id:1 status:done', fresh, NOW)).toBe(false)
  })
})
