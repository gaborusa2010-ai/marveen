// #185: GET /api/tool-log read only `since`. `?agent=roxy`, `?limit=5` and a
// misspelt `?agnet=` all answered 200 with the same unfiltered rows (measured
// on the live server 2026-10-06: 263 rows, identical md5 for every query, rows
// from three agents), so a caller read every agent's calls as one agent's.
// Pinned: `agent` / `agent_id` filter, `limit` keeps the newest rows, and an
// unknown parameter is a 400 instead of a silently widened answer.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { initDatabase, logToolCall, getDb } from '../db.js'
import { tryHandleToolLog } from '../web/routes/tool-log.js'
import type { RouteContext } from '../web/routes/types.js'

vi.mock('../logger.js', () => ({ logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() } }))

async function get(query: string): Promise<{ status: number; body: any }> {
  const out: { status: number; body: any } = { status: 200, body: null }
  const res: any = {
    writeHead(status: number) { out.status = status; return res },
    setHeader() { return res },
    end(chunk?: string) { if (chunk) out.body = JSON.parse(chunk) },
  }
  const url = new URL(`http://localhost:3420/api/tool-log${query}`)
  const ctx = { req: {} as any, res, path: url.pathname, method: 'GET', url } as RouteContext
  expect(await tryHandleToolLog(ctx)).toBe(true)
  return out
}

beforeEach(() => {
  initDatabase(':memory:')
  // Six rows, two agents, distinct timestamps so the order is unambiguous.
  const now = Math.floor(Date.now() / 1000)
  const rows: [string, string, number][] = [
    ['marveen', 'Read', now - 50], ['cody', 'Bash', now - 40], ['roxy', 'Grep', now - 30],
    ['cody', 'Edit', now - 20], ['marveen', 'Write', now - 10], ['cody', 'Read', now - 5],
  ]
  for (const [agent, tool] of rows) logToolCall('s1', tool, null, true, agent, null, null)
  const ids = (getDb().prepare('SELECT id FROM tool_call_log ORDER BY id').all() as { id: number }[]).map((r) => r.id)
  rows.forEach(([, , ts], i) => getDb().prepare('UPDATE tool_call_log SET created_at = ? WHERE id = ?').run(ts, ids[i]))
})

describe('GET /api/tool-log filters (#185)', () => {
  it('without parameters still returns every row in the window, oldest first', async () => {
    const out = await get('')
    expect(out.status).toBe(200)
    expect(out.body.map((r: any) => r.tool_name)).toEqual(['Read', 'Bash', 'Grep', 'Edit', 'Write', 'Read'])
  })

  it('?agent= returns only that agent\'s rows', async () => {
    const out = await get('?agent=cody')
    expect(out.status).toBe(200)
    expect(out.body.map((r: any) => r.agent_id)).toEqual(['cody', 'cody', 'cody'])
    expect(out.body.map((r: any) => r.tool_name)).toEqual(['Bash', 'Edit', 'Read'])
  })

  it('?agent_id= is the same filter', async () => {
    const out = await get('?agent_id=roxy')
    expect(out.status).toBe(200)
    expect(out.body.map((r: any) => r.tool_name)).toEqual(['Grep'])
  })

  it('an agent with no rows gets an empty list, not everyone\'s rows', async () => {
    const out = await get('?agent=nosuchagent')
    expect(out.status).toBe(200)
    expect(out.body).toEqual([])
  })

  it('?limit= keeps the NEWEST rows, still oldest first', async () => {
    const out = await get('?limit=2')
    expect(out.status).toBe(200)
    expect(out.body.map((r: any) => r.tool_name)).toEqual(['Write', 'Read'])
  })

  it('agent and limit combine', async () => {
    const out = await get('?agent=cody&limit=2')
    expect(out.body.map((r: any) => [r.agent_id, r.tool_name])).toEqual([['cody', 'Edit'], ['cody', 'Read']])
  })

  it('since still narrows the window', async () => {
    const out = await get('?since=25')
    expect(out.body.map((r: any) => r.tool_name)).toEqual(['Edit', 'Write', 'Read'])
  })

  it('a misspelt parameter is a 400 naming it, not a silently widened answer', async () => {
    const out = await get('?agnet=roxy')
    expect(out.status).toBe(400)
    expect(out.body.unknown).toEqual(['agnet'])
    expect(out.body.known).toEqual(['since', 'agent', 'agent_id', 'limit'])
  })

  for (const bad of ['0', '-3', '2.5', 'abc', '']) {
    it(`limit=${JSON.stringify(bad)} is a 400`, async () => {
      const out = await get(`?limit=${bad}`)
      expect(out.status).toBe(400)
      expect(out.body.error).toContain('limit')
    })
  }

  it('an empty agent is a 400, not "every agent"', async () => {
    const out = await get('?agent=')
    expect(out.status).toBe(400)
  })

  it('agent and agent_id naming different agents is a 400', async () => {
    const out = await get('?agent=cody&agent_id=roxy')
    expect(out.status).toBe(400)
  })

  it('agent and agent_id naming the same agent is accepted', async () => {
    const out = await get('?agent=roxy&agent_id=roxy')
    expect(out.status).toBe(200)
    expect(out.body.map((r: any) => r.tool_name)).toEqual(['Grep'])
  })
})
