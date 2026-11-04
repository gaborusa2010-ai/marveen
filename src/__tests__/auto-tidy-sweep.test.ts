import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'

// #638 rebuild 3: hot -> warm demotion and auto-close of old notifications,
// both run from the daily decay sweep. Same settings stub as
// retention-sweep.test.ts: a key in `stub` answers with that value, anything
// else falls through to the real resolution (registry default 0 = off).
const { stub } = vi.hoisted(() => ({ stub: {} as Record<string, string | number> }))
vi.mock('../settings-store.js', async (orig) => {
  const actual = await orig<typeof import('../settings-store.js')>()
  return {
    ...actual,
    getEffectiveSettingValue: (key: string) => (key in stub ? stub[key] : actual.getEffectiveSettingValue(key)),
  }
})

import {
  initDatabase,
  getDb,
  demoteStaleHotMemories,
  autoCloseStaleNotifications,
  getMemoryVersions,
  AUTO_CLOSED_RESULT,
} from '../db.js'
import { runDecaySweep } from '../memory.js'
import { getSettingDefinition } from '../config-registry.js'

const DAY = 86400
const NOW = () => Math.floor(Date.now() / 1000)

beforeAll(() => {
  process.env.NODE_ENV = 'test'
  initDatabase(':memory:')
})

beforeEach(() => {
  for (const k of Object.keys(stub)) delete stub[k]
  getDb().exec('DELETE FROM memories; DELETE FROM memory_versions; DELETE FROM agent_messages')
})

function addMemory(id: number, category: string, accessedDaysAgo: number, opts: { retracted?: boolean; createdDaysAgo?: number } = {}) {
  const now = NOW()
  getDb()
    .prepare(
      `INSERT INTO memories (id, chat_id, content, sector, salience, created_at, accessed_at, category, agent_id, keywords, retracted_at)
       VALUES (?, 'chat', ?, 'semantic', 1.0, ?, ?, ?, 'kiron', 'kw', ?)`,
    )
    .run(
      id,
      `memory ${id}`,
      now - (opts.createdDaysAgo ?? accessedDaysAgo) * DAY,
      now - accessedDaysAgo * DAY,
      category,
      opts.retracted ? now - DAY : null,
    )
}

const cat = (id: number) => (getDb().prepare('SELECT category, updated_by FROM memories WHERE id = ?').get(id) as { category: string; updated_by: string | null })

describe('demoteStaleHotMemories', () => {
  it('is off by default: nothing moves however old', () => {
    addMemory(1, 'hot', 100)
    expect(demoteStaleHotMemories()).toBe(0)
    expect(cat(1).category).toBe('hot')
  })

  it('moves only hot rows untouched for N days, keyed on accessed_at, with a version and updated_by=decay', () => {
    stub.HOT_MEMORY_DEMOTE_DAYS = 3
    addMemory(1, 'hot', 4) // stale -> warm
    addMemory(2, 'hot', 1, { createdDaysAgo: 30 }) // old but re-read -> stays
    addMemory(3, 'warm', 30) // not hot -> untouched
    addMemory(4, 'hot', 30, { retracted: true }) // retracted -> untouched
    addMemory(5, 'cold', 30)

    expect(demoteStaleHotMemories()).toBe(1)
    expect(cat(1)).toEqual({ category: 'warm', updated_by: 'decay' })
    expect(cat(2).category).toBe('hot')
    expect(cat(3).category).toBe('warm')
    expect(cat(4).category).toBe('hot')
    expect(cat(5).category).toBe('cold')
    const versions = getMemoryVersions(1)
    expect(versions).toHaveLength(1)
    expect(versions[0].category).toBe('hot')
    // Second run is a no-op.
    expect(demoteStaleHotMemories()).toBe(0)
  })

  it('treats garbage and negative settings as off', () => {
    addMemory(1, 'hot', 100)
    for (const v of ['', 'abc', -3, '0']) {
      stub.HOT_MEMORY_DEMOTE_DAYS = v
      expect(demoteStaleHotMemories()).toBe(0)
    }
    expect(cat(1).category).toBe('hot')
  })
})

function addMsg(id: number, from: string, content: string, status: string, daysAgo: number) {
  getDb()
    .prepare(`INSERT INTO agent_messages (id, from_agent, to_agent, content, status, created_at, delivered_at) VALUES (?, ?, 'marveen', ?, ?, ?, ?)`)
    .run(id, from, content, status, NOW() - daysAgo * DAY, status === 'pending' ? null : NOW() - daysAgo * DAY)
}
const msg = (id: number) => getDb().prepare('SELECT status, result, completed_at FROM agent_messages WHERE id = ?').get(id) as { status: string; result: string | null; completed_at: number | null }

describe('autoCloseStaleNotifications', () => {
  function seed() {
    addMsg(1, 'cody', '[Eredmény] msg_id:9 status:done', 'delivered', 2) // receipt -> close
    addMsg(2, 'system', '[CONTEXT-GUARD] Ujrainditottam', 'delivered', 2) // -> close
    addMsg(3, 'marveen', '[CONTEXT-GUARD] Ujrainditottam a marveen agentet', 'delivered', 2) // own-name guard -> close
    addMsg(4, 'system', '[handoff-failure] x', 'delivered', 2) // -> close
    addMsg(5, 'system', '[APPROVAL_REQUEST] x', 'delivered', 2) // actionable -> stays
    addMsg(6, 'system', '[kanban-lejart] x', 'delivered', 2) // actionable -> stays
    addMsg(7, 'cody', '[CODY -> MARVEEN] report', 'delivered', 2) // peer report -> stays
    addMsg(8, 'cody', '[Eredmény] msg_id:10 status:done', 'delivered', 0) // fresh -> stays
    addMsg(9, 'system', '[session-stuck] x', 'pending', 2) // pending -> stays
    addMsg(10, 'cody', '[handoff-failure] spoofed by a peer', 'delivered', 2) // non-system sender -> stays
    addMsg(11, 'system', 'x[CONTEXT-GUARD] not a prefix', 'delivered', 2) // -> stays
  }

  it('is off by default', () => {
    seed()
    expect(autoCloseStaleNotifications()).toBe(0)
    expect(msg(1).status).toBe('delivered')
  })

  it('closes only old delivered receipts and system notices, without touching actionable or fresh rows', () => {
    stub.NOTIFICATION_AUTOCLOSE_DAYS = 1
    seed()
    expect(autoCloseStaleNotifications()).toBe(4)
    for (const id of [1, 2, 3, 4]) {
      expect(msg(id).status).toBe('done')
      expect(msg(id).result).toBe(AUTO_CLOSED_RESULT)
      expect(msg(id).completed_at).toBeGreaterThan(0)
    }
    expect(msg(5).status).toBe('delivered')
    expect(msg(6).status).toBe('delivered')
    expect(msg(7).status).toBe('delivered')
    expect(msg(8).status).toBe('delivered')
    expect(msg(9).status).toBe('pending')
    expect(msg(10).status).toBe('delivered')
    expect(msg(11).status).toBe('delivered')
    // No [Eredmény] ack is generated for the closes.
    expect((getDb().prepare('SELECT COUNT(*) AS n FROM agent_messages').get() as { n: number }).n).toBe(11)
  })
})

describe('runDecaySweep wiring', () => {
  it('runs both when switched on', () => {
    stub.HOT_MEMORY_DEMOTE_DAYS = 3
    stub.NOTIFICATION_AUTOCLOSE_DAYS = 1
    addMemory(1, 'hot', 10)
    addMsg(1, 'cody', '[Eredmény] msg_id:9 status:done', 'delivered', 2)
    runDecaySweep()
    expect(cat(1).category).toBe('warm')
    expect(msg(1).status).toBe('done')
  })

  it('registry defaults are 0 (off) for upstream installs', () => {
    expect(getSettingDefinition('HOT_MEMORY_DEMOTE_DAYS')?.default).toBe(0)
    expect(getSettingDefinition('NOTIFICATION_AUTOCLOSE_DAYS')?.default).toBe(0)
  })
})
