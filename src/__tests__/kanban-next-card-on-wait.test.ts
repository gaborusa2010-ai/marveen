// #574: next card on wait, wake on unblock (owner, Telegram msg 8090).
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { Readable } from 'node:stream'

const sent: Array<{ from: string; to: string; content: string }> = []
const runningAgents = new Set<string>()

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
  return {
    ...actual,
    createAgentMessage: (from: string, to: string, content: string) => { sent.push({ from, to, content }); return { id: sent.length } },
  }
})

vi.mock('../web/agent-config.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../web/agent-config.js')>()),
  listAgentNames: () => ['dex', 'ria'],
  readAgentDisplayName: (n: string) => n,
}))

vi.mock('../web/agent-process.js', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../web/agent-process.js')>()),
  isAgentRunning: (n: string) => runningAgents.has(n),
}))

import { initDatabase, getDb, createKanbanCard, createLabel, setKanbanWaitReason, getKanbanCard } from '../db.js'
import { tryHandleKanban } from '../web/routes/kanban.js'
import { resetKanbanWaitNotifyState, sweepExpiredDateWaits, pickNextCard } from '../web/kanban-wait-notify.js'

async function move(id: string, body: Record<string, unknown>): Promise<number> {
  let status = 200
  const res: any = { writeHead(s: number) { status = s; return res }, setHeader() { return res }, end() {} }
  const req: any = Readable.from([Buffer.from(JSON.stringify(body))])
  req.headers = {}
  await tryHandleKanban({ req, res, path: `/api/kanban/${id}/move`, method: 'POST', url: new URL(`http://localhost/api/kanban/${id}/move`) } as never)
  return status
}

function seq(id: string): number { return getKanbanCard(id)!.seq! }

// Priority is rewritten by moveKanbanCard on column changes, so tests set it directly.
function setPriority(id: string, p: string): void {
  getDb().prepare('UPDATE kanban_cards SET priority=? WHERE id=?').run(p, id)
}

beforeEach(() => {
  sent.length = 0
  runningAgents.clear()
  runningAgents.add('dex')
  resetKanbanWaitNotifyState()
  initDatabase(':memory:')
})

describe('next card on wait', () => {
  it('names the next card: urgent > high > normal > low, in_progress before planned', async () => {
    createKanbanCard({ id: 'w', title: 'Waiting one', status: 'in_progress', assignee: 'dex' })
    createKanbanCard({ id: 'low1', title: 'Low', status: 'in_progress', assignee: 'dex' })
    createKanbanCard({ id: 'hiPlan', title: 'High planned', status: 'planned', assignee: 'dex' })
    createKanbanCard({ id: 'hiProg', title: 'High running', status: 'in_progress', assignee: 'dex' })
    createKanbanCard({ id: 'other', title: 'Someone else urgent', status: 'planned', assignee: 'ria' })
    createKanbanCard({ id: 'doneU', title: 'Done urgent', status: 'done', assignee: 'dex' })
    setPriority('low1', 'low'); setPriority('hiPlan', 'high'); setPriority('hiProg', 'high')
    setPriority('other', 'urgent'); setPriority('doneU', 'urgent')

    expect(await move('w', { status: 'waiting', actor: 'dex', wait: { kind: 'external', note: 'Ariel' } })).toBe(200)
    expect(sent).toHaveLength(1)
    expect(sent[0].from).toBe('system')
    expect(sent[0].to).toBe('dex')
    expect(sent[0].content).toContain(`#${seq('w')} (w)`)
    expect(sent[0].content).toContain('külső félre vár')
    expect(sent[0].content).toContain(`#${seq('hiProg')} (hiProg)`)
    expect(sent[0].content).toContain('"High running"')
  })

  it('urgent planned beats high in_progress; a blocked candidate is skipped', async () => {
    createKanbanCard({ id: 'w', title: 'W', status: 'planned', assignee: 'dex' })
    createKanbanCard({ id: 'hi', title: 'H', status: 'in_progress', assignee: 'dex' })
    createKanbanCard({ id: 'urg', title: 'U', status: 'planned', assignee: 'Dex' })
    createKanbanCard({ id: 'urgBlocked', title: 'UB', status: 'planned', assignee: 'dex' })
    createKanbanCard({ id: 'blk', title: 'Blocker', status: 'planned', assignee: 'ria' })
    setPriority('hi', 'high'); setPriority('urg', 'urgent'); setPriority('urgBlocked', 'urgent')
    getDb().prepare('INSERT INTO kanban_card_blockers (card_id, blocker_id, created_at) VALUES (?, ?, 0)').run('urgBlocked', 'blk')
    getDb().prepare('UPDATE kanban_cards SET sort_order=-5 WHERE id=?').run('urgBlocked')
    await move('w', { status: 'waiting', wait: { kind: 'date', until: 2_000_000_000 } })
    expect(sent).toHaveLength(1)
    expect(sent[0].content).toContain('(urg)')
    expect(sent[0].content).not.toContain('(urgBlocked)')
  })

  it('pickNextCard orders by priority, status, position, then age', () => {
    const c = (id: string, priority: any, status: any, sort_order = 0, created_at = 0) =>
      ({ id, seq: 1, title: id, priority, status, sort_order, created_at, assignee: 'dex', wait_kind: null, wait_note: null, wait_until: null })
    expect(pickNextCard([c('a', 'normal', 'in_progress'), c('b', 'high', 'planned')])!.id).toBe('b')
    expect(pickNextCard([c('a', 'high', 'planned'), c('b', 'high', 'in_progress')])!.id).toBe('b')
    expect(pickNextCard([c('a', 'low', 'planned', 2), c('b', 'low', 'planned', 1)])!.id).toBe('b')
    expect(pickNextCard([c('a', 'low', 'planned', 0, 9), c('b', 'low', 'planned', 0, 3)])!.id).toBe('b')
    expect(pickNextCard([])).toBeNull()
  })

  it('with no next card it says so and points to the main agent', async () => {
    createKanbanCard({ id: 'w', title: 'W', status: 'in_progress', assignee: 'dex' })
    await move('w', { status: 'waiting', wait: { kind: 'external', note: 'Vendor' } })
    expect(sent).toHaveLength(1)
    expect(sent[0].content).toContain('Nincs következő nyitott kártyád')
    expect(sent[0].content).toContain('fő-ágens')
  })

  it('dedupes the same card going waiting twice within 60s', async () => {
    createKanbanCard({ id: 'w', title: 'W', status: 'in_progress', assignee: 'dex' })
    await move('w', { status: 'waiting', wait: { kind: 'external', note: 'A' } })
    await move('w', { status: 'planned' })
    await move('w', { status: 'waiting', wait: { kind: 'external', note: 'B' } })
    expect(sent).toHaveLength(1)
  })

  it('owner-kind wait sends nothing', async () => {
    createLabel({ id: 'rv', name: 'Rád vár', color: '#fff' })
    createKanbanCard({ id: 'w', title: 'W', status: 'in_progress', assignee: 'dex' })
    createKanbanCard({ id: 'n', title: 'N', status: 'planned', assignee: 'dex' })
    expect(await move('w', { status: 'waiting', wait: { kind: 'owner' } })).toBe(200)
    expect(sent).toHaveLength(0)
  })

  it('owner-assigned, unassigned and down-session cards send nothing', async () => {
    createKanbanCard({ id: 'o', title: 'O', status: 'planned', assignee: 'Owner' })
    createKanbanCard({ id: 'u', title: 'U', status: 'planned' })
    createKanbanCard({ id: 'r', title: 'R', status: 'planned', assignee: 'ria' })
    await move('o', { status: 'waiting', wait: { kind: 'external', note: 'x' } })
    await move('u', { status: 'waiting', wait: { kind: 'external', note: 'x' } })
    await move('r', { status: 'waiting', wait: { kind: 'external', note: 'x' } })
    expect(sent).toHaveLength(0)
  })
})

describe('wake on unblock', () => {
  it('done blocker wakes the waiting card assignee, only when no other open blocker remains', async () => {
    createKanbanCard({ id: 'b1', title: 'B1', status: 'in_progress', assignee: 'ria' })
    createKanbanCard({ id: 'b2', title: 'B2', status: 'in_progress', assignee: 'ria' })
    createKanbanCard({ id: 'w', title: 'Waits', status: 'in_progress', assignee: 'dex' })
    await move('w', { status: 'waiting', wait: { kind: 'card', blockerId: 'b1' } })
    expect(sent[0].content).toContain(`másik kártyára vár (#${seq('b1')})`)
    getDb().prepare('INSERT INTO kanban_card_blockers (card_id, blocker_id, created_at) VALUES (?, ?, 1)').run('w', 'b2')
    sent.length = 0

    await move('b1', { status: 'done' })
    expect(sent).toHaveLength(0) // b2 still open
    await move('b2', { status: 'done' })
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ from: 'system', to: 'dex' })
    expect(sent[0].content).toContain(`#${seq('w')} (w) kártyád feloldódott`)
    expect(sent[0].content).toContain(`#${seq('b2')} (b2)`)
  })

  it('a PUT to done wakes too', async () => {
    createKanbanCard({ id: 'b', title: 'B', status: 'in_progress' })
    createKanbanCard({ id: 'w', title: 'W', status: 'waiting', assignee: 'dex' })
    setKanbanWaitReason('w', { kind: 'card', blockerId: 'b' })
    const res: any = { writeHead() { return res }, setHeader() { return res }, end() {} }
    const req: any = Readable.from([Buffer.from(JSON.stringify({ status: 'done' }))])
    req.headers = {}
    await tryHandleKanban({ req, res, path: '/api/kanban/b', method: 'PUT', url: new URL('http://localhost/api/kanban/b') } as never)
    expect(getKanbanCard('b')!.status).toBe('done')
    expect(sent.map((m) => m.to)).toEqual(['dex'])
  })
})

describe('date expiry', () => {
  it('notifies once, and a restart (fresh in-memory state) does not resend', () => {
    createKanbanCard({ id: 'w', title: 'Dated', status: 'waiting', assignee: 'dex' })
    createKanbanCard({ id: 'later', title: 'Later', status: 'waiting', assignee: 'dex' })
    setKanbanWaitReason('w', { kind: 'date', until: 1000 })
    setKanbanWaitReason('later', { kind: 'date', until: 5000 })
    expect(sweepExpiredDateWaits(2000)).toEqual(['w'])
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ from: 'system', to: 'dex' })
    expect(sent[0].content).toContain(`#${seq('w')} (w) kártyád várakozási időpontja lejárt`)
    resetKanbanWaitNotifyState()
    expect(sweepExpiredDateWaits(3000)).toEqual([])
    expect(sent).toHaveLength(1)
    expect(getKanbanCard('w')!.wait_notified_at).toBeTypeOf('number')
  })

  it('a rewritten date wait re-arms; a down session waits until it is up', () => {
    createKanbanCard({ id: 'w', title: 'W', status: 'waiting', assignee: 'ria' })
    setKanbanWaitReason('w', { kind: 'date', until: 1000 })
    expect(sweepExpiredDateWaits(2000)).toEqual([])
    runningAgents.add('ria')
    expect(sweepExpiredDateWaits(2001)).toEqual(['w'])
    setKanbanWaitReason('w', { kind: 'date', until: 2500 })
    expect(getKanbanCard('w')!.wait_notified_at).toBeNull()
    expect(sweepExpiredDateWaits(3000)).toEqual(['w'])
    expect(sent).toHaveLength(2)
  })

  it('owner-assigned date wait is marked without a message', () => {
    createKanbanCard({ id: 'w', title: 'W', status: 'waiting', assignee: 'Owner' })
    setKanbanWaitReason('w', { kind: 'date', until: 1000 })
    expect(sweepExpiredDateWaits(2000)).toEqual([])
    expect(sent).toHaveLength(0)
    expect(getKanbanCard('w')!.wait_notified_at).toBeTypeOf('number')
  })
})
