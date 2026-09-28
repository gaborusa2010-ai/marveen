// POST /api/kanban/:id/comments must refuse a card that does not exist.
//
// Measured 2026-09-01 (card 223c2180): posting to the id 'NEMLETEZO' answered
// 200 with a fresh comment id, and the row landed in kanban_comments pointing
// at nothing. Fifteen such rows (132-146) accumulated from one debugging
// session. Nothing surfaces them: every view joins from a card, so an orphan
// comment is written, acknowledged, and then invisible forever.
//
// Why this is the expensive direction. A 404 is a mistyped id announcing
// itself. A 200 with an id back is indistinguishable from success, so the
// caller believes the comment is on the card and stops checking -- and the
// place it was supposed to inform never hears it. The sibling routes on this
// same path (/breakdown, /breakdown/accept) have always checked; comments was
// the one that did not.
//
// GET is covered too, for the same reason one level quieter: an empty array
// for a wrong id reads as "this card has no comments yet", which is a false
// measurement that looks exactly like a clean one.
import type http from 'node:http'
import { Readable } from 'node:stream'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { createKanbanCard, deleteKanbanCard, getKanbanComments, initDatabase } from '../db.js'
import { tryHandleKanban } from '../web/routes/kanban.js'
import type { RouteContext } from '../web/routes/types.js'

// Throwaway ids no live card can collide with -- the house rule is to prove
// this on a disposable card, never on a real one.
const REAL = 'ZZPROBE1'
const MISSING = 'ZZNINCS9'

interface MockRes {
  statusCode: number
  body: string
  writeHead(status: number, headers?: Record<string, string | string[]>): MockRes
  setHeader(k: string, v: string): void
  end(data?: string): void
}

function mkRes(): MockRes {
  return {
    statusCode: 0,
    body: '',
    writeHead(status) { this.statusCode = status; return this },
    setHeader() {},
    end(data) { if (data !== undefined) this.body += data },
  }
}

async function call(method: string, path: string, body?: unknown) {
  const payload = body === undefined ? [] : [Buffer.from(JSON.stringify(body))]
  const req = Readable.from(payload) as unknown as http.IncomingMessage & Record<string, unknown>
  req.headers = {}
  const res = mkRes()
  const ctx = {
    req: req as http.IncomingMessage,
    res: res as unknown as http.ServerResponse,
    path,
    method,
    url: new URL(`http://127.0.0.1:3420${path}`),
    auth: { kind: 'token' },
  } as unknown as RouteContext
  const handled = await tryHandleKanban(ctx)
  return { res, handled, json: () => JSON.parse(res.body || '{}') as Record<string, unknown> }
}

// An in-memory database, so this test cannot touch the live board even by
// accident -- the suite already refuses to run on the live install, and a
// throwaway id is the second line of defence, not the first.
beforeAll(() => {
  process.env.NODE_ENV = 'test'
  initDatabase(':memory:')
})

afterEach(() => {
  deleteKanbanCard(REAL)
  deleteKanbanCard(MISSING)
})

describe('kanban comments and a card id that does not exist', () => {
  it('POST to a real card still works (positive control on the instrument)', async () => {
    createKanbanCard({ id: REAL, title: 'orphan-comment probe' })
    const r = await call('POST', `/api/kanban/${REAL}/comments`, { author: 'teszt', content: 'proba' })
    expect(r.handled).toBe(true)
    expect(r.res.statusCode === 0 || r.res.statusCode === 200).toBe(true)
    expect(getKanbanComments(REAL)).toHaveLength(1)
  })

  it('POST to a missing card answers 404 and writes nothing', async () => {
    const before = getKanbanComments(MISSING).length
    const r = await call('POST', `/api/kanban/${MISSING}/comments`, { author: 'teszt', content: 'proba' })
    expect(r.handled).toBe(true)
    expect(r.res.statusCode).toBe(404)
    // The assertion that actually matters: no row was created. A 404 that still
    // wrote would fix the report and leave the defect.
    expect(getKanbanComments(MISSING)).toHaveLength(before)
  })

  it('the 400 for a missing author still comes before the 404', async () => {
    // Order matters only so the caller gets the more specific complaint about
    // their own payload rather than a card-not-found for an id that is fine.
    createKanbanCard({ id: REAL, title: 'orphan-comment probe' })
    const r = await call('POST', `/api/kanban/${REAL}/comments`, { content: 'no author' })
    expect(r.res.statusCode).toBe(400)
  })

  it('GET on a missing card answers 404, not an empty list', async () => {
    const r = await call('GET', `/api/kanban/${MISSING}/comments`)
    expect(r.handled).toBe(true)
    expect(r.res.statusCode).toBe(404)
  })

  it('GET on a real card with no comments still answers with an empty list', async () => {
    createKanbanCard({ id: REAL, title: 'orphan-comment probe' })
    const r = await call('GET', `/api/kanban/${REAL}/comments`)
    expect(r.handled).toBe(true)
    expect(r.res.statusCode === 0 || r.res.statusCode === 200).toBe(true)
    expect(Array.isArray(r.json() as unknown)).toBe(true)
  })
})
