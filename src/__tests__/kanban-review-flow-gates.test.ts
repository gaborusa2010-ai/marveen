// #663 (owner tg 9204, 9211): a sub-agent's card goes "read fully -> skill -> work -> testing",
// the main agent reviews it, and only then does the agent start its next card. The board enforces
// the order on the status writes (POST /move and PUT), for a SUB-AGENT actor only:
//   1. done is refused (review_required) -- the main agent closes after the review;
//   2. in_progress is refused until the agent has read the card (read_card_first), a read being
//      GET /api/kanban/<id> carrying the agent's X-Agent-Id claim, within 12 hours;
//   3. in_progress is refused while another card of the agent is in testing (review_pending);
//   4. a card entering testing tells the main agent once, with no card text in the message.
// The owner's browser session, the main agent and an actor-less caller are never affected.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { Readable } from 'node:stream'
import type http from 'node:http'

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

vi.mock('../config.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../config.js')>()),
  MAIN_AGENT_ID: 'orin',
  BOT_NAME: 'Orin',
  OWNER_NAME: 'Owner',
}))

const { mockCreateAgentMessage } = vi.hoisted(() => ({ mockCreateAgentMessage: vi.fn() }))
vi.mock('../db.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../db.js')>()
  return { ...actual, createAgentMessage: mockCreateAgentMessage }
})

vi.mock('../web/agent-config.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../web/agent-config.js')>()),
  listAgentNames: () => ['dex', 'zara'],
  readAgentDisplayName: (n: string) => n,
}))

vi.mock('../web/agent-process.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../web/agent-process.js')>()),
  isAgentRunning: () => true,
}))

import { initDatabase, createKanbanCard, getKanbanCard, getKanbanCardEvents } from '../db.js'
import {
  tryHandleKanban, recordKanbanCardRead, resetKanbanCardReads, CARD_READ_TTL_MS,
} from '../web/routes/kanban.js'

type Auth = { kind: 'token' | 'session'; agent?: string; user?: string } | undefined
interface Result { status: number; body: any }

async function call(method: string, path: string, payload?: unknown, auth?: Auth): Promise<Result> {
  const req = Readable.from(payload === undefined ? [] : [Buffer.from(JSON.stringify(payload))]) as unknown as http.IncomingMessage
  let status = 200
  let raw = ''
  const res = {
    writeHead: vi.fn((s: number) => { status = s }),
    end: vi.fn((b?: string) => { raw = b ?? '' }),
    setHeader: vi.fn(),
  } as unknown as http.ServerResponse
  const handled = await tryHandleKanban({
    req, res, path, method, url: new URL(`http://localhost${path}`), auth,
  } as never)
  expect(handled).toBe(true)
  return { status, body: raw ? JSON.parse(raw) : {} }
}

const move = (id: string, body: Record<string, unknown>, auth?: Auth) => call('POST', `/api/kanban/${id}/move`, body, auth)
const put = (id: string, body: Record<string, unknown>, auth?: Auth) => call('PUT', `/api/kanban/${id}`, body, auth)
const readCard = (id: string, agent?: string) => call('GET', `/api/kanban/${id}`, undefined, { kind: 'token', agent })
const reviewNotices = () => mockCreateAgentMessage.mock.calls.filter((c) => String(c[2]).startsWith('[ellenorzes]'))

beforeEach(() => {
  vi.clearAllMocks()
  initDatabase(':memory:')
  resetKanbanCardReads()
  createKanbanCard({ id: 'c-1', title: 'SECRET TITLE one', assignee: 'dex', status: 'in_progress' })
  createKanbanCard({ id: 'c-2', title: 'Second', assignee: 'dex', status: 'planned' })
})

describe('gate 1: a sub-agent cannot close a card', () => {
  it('refuses done from a sub-agent actor with review_required, and nothing moves', async () => {
    const eventsBefore = getKanbanCardEvents('c-1').length
    const r = await move('c-1', { status: 'done', actor: 'dex' })
    expect(r.status).toBe(409)
    expect(r.body.error).toBe('review_required')
    expect(r.body.hint).toContain('"status":"testing","actor":"dex"')
    expect(getKanbanCard('c-1')?.status).toBe('in_progress')
    expect(getKanbanCardEvents('c-1')).toHaveLength(eventsBefore)
  })

  it('matches the actor case-insensitively', async () => {
    expect((await move('c-1', { status: 'done', actor: 'Dex' })).status).toBe(409)
  })

  it('refuses the same close through the whole-card PUT', async () => {
    const r = await put('c-1', { status: 'done', actor: 'dex' })
    expect(r.status).toBe(409)
    expect(r.body.error).toBe('review_required')
    expect(getKanbanCard('c-1')?.status).toBe('in_progress')
    // A PUT that does not touch the status is not gated.
    expect((await put('c-1', { title: 'Renamed', actor: 'dex' })).status).toBe(200)
  })

  it('lets the main agent, an actor-less caller and the owner session close', async () => {
    expect((await move('c-1', { status: 'done', actor: 'orin' })).status).toBe(200)
    expect((await move('c-2', { status: 'done' })).status).toBe(200)
    createKanbanCard({ id: 'c-3', title: 'Owner', assignee: 'dex', status: 'in_progress' })
    // Even a session whose body names an agent is the owner's browser, not that agent.
    expect((await move('c-3', { status: 'done', actor: 'dex' }, { kind: 'session', user: 'owner' })).status).toBe(200)
  })

  it('a name that is not a registered agent is not gated', async () => {
    expect((await move('c-1', { status: 'done', actor: 'scheduler' })).status).toBe(200)
  })
})

describe('gate 2: read the card fully before in_progress', () => {
  it('refuses in_progress on an unread card with the exact read calls', async () => {
    const r = await move('c-2', { status: 'in_progress', actor: 'dex' })
    expect(r.status).toBe(409)
    expect(r.body.error).toBe('read_card_first')
    expect(r.body.reads).toHaveLength(2)
    expect(r.body.reads[0]).toContain('-H "X-Agent-Id: dex"')
    expect(r.body.reads[0]).toMatch(/\/api\/kanban\/c-2$/)
    expect(r.body.reads[1]).toMatch(/\/api\/kanban\/c-2\/comments$/)
    expect(getKanbanCard('c-2')?.status).toBe('planned')
  })

  it('a GET of the card with the agent claim records the read and unlocks the move', async () => {
    expect((await readCard('c-2', 'dex')).status).toBe(200)
    expect((await move('c-2', { status: 'in_progress', actor: 'dex' })).status).toBe(200)
    expect(getKanbanCard('c-2')?.status).toBe('in_progress')
  })

  it('a GET without the claim, by another agent, or of another card does not count', async () => {
    await readCard('c-2')
    await readCard('c-2', 'zara')
    await readCard('c-1', 'dex')
    expect((await move('c-2', { status: 'in_progress', actor: 'dex' })).body.error).toBe('read_card_first')
  })

  it('a read older than 12 hours does not count', async () => {
    recordKanbanCardRead('dex', 'c-2', Date.now() - CARD_READ_TTL_MS - 60_000)
    expect((await move('c-2', { status: 'in_progress', actor: 'dex' })).body.error).toBe('read_card_first')
    recordKanbanCardRead('dex', 'c-2', Date.now() - CARD_READ_TTL_MS + 60_000)
    expect((await move('c-2', { status: 'in_progress', actor: 'dex' })).status).toBe(200)
  })

  it('the PUT is gated the same way', async () => {
    expect((await put('c-2', { status: 'in_progress', actor: 'dex' })).body.error).toBe('read_card_first')
  })

  it('the main agent assigning the card needs no read', async () => {
    expect((await move('c-2', { status: 'in_progress', actor: 'orin' })).status).toBe(200)
  })
})

describe('gate 3: no new card while one is in review', () => {
  beforeEach(async () => {
    createKanbanCard({ id: 'c-t', title: 'Under review', assignee: 'dex', status: 'testing' })
    recordKanbanCardRead('dex', 'c-2')
  })

  it('refuses in_progress while another card of the agent is in testing, naming it by seq', async () => {
    const r = await move('c-2', { status: 'in_progress', actor: 'dex' })
    expect(r.status).toBe(409)
    expect(r.body.error).toBe('review_pending')
    expect(r.body.card).toBe(`#${getKanbanCard('c-t')?.seq}`)
    expect(getKanbanCard('c-2')?.status).toBe('planned')
  })

  it('opens again once the main agent closes the reviewed card', async () => {
    expect((await move('c-t', { status: 'done', actor: 'orin' })).status).toBe(200)
    expect((await move('c-2', { status: 'in_progress', actor: 'dex' })).status).toBe(200)
  })

  it('another agent in testing does not block this one', async () => {
    createKanbanCard({ id: 'c-z', title: 'Zara review', assignee: 'zara', status: 'testing' })
    expect((await move('c-t', { status: 'done', actor: 'orin' })).status).toBe(200)
    expect((await move('c-2', { status: 'in_progress', actor: 'dex' })).status).toBe(200)
  })
})

describe('gate 4: testing tells the main agent', () => {
  it('a sub-agent moving its card to testing sends one notice with no card text', async () => {
    expect((await move('c-1', { status: 'testing', actor: 'dex' })).status).toBe(200)
    const notices = reviewNotices()
    expect(notices).toHaveLength(1)
    expect(notices[0][0]).toBe('system')
    expect(notices[0][1]).toBe('orin')
    expect(notices[0][2]).toBe(`[ellenorzes] #${getKanbanCard('c-1')?.seq} c-1 dex: kesz, ellenorizd`)
    expect(String(notices[0][2])).not.toContain('SECRET')
    // A reorder inside testing is not a second request.
    await move('c-1', { status: 'testing', actor: 'dex', sort_order: 5 })
    expect(reviewNotices()).toHaveLength(1)
  })

  it('the PUT to testing sends the same notice', async () => {
    expect((await put('c-1', { status: 'testing', actor: 'dex' })).status).toBe(200)
    expect(reviewNotices()).toHaveLength(1)
  })

  it('the main agent or an actor-less caller moving to testing sends nothing', async () => {
    await move('c-1', { status: 'testing', actor: 'orin' })
    await move('c-2', { status: 'testing' })
    expect(reviewNotices()).toHaveLength(0)
  })
})
