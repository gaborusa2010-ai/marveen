import { randomBytes } from 'node:crypto'
import { listOwnerTodos, getOwnerTodo, createOwnerTodo, updateOwnerTodo, softDeleteOwnerTodo } from '../../db.js'
import { readBody, json, rejectUnknownQueryParams, methodNotAllowed } from '../http-helpers.js'
import type { RouteContext } from './types.js'

// The owner's own to-do list (#581), a separate page from the team notes
// ("Jegyzetek", notes.ts). Agents may add items for the owner too: they pass
// their name as created_by, which the page shows; without it the item counts
// as the owner's own. Deletion is soft, and GET lists done items for 30 days.

const POST_FIELDS = ['content', 'due_date', 'created_by'] as const
const PUT_FIELDS = ['content', 'due_date', 'done'] as const
const MAX_CONTENT_CHARS = 2000
const MAX_NAME_CHARS = 64
const DONE_WINDOW_SECONDS = 30 * 86400
type Body = Record<string, unknown>

function newTodoId(): string {
  for (let i = 0; i < 10; i++) {
    const id = randomBytes(3).toString('hex')
    if (!getOwnerTodo(id)) return id
  }
  throw new Error('Could not allocate a unique todo id')
}

function isValidDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const d = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value
}

async function readJsonObject(ctx: RouteContext): Promise<Body | null> {
  const raw = (await readBody(ctx.req)).toString().trim()
  if (!raw) { json(ctx.res, { error: 'JSON body required' }, 400); return null }
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { json(ctx.res, { error: 'Body is not valid JSON' }, 400); return null }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    json(ctx.res, { error: 'Body must be a JSON object' }, 400); return null
  }
  return parsed as Body
}

function rejectUnknownFields(ctx: RouteContext, body: Body, accepted: readonly string[]): boolean {
  const unknown = Object.keys(body).filter((key) => !accepted.includes(key))
  if (!unknown.length) return false
  json(ctx.res, { error: `Unknown field(s): ${unknown.join(', ')}. Accepted: ${accepted.join(', ')}` }, 400)
  return true
}

function stringField(ctx: RouteContext, body: Body, name: string, required: boolean, max: number): string | undefined | null {
  const value = body[name]
  if (value === undefined) {
    if (!required) return undefined
    json(ctx.res, { error: `${name} is required` }, 400); return null
  }
  if (typeof value !== 'string' || !value.trim()) {
    json(ctx.res, { error: `${name} must be a non-empty string` }, 400); return null
  }
  const trimmed = value.trim()
  if (trimmed.length > max) {
    json(ctx.res, { error: `${name} is too long (max ${max} characters)` }, 400); return null
  }
  return trimmed
}

function dueDateField(ctx: RouteContext, body: Body): string | null | undefined | false {
  const value = body.due_date
  if (value === undefined) return undefined
  if (value === null || value === '') return null
  if (typeof value !== 'string' || !isValidDate(value.trim())) {
    json(ctx.res, { error: 'due_date must be a date in YYYY-MM-DD format (or null)' }, 400); return false
  }
  return value.trim()
}

export async function tryHandleTodos(ctx: RouteContext): Promise<boolean> {
  const { res, path, method, url } = ctx
  if (path === '/api/todos') {
    if (method === 'GET') {
      if (rejectUnknownQueryParams(res, url, [])) return true
      json(res, { todos: listOwnerTodos(Math.floor(Date.now() / 1000) - DONE_WINDOW_SECONDS) })
      return true
    }
    if (method === 'POST') {
      const body = await readJsonObject(ctx)
      if (!body) return true
      if (rejectUnknownFields(ctx, body, POST_FIELDS)) return true
      const content = stringField(ctx, body, 'content', true, MAX_CONTENT_CHARS)
      if (content === null) return true
      const createdBy = body.created_by === undefined ? 'owner' : stringField(ctx, body, 'created_by', false, MAX_NAME_CHARS)
      if (createdBy === null) return true
      const dueDate = dueDateField(ctx, body)
      if (dueDate === false) return true
      const id = newTodoId()
      createOwnerTodo({ id, content: content!, due_date: dueDate ?? null, created_by: createdBy! })
      json(res, { todo: getOwnerTodo(id) }, 201)
      return true
    }
    methodNotAllowed(res, method, ['GET', 'POST'])
    return true
  }

  const match = path.match(/^\/api\/todos\/([^/]+)$/)
  if (!match) return false
  const id = decodeURIComponent(match[1])
  if (method === 'PUT') {
    const body = await readJsonObject(ctx)
    if (!body) return true
    if (rejectUnknownFields(ctx, body, PUT_FIELDS)) return true
    const content = stringField(ctx, body, 'content', false, MAX_CONTENT_CHARS)
    if (content === null) return true
    const dueDate = dueDateField(ctx, body)
    if (dueDate === false) return true
    let doneAt: number | null | undefined
    if (body.done !== undefined) {
      if (typeof body.done !== 'boolean') { json(res, { error: 'done must be a boolean' }, 400); return true }
      const existing = getOwnerTodo(id)
      doneAt = body.done ? (existing?.done_at ?? Math.floor(Date.now() / 1000)) : null
    }
    if (!updateOwnerTodo(id, { content, due_date: dueDate, done_at: doneAt })) {
      json(res, { error: 'Todo not found or already deleted' }, 404); return true
    }
    json(res, { todo: getOwnerTodo(id) })
    return true
  }
  if (method === 'DELETE') {
    if (!softDeleteOwnerTodo(id)) { json(res, { error: 'Todo not found or already deleted' }, 404); return true }
    json(res, { ok: true })
    return true
  }
  methodNotAllowed(res, method, ['PUT', 'DELETE'])
  return true
}
