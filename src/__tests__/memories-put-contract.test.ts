// PUT500901 (2026-09-01): PUT /api/memories/:id answered an opaque 500 to the
// most natural partial update -- `{"category":"cold"}` when only the tier moves.
// `content` was typed required and never validated, so undefined reached a NOT
// NULL column. A caller cannot tell a server fault from its own bad request, and
// that is the exact failure shape this day was spent removing.
//
// v1.38.0 (2026-09-16) brought upstream's real partial update: an OMITTED
// content is backfilled from the stored row. What this contract still pins is
// the other half: content that IS sent but is not a non-empty string must be
// refused with a 400 that names the field, before anything reaches the
// database. Source-contract test: it fails the moment that guard is dropped.
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const ROUTE = readFileSync(
  join(import.meta.dirname, '..', 'web', 'routes', 'memories.ts'), 'utf-8')

describe('PUT /api/memories/:id refuses clearly instead of throwing', () => {
  const PUT = ROUTE.slice(ROUTE.indexOf("memUpdateMatch && (method === 'PUT'"))
  const call = PUT.indexOf('updateMemory(id, effectiveContent')

  it('the route still exists in the shape this test reads', () => {
    expect(PUT.length).toBeGreaterThan(0)
    expect(call).toBeGreaterThan(-1)
  })

  it('validates a sent content before it reaches the database', () => {
    const guard = PUT.indexOf("typeof content !== 'string'")
    expect(guard).toBeGreaterThan(-1)
    expect(call).toBeGreaterThan(guard)   // the check runs FIRST
  })

  it('answers 400, not 500, and names what is wrong', () => {
    const upTo = PUT.slice(0, call)
    expect(upTo).toContain('400')
    // v1.41.0 merge: upstream's explicit-content check ("Content is required")
    // runs above the backfill and replaced the local PUT500901 wording.
    expect(upTo.toLowerCase()).toContain('content is required')
  })

  it('an omitted content is a partial update, backfilled from the row', () => {
    const upTo = PUT.slice(0, call)
    expect(upTo).toContain('effectiveContent === undefined')
    // The backfill now reads the pre-image the version snapshot already loaded.
    expect(upTo).toContain('effectiveContent = before.content')
  })
})
