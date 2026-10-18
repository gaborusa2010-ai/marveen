import { describe, it, expect, beforeEach } from 'vitest'
import { Readable } from 'node:stream'
import { initDatabase, getDb, getOwnerTodo } from '../db.js'
import { tryHandleTodos } from '../web/routes/todos.js'
import type { RouteContext } from '../web/routes/types.js'

async function call(method: string, pathAndQuery: string, payload?: unknown): Promise<{ handled: boolean; status: number; body: any }> {
  const out = { status: 200, body: null as any }
  const res: any = {
    writeHead(status: number) { out.status = status; return res },
    setHeader() { return res },
    end(chunk?: string) { if (chunk) out.body = JSON.parse(chunk) },
  }
  const raw = payload === undefined ? '' : typeof payload === 'string' ? payload : JSON.stringify(payload)
  const req: any = Readable.from(raw ? [Buffer.from(raw)] : [])
  const url = new URL(`http://localhost:3420${pathAndQuery}`)
  const handled = await tryHandleTodos({ req, res, path: url.pathname, method, url } as RouteContext)
  return { handled, ...out }
}

async function create(fields: Record<string, unknown>): Promise<any> {
  const response = await call('POST', '/api/todos', fields)
  expect(response.status).toBe(201)
  return response.body.todo
}

describe('/api/todos', () => {
  beforeEach(() => { initDatabase(':memory:') })

  it('creates with owner default and trims values', async () => {
    const todo = await create({ content: '  Pay invoice  ', created_by: '  agent-one  ', due_date: '2026-10-20' })
    expect(todo.id).toMatch(/^[0-9a-f]{6}$/)
    expect(todo).toMatchObject({ content: 'Pay invoice', created_by: 'agent-one', due_date: '2026-10-20', done_at: null, deleted_at: null })
    const defaultOwner = await create({ content: 'Owner item' })
    expect(defaultOwner.created_by).toBe('owner')
  })

  it('validates create fields', async () => {
    const cases: Array<[unknown, string]> = [
      [{}, 'content'],
      [{ content: '   ' }, 'content'],
      [{ content: 'x'.repeat(2001) }, 'max 2000'],
      [{ content: 'x', due_date: '2026-02-30' }, 'YYYY-MM-DD'],
      [{ content: 'x', priority: 1 }, 'Accepted: content, due_date, created_by'],
      [{ content: 'x', created_by: 'a'.repeat(65) }, 'max 64'],
    ]
    for (const [payload, needle] of cases) {
      const response = await call('POST', '/api/todos', payload)
      expect(response.status).toBe(400)
      expect(response.body.error).toContain(needle)
    }
  })

  it('orders dated and undated open items before completed items', async () => {
    const late = await create({ content: 'late', due_date: '2026-10-20' })
    const undated = await create({ content: 'undated' })
    const overdue = await create({ content: 'overdue', due_date: '2020-01-01' })
    const done = await create({ content: 'done', due_date: '2019-01-01' })
    await call('PUT', `/api/todos/${done.id}`, { done: true })
    const response = await call('GET', '/api/todos')
    expect(response.status).toBe(200)
    expect(response.body.todos.map((todo: any) => todo.id)).toEqual([overdue.id, late.id, undated.id, done.id])
    expect((await call('GET', '/api/todos?extra=1')).status).toBe(400)
  })

  it('round-trips done and undone and requires a boolean', async () => {
    const todo = await create({ content: 'toggle me' })
    const done = await call('PUT', `/api/todos/${todo.id}`, { done: true })
    expect(typeof done.body.todo.done_at).toBe('number')
    const firstDoneAt = done.body.todo.done_at
    expect((await call('PUT', `/api/todos/${todo.id}`, { done: true })).body.todo.done_at).toBe(firstDoneAt)
    expect((await call('PUT', `/api/todos/${todo.id}`, { done: 'true' })).status).toBe(400)
    const undone = await call('PUT', `/api/todos/${todo.id}`, { done: false })
    expect(undone.body.todo.done_at).toBeNull()
  })

  it('edits content and clears the due date', async () => {
    const todo = await create({ content: 'old', due_date: '2026-10-20' })
    const response = await call('PUT', `/api/todos/${todo.id}`, { content: '  new  ', due_date: null })
    expect(response.status).toBe(200)
    expect(response.body.todo).toMatchObject({ content: 'new', due_date: null })
  })

  it('soft deletes and rejects later mutation', async () => {
    const todo = await create({ content: 'remove me' })
    expect((await call('DELETE', `/api/todos/${todo.id}`)).body).toEqual({ ok: true })
    expect(getOwnerTodo(todo.id)?.deleted_at).not.toBeNull()
    expect((await call('GET', '/api/todos')).body.todos).toEqual([])
    expect((await call('DELETE', `/api/todos/${todo.id}`)).status).toBe(404)
    expect((await call('PUT', `/api/todos/${todo.id}`, { content: 'no' })).status).toBe(404)
  })

  it('does not list completed items older than 30 days', async () => {
    const todo = await create({ content: 'ancient completion' })
    await call('PUT', `/api/todos/${todo.id}`, { done: true })
    getDb().prepare('UPDATE owner_todos SET done_at = ? WHERE id = ?').run(Math.floor(Date.now() / 1000) - 31 * 86400, todo.id)
    expect((await call('GET', '/api/todos')).body.todos).toEqual([])
  })

  it('returns 405 for wrong methods and false for foreign paths', async () => {
    expect((await call('PATCH', '/api/todos/abc123', {})).status).toBe(405)
    expect((await call('DELETE', '/api/todos')).status).toBe(405)
    expect((await call('GET', '/api/todosx')).handled).toBe(false)
  })
})
