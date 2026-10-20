import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const settings = new Map<string, string>()
vi.mock('../settings-store.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../settings-store.js')>()
  return { ...actual, getEffectiveSettingValue: (key: string) => settings.get(key) ?? actual.getEffectiveSettingValue(key) }
})
import { getKanbanCard, initDatabase } from '../db.js'
import { callKanban } from './kanban-features-helpers.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const source = readFileSync(join(root, 'web', 'app.js'), 'utf8')
const start = source.indexOf('// === Kanban map helpers (pure) ===')
const end = source.indexOf('// === end kanban map helpers ===', start)
const ctx: Record<string, any> = {}
vm.createContext(ctx)
vm.runInContext(source.slice(start, end) + '\nthis.h={kanbanMapProject,kanbanMapUnproject,kanbanMapFitView,kanbanMapGroupByPlace,kanbanMapNormaliseAddress,kanbanMapCacheEntry,kanbanMapCreateGeocoder}', ctx)
const h = ctx.h

beforeEach(() => { initDatabase(':memory:'); settings.clear() })

describe('location API', () => {
  it('round trips through POST, PUT and GET, trimming and nulling empty values', async () => {
    const post = await callKanban('POST', '/api/kanban', { title: 'Site', location: '  Main Street 1  ' })
    expect(post.status).toBe(200)
    expect(getKanbanCard(post.body.id)!.location).toBe('Main Street 1')
    expect((await callKanban('GET', '/api/kanban')).body.find((c: any) => c.id === post.body.id).location).toBe('Main Street 1')
    expect((await callKanban('PUT', `/api/kanban/${post.body.id}`, { location: '  Other Road 2 ' })).status).toBe(200)
    expect(getKanbanCard(post.body.id)!.location).toBe('Other Road 2')
    await callKanban('PUT', `/api/kanban/${post.body.id}`, { location: '   ' })
    expect(getKanbanCard(post.body.id)!.location).toBeNull()
  })

  it('rejects invalid PUT values without changing the card', async () => {
    const post = await callKanban('POST', '/api/kanban', { title: 'Site', location: 'Kept' })
    for (const location of ['x'.repeat(301), 42]) expect((await callKanban('PUT', `/api/kanban/${post.body.id}`, { location })).status).toBe(400)
    expect(getKanbanCard(post.body.id)!.location).toBe('Kept')
  })
})

describe('map helpers', () => {
  it('projects and unprojects, and fitView contains all points', () => {
    const points = [{ lat: 47.5, lon: 19.04 }, { lat: 51.5, lon: -0.12 }]
    const p = h.kanbanMapProject(points[0].lat, points[0].lon, 12), back = h.kanbanMapUnproject(p.x, p.y, 12)
    expect(back.lat).toBeCloseTo(points[0].lat, 8); expect(back.lon).toBeCloseTo(points[0].lon, 8)
    const view = h.kanbanMapFitView(points, 800, 500), centre = h.kanbanMapProject(view.lat, view.lon, view.zoom)
    for (const point of points) { const q = h.kanbanMapProject(point.lat, point.lon, view.zoom); expect(Math.abs(q.x - centre.x)).toBeLessThanOrEqual(370); expect(Math.abs(q.y - centre.y)).toBeLessThanOrEqual(220) }
  })

  it('groups cards by rounded place and normalises addresses', () => {
    const groups = h.kanbanMapGroupByPlace([{ lat: 1, lon: 2, card: { id: 'a' } }, { lat: 1.00001, lon: 2.00001, card: { id: 'b' } }])
    expect(groups).toHaveLength(1); expect(groups[0].cards.map((c: any) => c.id)).toEqual(['a', 'b'])
    expect(h.kanbanMapNormaliseAddress('  FŐ   utca 1. ')).toBe('fő utca 1.')
  })

  it('expires entries after 30 days and retains misses', () => {
    const now = 4_000_000
    expect(h.kanbanMapCacheEntry({ miss: true, at: now - 29 * 86400 }, now)).toEqual({ miss: true })
    expect(h.kanbanMapCacheEntry({ lat: 1, lon: 2, at: now - 31 * 86400 }, now)).toBeNull()
    expect(h.kanbanMapCacheEntry({ error: true, at: now }, now)).toBeNull()
  })

  it('serialises requests at least 1100ms apart; misses but not errors are cacheable results', async () => {
    let clock = 0; const calls: number[] = []; const rows: any[] = [[], new Error('offline')]
    const geocoder = h.kanbanMapCreateGeocoder({ now: () => clock, sleep: async (ms: number) => { clock += ms }, fetch: async () => { calls.push(clock); const value = rows.shift(); if (value instanceof Error) throw value; return { ok: true, json: async () => value } } })
    const miss = await geocoder.lookup('bad'); const error = await geocoder.lookup('offline')
    expect(calls[1] - calls[0]).toBeGreaterThanOrEqual(1100)
    expect(miss).toEqual({ miss: true }); expect(error).toEqual({ error: true })
  })
})
