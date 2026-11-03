import { describe, it, expect } from 'vitest'
import { hotThresholdPct } from '../web/context-guard-runner.js'

// #641: a pinned window is too small for a 5-minute sweep: a busy stretch
// crosses the act and hard tiers between two sweeps, so a pinned agent is
// always on the hot (1-minute) cadence.
describe('hotThresholdPct', () => {
  it('no pinned limit keeps the 80% default', () => {
    expect(hotThresholdPct(null)).toBe(0.8)
    expect(hotThresholdPct(0)).toBe(0.8)
  })

  it("a pinned agent is always hot, also a fresh session right after its first reading", () => {
    // 2026-10-25 07:29: ~109k (73% of marveen's 150k) was not hot under the 80% rule;
    // 2026-11-02 18:00:51: a fresh session (~60k = 40%) fell off the hot cadence under the 60k headroom rule.
    const hot = hotThresholdPct(150_000)
    expect(hot).toBe(0)
    expect(60_000 / 150_000).toBeGreaterThanOrEqual(hot)
    expect(hotThresholdPct(250_000)).toBe(0)
  })
})
