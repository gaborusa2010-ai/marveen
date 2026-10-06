/**
 * #347: PUT/PATCH /api/memories/:id took only six keys out of the body and
 * dropped the rest without a word, while still answering ok:true. A typo
 * ("keyword") or a field the route never had ("append") read as a successful
 * edit. Now an unknown key refuses the whole write with a 400 that names it.
 *
 * Both directions are pinned: the refusals AND every body shape the real
 * callers send (dashboard edit modal, scaffold write-trace, the docs recipe,
 * a tier-only move), so the guard cannot overshoot into the callers.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { Readable } from 'node:stream'
import { initDatabase, saveAgentMemory, getDb } from '../db.js'
import { tryHandleMemories } from '../web/routes/memories.js'
import type { RouteContext } from '../web/routes/types.js'

vi.mock('../config.js', async () => {
  const actual = await vi.importActual<typeof import('../config.js')>('../config.js')
  return { ...actual, MAIN_AGENT_ID: 'agent-a', ALLOWED_CHAT_ID: 'test-chat', OLLAMA_URL: '' }
})

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

async function send(id: number, body: string, method = 'PUT'): Promise<{ status: number; body: any }> {
  const path = `/api/memories/${id}`
  let responseBody = ''
  let statusCode = 200
  const res = {
    writeHead: (code: number) => { statusCode = code },
    end: (b?: string) => { responseBody = b || '' },
  }
  const req = Readable.from([Buffer.from(body)])
  const ctx = { req: req as any, res: res as any, path, method, url: new URL(`http://localhost:3420${path}`) } as RouteContext
  expect(await tryHandleMemories(ctx)).toBe(true)
  return { status: statusCode, body: responseBody ? JSON.parse(responseBody) : null }
}

function row(id: number): { content: string; category: string; keywords: string | null; agent_id: string } {
  return getDb().prepare('SELECT content, category, keywords, agent_id FROM memories WHERE id = ?').get(id) as any
}

let id = 0

beforeAll(() => {
  initDatabase(':memory:')
  id = saveAgentMemory('agent-a', 'original content', 'warm', 'orig-kw').id
})

afterAll(() => { vi.restoreAllMocks() })

describe('PUT/PATCH /api/memories/:id refuses unknown fields', () => {
  it.each([
    ['append', '{"append":"more text"}'],
    ['zzz_unknown', '{"content":"changed","zzz_unknown":1}'],
    ['keyword', '{"keyword":"typo-of-keywords"}'],
  ])('400 names the unknown key "%s" and changes nothing', async (key, body) => {
    const before = row(id)
    for (const method of ['PUT', 'PATCH']) {
      const r = await send(id, body, method)
      expect(r.status).toBe(400)
      expect(r.body.error).toContain(`Unknown field(s): ${key}`)
      expect(r.body.error).toContain('Accepted: content, category, tier, agent_id, keywords, updated_by')
      expect(row(id)).toEqual(before)
    }
  })

  it.each([['null'], ['[]'], ['"text"'], ['42']])('400 for a body that is not an object: %s', async (body) => {
    const r = await send(id, body)
    expect(r.status).toBe(400)
    expect(r.body.error).toContain('Body must be a JSON object')
  })
})

describe('the real callers still go through', () => {
  it('dashboard edit modal: content, tier, agent_id, keywords', async () => {
    const r = await send(id, JSON.stringify({ content: 'edited in the UI', tier: 'hot', agent_id: 'agent-a', keywords: 'ui-kw' }))
    expect(r.status).toBe(200)
    expect(r.body).toEqual({ ok: true })
    expect(row(id)).toMatchObject({ content: 'edited in the UI', category: 'hot', keywords: 'ui-kw' })
  })

  it('scaffold write-trace: category and updated_by, content omitted', async () => {
    const r = await send(id, JSON.stringify({ category: 'cold', updated_by: 'agent-b' }), 'PATCH')
    expect(r.status).toBe(200)
    expect(row(id)).toMatchObject({ content: 'edited in the UI', category: 'cold', agent_id: 'agent-a' })
  })

  it('docs recipe: content only', async () => {
    const r = await send(id, JSON.stringify({ content: 'rewritten' }))
    expect(r.status).toBe(200)
    expect(row(id).content).toBe('rewritten')
  })

  it('an empty object is still a no-op partial update, not an unknown-field error', async () => {
    const r = await send(id, '{}')
    expect(r.status).toBe(200)
  })

  it('a missing row is still a 404, not swallowed by the field check', async () => {
    const r = await send(99999999, JSON.stringify({ category: 'cold' }))
    expect(r.status).toBe(404)
  })
})
