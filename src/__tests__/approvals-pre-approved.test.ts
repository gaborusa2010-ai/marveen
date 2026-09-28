/**
 * POST /api/approvals with `pre_approved_by`: record the audit row, do NOT ping
 * the owner.
 *
 * WHY (2026-09-01): acting under a standing permission still deserves a trail,
 * so the action was posted as an approval and resolved a second later. But the
 * notification fires on POST -- so the owner got a full "[JÓVÁHAGYÁS KELL]" push
 * twenty minutes after telling me, for the second time that day, that re-asking
 * for permission he had already given wastes his time. The row was the point;
 * the ping was the noise he had just complained about.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { initDatabase, getApproval } from '../db.js'
import { tryHandleApprovals } from '../web/routes/approvals.js'
import type { RouteContext } from '../web/routes/types.js'

const notified: string[] = []

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

// The owner-facing channel: every call here is a push to his phone.
vi.mock('../web/channel-send.js', async (orig) => {
  try {
    const actual = await (orig as () => Promise<Record<string, unknown>>)()
    return { ...actual, sendToOwner: vi.fn(async (t: string) => { notified.push(t) }) }
  } catch {
    return { sendToOwner: vi.fn(async (t: string) => { notified.push(t) }) }
  }
})

function makeCtx(body: unknown) {
  const raw = Buffer.from(JSON.stringify(body))
  let responseBody = ''
  let status = 200
  const req: any = {
    headers: {},
    [Symbol.asyncIterator]: async function* () { yield raw },
    on(ev: string, cb: (...a: any[]) => void) {
      if (ev === 'data') cb(raw)
      if (ev === 'end') cb()
      return this
    },
  }
  const res = {
    writeHead: vi.fn((code: number) => { status = code }),
    setHeader: vi.fn(),
    end: (b?: string) => { responseBody = b || '' },
  }
  return {
    ctx: { req, res: res as any, path: '/api/approvals', method: 'POST', url: new URL('http://localhost:3420/api/approvals') } as RouteContext,
    getBody: () => (responseBody ? JSON.parse(responseBody) : null),
    getStatus: () => status,
  }
}

beforeAll(() => { initDatabase(':memory:') })
afterAll(() => { vi.restoreAllMocks() })

const base = { agent_id: 'marveen', category: 'skill_patch', action_description: 'teszt' }

describe('POST /api/approvals pre_approved_by', () => {
  it('records the row already approved, naming the standing permission', async () => {
    const { ctx, getBody, getStatus } = makeCtx({ ...base, pre_approved_by: 'standing_authorization:1304' })
    expect(await tryHandleApprovals(ctx)).toBe(true)
    expect(getStatus()).toBe(201)
    const body = getBody()
    expect(body.status).toBe('approved')
    expect(body.resolved_by).toBe('standing_authorization:1304')
    // The trail must survive a re-read, not just the response shape.
    expect(getApproval(body.id)?.status).toBe('approved')
  })

  it('refuses the requesting agent as the permission source', async () => {
    // Self-approval, one step earlier than the PATCH guard catches it.
    const { ctx, getStatus } = makeCtx({ ...base, pre_approved_by: 'marveen' })
    expect(await tryHandleApprovals(ctx)).toBe(true)
    expect(getStatus()).toBe(403)
  })

  it('without the field the request stays pending -- the normal ask still works', async () => {
    const { ctx, getBody } = makeCtx(base)
    expect(await tryHandleApprovals(ctx)).toBe(true)
    expect(getBody().status).toBe('pending')
    expect(getBody().resolved_by).toBeNull()
  })

  it('an empty or whitespace value is not a permission', async () => {
    const { ctx, getBody } = makeCtx({ ...base, pre_approved_by: '   ' })
    expect(await tryHandleApprovals(ctx)).toBe(true)
    expect(getBody().status).toBe('pending')
  })
})
