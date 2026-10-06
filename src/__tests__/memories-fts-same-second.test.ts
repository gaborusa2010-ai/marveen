import { describe, it, expect, vi, afterAll } from 'vitest'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { initDatabase, getDb, saveAgentMemory, updateMemory } from '../db.js'

vi.mock('../logger.js', () => ({ logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() } }))

// #436: the memories_touch trigger's inner UPDATE used to re-run the FTS sync
// (memories_au fired on every update), so a same-second content edit followed
// by a keywords edit sharing a token failed with "database disk image is
// malformed". These tests pin: the write succeeds, the index matches the
// table, and the sync trigger only fires on content/keywords writes.

function ftsIntegrity(): void {
  // rank=1 also compares the index against the external content table.
  getDb().prepare("INSERT INTO memories_fts(memories_fts, rank) VALUES('integrity-check', 1)").run()
}

function matches(term: string): number[] {
  return (getDb().prepare('SELECT rowid FROM memories_fts WHERE memories_fts MATCH ? ORDER BY rowid').all(term) as { rowid: number }[])
    .map((r) => r.rowid)
}

describe('memories FTS sync with the touch trigger (#436)', () => {
  for (const recursive of [false, true]) {
    describe(`recursive_triggers=${recursive ? 'ON' : 'OFF'}`, () => {
      const fresh = () => {
        initDatabase(':memory:')
        if (recursive) getDb().exec('PRAGMA recursive_triggers=ON')
      }

      it('a same-second content edit then a keywords edit sharing a token succeeds and stays indexed', () => {
        fresh()
        const id = saveAgentMemory('agent-a', 'alpha', 'warm', 'kw').id
        updateMemory(id, 'beta')
        expect(() => updateMemory(id, 'beta', undefined, undefined, 'kw extra')).not.toThrow()
        expect(() => ftsIntegrity()).not.toThrow()
        expect(matches('extra')).toEqual([id])
        expect(matches('kw')).toEqual([id])
        expect(matches('alpha')).toEqual([])
      })

      it('the original repro sequence (new-kw) succeeds', () => {
        fresh()
        const id = saveAgentMemory('agent-a', 'alpha', 'warm', 'kw').id
        updateMemory(id, 'beta')
        expect(() => updateMemory(id, 'beta', undefined, undefined, 'new-kw')).not.toThrow()
        expect(() => ftsIntegrity()).not.toThrow()
        expect(matches('new')).toEqual([id])
      })

      it('a raw content UPDATE (touch fires) leaves the index matching the table', () => {
        fresh()
        const other = saveAgentMemory('agent-a', 'unrelated gamma', 'warm', 'kw').id
        const id = saveAgentMemory('agent-a', 'alpha one', 'warm', 'kw').id
        getDb().prepare('UPDATE memories SET content = ? WHERE id = ?').run('alpha two', id)
        expect(() => ftsIntegrity()).not.toThrow()
        expect(matches('two')).toEqual([id])
        expect(matches('one')).toEqual([])
        expect(matches('alpha')).toEqual([id])
        expect(matches('kw')).toEqual([other, id])
      })

      it('a raw keywords UPDATE right after a content edit leaves the index matching the table', () => {
        fresh()
        const id = saveAgentMemory('agent-a', 'alpha', 'warm', 'kw').id
        updateMemory(id, 'beta')
        getDb().prepare('UPDATE memories SET keywords = ? WHERE id = ?').run('kw nine', id)
        expect(() => ftsIntegrity()).not.toThrow()
        expect(matches('nine')).toEqual([id])
      })

      it('a category-only and a maintenance UPDATE keep the index intact', () => {
        fresh()
        const id = saveAgentMemory('agent-a', 'alpha', 'warm', 'kw').id
        getDb().prepare('UPDATE memories SET category = ? WHERE id = ?').run('cold', id)
        getDb().prepare('UPDATE memories SET accessed_at = ?, salience = ? WHERE id = ?').run(1, 0.5, id)
        expect(() => ftsIntegrity()).not.toThrow()
        expect(matches('alpha')).toEqual([id])
      })
    })
  }

  it('the sync trigger is scoped to the indexed columns', () => {
    initDatabase(':memory:')
    const { sql } = getDb().prepare("SELECT sql FROM sqlite_master WHERE name = 'memories_au'").get() as { sql: string }
    expect(sql).toMatch(/AFTER UPDATE OF content, keywords ON memories/)
  })

  describe('upgrade of an existing database', () => {
    const dir = mkdtempSync(join(tmpdir(), 'memfts436-'))
    afterAll(() => rmSync(dir, { recursive: true, force: true }))

    it('an installed old memories_au body is replaced on the next init', () => {
      const file = join(dir, 'up.db')
      initDatabase(file)
      getDb().exec('DROP TRIGGER memories_au')
      getDb().exec("CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN INSERT INTO memories_fts(memories_fts, rowid, content, keywords) VALUES('delete', old.id, old.content, old.keywords); INSERT INTO memories_fts(rowid, content, keywords) VALUES (new.id, new.content, new.keywords); END")
      const id = saveAgentMemory('agent-a', 'alpha', 'warm', 'kw').id
      getDb().close()
      initDatabase(file)
      const { sql } = getDb().prepare("SELECT sql FROM sqlite_master WHERE name = 'memories_au'").get() as { sql: string }
      expect(sql).toMatch(/AFTER UPDATE OF content, keywords ON memories/)
      updateMemory(id, 'beta')
      expect(() => updateMemory(id, 'beta', undefined, undefined, 'kw extra')).not.toThrow()
      expect(() => ftsIntegrity()).not.toThrow()
      getDb().close()
    })
  })
})
