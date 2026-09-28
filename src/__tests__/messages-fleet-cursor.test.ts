// GET /api/messages: the fleet-wide path must honour `before`, and must say
// when it capped the answer.
//
// Two defects, one card (c1fa4c60), found on 2026-09-03 while counting a day of
// traffic:
//
//  1. THE CURSOR WAS ACCEPTED AND IGNORED. rejectUnknownQueryParams lists
//     'before' as known, so no error; the route parsed it; and then the
//     fleet-wide branch called listAgentMessages(limit, status) without it.
//     Measured: ?limit=200 and ?limit=200&before=1600 returned bit-for-bit the
//     same rows. The per-agent branch had the cursor all along, so anyone who
//     tested paging with ?agent= saw it work.
//
//  2. THE 200-ROW CAP WAS SILENT. ?limit=1000 answers 200 rows with HTTP 200.
//     A day's count taken that way lost 18 of 218 rows -- 8.3%, all of them
//     from before 08:12, because a DESC cap always eats the OLDEST end. The
//     short answer is indistinguishable from a quiet morning.
//
// The pairing is what makes it expensive: without the cursor there was no way
// to page past the cap, and without the header there was no sign you needed to.
import type http from 'node:http'
import { Readable } from 'node:stream'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { createAgentMessage, getDb, initDatabase, listAgentMessages } from '../db.js'
import { tryHandleMessages } from '../web/routes/messages.js'
import type { RouteContext } from '../web/routes/types.js'

interface MockRes {
  statusCode: number
  headers: Record<string, string>
  body: string
  writeHead(status: number, headers?: Record<string, string | string[]>): MockRes
  setHeader(k: string, v: string): void
  end(data?: string | Buffer): void
}

function mkRes(): MockRes {
  return {
    statusCode: 0,
    headers: {},
    body: '',
    writeHead(status) { this.statusCode = status; return this },
    setHeader(k, v) { this.headers[k] = v },
    end(data) { if (data !== undefined) this.body += data.toString() },
  }
}

async function call(path: string) {
  const req = Readable.from([]) as unknown as http.IncomingMessage & Record<string, unknown>
  req.headers = {}   // no accept-encoding: keep the body readable, no gzip
  const res = mkRes()
  const ctx = {
    req: req as http.IncomingMessage,
    res: res as unknown as http.ServerResponse,
    path: new URL(`http://127.0.0.1:3420${path}`).pathname,
    method: 'GET',
    url: new URL(`http://127.0.0.1:3420${path}`),
    auth: { kind: 'token' },
  } as unknown as RouteContext
  const handled = await tryHandleMessages(ctx)
  return { res, handled, rows: () => JSON.parse(res.body || '[]') as Array<{ id: number }> }
}

beforeAll(() => {
  process.env.NODE_ENV = 'test'
  initDatabase(':memory:')
})

beforeEach(() => {
  getDb().prepare('DELETE FROM agent_messages').run()
  for (let i = 0; i < 25; i++) createAgentMessage('marveen', 'cody', `sor ${i}`)
})

describe('GET /api/messages, fleet-wide', () => {
  it('the before cursor actually narrows the result', async () => {
    const all = await call('/api/messages?limit=25')
    const ids = all.rows().map(r => r.id)
    expect(ids).toHaveLength(25)

    const pivot = ids[10]
    const paged = await call(`/api/messages?limit=25&before=${pivot}`)
    const pagedIds = paged.rows().map(r => r.id)

    // The assertion the old code failed: a different set, not the same one.
    expect(pagedIds).not.toEqual(ids)
    expect(Math.max(...pagedIds)).toBeLessThan(pivot)
  })

  it('paging with the cursor walks the whole table without repeats', async () => {
    const seen = new Set<number>()
    let before: number | undefined
    for (let page = 0; page < 10; page++) {
      const r = await call(`/api/messages?limit=10${before !== undefined ? `&before=${before}` : ''}`)
      const rows = r.rows()
      if (rows.length === 0) break
      for (const row of rows) {
        // A repeat here is the tie-break bug: rows sharing a created_at second
        // under an unstable ORDER BY come back on two consecutive pages.
        expect(seen.has(row.id)).toBe(false)
        seen.add(row.id)
      }
      before = rows[rows.length - 1].id
    }
    expect(seen.size).toBe(25)
  })

  it('a capped answer says so in the headers', async () => {
    const r = await call('/api/messages?limit=10')
    expect(r.res.headers['X-Result-Truncated']).toBe('true')
    expect(r.res.headers['X-Result-Limit']).toBe('10')
    const rows = r.rows()
    expect(r.res.headers['X-Next-Before']).toBe(String(rows[rows.length - 1].id))
  })

  it('an answer that fits says nothing (no false alarm on a short list)', async () => {
    const r = await call('/api/messages?limit=100')
    expect(r.rows()).toHaveLength(25)
    expect(r.res.headers['X-Result-Truncated']).toBeUndefined()
  })

  it('the status filter still applies alongside the cursor', async () => {
    const ids = (await call('/api/messages?limit=25')).rows().map(r => r.id)
    getDb().prepare('UPDATE agent_messages SET status = ? WHERE id = ?').run('done', ids[15])
    const rows = listAgentMessages(25, 'done', ids[10])
    // Both conditions at once: status AND the cursor -- the branch that builds
    // the WHERE clause from two optional halves is where this would break.
    expect(rows.map(r => r.id)).toEqual([ids[15]])
  })
})
