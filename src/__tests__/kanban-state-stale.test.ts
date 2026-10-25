// State staleness (owner, Telegram msg 9298, 2026-10-25): decisions and results were written only
// as comments while the description stayed old, and owner-facing questions were built from the
// description. kanban_cards.description_updated_at records when the description last changed; a
// counting comment newer than it makes the card `state_stale` (with `stale_comments` = how many).
// Machine notices (author "system", "kartya-es-ertesites", automated=1, "[Heti átnézés]") never
// count, and done/archived cards are never stale. A SUB-AGENT is refused (409 state_update_first)
// when it moves a stale card to testing, or starts another card while one of its own open cards is
// stale. The main agent, the owner's session and an actor-less caller are not gated.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { Readable } from 'node:stream'
import type http from 'node:http'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

vi.mock('../config.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../config.js')>()),
  MAIN_AGENT_ID: 'orin',
  BOT_NAME: 'Orin',
  OWNER_NAME: 'Owner',
}))

vi.mock('../db.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../db.js')>()
  return { ...actual, createAgentMessage: vi.fn() }
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

import {
  initDatabase, createKanbanCard, getKanbanCard, getDb, addKanbanComment, updateKanbanCard,
  kanbanStateStaleness,
} from '../db.js'
import { tryHandleKanban, recordKanbanCardRead, resetKanbanCardReads, kanbanMoveInstructions } from '../web/routes/kanban.js'

type Auth = { kind: 'token' | 'session'; agent?: string; user?: string } | undefined
interface Result { status: number; body: any }

async function call(method: string, path: string, payload?: unknown, auth?: Auth): Promise<Result> {
  const req = Readable.from(payload === undefined ? [] : [Buffer.from(JSON.stringify(payload))]) as unknown as http.IncomingMessage
  ;(req as unknown as { headers: Record<string, string> }).headers = {}
  let status = 200
  let raw = ''
  const res = {
    writeHead: vi.fn((s: number) => { status = s }),
    end: vi.fn((b?: string) => { raw = b ?? '' }),
    setHeader: vi.fn(),
  } as unknown as http.ServerResponse
  const handled = await tryHandleKanban({ req, res, path, method, url: new URL(`http://localhost${path}`), auth } as never)
  expect(handled).toBe(true)
  return { status, body: raw ? JSON.parse(raw) : {} }
}

const move = (id: string, body: Record<string, unknown>) => call('POST', `/api/kanban/${id}/move`, body)
const put = (id: string, body: Record<string, unknown>) => call('PUT', `/api/kanban/${id}`, body)
const listRow = async (id: string) => ((await call('GET', '/api/kanban')).body as any[]).find((c) => c.id === id)

// Comments and descriptions written in the same second would tie; tests place the
// description stamp explicitly in the past instead of sleeping.
function stampDescription(id: string, at: number): void {
  getDb().prepare('UPDATE kanban_cards SET description_updated_at = ? WHERE id = ?').run(at, id)
}
const PAST = Math.floor(Date.now() / 1000) - 3600

beforeEach(() => {
  vi.clearAllMocks()
  initDatabase(':memory:')
  resetKanbanCardReads()
  createKanbanCard({ id: 'c-1', title: 'One', description: 'MOSTANI ALLAPOT: old', assignee: 'dex', status: 'in_progress' })
  createKanbanCard({ id: 'c-2', title: 'Two', assignee: 'dex', status: 'planned' })
  stampDescription('c-1', PAST)
  stampDescription('c-2', PAST)
})

describe('description_updated_at column', () => {
  it('a new card is stamped with its created_at', () => {
    createKanbanCard({ id: 'c-new', title: 'New' })
    const c = getKanbanCard('c-new')!
    expect(c.description_updated_at).toBe(c.created_at)
  })

  it('the migration adds the column and backfills existing rows to the migration time', () => {
    const dir = mkdtempSync(join(tmpdir(), 'state-stale-'))
    try {
      const file = join(dir, 'legacy.db')
      initDatabase(file)
      createKanbanCard({ id: 'legacy', title: 'Legacy card' })
      // Make it a pre-migration database: no stamp column, no stamp triggers.
      const db = getDb()
      db.exec('DROP TRIGGER kanban_cards_description_stamp_insert')
      db.exec('DROP TRIGGER kanban_cards_description_stamp_update')
      db.exec('ALTER TABLE kanban_cards DROP COLUMN description_updated_at')
      db.prepare('UPDATE kanban_cards SET created_at = 1000 WHERE id = ?').run('legacy')
      const before = Math.floor(Date.now() / 1000)
      initDatabase(file)
      const after = Math.floor(Date.now() / 1000)
      const stamp = getKanbanCard('legacy')!.description_updated_at!
      expect(stamp).toBeGreaterThanOrEqual(before)
      expect(stamp).toBeLessThanOrEqual(after)
      // Re-running the migration does not move an existing stamp.
      getDb().prepare('UPDATE kanban_cards SET description_updated_at = 5 WHERE id = ?').run('legacy')
      initDatabase(file)
      expect(getKanbanCard('legacy')!.description_updated_at).toBe(5)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('a raw SQL description write is stamped by the trigger; other columns leave it alone', () => {
    getDb().prepare('UPDATE kanban_cards SET title = ? WHERE id = ?').run('Renamed', 'c-1')
    expect(getKanbanCard('c-1')!.description_updated_at).toBe(PAST)
    getDb().prepare('UPDATE kanban_cards SET description = ? WHERE id = ?').run('MOSTANI ALLAPOT: new', 'c-1')
    expect(getKanbanCard('c-1')!.description_updated_at!).toBeGreaterThan(PAST)
  })
})

describe('state_stale flag', () => {
  it('is false with no comments and true after a counting comment, on the list and the single GET', async () => {
    expect((await listRow('c-1')).state_stale).toBe(false)
    expect((await listRow('c-1')).stale_comments).toBe(0)
    addKanbanComment('c-1', 'dex', 'Decided: option B')
    addKanbanComment('c-1', 'Owner', 'ok')
    const row = await listRow('c-1')
    expect(row.state_stale).toBe(true)
    expect(row.stale_comments).toBe(2)
    expect(row.description_updated_at).toBe(PAST)
    const single = await call('GET', '/api/kanban/c-1')
    expect(single.body.state_stale).toBe(true)
    expect(single.body.stale_comments).toBe(2)
  })

  it('comments older than the description do not count', async () => {
    addKanbanComment('c-1', 'dex', 'old news')
    stampDescription('c-1', Math.floor(Date.now() / 1000) + 10)
    expect((await listRow('c-1')).state_stale).toBe(false)
  })

  it('excluded authors and notices never make a card stale', async () => {
    addKanbanComment('c-1', 'system', 'A(z) #5 kártya kész lett, és ez volt az utolsó, amire ez a kártya várt. Folytatható.')
    addKanbanComment('c-1', 'System', 'case does not matter')
    addKanbanComment('c-1', 'kartya-es-ertesites', '[kartya-es-ertesites.py] Mezomozgatas')
    addKanbanComment('c-1', 'dex', 'sweep footprint', { automated: true })
    addKanbanComment('c-1', 'Owner', '[Heti átnézés] Owner döntése: marad (a számláló nullázva)')
    const row = await listRow('c-1')
    expect(row.stale_comments).toBe(0)
    expect(row.state_stale).toBe(false)
  })

  it('a done or archived card is never stale, though its count is still reported', async () => {
    addKanbanComment('c-1', 'dex', 'result')
    getDb().prepare("UPDATE kanban_cards SET status = 'done' WHERE id = 'c-1'").run()
    const row = await listRow('c-1')
    expect(row.stale_comments).toBe(1)
    expect(row.state_stale).toBe(false)
    expect(kanbanStateStaleness({ id: 'c-2', status: 'planned', archived_at: 123 }).state_stale).toBe(false)
  })

  it('a description edit through PUT clears it; any other edit does not', async () => {
    addKanbanComment('c-1', 'dex', 'Decided: option B')
    await put('c-1', { title: 'Renamed', actor: 'marveen' })
    expect((await listRow('c-1')).state_stale).toBe(true)
    // Same text echoed back is no change either.
    await put('c-1', { description: 'MOSTANI ALLAPOT: old', actor: 'marveen' })
    expect((await listRow('c-1')).state_stale).toBe(true)
    // The comment is 1 s in the past so a same-second stamp is strictly newer.
    getDb().prepare('UPDATE kanban_comments SET created_at = created_at - 1').run()
    const r = await put('c-1', { description: 'MOSTANI ALLAPOT: option B chosen\n---\nold', actor: 'marveen' })
    expect(r.status).toBe(200)
    const row = await listRow('c-1')
    expect(row.state_stale).toBe(false)
    expect(row.description_updated_at).toBeGreaterThan(PAST)
  })

  it('updateKanbanCard stamps with its own clock', () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date('2030-01-01T00:00:00Z'))
      updateKanbanCard('c-1', { description: 'new' })
      expect(getKanbanCard('c-1')!.description_updated_at).toBe(Date.UTC(2030, 0, 1) / 1000)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('comment POST hint', () => {
  it('a counting comment is told to rewrite the MOSTANI ALLAPOT block, with the PUT shape', async () => {
    const r = await call('POST', '/api/kanban/c-1/comments', { author: 'dex', content: 'Decided: B' })
    expect(r.status).toBe(200)
    expect(r.body.state_stale).toBe(true)
    expect(r.body.hint).toContain('MOSTANI ALLAPOT')
    expect(r.body.hint).toContain('-X PUT')
    expect(r.body.hint).toContain('/api/kanban/c-1')
    expect(r.body.hint).toContain('--data-binary @')
  })

  it('a system notice gets no hint', async () => {
    const r = await call('POST', '/api/kanban/c-1/comments', { author: 'system', content: 'notice' })
    expect(r.body.hint).toBeUndefined()
  })
})

describe('gate: state_update_first', () => {
  it('refuses a sub-agent moving its stale card to testing, by /move and by PUT', async () => {
    addKanbanComment('c-1', 'dex', 'Result: done, B chosen')
    const r = await move('c-1', { status: 'testing', actor: 'dex' })
    expect(r.status).toBe(409)
    expect(r.body.error).toBe('state_update_first')
    expect(r.body.hint).toContain('MOSTANI ALLAPOT')
    expect(getKanbanCard('c-1')!.status).toBe('in_progress')
    const p = await put('c-1', { status: 'testing', actor: 'dex' })
    expect(p.status).toBe(409)
    expect(p.body.error).toBe('state_update_first')
  })

  it('lets the same move through once the description is rewritten (control)', async () => {
    addKanbanComment('c-1', 'dex', 'Result: done, B chosen')
    getDb().prepare('UPDATE kanban_comments SET created_at = created_at - 1').run()
    await put('c-1', { description: 'MOSTANI ALLAPOT: B chosen, done', actor: 'dex' })
    const r = await move('c-1', { status: 'testing', actor: 'dex' })
    expect(r.status).toBe(200)
    expect(getKanbanCard('c-1')!.status).toBe('testing')
  })

  it('refuses a sub-agent starting another card while one of its own open cards is stale, naming it', async () => {
    addKanbanComment('c-1', 'Owner', 'Decision: go with B')
    recordKanbanCardRead('dex', 'c-2')
    const r = await move('c-2', { status: 'in_progress', actor: 'dex' })
    expect(r.status).toBe(409)
    expect(r.body.error).toBe('state_update_first')
    expect(r.body.card).toBe(`#${getKanbanCard('c-1')!.seq}`)
    expect(r.body.hint).toContain('/api/kanban/c-1')
    expect(getKanbanCard('c-2')!.status).toBe('planned')
  })

  it('does not count another agent\'s stale card', async () => {
    createKanbanCard({ id: 'z-1', title: 'Zara', assignee: 'zara', status: 'planned' })
    stampDescription('z-1', PAST)
    addKanbanComment('z-1', 'zara', 'news')
    recordKanbanCardRead('dex', 'c-2')
    const r = await move('c-2', { status: 'in_progress', actor: 'dex' })
    expect(r.status).toBe(200)
  })

  it('does not gate the main agent, the owner session or an actor-less caller', async () => {
    addKanbanComment('c-1', 'dex', 'Result')
    createKanbanCard({ id: 'c-3', title: 'Three', assignee: 'dex', status: 'planned' })
    expect((await move('c-2', { status: 'in_progress', actor: 'orin' })).status).toBe(200)
    expect((await move('c-3', { status: 'in_progress' })).status).toBe(200)
    expect((await move('c-1', { status: 'testing', actor: 'orin' })).status).toBe(200)
    await move('c-1', { status: 'in_progress', actor: 'orin' })
    const s = await call('POST', '/api/kanban/c-1/move', { status: 'testing', actor: 'dex' }, { kind: 'session', user: 'Owner' })
    expect(s.status).toBe(200)
  })
})

describe('dispatch instructions', () => {
  it('tell the agent to rewrite the MOSTANI ALLAPOT block after a state-changing comment', () => {
    expect(kanbanMoveInstructions('c-1', 'dex')).toContain('MOSTANI ALLAPOT')
    expect(kanbanMoveInstructions('c-1', 'dex')).toContain('state_update_first')
    expect(kanbanMoveInstructions('c-1', 'orin')).toContain('MOSTANI ALLAPOT')
  })
})
