// Owner tg 8585 (2026-10-20): sub-agents with their own Telegram bot load the
// hook-only plugins listed in store/subagent-plugins.json (telegram-reaction).
import { describe, it, expect } from 'vitest'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { readSubagentPluginIds } from '../web/agent-process.js'

describe('readSubagentPluginIds', () => {
  const dir = mkdtempSync(join(tmpdir(), 'subagent-plugins-'))
  const file = (body: string) => { const p = join(dir, `${Math.random()}.json`); writeFileSync(p, body); return p }

  it('returns the listed plugin ids, trimmed, blanks dropped', () => {
    expect(readSubagentPluginIds(file('{"plugins":[" telegram-reaction@marveen-telegram-reaction ","",3]}')))
      .toEqual(['telegram-reaction@marveen-telegram-reaction'])
  })
  it('absent, malformed or wrong-shaped file -> []', () => {
    expect(readSubagentPluginIds(join(dir, 'missing.json'))).toEqual([])
    expect(readSubagentPluginIds(file('{not json'))).toEqual([])
    expect(readSubagentPluginIds(file('{"plugins":"x"}'))).toEqual([])
  })
  it('cleanup', () => { rmSync(dir, { recursive: true, force: true }) })
})
