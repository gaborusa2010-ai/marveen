/**
 * Regression test for the "known param name, unknown value" bug (2026-09-01).
 *
 * Root cause: GET /api/messages accepted ?status=<anything> and honoured only
 * 'pending'. Every other value -- a real column state like 'done', or pure
 * nonsense like 'unread', which this schema has never had -- fell through to
 * the UNFILTERED conversation and came back as a full 200. So an agent checking
 * its inbox with ?status=unread read "dozens waiting" while the true pending
 * count was zero. Measured by Roxy: unread / read / delivered / zoldbeka all
 * returned the same 113 rows.
 *
 * That is one level below the param-NAME guard: the name was right, so no
 * amount of name validation would have caught it. Both halves are needed --
 * the accepted values must be the ones the CHECK constraint allows, and they
 * must actually filter.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { initDatabase, createAgentMessage, markMessageDone, markMessageFailed } from '../db.js'
import { tryHandleMessages } from '../web/routes/messages.js'
import type { RouteContext } from '../web/routes/types.js'

vi.mock('../config.js', async () => {
  const actual = await vi.importActual<typeof import('../config.js')>('../config.js')
  return { ...actual, MAIN_AGENT_ID: 'agent-a', ALLOWED_CHAT_ID: 'test-chat', OLLAMA_URL: '' }
})

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

function makeCtx(searchParams: Record<string, string> = {}) {
  const url = new URL('http://localhost:3420/api/messages')
  for (const [k, v] of Object.entries(searchParams)) url.searchParams.set(k, v)

  let responseBody = ''
  let status = 200
  const res = {
    writeHead: vi.fn((code: number) => { status = code }),
    setHeader: vi.fn(),
    end: (body?: string) => { responseBody = body || '' },
  }

  return {
    ctx: { req: { headers: {} } as any, res: res as any, path: '/api/messages', method: 'GET', url } as RouteContext,
    getBody: () => (responseBody ? JSON.parse(responseBody) : null),
    getStatus: () => status,
  }
}

beforeAll(() => {
  initDatabase(':memory:')
  // One message per reachable state, so a filter that does nothing is
  // distinguishable from one that works: the counts must differ.
  createAgentMessage('agent-b', 'agent-a', 'still waiting')          // pending
  const toFinish = createAgentMessage('agent-b', 'agent-a', 'handled')
  markMessageDone(toFinish.id, 'ok')                                  // done
  const toFail = createAgentMessage('agent-b', 'agent-a', 'broke')
  markMessageFailed(toFail.id, 'boom')                                // failed
})

afterAll(() => { vi.restoreAllMocks() })

describe('GET /api/messages status value', () => {
  it('rejects a status the schema has never had, and names the real one', async () => {
    const { ctx, getBody, getStatus } = makeCtx({ agent: 'agent-a', status: 'unread' })
    expect(await tryHandleMessages(ctx)).toBe(true)
    expect(getStatus()).toBe(400)
    const body = getBody()
    expect(body.error).toBe('unknown status value')
    expect(body.known).toEqual(['pending', 'delivered', 'done', 'failed'])
    // The hint has to point at the state that actually means "waiting", or the
    // caller just guesses again.
    expect(body.hint).toContain('pending')
    expect(body.hint).toContain('unread')
  })

  it('the hint quotes the value actually sent, not a canned one', async () => {
    // A caller who typed "zoldbeka" and is told 'there is no "unread" state'
    // spends a moment believing it typed something else. (Roxy, 2026-09-01.)
    const { ctx, getBody } = makeCtx({ agent: 'agent-a', status: 'zoldbeka' })
    await tryHandleMessages(ctx)
    const hint = getBody().hint as string
    expect(hint).toContain('zoldbeka')
    expect(hint).not.toContain('unread')
  })

  it('rejects arbitrary nonsense rather than answering with everything', async () => {
    const { ctx, getStatus } = makeCtx({ agent: 'agent-a', status: 'zoldbeka' })
    expect(await tryHandleMessages(ctx)).toBe(true)
    expect(getStatus()).toBe(400)
  })

  it('each accepted status actually filters -- the counts must differ', async () => {
    const counts: Record<string, number> = {}
    for (const status of ['pending', 'done', 'failed']) {
      const { ctx, getBody, getStatus } = makeCtx({ agent: 'agent-a', status, limit: '200' })
      expect(await tryHandleMessages(ctx)).toBe(true)
      expect(getStatus()).toBe(200)
      const rows = getBody() as any[]
      counts[status] = rows.length
      for (const row of rows) expect(row.status).toBe(status)
    }
    expect(counts).toEqual({ pending: 1, done: 1, failed: 1 })

    // Positive control: unfiltered returns MORE than any single state. Without
    // it, three filters that all silently returned nothing would also "pass".
    const { ctx, getBody } = makeCtx({ agent: 'agent-a', limit: '200' })
    await tryHandleMessages(ctx)
    expect((getBody() as any[]).length).toBe(3)
  })

  it('the global list (no agent) filters by status too', async () => {
    const { ctx, getBody, getStatus } = makeCtx({ status: 'done', limit: '200' })
    expect(await tryHandleMessages(ctx)).toBe(true)
    expect(getStatus()).toBe(200)
    const rows = getBody() as any[]
    expect(rows.length).toBe(1)
    expect(rows[0].status).toBe('done')
  })
})
