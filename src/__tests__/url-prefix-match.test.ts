// Contract tests for egress prefix matching (DOTSEG901).
//
// Roxy measured the hole on 2026-09-01, against a local HTTP server rather than
// from theory:
//
//   allowed prefix : https://cloud.leonardo.ai/api/rest/v1/me
//   command        : curl .../api/rest/v1/me/../generations
//   gate verdict   : allowed  (plain startsWith)
//   PATH_RECEIVED  : /api/rest/v1/generations
//
// The one grant form that exists in order to be NARROW could be widened by six
// characters. It was the third instance that day of the same shape: the gate
// matches TEXT while the world interprets a PATH.

import { describe, it, expect } from 'vitest'
// @ts-expect-error -- plain .mjs hook script, no types
import { prefixAllows, normalizeUrl, removeDotSegments, splitUrl } from '../../scripts/hooks/lib/url-prefix.mjs'

const P = 'https://cloud.leonardo.ai/api/rest/v1/me'

describe('url prefix: the traversal bypass is closed', () => {
  it('refuses a prefix match that only holds before normalisation', () => {
    expect(prefixAllows(`${P}/../generations`, P)).toBe(false)
  })

  it('refuses deeper traversals too', () => {
    expect(prefixAllows(`${P}/../../v2/generations`, P)).toBe(false)
    expect(prefixAllows(`${P}/./../generations`, P)).toBe(false)
  })

  it('still allows a path that merely takes a detour to the same place', () => {
    expect(prefixAllows('https://cloud.leonardo.ai/api/rest/v1/./me', P)).toBe(true)
    expect(prefixAllows('https://cloud.leonardo.ai/api/rest/v1/x/../me', P)).toBe(true)
  })
})

describe('url prefix: the match ends on a segment boundary', () => {
  it('a grant for /me is not a grant for /meta', () => {
    expect(prefixAllows('https://cloud.leonardo.ai/api/rest/v1/meta', P)).toBe(false)
  })

  it('allows the exact path, a deeper segment, and a query', () => {
    expect(prefixAllows(P, P)).toBe(true)
    expect(prefixAllows(`${P}/sub`, P)).toBe(true)
    expect(prefixAllows(`${P}?x=1`, P)).toBe(true)
    expect(prefixAllows(`${P}#frag`, P)).toBe(true)
  })

  it('treats a prefix ending in / as a directory grant', () => {
    expect(prefixAllows('https://h/api/v1/x', 'https://h/api/')).toBe(true)
    expect(prefixAllows('https://h/apifoo', 'https://h/api/')).toBe(false)
    // ...and normalisation still applies inside a directory grant.
    expect(prefixAllows('https://h/api/../evil', 'https://h/api/')).toBe(false)
  })
})

describe('url prefix: the pieces', () => {
  it('removes dot segments the way a client does', () => {
    expect(removeDotSegments('/a/b/../c')).toBe('/a/c')
    expect(removeDotSegments('/a/./b')).toBe('/a/b')
    expect(removeDotSegments('/../x')).toBe('/x')
    expect(removeDotSegments('/a//b')).toBe('/a/b')
  })

  it('splits a URL into head, path and tail without new URL()', () => {
    expect(splitUrl('https://h:8080/a/b?q=1#f')).toEqual({ head: 'https://h:8080', path: '/a/b', tail: '?q=1#f' })
    expect(splitUrl('https://h').head).toBe('https://h')
  })

  it('normalises the whole URL, keeping query and fragment', () => {
    expect(normalizeUrl(`${P}/../generations?x=1`)).toBe('https://cloud.leonardo.ai/api/rest/v1/generations?x=1')
  })

  it('never throws on a shell-flavoured literal', () => {
    expect(() => prefixAllows('http://localhost:$PORT/api', 'http://localhost/')).not.toThrow()
    expect(() => prefixAllows('not a url', P)).not.toThrow()
  })
})
