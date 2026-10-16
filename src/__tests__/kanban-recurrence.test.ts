// Recurring cards (card #452): the next-date computation (incl. month-end and
// DST), and the once-only spawn of the successor when a card reaches done.
import { describe, it, expect, beforeEach, vi } from 'vitest'

const settings = new Map<string, string>()
vi.mock('../settings-store.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../settings-store.js')>()
  return { ...actual, getEffectiveSettingValue: (k: string) => settings.get(k) ?? actual.getEffectiveSettingValue(k) }
})

import {
  parseKanbanRecurrence, nextKanbanOccurrenceDate, nextKanbanDueDate, formatKanbanDue,
} from '../kanban-recurrence.js'
import {
  initDatabase, createKanbanCard, getKanbanCard, moveKanbanCard, spawnKanbanRecurrence,
  getKanbanComments, listKanbanCards, addKanbanChecklistItem, updateKanbanChecklistItem,
  listKanbanChecklist, createLabel, addLabelToCard, getLabelsForCard, updateKanbanCard,
} from '../db.js'
import { callKanban } from './kanban-features-helpers.js'

const TZ = 'Europe/London'
const utc = (y: number, m: number, d: number, h = 0, mi = 0) => Date.UTC(y, m - 1, d, h, mi) / 1000
const ymd = (sec: number) => new Date(sec * 1000).toISOString().slice(0, 10)

describe('parseKanbanRecurrence', () => {
  it('accepts the four shapes and null', () => {
    expect(parseKanbanRecurrence({ freq: 'daily' })).toEqual({ ok: true, rule: { freq: 'daily' } })
    expect(parseKanbanRecurrence('{"freq":"weekly","weekday":3}')).toEqual({ ok: true, rule: { freq: 'weekly', weekday: 3 } })
    expect(parseKanbanRecurrence({ freq: 'monthly', day: '31' })).toEqual({ ok: true, rule: { freq: 'monthly', day: 31 } })
    expect(parseKanbanRecurrence({ freq: 'interval', days: 10 })).toEqual({ ok: true, rule: { freq: 'interval', days: 10 } })
    expect(parseKanbanRecurrence(null)).toEqual({ ok: true, rule: null })
  })
  it('rejects unknown freq, unknown keys and out-of-range values', () => {
    expect(parseKanbanRecurrence({ freq: 'yearly' }).ok).toBe(false)
    expect(parseKanbanRecurrence({ freq: 'daily', day: 3 }).ok).toBe(false)
    expect(parseKanbanRecurrence({ freq: 'weekly', weekday: 0 }).ok).toBe(false)
    expect(parseKanbanRecurrence({ freq: 'monthly', day: 32 }).ok).toBe(false)
    expect(parseKanbanRecurrence({ freq: 'interval', days: 0 }).ok).toBe(false)
    expect(parseKanbanRecurrence('not json').ok).toBe(false)
  })
})

describe('next occurrence', () => {
  it('daily / interval add days across month and year ends', () => {
    expect(nextKanbanOccurrenceDate({ freq: 'daily' }, { y: 2026, m: 12, d: 31 })).toEqual({ y: 2027, m: 1, d: 1 })
    expect(nextKanbanOccurrenceDate({ freq: 'interval', days: 10 }, { y: 2026, m: 2, d: 25 })).toEqual({ y: 2026, m: 3, d: 7 })
  })
  it('weekly: the next matching weekday, a full week when already on it', () => {
    // 2026-10-08 is a Thursday (ISO 4).
    expect(nextKanbanOccurrenceDate({ freq: 'weekly', weekday: 4 }, { y: 2026, m: 10, d: 8 })).toEqual({ y: 2026, m: 10, d: 15 })
    expect(nextKanbanOccurrenceDate({ freq: 'weekly', weekday: 1 }, { y: 2026, m: 10, d: 8 })).toEqual({ y: 2026, m: 10, d: 12 })
    expect(nextKanbanOccurrenceDate({ freq: 'weekly', weekday: 7 }, { y: 2026, m: 10, d: 8 })).toEqual({ y: 2026, m: 10, d: 11 })
  })
  it('monthly on the 31st clamps to shorter months without drifting', () => {
    const rule = { freq: 'monthly' as const, day: 31 }
    const feb = nextKanbanOccurrenceDate(rule, { y: 2027, m: 1, d: 31 })
    expect(feb).toEqual({ y: 2027, m: 2, d: 28 })
    const mar = nextKanbanOccurrenceDate(rule, feb)
    expect(mar).toEqual({ y: 2027, m: 3, d: 31 })
    expect(nextKanbanOccurrenceDate(rule, { y: 2028, m: 1, d: 31 })).toEqual({ y: 2028, m: 2, d: 29 })
    expect(nextKanbanOccurrenceDate(rule, { y: 2026, m: 3, d: 31 })).toEqual({ y: 2026, m: 4, d: 30 })
    expect(nextKanbanOccurrenceDate(rule, { y: 2026, m: 12, d: 31 })).toEqual({ y: 2027, m: 1, d: 31 })
  })
  it('monthly: a later day in the same month comes first', () => {
    expect(nextKanbanOccurrenceDate({ freq: 'monthly', day: 20 }, { y: 2026, m: 10, d: 8 })).toEqual({ y: 2026, m: 10, d: 20 })
    expect(nextKanbanOccurrenceDate({ freq: 'monthly', day: 5 }, { y: 2026, m: 10, d: 8 })).toEqual({ y: 2026, m: 11, d: 5 })
  })
})

describe('nextKanbanDueDate', () => {
  it('a date-only due (UTC midnight, what the dashboard stores) stays date-only', () => {
    const next = nextKanbanDueDate({ freq: 'monthly', day: 31 }, utc(2027, 1, 31), utc(2027, 1, 31, 12), TZ)
    expect(next).toBe(utc(2027, 2, 28))
    expect(formatKanbanDue(next, TZ)).toBe('2027-02-28')
  })
  it('a timed due keeps its London wall-clock time across the DST change', () => {
    // 2026-10-24 09:00 BST (08:00 UTC) + 1 week = 2026-10-31 09:00 GMT (09:00 UTC)... clocks go back 2026-10-25.
    const next = nextKanbanDueDate({ freq: 'interval', days: 7 }, utc(2026, 10, 24, 8), 0, TZ)
    expect(next).toBe(utc(2026, 10, 31, 9))
  })
  it('no due date: steps from today in London, as a date-only value', () => {
    // 23:30 UTC on 2026-07-01 is already 2026-07-02 00:30 in London (BST).
    const next = nextKanbanDueDate({ freq: 'daily' }, null, utc(2026, 7, 1, 23, 30), TZ)
    expect(ymd(next)).toBe('2026-07-03')
    expect(next % 86400).toBe(0)
  })
})

describe('spawnKanbanRecurrence', () => {
  beforeEach(() => { initDatabase(':memory:'); settings.clear() })

  it('creates the successor once, copies the card, moves the rule, comments both', () => {
    createKanbanCard({
      id: 'rec1', title: 'Weekly report', description: 'desc', assignee: 'cody', priority: 'urgent',
      project: 'ops', due_date: utc(2026, 10, 8), recurrence: '{"freq":"weekly","weekday":4}',
    })
    const label = createLabel({ id: 'lab1', name: 'x', color: '#64748b' })
    addLabelToCard('rec1', label.id)
    const item = addKanbanChecklistItem('rec1', 'step one')
    addKanbanChecklistItem('rec1', 'step two')
    updateKanbanChecklistItem('rec1', item.id, { done: true })
    moveKanbanCard('rec1', 'done', 0)

    const first = spawnKanbanRecurrence('rec1', { priority: 'urgent' })
    expect(first.spawned).toBe(true)
    const nextId = (first as { id: string }).id
    const next = getKanbanCard(nextId)!
    expect(next).toMatchObject({
      title: 'Weekly report', description: 'desc', assignee: 'cody', priority: 'urgent', project: 'ops',
      status: 'planned', recurrence: '{"freq":"weekly","weekday":4}',
    })
    expect(ymd(next.due_date!)).toBe('2026-10-15')
    expect(getLabelsForCard(nextId).map((l) => l.id)).toEqual(['lab1'])
    expect(listKanbanChecklist(nextId).map((i) => [i.text, i.done])).toEqual([['step one', 0], ['step two', 0]])
    const old = getKanbanCard('rec1')!
    expect(old.recurrence_spawned_id).toBe(nextId)
    expect(old.recurrence).toBeNull()
    expect(getKanbanComments('rec1').some((c) => c.automated === 1 && c.content.includes(nextId))).toBe(true)
    expect(getKanbanComments(nextId).some((c) => c.automated === 1 && c.content.includes('rec1'))).toBe(true)

    // Second call: no second successor.
    const again = spawnKanbanRecurrence('rec1')
    expect(again).toMatchObject({ spawned: false, reason: 'already_spawned', id: nextId })
    expect(listKanbanCards().filter((c) => c.title === 'Weekly report')).toHaveLength(2)
  })

  it('does nothing for a card that is not done or not recurring', () => {
    createKanbanCard({ id: 'a', title: 'A', recurrence: '{"freq":"daily"}' })
    expect(spawnKanbanRecurrence('a')).toMatchObject({ spawned: false, reason: 'not_done' })
    createKanbanCard({ id: 'b', title: 'B', status: 'done' })
    expect(spawnKanbanRecurrence('b')).toMatchObject({ spawned: false, reason: 'not_recurring' })
  })

  it('high priority is not carried into planned (owner: high = being worked on)', () => {
    createKanbanCard({ id: 'h', title: 'H', priority: 'high', status: 'done', recurrence: '{"freq":"daily"}' })
    const r = spawnKanbanRecurrence('h', { priority: 'high' })
    expect(getKanbanCard((r as { id: string }).id)!.priority).toBe('normal')
  })
})

describe('routes: done -> successor, idempotent across repeated moves', () => {
  beforeEach(() => { initDatabase(':memory:'); settings.clear() })

  it('POST /move to done twice (with a reopen between) yields exactly one successor', async () => {
    createKanbanCard({ id: 'r1', title: 'Daily standup', status: 'in_progress', due_date: utc(2026, 10, 8), recurrence: '{"freq":"daily"}' })
    expect((await callKanban('POST', '/api/kanban/r1/move', { status: 'done' })).status).toBe(200)
    expect((await callKanban('POST', '/api/kanban/r1/move', { status: 'in_progress' })).status).toBe(200)
    expect((await callKanban('POST', '/api/kanban/r1/move', { status: 'done' })).status).toBe(200)
    const titled = listKanbanCards().filter((c) => c.title === 'Daily standup')
    expect(titled).toHaveLength(2)
    const successor = titled.find((c) => c.id !== 'r1')!
    expect(successor.status).toBe('planned')
    expect(ymd(successor.due_date!)).toBe('2026-10-09')
    // in_progress coupled the priority to high and done to low; the successor
    // gets neither (pre-move high -> normal).
    expect(successor.priority).toBe('normal')
  })

  it('PUT status=done spawns too; PUT recurrence validates and canonicalises', async () => {
    createKanbanCard({ id: 'r2', title: 'Monthly invoice' })
    const bad = await callKanban('PUT', '/api/kanban/r2', { recurrence: { freq: 'monthly', day: 40 } })
    expect(bad.status).toBe(400)
    const ok = await callKanban('PUT', '/api/kanban/r2', { recurrence: { day: 31, freq: 'monthly' } })
    expect(ok.status).toBe(200)
    expect(getKanbanCard('r2')!.recurrence).toBe('{"freq":"monthly","day":31}')
    await callKanban('PUT', '/api/kanban/r2', { status: 'done' })
    await callKanban('PUT', '/api/kanban/r2', { status: 'done' })
    expect(listKanbanCards().filter((c) => c.title === 'Monthly invoice')).toHaveLength(2)
  })

  it('a switched-off feature refuses a rule change (403) and spawns nothing; data is kept', async () => {
    createKanbanCard({ id: 'r3', title: 'Kept rule', recurrence: '{"freq":"daily"}' })
    settings.set('KANBAN_FEATURE_RECURRING', '0')
    const refused = await callKanban('PUT', '/api/kanban/r3', { recurrence: { freq: 'weekly', weekday: 1 } })
    expect(refused.status).toBe(403)
    expect(refused.body).toEqual({ error: 'feature disabled' })
    // The dashboard's whole-card echo of the UNCHANGED rule is not a write.
    const card = getKanbanCard('r3')!
    expect((await callKanban('PUT', '/api/kanban/r3', { ...card, title: 'Kept rule!' })).status).toBe(200)
    await callKanban('POST', '/api/kanban/r3/move', { status: 'done' })
    expect(listKanbanCards().filter((c) => c.title.startsWith('Kept rule'))).toHaveLength(1)
    expect(getKanbanCard('r3')!.recurrence).toBe('{"freq":"daily"}')
    // Writes through other paths still work while it is off.
    expect(updateKanbanCard('r3', { title: 'x' })).toBe(true)
  })
})
