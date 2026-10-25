import { describe, it, expect } from 'vitest'
import { hotThresholdPct } from '../web/context-guard-runner.js'

// #641: the hot cadence starts a fixed token headroom below the hard tier, so a
// small pinned window is not left on the 5-minute sweep until 80%.
describe('hotThresholdPct', () => {
  it('no pinned limit keeps the 80% default', () => {
    expect(hotThresholdPct(null, 0.97)).toBe(0.8)
  })

  it("marveen's pinned 150k is hot 60k below the hard tier", () => {
    // 2026-10-25 07:29: ~109k (73%) was not hot under the old rule.
    const hot = hotThresholdPct(150_000, 0.97)
    expect(hot).toBeCloseTo(0.57, 5)
    expect(109_000 / 150_000).toBeGreaterThanOrEqual(hot)
  })

  it('a large window never raises the threshold above 80%', () => {
    expect(hotThresholdPct(1_000_000, 0.97)).toBe(0.8)
  })

  it('a window smaller than the headroom is always hot', () => {
    expect(hotThresholdPct(40_000, 0.97)).toBe(0)
  })
})
