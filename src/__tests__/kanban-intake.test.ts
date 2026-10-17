import { beforeEach, describe, expect, it, vi } from 'vitest'

const settings = new Map<string, string>()
vi.mock('../settings-store.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../settings-store.js')>()
  return { ...actual, getEffectiveSettingValue: (key: string) => settings.get(key) ?? actual.getEffectiveSettingValue(key) }
})

import {
  createLabel, getKanbanCard, getLabelsForCard, initDatabase, listKanbanCards, listLabels, normaliseDueDate,
} from '../db.js'
import { callKanban } from './kanban-features-helpers.js'

beforeEach(() => { initDatabase(':memory:'); settings.clear() })

const valid = { client_name: 'Kiss Anna', request: 'Tetőjavítást szeretnék.\nA hátsó épületen.', contact: 'anna@example.com', address: 'Fő utca 1.', wanted_by: '2026-10-17', project: 'Tető' }

describe('kanban client intake API', () => {
  it('creates the unassigned planned card, exact description, label, and matching seq', async () => {
    const response = await callKanban('POST', '/api/kanban/intake', valid)
    expect(response.status).toBe(200)
    const card = getKanbanCard(response.body.id)!
    expect(response.body).toEqual({ ok: true, id: card.id, seq: card.seq })
    expect(card).toMatchObject({
      title: 'Ügyfél: Kiss Anna: Tetőjavítást szeretnék.',
      description: 'Ügyfél-kérés (űrlap)\nNév: Kiss Anna\nElérhetőség: anna@example.com\nHelyszín: Fő utca 1.\nMikorra: 2026-10-17\nProjekt: Tető\n\nKérés:\nTetőjavítást szeretnék.\nA hátsó épületen.',
      status: 'planned', priority: 'normal', assignee: null, project: 'Tető',
    })
    expect(getLabelsForCard(card.id).map((label) => label.name)).toEqual(['Ügyfél-kérés'])
  })

  it('reuses its label on later submissions', async () => {
    await callKanban('POST', '/api/kanban/intake', valid)
    await callKanban('POST', '/api/kanban/intake', { client_name: 'Béla', request: 'Másik kérés' })
    expect(listLabels().filter((label) => label.name === 'Ügyfél-kérés')).toHaveLength(1)
  })

  it('reuses an accent- and case-insensitive label name', async () => {
    createLabel({ id: 'existing', name: 'ugyfel-keres', color: '#123456' })
    const response = await callKanban('POST', '/api/kanban/intake', valid)
    expect(listLabels()).toHaveLength(1)
    expect(getLabelsForCard(response.body.id).map((label) => label.id)).toEqual(['existing'])
  })

  it('refuses writes while the feature is switched off', async () => {
    settings.set('KANBAN_FEATURE_INTAKE_FORM', '0')
    const response = await callKanban('POST', '/api/kanban/intake', valid)
    expect(response.status).toBe(403)
    expect(response.body).toEqual({ error: 'feature disabled' })
    expect(listKanbanCards()).toHaveLength(0)
  })

  it.each([
    [{ request: 'x' }, 'client_name'],
    [{ client_name: 'x' }, 'request'],
    [{ client_name: 'x', request: 'y', contact: 'z'.repeat(201) }, 'contact'],
    [{ client_name: 'x', request: 'y', wanted_by: '2026-02-30' }, 'wanted_by'],
    [{ client_name: 'x', request: 'y', wanted_by: '17/10/2026' }, 'wanted_by'],
    [{ client_name: 'x', request: 'y', surprise: true }, 'surprise'],
    [[], 'object'],
    ['text', 'object'],
    [null, 'object'],
  ])('rejects invalid input without creating a card: %j', async (payload, errorPart) => {
    const response = await callKanban('POST', '/api/kanban/intake', payload)
    expect(response.status).toBe(400)
    expect(response.body.error).toContain(errorPart)
    expect(listKanbanCards()).toHaveLength(0)
  })

  it('uses the dashboard due-date convention', async () => {
    const response = await callKanban('POST', '/api/kanban/intake', { client_name: 'Anna', request: 'Kérés', wanted_by: '2026-10-17' })
    // The date field's convention: a bare day is stored as UTC midnight of that day.
    expect(getKanbanCard(response.body.id)!.due_date).toBe(Date.UTC(2026, 9, 17) / 1000)
    expect(normaliseDueDate('2026-10-17')).toBe(Date.UTC(2026, 9, 17) / 1000)
  })

  it('titles the card from the first non-empty line of the request', async () => {
    const response = await callKanban('POST', '/api/kanban/intake', { client_name: 'Anna', request: '\n  \n  Ajtó cseréje  \nrészletek' })
    expect(getKanbanCard(response.body.id)!.title).toBe('Ügyfél: Anna: Ajtó cseréje')
  })

  it('cuts a long first line to 120 characters with an ellipsis and adds no double-hyphen separator', async () => {
    const response = await callKanban('POST', '/api/kanban/intake', { client_name: 'Anna', request: 'x'.repeat(300) })
    const card = getKanbanCard(response.body.id)!
    expect(card.title).toHaveLength(120)
    expect(card.title.endsWith('…')).toBe(true)
    expect(card.title).not.toContain(' -- ')
    expect(card.description).not.toContain(' -- ')
  })

  it('stores markup-looking request text verbatim as plain text', async () => {
    const request = '<script>alert("x")</script>\nmarad'
    const response = await callKanban('POST', '/api/kanban/intake', { client_name: 'Anna', request })
    expect(getKanbanCard(response.body.id)!.description).toContain(`Kérés:\n${request}`)
  })
})
