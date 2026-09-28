/**
 * Regression test for the silent filter-widening bug (2026-09-01).
 *
 * Root cause: only /api/messages validated its query-param names. Everywhere
 * else an unknown param was dropped in silence, so a TYPO in a filter widened
 * the query and answered 200 with a plausible, larger result set:
 *   ?agnet=cody     -> the whole fleet's memories, returned as cody's
 *   ?categoy=hot    -> every tier
 *   ?dat=2026-08-30 -> today's log under yesterday's name
 * Measured by Cody across /api/memories and /api/daily-log.
 *
 * An empty list invites suspicion; a full one never does. So the guard has to
 * fail loudly -- and, just as importantly, must NOT reject the documented
 * filters the dashboard and the fleet actually send.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { initDatabase, saveAgentMemory, appendDailyLog } from '../db.js'
import { tryHandleMemories } from '../web/routes/memories.js'
import { tryHandleDailyLog } from '../web/routes/daily-log.js'
import type { RouteContext } from '../web/routes/types.js'

vi.mock('../config.js', async () => {
  const actual = await vi.importActual<typeof import('../config.js')>('../config.js')
  return { ...actual, MAIN_AGENT_ID: 'agent-a', ALLOWED_CHAT_ID: 'test-chat', OLLAMA_URL: '' }
})

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

function makeCtx(path: string, searchParams: Record<string, string> = {}) {
  const url = new URL(`http://localhost:3420${path}`)
  for (const [k, v] of Object.entries(searchParams)) url.searchParams.set(k, v)

  let responseBody = ''
  let status = 200
  const res = {
    writeHead: vi.fn((code: number) => { status = code }),
    end: (body?: string) => { responseBody = body || '' },
  }

  return {
    ctx: { req: {} as any, res: res as any, path, method: 'GET', url } as RouteContext,
    getBody: () => (responseBody ? JSON.parse(responseBody) : null),
    getStatus: () => status,
  }
}

beforeAll(() => {
  initDatabase(':memory:')
  saveAgentMemory('agent-a', 'alpha owned by a', 'warm', 'contract')
  saveAgentMemory('agent-b', 'alpha owned by b', 'hot', 'contract')
  appendDailyLog('agent-a', 'a log line')
})

afterAll(() => { vi.restoreAllMocks() })

describe('unknown query param guard', () => {
  it('a typo in the agent filter is rejected, not silently widened', async () => {
    const { ctx, getBody, getStatus } = makeCtx('/api/memories', { agnet: 'agent-a' })
    expect(await tryHandleMemories(ctx)).toBe(true)
    expect(getStatus()).toBe(400)
    const body = getBody()
    expect(body.error).toBe('unknown query parameter')
    expect(body.unknown).toEqual(['agnet'])
    // The hint has to name the RIGHT parameter, or the 400 just moves the
    // guessing one step along.
    expect(body.known).toContain('agent')
  })

  it('a typo in the tier filter is rejected', async () => {
    const { ctx, getStatus } = makeCtx('/api/memories', { agent: 'agent-a', categoy: 'hot' })
    expect(await tryHandleMemories(ctx)).toBe(true)
    expect(getStatus()).toBe(400)
  })

  it('a typo in the daily-log date is rejected instead of falling back to today', async () => {
    const { ctx, getBody, getStatus } = makeCtx('/api/daily-log', { agent: 'agent-a', dat: '2026-08-30' })
    expect(await tryHandleDailyLog(ctx)).toBe(true)
    expect(getStatus()).toBe(400)
    expect(getBody().unknown).toEqual(['dat'])
  })

  // The other half of the gate: over-rejecting is a real failure too. These are
  // the exact param sets web/app.js and the fleet's documented curl calls send.
  it('every documented memories filter is still accepted', async () => {
    const legit: Record<string, string>[] = [
      { agent: 'agent-a' },
      { agent: 'agent-a', q: 'alpha' },
      { agent: 'agent-a', category: 'warm' },
      { agent: 'agent-a', tier: 'warm', limit: '50' },
      { agent_id: 'agent-a', limit: '5' },
      { q: 'alpha', mode: 'fts', limit: '10' },
    ]
    for (const params of legit) {
      const { ctx, getStatus } = makeCtx('/api/memories', params)
      expect(await tryHandleMemories(ctx)).toBe(true)
      expect({ params, status: getStatus() }).toEqual({ params, status: 200 })
    }
  })

  it('the documented daily-log filters are still accepted', async () => {
    const legit: Record<string, string>[] = [{ agent: 'agent-a' }, { agent_id: 'agent-a' }, { agent: 'agent-a', date: '2026-09-01' }]
    for (const params of legit) {
      const { ctx, getStatus } = makeCtx('/api/daily-log', params)
      expect(await tryHandleDailyLog(ctx)).toBe(true)
      expect({ params, status: getStatus() }).toEqual({ params, status: 200 })
    }
  })

  it('a request with no query params at all is untouched', async () => {
    const { ctx, getStatus } = makeCtx('/api/daily-log')
    expect(await tryHandleDailyLog(ctx)).toBe(true)
    expect(getStatus()).toBe(200)
  })
})
