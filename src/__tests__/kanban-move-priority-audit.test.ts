// #783: the "priority follows the column" rule in moveKanbanCard changes the
// priority with its own UPDATE. That change must leave the same
// kanban_card_field_events row updateKanbanCard writes, or the field history
// cannot say why a hand-set priority moved.

import { describe, it, expect, beforeEach } from 'vitest'
import { initDatabase, createKanbanCard, moveKanbanCard, getKanbanCardFieldEvents, getKanbanCard } from '../db.js'

beforeEach(() => {
  initDatabase(':memory:')
})

const priorityEvents = (id: string) =>
  getKanbanCardFieldEvents(id).filter((e) => e.field === 'priority')

describe('moveKanbanCard priority audit', () => {
  it('logs the column-driven priority change with old/new value and actor', () => {
    createKanbanCard({ id: 'p-a', title: 'Card', priority: 'normal' })

    moveKanbanCard('p-a', 'in_progress', 0, 'cody')
    expect(getKanbanCard('p-a')?.priority).toBe('high')
    let ev = priorityEvents('p-a')
    expect(ev).toHaveLength(1)
    expect(ev[0]).toMatchObject({ old_value: 'normal', new_value: 'high', actor: 'cody' })

    moveKanbanCard('p-a', 'waiting', 0, 'marveen')
    ev = priorityEvents('p-a')
    expect(ev).toHaveLength(2)
    expect(ev[1]).toMatchObject({ old_value: 'high', new_value: 'normal', actor: 'marveen' })
  })

  it('logs nothing when the rule leaves the priority as it was', () => {
    createKanbanCard({ id: 'p-b', title: 'Card', priority: 'high' })
    moveKanbanCard('p-b', 'in_progress', 0, 'cody')
    expect(priorityEvents('p-b')).toHaveLength(0)
  })

  it('logs nothing for urgent, which the rule never touches', () => {
    createKanbanCard({ id: 'p-c', title: 'Card', priority: 'urgent' })
    moveKanbanCard('p-c', 'in_progress', 0, 'cody')
    moveKanbanCard('p-c', 'done', 0, 'cody')
    expect(getKanbanCard('p-c')?.priority).toBe('urgent')
    expect(priorityEvents('p-c')).toHaveLength(0)
  })

  it('logs nothing on a same-column reorder', () => {
    createKanbanCard({ id: 'p-d', title: 'Card', priority: 'low' })
    moveKanbanCard('p-d', 'planned', 3, 'cody')
    expect(priorityEvents('p-d')).toHaveLength(0)
  })
})
