// #437: two more write routes that dropped unknown keys while answering
// ok:true, the same defect class as the kanban PUT (#1023) and the memory
// PUT/PATCH (#347):
//   - POST /api/kanban/:id/move -- `wait_kind`/`wait_note` instead of
//     `wait: {kind, note}` moved the card and lost the wait reason (measured
//     2026-10-06);
//   - POST /api/memories -- a typo ("keyword") saved the memory without the
//     field the caller believed it set.
// Pinned: an unknown key or a non-object body is a 400 naming the field, and
// nothing is written; every body shape the real callers send still passes.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Readable } from 'node:stream'
import { initDatabase, createKanbanCard, getKanbanCard, getDb } from '../db.js'
import { tryHandleKanban } from '../web/routes/kanban.js'
import { tryHandleMemories } from '../web/routes/memories.js'
import type { RouteContext } from '../web/routes/types.js'

vi.mock('../config.js', async () => {
  const actual = await vi.importActual<typeof import('../config.js')>('../config.js')
  return { ...actual, MAIN_AGENT_ID: 'agent-a', ALLOWED_CHAT_ID: 'test-chat', OLLAMA_URL: '' }
})
vi.mock('../logger.js', () => ({ logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() } }))

function postCtx(path: string, raw: string): { ctx: RouteContext; out: { status: number; body: any } } {
  const out: { status: number; body: any } = { status: 200, body: null }
  const res: any = {
    writeHead(status: number) { out.status = status; return res },
    setHeader() { return res },
    end(chunk?: string) { if (chunk) out.body = JSON.parse(chunk) },
  }
  const req: any = Readable.from([Buffer.from(raw)])
  const url = new URL(`http://localhost:3420${path}`)
  return { ctx: { req, res, path: url.pathname, method: 'POST', url } as RouteContext, out }
}

async function move(id: string, payload: unknown) {
  const { ctx, out } = postCtx(`/api/kanban/${id}/move`, JSON.stringify(payload))
  expect(await tryHandleKanban(ctx)).toBe(true)
  return out
}

async function saveMemory(payload: unknown) {
  const { ctx, out } = postCtx('/api/memories', JSON.stringify(payload))
  expect(await tryHandleMemories(ctx)).toBe(true)
  return out
}

const memoryCount = () => (getDb().prepare('SELECT count(*) AS c FROM memories').get() as { c: number }).c

beforeEach(() => { initDatabase(':memory:') })

describe('POST /api/kanban/:id/move -- unknown keys (#437)', () => {
  it('refuses wait_kind/wait_note with 400 and does NOT move the card', async () => {
    createKanbanCard({ id: 'm1', title: 'Card one', status: 'planned' })
    const out = await move('m1', { status: 'waiting', actor: 'cody', wait_kind: 'agent', wait_note: 'x' })
    expect(out.status).toBe(400)
    expect(out.body.error).toContain('wait_kind')
    expect(out.body.error).toContain('wait_note')
    expect(out.body.error).toContain('status, sort_order, actor, wait')
    expect(getKanbanCard('m1')!.status).toBe('planned')
  })

  it('refuses the message-close fields a mixed-up call would send', async () => {
    createKanbanCard({ id: 'm2', title: 'Card two', status: 'planned' })
    const out = await move('m2', { status: 'done', result: 'x', notify: false })
    expect(out.status).toBe(400)
    expect(out.body.error).toMatch(/result, notify|notify, result/)
    expect(getKanbanCard('m2')!.status).toBe('planned')
  })

  for (const [label, raw] of [['null', 'null'], ['an array', '["done"]'], ['a string', '"done"']] as const) {
    it(`refuses ${label} as the body`, async () => {
      createKanbanCard({ id: 'm3', title: 'Card three', status: 'planned' })
      const { ctx, out } = postCtx('/api/kanban/m3/move', raw)
      expect(await tryHandleKanban(ctx)).toBe(true)
      expect(out.status).toBe(400)
      expect(out.body.error).toContain('JSON object')
      expect(getKanbanCard('m3')!.status).toBe('planned')
    })
  }

  it('accepts the dashboard drag-and-drop shape (status, sort_order, actor)', async () => {
    createKanbanCard({ id: 'm4', title: 'Card four', status: 'planned' })
    const out = await move('m4', { status: 'waiting', sort_order: 3, actor: 'dashboard' })
    expect(out.status).toBe(200)
    expect(getKanbanCard('m4')!.status).toBe('waiting')
  })

  it('accepts the agent shapes ({status, actor} and bare {status})', async () => {
    createKanbanCard({ id: 'm5', title: 'Card five', status: 'planned' })
    expect((await move('m5', { status: 'waiting', actor: 'cody' })).status).toBe(200)
    expect((await move('m5', { status: 'done' })).status).toBe(200)
    expect(getKanbanCard('m5')!.status).toBe('done')
  })

  it('does not refuse the wait field itself', async () => {
    createKanbanCard({ id: 'm6', title: 'Card six', status: 'planned' })
    const out = await move('m6', { status: 'waiting', actor: 'cody', wait: { kind: 'external', note: 'x' } })
    expect(out.status).not.toBe(400)
  })

  it('a missing card is still a 404', async () => {
    expect((await move('nope', { status: 'done' })).status).toBe(404)
  })
})

describe('POST /api/memories -- unknown keys (#437)', () => {
  it('refuses a typo ("keyword") with 400 and saves nothing', async () => {
    const before = memoryCount()
    const out = await saveMemory({ agent_id: 'cody', content: 'x', category: 'warm', keyword: 'a, b' })
    expect(out.status).toBe(400)
    expect(out.body.error).toContain('keyword')
    expect(out.body.error).toContain('agent_id, content, tier, category, keywords')
    expect(memoryCount()).toBe(before)
  })

  it('refuses a field the route never had ("updated_by") and saves nothing', async () => {
    const before = memoryCount()
    const out = await saveMemory({ agent_id: 'cody', content: 'x', updated_by: 'cody' })
    expect(out.status).toBe(400)
    expect(out.body.error).toContain('updated_by')
    expect(memoryCount()).toBe(before)
  })

  for (const [label, raw] of [['null', 'null'], ['an array', '[]'], ['a string', '"x"']] as const) {
    it(`refuses ${label} as the body`, async () => {
      const before = memoryCount()
      const { ctx, out } = postCtx('/api/memories', raw)
      expect(await tryHandleMemories(ctx)).toBe(true)
      expect(out.status).toBe(400)
      expect(out.body.error).toContain('JSON object')
      expect(memoryCount()).toBe(before)
    })
  }

  it('accepts the CLAUDE.md template / fleet.py shape', async () => {
    const out = await saveMemory({ agent_id: 'cody', content: 'template shape', category: 'cold', keywords: 'a, b' })
    expect(out.status).toBe(200)
    expect(out.body.ok).toBe(true)
    const row = getDb().prepare('SELECT agent_id, category, keywords FROM memories WHERE id = ?').get(out.body.id)
    expect(row).toEqual({ agent_id: 'cody', category: 'cold', keywords: 'a, b' })
  })

  it('accepts the dashboard modal shape (tier instead of category)', async () => {
    const out = await saveMemory({ agent_id: 'cody', content: 'modal shape', tier: 'hot', keywords: '' })
    expect(out.status).toBe(200)
  })

  it('accepts the minimal shapes ({agent_id, content} and {content})', async () => {
    expect((await saveMemory({ agent_id: 'cody', content: 'minimal' })).status).toBe(200)
    expect((await saveMemory({ content: 'bare' })).status).toBe(200)
  })

  it('an empty content is still the existing 400', async () => {
    const out = await saveMemory({ agent_id: 'cody', content: '  ' })
    expect(out.status).toBe(400)
    expect(out.body.error).toBe('Content is required')
  })
})
