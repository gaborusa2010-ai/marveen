// #616: the Pinterest gallery extension's 'gallery' scoped device key reaches
// exactly two endpoints, decided before any handler; and only the extension's
// pinned origin gets past the CSRF check, on those two endpoints only.
import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Readable } from 'node:stream'
import { initDatabase } from '../db.js'
import { createDeviceKey, resolveDeviceKey, _clearDeviceKeyCacheForTest } from '../web/auth-device-keys.js'
import { resolveAuth } from '../web/auth-gate.js'
import { isBlockedCrossOriginWrite } from '../web/csrf-origin.js'
import { galleryGateDecision, isGalleryExtensionWrite, GALLERY_EXTENSION_ORIGIN } from '../web/gallery-gate.js'
import { tryHandleAuth } from '../web/routes/auth.js'
import type { RouteContext } from '../web/routes/types.js'
import { enumerateApiPaths } from './helpers/api-paths.js'

beforeAll(() => { initDatabase(':memory:') })
beforeEach(() => { _clearDeviceKeyCacheForTest() })

const gallery = { kind: 'device', scope: 'gallery' }
const METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']
// Literals, not the exported list: a widened list must not move the test with it.
const EXPECTED_OPEN = ['GET /api/gallery/topics', 'POST /api/gallery/items']

describe('galleryGateDecision over every enumerated /api path', () => {
  const paths = enumerateApiPaths()

  it('the enumeration sees the gallery routes and the rest of the API', () => {
    expect(paths.length).toBeGreaterThan(150)
    expect(paths).toContain('/api/gallery/items')
    expect(paths).toContain('/api/gallery/topics')
    expect(paths).toContain('/api/auth/device-keys')
    expect(paths).toContain('/api/memories')
  })

  it('a gallery key is refused everywhere except its two endpoints', () => {
    const open: string[] = []
    for (const p of paths) for (const m of METHODS) if (galleryGateDecision(gallery, m, p) === null) open.push(`${m} ${p}`)
    expect(open.sort()).toEqual(EXPECTED_OPEN)
  })

  it('the item sub-paths (image, edit, delete) stay closed to the extension', () => {
    for (const [m, p] of [['GET', '/api/gallery/items'], ['GET', '/api/gallery/items/0123abcd/image'], ['PUT', '/api/gallery/items/0123abcd'], ['DELETE', '/api/gallery/items/0123abcd']]) {
      expect(galleryGateDecision(gallery, m!, p!)).toMatch(/not a gallery extension endpoint/)
    }
  })

  it('every other principal passes untouched', () => {
    for (const auth of [{ kind: 'token' }, { kind: 'session' }, { kind: 'device', scope: 'full' }, { kind: 'device', scope: 'operator' }, { kind: 'federation' }, { kind: 'none' }]) {
      expect(galleryGateDecision(auth, 'GET', '/api/vault')).toBeNull()
    }
  })
})

describe('the CSRF exemption', () => {
  const allowed = new Set(['http://localhost:3420'])
  const blocked = (method: string, path: string, origin: string) =>
    !isGalleryExtensionWrite(method, path, origin) && isBlockedCrossOriginWrite(method, origin, 'localhost:3420', undefined, allowed)

  it('the pinned extension origin may POST a gallery item', () => {
    expect(GALLERY_EXTENSION_ORIGIN).toBe('chrome-extension://fcbpplcgmhgbeandlnfflfcendpkmkbb')
    expect(blocked('POST', '/api/gallery/items', GALLERY_EXTENSION_ORIGIN)).toBe(false)
  })

  it('the same origin is still blocked on any other write', () => {
    for (const [m, p] of [['POST', '/api/memories'], ['PUT', '/api/gallery/items/0123abcd'], ['DELETE', '/api/gallery/items/0123abcd'], ['POST', '/api/gallery/items/'], ['POST', '/api/auth/device-keys']]) {
      expect(blocked(m!, p!, GALLERY_EXTENSION_ORIGIN)).toBe(true)
    }
  })

  it('another extension or a web page is blocked on the gallery path', () => {
    for (const o of ['chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'https://www.pinterest.com', 'null', `${GALLERY_EXTENSION_ORIGIN}.evil`]) {
      expect(blocked('POST', '/api/gallery/items', o)).toBe(true)
    }
  })
})

describe('the gate binding in web.ts', () => {
  const SRC = readFileSync(join(__dirname, '../web.ts'), 'utf-8')
  it('the gallery gate runs inside the auth block, before network-info and every handler', () => {
    const unauthorized = SRC.indexOf("res.end(JSON.stringify({ error: 'Unauthorized' }))")
    const gate = SRC.indexOf('galleryGateDecision(auth, method, path)')
    const networkInfo = SRC.indexOf("if (path === '/api/network-info' && method === 'GET')")
    const firstHandler = SRC.indexOf('if (await tryHandleAuth(routeCtx)) return')
    expect(gate).toBeGreaterThan(unauthorized)
    expect(gate).toBeLessThan(networkInfo)
    expect(gate).toBeLessThan(firstHandler)
  })
  it('the CSRF exemption is a conjunct of the one origin check', () => {
    expect(SRC).toMatch(/if \(!isGalleryExtensionWrite\(method, path, origin\) &&\s+isBlockedCrossOriginWrite\(/)
  })
})

function fakeCtx(method: string, path: string, body: unknown, auth: RouteContext['auth']) {
  const out: { status: number; body: any } = { status: 0, body: null }
  const res: any = { setHeader() { return res }, writeHead(s: number) { out.status = s; return res }, end(c?: string) { if (c) out.body = JSON.parse(c) } }
  const req: any = Readable.from([Buffer.from(body === undefined ? '' : JSON.stringify(body))])
  req.headers = { 'content-type': 'application/json' }
  return { ctx: { req, res, path, method, url: new URL(`http://localhost${path}`), auth } as RouteContext, out }
}

describe('gallery device keys', () => {
  it('the owner can mint one; it does not expire by default', async () => {
    const { ctx, out } = fakeCtx('POST', '/api/auth/device-keys', { name: 'pinterest-gallery', scope: 'gallery' }, { kind: 'token' })
    await tryHandleAuth(ctx)
    expect(out.status).toBe(201)
    expect(out.body.scope).toBe('gallery')
    expect(out.body.expires_at).toBeNull()
    expect(out.body.key).toMatch(/^mvdk_/)
  })

  it('a device key cannot mint one (no escalation through the extension key)', async () => {
    const { ctx, out } = fakeCtx('POST', '/api/auth/device-keys', { name: 'x', scope: 'gallery' }, { kind: 'device', device: 'ext', deviceId: 1, scope: 'gallery' })
    await tryHandleAuth(ctx)
    expect(out.status).toBe(403)
  })

  it('the scope travels from the stored key to the gate', () => {
    const k = createDeviceKey('pinterest-gallery', { scope: 'gallery' })
    _clearDeviceKeyCacheForTest() // force the DB path
    expect(resolveDeviceKey(k.key)?.scope).toBe('gallery')
    const req: any = { headers: { authorization: `Bearer ${k.key}` } }
    const auth = resolveAuth(req, new URL('http://x/api/vault'), '/api/vault', 'GET', 'a'.repeat(64))
    expect(auth).toMatchObject({ kind: 'device', scope: 'gallery' })
    expect(galleryGateDecision(auth, 'GET', '/api/vault')).not.toBeNull()
  })
})
