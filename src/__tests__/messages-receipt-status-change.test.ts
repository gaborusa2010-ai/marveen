/**
 * Regression test: a SECOND receipt must not be sent when the status does not change.
 *
 * Found by Margo, n=2, the same shape both times: correcting the `result` of an
 * ALREADY closed row sent another "[Eredmény] msg_id:N status:done" to the sender.
 * A receipt is a message, and a message wakes the recipient -- so a plain text
 * correction woke an agent for nothing.
 *
 * WHY THE CAUSE IS NOT WHERE IT LOOKS: `markMessageDone` reports `changes > 0`,
 * and the UPDATE's WHERE has only the id (src/db.ts), so a `done -> done` PUT
 * rewrites `result` and `completed_at` and the row IS touched. `ok` is therefore
 * true for a correction too. The missing condition is not on the UPDATE -- it is
 * on the RECEIPT.
 *
 * WHAT WE DELIBERATELY DID NOT DO, and this test guards that half too: we did not
 * add `AND status != 'done'` to the UPDATE. Correcting a closed row's `result` is
 * a legitimate operation (Roxy did exactly that when fixing a guessed identifier),
 * and that guard would make the correction itself fail silently. Hence case 2
 * asserts BOTH that no second receipt appears AND that the corrected text really
 * landed in the row.
 *
 * The three cases are one positive, one negative control and one boundary:
 *   1. pending -> done   receipt IS created  (without this, case 2 would also pass
 *                        if receipts were broken outright)
 *   2. done    -> done   NO second receipt, and the new `result` IS stored
 *   3. failed  -> done   receipt IS created -- a real status change, not a rewrite
 *
 * Mutation check (run it, it takes a minute): remove `statusChanged` from the
 * condition in src/web/routes/messages.ts and case 2 must turn red.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { initDatabase, createAgentMessage, getAgentMessage, listAgentMessages, markMessageFailed } from '../db.js'
import { tryHandleMessages } from '../web/routes/messages.js'
import type { RouteContext } from '../web/routes/types.js'
import { Readable } from 'node:stream'

vi.mock('../config.js', async () => {
  const actual = await vi.importActual<typeof import('../config.js')>('../config.js')
  return { ...actual, MAIN_AGENT_ID: 'agent-a', ALLOWED_CHAT_ID: 'test-chat', OLLAMA_URL: '' }
})

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

/** PUT /api/messages/:id with an explicit status -- the path that emits the receipt. */
async function putStatus(id: number, status: string, result: string) {
  const url = new URL(`http://localhost:3420/api/messages/${id}`)
  let responseBody = ''
  const res = {
    writeHead: vi.fn(),
    setHeader: vi.fn(),
    end: (body?: string) => { responseBody = body || '' },
  }
  // readBody() consumes the request with req.on('data'/'end'), so the stand-in has
  // to be a real stream, not an async iterator.
  const req: any = Readable.from([Buffer.from(JSON.stringify({ status, result }))])
  req.headers = { 'content-type': 'application/json' }
  const ctx = { req, res: res as any, path: `/api/messages/${id}`, method: 'PUT', url } as RouteContext
  await tryHandleMessages(ctx)
  return responseBody ? JSON.parse(responseBody) : null
}

/** How many receipts exist for ONE message id.
 *
 * Match on the msg_id, never on "the newest row": created_at has one-second
 * granularity, so rows written inside the same second come back in an order
 * SQLite is free to choose. Counting is the point here, not reading the text.
 */
function receiptCount(id: number): number {
  const rows = listAgentMessages(200) as Array<{ content: string }>
  return rows.filter(r => r.content.startsWith(`[Eredmény] msg_id:${id} `)).length
}

beforeAll(() => { initDatabase(':memory:') })
afterAll(() => { vi.restoreAllMocks() })

describe('the receipt hangs on the status CHANGE, not on the UPDATE succeeding', () => {
  it('pending -> done creates a receipt (the positive control)', async () => {
    const msg = createAgentMessage('agent-a', 'agent-b', 'do the thing')
    const r = await putStatus(msg.id, 'done', 'kesz')
    expect(r).toEqual({ ok: true })
    expect(receiptCount(msg.id)).toBe(1)
  })

  it('done -> done is refused (409) and sends NO second receipt', async () => {
    const msg = createAgentMessage('agent-a', 'agent-b', 'do the other thing')
    await putStatus(msg.id, 'done', 'elso valasz')
    expect(receiptCount(msg.id)).toBe(1)

    // Since upstream v1.44.0 (closeOpenMessage) a second close is a 409: the
    // first close stands, the result is not overwritten.
    const again = await putStatus(msg.id, 'done', 'javitott valasz')
    expect(again).toMatchObject({ error: 'Message already closed', status: 'done' })
    expect(getAgentMessage(msg.id)?.result).toBe('elso valasz')

    // ...and it must not have woken the sender a second time.
    expect(receiptCount(msg.id)).toBe(1)
  })

  it('failed -> done is refused too: a failed message is already closed', async () => {
    const msg = createAgentMessage('agent-a', 'agent-b', 'do the third thing')
    markMessageFailed(msg.id, 'elszallt')
    expect(receiptCount(msg.id)).toBe(0)

    const r = await putStatus(msg.id, 'done', 'megis sikerult')
    expect(r).toMatchObject({ error: 'Message already closed', status: 'failed' })
    expect(receiptCount(msg.id)).toBe(0)
  })
})
