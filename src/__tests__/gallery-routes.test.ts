import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { Readable } from 'node:stream'
import { mkdtemp, writeFile, rm, access } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { initDatabase, getDb } from '../db.js'
import { GALLERY_DAILY_CAP, runFetchBudget, setGalleryBudgetReader, setGalleryDir, setGalleryFetcher, tryHandleGallery, type FetchResult, type GalleryBudget } from '../web/routes/gallery.js'
import type { RouteContext } from '../web/routes/types.js'

let dir: string
async function call(method: string, target: string, payload?: unknown) {
  const out: any = { status: 200, body: null, headers: {}, raw: null }
  const res: any = { writeHead(status: number, headers?: object) { out.status = status; out.headers = headers || {}; return res }, end(chunk?: any) { out.raw = chunk; if (typeof chunk === 'string' && out.headers['Content-Type']?.includes('json')) out.body = JSON.parse(chunk) } }
  const raw = payload === undefined ? '' : typeof payload === 'string' ? payload : JSON.stringify(payload)
  const req: any = Readable.from(raw ? [Buffer.from(raw)] : [])
  const url = new URL(`http://localhost:3420${target}`)
  const handled = await tryHandleGallery({ req, res, path: url.pathname, method, url, auth: { kind: 'device', device: 'Chrome' } } as RouteContext)
  return { ...out, handled }
}
function fake(sha = 'a'.repeat(64), type: 'png' | 'jpeg' = 'png') {
  return async (url: string): Promise<FetchResult> => {
    const path = join(dir, `${sha.slice(0, 16)}.${type}`); await writeFile(path, Buffer.from('image'))
    return { ok: true, receipt: { path, bytes: 5, type, sha256: sha, url } }
  }
}
const BUDGET: GalleryBudget = { used: 3, cap: 200, left: 197, next_free_at: '2026-10-25T17:25:21Z' }
beforeEach(async () => { initDatabase(':memory:'); dir = await mkdtemp(join(tmpdir(), 'gallery-route-')); setGalleryDir(dir); setGalleryFetcher(fake()); setGalleryBudgetReader(async () => BUDGET) })
afterEach(async () => { setGalleryFetcher(null); setGalleryBudgetReader(null); setGalleryDir(null); await rm(dir, { recursive: true, force: true }) })

describe('/api/gallery', () => {
  it.each([
    [undefined, 'JSON'], ['{bad', 'JSON'], [{ extra: 1 }, 'Unknown'], [{}, 'image_url'],
    [{ image_url: 'x'.repeat(2049) }, 'image_url'], [{ image_url: 'javascript:alert(1)' }, 'http'],
    [{ image_url: 'ftp://x/a' }, 'http'], [{ image_url: 'https://u:p@x/a' }, 'credentials'],
    [{ image_url: 'https://i.pinimg.com/originals/a.jpg', topic: 'bad\n' }, 'control'], [{ image_url: 'https://i.pinimg.com/originals/a.jpg', topic: 'x'.repeat(65) }, '64'],
  ])('rejects invalid POST input %#', async (payload, needle) => {
    const result = await call('POST', '/api/gallery/items', payload)
    expect(result.status).toBe(400); expect(result.body.error).toContain(needle)
  })
  it.each(['http://i.pinimg.com/originals/a.jpg', 'https://evil.test/a.jpg', 'https://i.pinimg.com.evil.test/a.jpg', 'https://pinimg.com/a.jpg'])('refuses image_url outside https i.pinimg.com without fetching: %s', async (image_url) => {
    let fetched = 0; setGalleryFetcher(async url => { fetched++; return fake()(url) })
    const result = await call('POST', '/api/gallery/items', { image_url })
    expect(result.status).toBe(400); expect(result.body.error).toContain('i.pinimg.com'); expect(fetched).toBe(0)
  })
  it.each([['refused', 403], ['budget', 429], ['fetch_failed', 502], ['too_big', 413], ['not_image', 415], ['error', 500]] as const)('maps %s to %i', async (outcome, status) => {
    setGalleryFetcher(async () => ({ ok: false, outcome, detail: 'detail' }))
    const result = await call('POST', '/api/gallery/items', { image_url: 'https://i.pinimg.com/originals/a.jpg' })
    expect(result.status).toBe(status); expect(result.body).toMatchObject({ outcome, detail: 'detail' })
  })
  it('upgrades pinimg size and retries only fetch failures', async () => {
    const calls: string[] = []
    setGalleryFetcher(async url => { calls.push(url); return calls.length === 1 ? { ok: false, outcome: 'fetch_failed', detail: '' } : fake()(url) })
    expect((await call('POST', '/api/gallery/items', { image_url: 'https://i.pinimg.com/236x/a/b.jpg' })).status).toBe(201)
    expect(calls.map(x => new URL(x).pathname)).toEqual(['/736x/a/b.jpg', '/236x/a/b.jpg'])
    calls.length = 0; setGalleryFetcher(async url => { calls.push(url); return { ok: false, outcome: 'refused', detail: '' } })
    await call('POST', '/api/gallery/items', { image_url: 'https://i.pinimg.com/60x60/a.jpg' }); expect(calls).toHaveLength(1)
  })
  it('deduplicates, lists and filters without exposing private fields', async () => {
    const first = await call('POST', '/api/gallery/items', { image_url: 'https://i.pinimg.com/originals/a.jpg', topic: 'Kert' })
    expect(first.status).toBe(201); expect(first.body.duplicate).toBe(false)
    const second = await call('POST', '/api/gallery/items', { image_url: 'https://i.pinimg.com/originals/b.jpg', topic: 'Más' })
    expect(second.status).toBe(200); expect(second.body.duplicate).toBe(true); expect(getDb().prepare('SELECT COUNT(*) n FROM gallery_items').get()).toMatchObject({ n: 1 })
    for (const result of [first, second, await call('GET', '/api/gallery/items'), await call('GET', '/api/gallery/items?topic=Kert'), await call('GET', '/api/gallery/topics')]) {
      const text = JSON.stringify(result.body); expect(text).not.toContain(dir); expect(text).not.toMatch(/"file"\s*:/)
    }
    expect((await call('GET', '/api/gallery/items?topic=Kert')).body).toHaveLength(1)
    expect((await call('GET', '/api/gallery/topics')).body).toEqual([{ topic: 'Kert', count: 1 }])
  })
  it('updates topic, serves bytes, and deletes row and file', async () => {
    const created = await call('POST', '/api/gallery/items', { image_url: 'https://i.pinimg.com/originals/a.jpg' }); const id = created.body.item.id
    const updated = await call('PUT', `/api/gallery/items/${id}`, { topic: 'Otthon' }); expect(updated.body.item.topic).toBe('Otthon')
    const image = await call('GET', `/api/gallery/items/${id}/image`); expect(image.status).toBe(200); expect(image.headers['Content-Type']).toBe('image/png'); expect(Buffer.from(image.raw).toString()).toBe('image')
    expect((await call('GET', '/api/gallery/items/bad/image')).status).toBe(404)
    const file = join(dir, `${'a'.repeat(16)}.png`); await rm(file); expect((await call('GET', `/api/gallery/items/${id}/image`)).status).toBe(404); await writeFile(file, 'image')
    expect((await call('DELETE', `/api/gallery/items/${id}`)).status).toBe(200); await expect(access(file)).rejects.toThrow()
  })

  it('GET /budget returns the counter, 503 when it cannot be read, and takes no input', async () => {
    expect(await call('GET', '/api/gallery/budget')).toMatchObject({ status: 200, body: BUDGET })
    setGalleryBudgetReader(async () => null)
    expect((await call('GET', '/api/gallery/budget')).status).toBe(503)
    setGalleryBudgetReader(async () => { throw new Error('boom') })
    expect((await call('GET', '/api/gallery/budget')).status).toBe(503)
    expect((await call('GET', '/api/gallery/budget?x=1')).status).toBe(400)
    expect((await call('POST', '/api/gallery/budget', {})).status).toBe(405)
  })
  it('every save answer carries the counter, and a failed read does not fail the save', async () => {
    expect((await call('POST', '/api/gallery/items', { image_url: 'https://i.pinimg.com/originals/a.jpg' })).body.budget).toEqual(BUDGET)
    setGalleryFetcher(async () => ({ ok: false, outcome: 'budget', detail: '' }))
    expect((await call('POST', '/api/gallery/items', { image_url: 'https://i.pinimg.com/originals/c.jpg' })).body.budget).toEqual(BUDGET)
    setGalleryFetcher(fake('b'.repeat(64))); setGalleryBudgetReader(async () => null)
    const saved = await call('POST', '/api/gallery/items', { image_url: 'https://i.pinimg.com/originals/d.jpg' })
    expect(saved.status).toBe(201); expect(saved.body.budget).toBeNull()
  })
  it('a picture already saved under another size is answered without a download', async () => {
    const name = 'cf87a61d30486d7d3054e8cf3347198a'
    let fetched = 0; setGalleryFetcher(async url => { fetched++; return fake()(url) })
    expect((await call('POST', '/api/gallery/items', { image_url: `https://i.pinimg.com/originals/cf/87/a6/${name}.jpg` })).status).toBe(201)
    const again = await call('POST', '/api/gallery/items', { image_url: `https://i.pinimg.com/736x/cf/87/a6/${name}.jpg`, topic: 'Más' })
    expect(again.status).toBe(200); expect(again.body).toMatchObject({ duplicate: true, budget: BUDGET }); expect(fetched).toBe(1)
    setGalleryFetcher(async url => { fetched++; return fake('c'.repeat(64))(url) })
    expect((await call('POST', '/api/gallery/items', { image_url: `https://i.pinimg.com/736x/cf/87/a6/${'0'.repeat(32)}.jpg` })).status).toBe(201)
    expect(fetched).toBe(2)
  })
})

describe('runFetchBudget against the real fetch-budget.py', () => {
  const line = (ago: number, source = ' source="gallery"') =>
    `${new Date(Date.now() - ago).toISOString()} ALLOWED_IMAGE url="https://i.pinimg.com/a.jpg" reason="t" agent_type="t"${source}\n`
  it('counts the gallery bucket of the rolling 24h window', async () => {
    const log = join(dir, 'egress.log')
    await writeFile(log, line(60_000) + line(3_600_000) + line(25 * 3_600_000) + line(60_000, ''))
    const budget = await runFetchBudget({ log })
    expect(budget).toMatchObject({ used: 2, cap: GALLERY_DAILY_CAP, left: GALLERY_DAILY_CAP - 2 })
    expect(Date.parse(budget!.next_free_at!)).toBeGreaterThan(Date.now() + 22 * 3_600_000)
  })
  it('returns null when the script cannot answer', async () => {
    expect(await runFetchBudget({ script: join(dir, 'missing.py') })).toBeNull()
  })
})
