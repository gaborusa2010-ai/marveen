import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { Readable } from 'node:stream'
import { mkdtemp, writeFile, rm, access } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { initDatabase, getDb } from '../db.js'
import { setGalleryDir, setGalleryFetcher, tryHandleGallery, type FetchResult } from '../web/routes/gallery.js'
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
beforeEach(async () => { initDatabase(':memory:'); dir = await mkdtemp(join(tmpdir(), 'gallery-route-')); setGalleryDir(dir); setGalleryFetcher(fake()) })
afterEach(async () => { setGalleryFetcher(null); setGalleryDir(null); await rm(dir, { recursive: true, force: true }) })

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
})
