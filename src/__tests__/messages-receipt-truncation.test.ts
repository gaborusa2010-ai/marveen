/**
 * Regression test for the SILENT receipt truncation (found 2026-09-02 by Cody).
 *
 * When an agent closes a delegated message, the delegator gets an automatic
 * "[Eredmény] msg_id:N status:done" receipt built from the `result` text. That
 * receipt sliced the result at 500 characters with no marker at all, so the
 * reader saw a sentence that simply stopped -- indistinguishable from a sentence
 * the sender chose to end there.
 *
 * How it was found, and why the shape of the evidence matters: two unrelated
 * receipts were both stored at EXACTLY 535 characters (a 35-character prefix
 * plus a 500-character slice), and both ended mid-word. One truncated message is
 * an anecdote; two different messages at an identical length is a ceiling.
 *
 * Measured at the same time, and it decides how bad the bug is: the stored
 * `result` is NOT truncated (511 and 506 characters in those two cases). Nothing
 * was ever lost -- only the receipt was short, and silent about it. That is why
 * the fix is a visible marker naming the true length and where to fetch the rest,
 * rather than a bigger slice: the cure for a silent cut is saying it was cut.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { initDatabase, createAgentMessage, getAgentMessage, listAgentMessages } from '../db.js'
import { tryHandleMessages, RESULT_NOTIFY_MAX } from '../web/routes/messages.js'
import type { RouteContext } from '../web/routes/types.js'
import { Readable } from 'node:stream'

vi.mock('../config.js', async () => {
  const actual = await vi.importActual<typeof import('../config.js')>('../config.js')
  return { ...actual, MAIN_AGENT_ID: 'agent-a', ALLOWED_CHAT_ID: 'test-chat', OLLAMA_URL: '' }
})

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

/** PUT /api/messages/:id -- the path that closes a row and emits the receipt. */
async function closeMessage(id: number, result: string) {
  const url = new URL(`http://localhost:3420/api/messages/${id}`)
  let responseBody = ''
  const res = {
    writeHead: vi.fn(),
    setHeader: vi.fn(),
    end: (body?: string) => { responseBody = body || '' },
  }
  const body = JSON.stringify({ status: 'done', result })
  // readBody() consumes the request with req.on('data'/'end'), so the stand-in
  // has to be a real stream, not an async iterator.
  const req: any = Readable.from([Buffer.from(body)])
  req.headers = { 'content-type': 'application/json' }
  const ctx = {
    req, res: res as any, path: `/api/messages/${id}`, method: 'PUT', url,
  } as RouteContext
  await tryHandleMessages(ctx)
  return responseBody ? JSON.parse(responseBody) : null
}

/** The receipt for ONE closed message, found by its id.
 *
 * Two traps live in this helper, both hit while writing it:
 *
 * 1. listAgentMessages is (limit, status) -- NOT (agent). An agent name passed as
 *    the limit is a datatype mismatch, not a filter.
 * 2. Do NOT take "the newest receipt" and hope it is yours. The query orders by
 *    created_at DESC, and created_at has one-second granularity, so three closes
 *    inside the same second come back in an order SQLite is free to choose. The
 *    first version of this test read the previous test's receipt and reported a
 *    missing marker that was never missing. Match the msg_id instead -- it is the
 *    only thing that ties a receipt to its message.
 */
function receiptFor(id: number): string {
  const rows = listAgentMessages(200) as Array<{ content: string }>
  const receipt = rows.find(r => r.content.startsWith(`[Eredmény] msg_id:${id} `))
  return receipt ? receipt.content : ''
}


/** GET /api/messages/:id -- the endpoint the truncation marker points at. */
async function getMessage(id: number) {
  const url = new URL(`http://localhost:3420/api/messages/${id}`)
  let responseBody = ''
  let status = 200
  const res = {
    writeHead: vi.fn((code: number) => { status = code }),
    setHeader: vi.fn(),
    end: (body?: string) => { responseBody = body || '' },
  }
  const ctx = {
    req: { headers: {} } as any, res: res as any,
    path: `/api/messages/${id}`, method: 'GET', url,
  } as RouteContext
  await tryHandleMessages(ctx)
  return { status, body: responseBody ? JSON.parse(responseBody) : null }
}

beforeAll(() => { initDatabase(':memory:') })
afterAll(() => { vi.restoreAllMocks() })

describe('inter-agent receipt truncation', () => {
  it('a short result rides in the receipt untouched', async () => {
    const msg = createAgentMessage('agent-a', 'agent-b', 'do the short thing')
    const short = 'Kesz, minden rendben.'
    await closeMessage(msg.id, short)

    const receipt = receiptFor(msg.id)
    expect(receipt).toContain(short)
    expect(receipt).not.toContain('levágva')
  })

  it('a long result is cut, and the receipt SAYS it was cut', async () => {
    const msg = createAgentMessage('agent-a', 'agent-b', 'do the long thing')
    // Comfortably past the ceiling, so the cut is certain. The ceiling is READ,
    // not hardcoded: upstream v1.37.0 moved it from 500 to 2000 and factored the
    // marker into resultSummary(), which silently retired a 500 written in here.
    // A test that hardcodes the number it is measuring stops measuring it.
    const long = 'x'.repeat(RESULT_NOTIFY_MAX + 190) + 'VEGSZO'
    await closeMessage(msg.id, long)

    const receipt = receiptFor(msg.id)
    // The marker is the whole point -- without it the reader cannot tell a cut
    // from a sentence that ended.
    expect(receipt).toContain('levágva')
    // It must say HOW MUCH is missing. The marker names the REMAINDER, not the
    // total, so that is what is asserted.
    expect(receipt).toContain(String(long.length - RESULT_NOTIFY_MAX))
    // And where to go for the rest: the id of the row that holds the full text,
    // plus the command that fetches it.
    expect(receipt).toContain(String(msg.id))
    expect(receipt).toContain('agent-msg-get.sh')
    // The tail really is gone from the receipt: this is a cut, not a rename.
    expect(receipt).not.toContain('VEGSZO')
  })

  it('the STORED result keeps the full text -- only the receipt is short', async () => {
    const msg = createAgentMessage('agent-a', 'agent-b', 'store the long thing')
    const long = 'y'.repeat(690) + 'VEGSZO'
    await closeMessage(msg.id, long)

    const stored = getAgentMessage(msg.id) as { result: string } | undefined
    expect(stored?.result).toBe(long)
    expect(stored?.result.length).toBe(long.length)
  })

  // Added after Cody measured that the marker pointed at a 404: the first version
  // of this suite proved the SENTENCE was written, never that the door it named
  // was open. A claim about the text is not a claim about the world.
  it('the endpoint the marker names actually answers', async () => {
    const msg = createAgentMessage('agent-a', 'agent-b', 'fetchable')
    const long = 'z'.repeat(690) + 'VEGSZO'
    await closeMessage(msg.id, long)

    const got = await getMessage(msg.id)
    expect(got.status).toBe(200)
    expect(got.body?.id).toBe(msg.id)
    // And it serves the FULL result -- which is the only reason to point there.
    expect(got.body?.result).toBe(long)
  })

  it('an unknown id answers 404, not the whole list', async () => {
    const got = await getMessage(999999)
    expect(got.status).toBe(404)
    expect(Array.isArray(got.body)).toBe(false)
  })
})
