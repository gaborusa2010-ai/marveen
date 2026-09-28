// STARVATION901 (card a8f8aa54).
//
// The memoria-heartbeat was skipped 101 times across FIVE DAYS in silence. Every
// individual skip was correct: skipIfBusy drops a short-cadence tick on purpose,
// because queueing 30-minute heartbeats produces spurious "waiting 60 minutes"
// alerts during a live conversation. Nothing was wrong with any one decision --
// nobody was counting them. A task that never runs looks exactly like a task
// with nothing to report.
//
// These tests pin the repeat-protection arithmetic, which is where this kind of
// alert usually goes wrong in one of two ways: it fires once and then lets a
// still-starving task fall silent again (the original bug in a new hat), or it
// fires on every tick and gets muted.
import { describe, it, expect } from 'vitest'
import { isStarvationAlertPoint } from '../web/schedule-runner.js'

describe('starvation alert: when to speak', () => {
  it('stays quiet below the threshold', () => {
    for (const n of [0, 1, 3, 9]) expect(isStarvationAlertPoint(n)).toBe(false)
  })

  it('fires exactly at the threshold', () => {
    expect(isStarvationAlertPoint(10)).toBe(true)
  })

  it('then fires only at doublings, so it cannot become noise', () => {
    expect(isStarvationAlertPoint(20)).toBe(true)
    expect(isStarvationAlertPoint(40)).toBe(true)
    expect(isStarvationAlertPoint(80)).toBe(true)
    expect(isStarvationAlertPoint(160)).toBe(true)
    for (const n of [11, 15, 21, 39, 41, 79, 100]) expect(isStarvationAlertPoint(n)).toBe(false)
  })

  it('would have spoken up during the real incident, and more than once', () => {
    // 101 skips over five days: the alert points crossed on the way there were
    // 10, 20, 40 and 80. Four chances to notice, against the nought we got.
    const points = Array.from({ length: 101 }, (_, i) => i + 1).filter((n) => isStarvationAlertPoint(n))
    expect(points).toEqual([10, 20, 40, 80])
  })

  it('an explicit threshold still doubles from ITS OWN base, not from 10', () => {
    expect(isStarvationAlertPoint(3, 3)).toBe(true)
    expect(isStarvationAlertPoint(6, 3)).toBe(true)
    expect(isStarvationAlertPoint(12, 3)).toBe(true)
    expect(isStarvationAlertPoint(10, 3)).toBe(false)
  })
})
