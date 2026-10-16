// Checklist inside a card (card #453): CRUD through the HTTP route, the
// unknown-field 400, the board counts, and cleanup with the card.
import { describe, it, expect, beforeEach, vi } from 'vitest'

const settings = new Map<string, string>()
vi.mock('../settings-store.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../settings-store.js')>()
  return { ...actual, getEffectiveSettingValue: (k: string) => settings.get(k) ?? actual.getEffectiveSettingValue(k) }
})

import { initDatabase, createKanbanCard, deleteKanbanCard, listKanbanChecklist } from '../db.js'
import { callKanban } from './kanban-features-helpers.js'

beforeEach(() => { initDatabase(':memory:'); settings.clear() })

describe('checklist API', () => {
  it('add, list, tick, edit, untick, delete', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    const a = await callKanban('POST', '/api/kanban/c1/checklist', { text: '  first  ' })
    expect(a.status).toBe(200)
    expect(a.body).toMatchObject({ card_id: 'c1', text: 'first', done: 0, done_at: null })
    const b = await callKanban('POST', '/api/kanban/c1/checklist', { text: 'second' })
    expect(b.body.sort_order).toBeGreaterThan(a.body.sort_order)

    const list = await callKanban('GET', '/api/kanban/c1/checklist')
    expect(list.body.map((i: any) => i.text)).toEqual(['first', 'second'])

    const ticked = await callKanban('PUT', `/api/kanban/c1/checklist/${a.body.id}`, { done: true })
    expect(ticked.body.done).toBe(1)
    expect(typeof ticked.body.done_at).toBe('number')
    const edited = await callKanban('PUT', `/api/kanban/c1/checklist/${a.body.id}`, { text: 'first!' })
    expect(edited.body).toMatchObject({ text: 'first!', done: 1, done_at: ticked.body.done_at })
    const unticked = await callKanban('PUT', `/api/kanban/c1/checklist/${a.body.id}`, { done: false })
    expect(unticked.body).toMatchObject({ done: 0, done_at: null })

    expect((await callKanban('DELETE', `/api/kanban/c1/checklist/${b.body.id}`)).status).toBe(200)
    expect((await callKanban('DELETE', `/api/kanban/c1/checklist/${b.body.id}`)).status).toBe(404)
    expect(listKanbanChecklist('c1')).toHaveLength(1)
  })

  it('validation: unknown fields, empty text, wrong types, foreign card, missing card', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    createKanbanCard({ id: 'c2', title: 'Other' })
    expect((await callKanban('POST', '/api/kanban/c1/checklist', { text: 'x', done: true })).status).toBe(400)
    expect((await callKanban('POST', '/api/kanban/c1/checklist', { text: '   ' })).status).toBe(400)
    expect((await callKanban('POST', '/api/kanban/nope/checklist', { text: 'x' })).status).toBe(404)
    expect((await callKanban('GET', '/api/kanban/nope/checklist')).status).toBe(404)
    const item = (await callKanban('POST', '/api/kanban/c1/checklist', { text: 'x' })).body
    expect((await callKanban('PUT', `/api/kanban/c1/checklist/${item.id}`, { done: 'yes' })).status).toBe(400)
    expect((await callKanban('PUT', `/api/kanban/c1/checklist/${item.id}`, { checked: true })).status).toBe(400)
    // An item is only reachable through its own card.
    expect((await callKanban('PUT', `/api/kanban/c2/checklist/${item.id}`, { done: true })).status).toBe(404)
    expect((await callKanban('PATCH', '/api/kanban/c1/checklist')).status).toBe(405)
  })

  it('the board GET carries checklist_total / checklist_done', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    createKanbanCard({ id: 'c2', title: 'Empty' })
    const one = (await callKanban('POST', '/api/kanban/c1/checklist', { text: 'a' })).body
    await callKanban('POST', '/api/kanban/c1/checklist', { text: 'b' })
    await callKanban('POST', '/api/kanban/c1/checklist', { text: 'c' })
    await callKanban('PUT', `/api/kanban/c1/checklist/${one.id}`, { done: true })
    const board = (await callKanban('GET', '/api/kanban')).body
    expect(board.find((c: any) => c.id === 'c1')).toMatchObject({ checklist_total: 3, checklist_done: 1 })
    expect(board.find((c: any) => c.id === 'c2')).toMatchObject({ checklist_total: 0, checklist_done: 0 })
    // ...and the whole-card PUT that echoes those counts back is still accepted.
    const card = board.find((c: any) => c.id === 'c1')
    expect((await callKanban('PUT', '/api/kanban/c1', { ...card, title: 'Card 2' })).status).toBe(200)
  })

  it('deleting the card removes its items', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    await callKanban('POST', '/api/kanban/c1/checklist', { text: 'a' })
    deleteKanbanCard('c1')
    expect(listKanbanChecklist('c1')).toHaveLength(0)
  })

  it('switched off: writes 403, the read stays open, items are kept', async () => {
    createKanbanCard({ id: 'c1', title: 'Card' })
    const item = (await callKanban('POST', '/api/kanban/c1/checklist', { text: 'kept' })).body
    settings.set('KANBAN_FEATURE_CHECKLIST', '0')
    const post = await callKanban('POST', '/api/kanban/c1/checklist', { text: 'new' })
    expect(post.status).toBe(403)
    expect(post.body).toEqual({ error: 'feature disabled' })
    expect((await callKanban('PUT', `/api/kanban/c1/checklist/${item.id}`, { done: true })).status).toBe(403)
    expect((await callKanban('DELETE', `/api/kanban/c1/checklist/${item.id}`)).status).toBe(403)
    expect((await callKanban('GET', '/api/kanban/c1/checklist')).body.map((i: any) => i.text)).toEqual(['kept'])
  })
})
