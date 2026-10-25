// estimate_date (owner, Telegram msg 9294, 2026-10-25): a card without a fixed
// deadline gets an ESTIMATED finish date. Same storage shape as due_date (unix
// seconds, bare "YYYY-MM-DD" -> UTC midnight, null clears), accepted by POST and
// PUT, returned by GET -- but never treated as a deadline: the waiting-overdue
// list stays on due_date only.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { Readable } from 'node:stream'
import type http from 'node:http'

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

vi.mock('../db.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../db.js')>()
  return { ...actual, createAgentMessage: vi.fn() }
})

import { initDatabase, createKanbanCard, getKanbanCard, getStuckKanbanCards } from '../db.js'
import { tryHandleKanban } from '../web/routes/kanban.js'

async function call(method: string, path: string, payload?: unknown): Promise<{ status: number; body: any }> {
  const text = payload === undefined ? '' : JSON.stringify(payload)
  const req = Readable.from([Buffer.from(text)]) as unknown as http.IncomingMessage
  let status = 200
  let chunk = ''
  const res = {
    writeHead: (s: number) => { status = s; return res },
    end: (c?: unknown) => { if (c !== undefined) chunk = String(c) },
    setHeader: () => {},
  } as unknown as http.ServerResponse
  const handled = await tryHandleKanban({ req, res, path, method, url: new URL(`http://localhost${path}`) } as never)
  expect(handled).toBe(true)
  return { status, body: chunk ? JSON.parse(chunk) : undefined }
}

beforeEach(() => {
  initDatabase(':memory:')
})

describe('estimate_date round-trip', () => {
  it('POST stores it, GET returns it, PUT changes and clears it', async () => {
    const created = await call('POST', '/api/kanban', { title: 'Estimated', estimate_date: '2026-11-03' })
    expect(created.status).toBe(200)
    const id = created.body.id
    expect(getKanbanCard(id)!.estimate_date).toBe(Date.UTC(2026, 10, 3) / 1000)

    const put = await call('PUT', `/api/kanban/${id}`, { estimate_date: Date.UTC(2026, 10, 10) / 1000, actor: 'marveen' })
    expect(put.status).toBe(200)
    const list = await call('GET', '/api/kanban')
    const row = (list.body as any[]).find((c) => c.id === id)
    expect(row.estimate_date).toBe(Date.UTC(2026, 10, 10) / 1000)
    expect(row.due_date).toBeNull()

    const cleared = await call('PUT', `/api/kanban/${id}`, { estimate_date: null, actor: 'marveen' })
    expect(cleared.status).toBe(200)
    expect(getKanbanCard(id)!.estimate_date).toBeNull()
  })

  it('a PUT of an unrelated field leaves estimate_date alone', async () => {
    createKanbanCard({ id: 'e1', title: 'Keep', estimate_date: Date.UTC(2026, 11, 1) / 1000 })
    await call('PUT', '/api/kanban/e1', { title: 'Keep renamed', actor: 'marveen' })
    expect(getKanbanCard('e1')!.estimate_date).toBe(Date.UTC(2026, 11, 1) / 1000)
  })
})

describe('estimate_date is not a deadline', () => {
  it('a waiting card with a past estimate is not overdue, one with a past due_date is', async () => {
    const past = Math.floor(Date.now() / 1000) - 5 * 86400
    createKanbanCard({ id: 'w-est', title: 'Waiting, estimate passed', status: 'waiting', estimate_date: past })
    createKanbanCard({ id: 'w-due', title: 'Waiting, due passed', status: 'waiting', due_date: past })
    const res = getStuckKanbanCards({ plannedDays: 7, activeDays: 3, creatorCommentWindowSec: 600 })
    const overdueIds = res.waiting.overdue.map((c) => c.id)
    expect(overdueIds).toContain('w-due')
    expect(overdueIds).not.toContain('w-est')
    expect(res.waiting.without_deadline).toBe(1)
  })
})
