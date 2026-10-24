import { describe, expect, it } from 'vitest'
import { largestFromSrcset, extractBackgroundUrl, pinPageUrl, notificationText, candidateFromFacts, budgetText, menuTitle, clockTime } from '../../extensions/pinterest-gallery/lib.js'

describe('Pinterest gallery extension helpers', () => {
  it('selects the largest valid srcset candidate', () => {
    expect(largestFromSrcset('a.jpg 236w, b.jpg 474w')).toBe('b.jpg')
    expect(largestFromSrcset(' a.jpg 1x , b.jpg 2x ')).toBe('b.jpg')
    expect(largestFromSrcset('')).toBeNull()
    expect(largestFromSrcset('garbage, x nope')).toBeNull()
  })
  it('extracts a single CSS background URL', () => {
    expect(extractBackgroundUrl('url("https://x.test/a.jpg")')).toBe('https://x.test/a.jpg')
    expect(extractBackgroundUrl(" url('a b.jpg') ")).toBe('a b.jpg')
    expect(extractBackgroundUrl('none')).toBeNull()
  })
  it('accepts only numeric Pinterest pin paths', () => {
    expect(pinPageUrl('/pin/123/', 'https://www.pinterest.com/x')).toBe('https://www.pinterest.com/pin/123/')
    expect(pinPageUrl('/pin/nope/', 'https://www.pinterest.com')).toBeNull()
  })
  it('builds the save candidate from the facts the content script reports', () => {
    const page = 'https://www.pinterest.com/search/?q=kert'
    expect(candidateFromFacts({ srcset: 'a.jpg 236w, b.jpg 736w', currentSrc: 'c.jpg', src: 'd.jpg', anchorHref: 'https://www.pinterest.com/pin/42/', pageUrl: page }))
      .toEqual({ imageUrl: 'b.jpg', pageUrl: 'https://www.pinterest.com/pin/42/' })
    expect(candidateFromFacts({ srcset: '', currentSrc: '', src: 'd.jpg', anchorHref: 'https://www.pinterest.com/ideas/', pageUrl: page }))
      .toEqual({ imageUrl: 'd.jpg', pageUrl: page })
    expect(candidateFromFacts({ backgroundImage: 'url("https://i.pinimg.com/x.jpg")', pageUrl: page }).imageUrl).toBe('https://i.pinimg.com/x.jpg')
  })
  it('reports an empty candidate when nothing image-like is under the pointer', () => {
    expect(candidateFromFacts({ srcset: null, currentSrc: null, src: null, backgroundImage: null, anchorHref: null, pageUrl: 'https://www.pinterest.com/' }))
      .toEqual({ imageUrl: null, pageUrl: 'https://www.pinterest.com/' })
    expect(candidateFromFacts(undefined)).toEqual({ imageUrl: null, pageUrl: null })
    expect(candidateFromFacts({ src: 42, pageUrl: ['x'] })).toEqual({ imageUrl: null, pageUrl: null })
  })
  it('maps response statuses to Hungarian notifications', () => {
    expect(notificationText(201, { item: { topic: 'Kert' } })).toBe('Mentve: Kert')
    expect(notificationText(200, { duplicate: true })).toBe('Már a galériában van')
    expect(notificationText(403, { outcome: 'refused' })).toContain('helyről')
    expect(notificationText(401, {})).toContain('kulcsa')
    expect(notificationText(413, {})).toBe('Túl nagy kép')
    expect(notificationText(415, {})).toBe('Ez nem kép')
    expect(notificationText(429, {})).toContain('Túl sok')
    expect(notificationText(502, {})).toContain('nem tölthető')
  })
  it('shows the remaining allowance, and nothing at all for a missing or malformed counter', () => {
    const left = { used: 113, cap: 200, left: 87, next_free_at: '2026-10-25T17:25:21Z' }
    const out = { used: 200, cap: 200, left: 0, next_free_at: '2026-10-25T17:25:21Z' }
    const at = clockTime(out.next_free_at)
    expect(at).toMatch(/^\d\d:\d\d$/)
    expect(budgetText(left)).toBe('még 87 menthető')
    expect(budgetText(out)).toBe(`elfogyott, újra ${at}-tól`)
    expect(budgetText({ ...out, next_free_at: null })).toBe('elfogyott')
    expect(budgetText({ ...out, next_free_at: 'nonsense' })).toBe('elfogyott')
    for (const bad of [null, undefined, {}, { used: 1, cap: 2 }, { used: -1, cap: 2, left: 3 }, { used: '1', cap: 2, left: 1 }, { used: 1.5, cap: 2, left: 1 }]) {
      expect(budgetText(bad)).toBeNull(); expect(menuTitle(bad)).toBe('Mentés a galériába')
    }
    expect(menuTitle(left)).toBe('Mentés a galériába (még 87/200)')
    expect(menuTitle(out)).toBe(`Mentés a galériába (elfogyott, újra ${at}-tól)`)
    expect(notificationText(201, { item: { topic: 'Kert' }, budget: left })).toBe('Mentve: Kert · még 87 menthető')
    expect(notificationText(200, { duplicate: true, budget: left })).toBe('Már a galériában van · még 87 menthető')
    expect(notificationText(429, { outcome: 'budget', budget: out })).toBe(`Elfogyott a mentési keret · elfogyott, újra ${at}-tól`)
    expect(notificationText(429, { error: 'busy' })).toContain('Túl sok')
    expect(notificationText(201, { item: { topic: 'Kert' }, budget: null })).toBe('Mentve: Kert')
  })
})
