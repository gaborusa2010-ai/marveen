import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mkdtempSync, rmSync, readFileSync, existsSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

let tmpRoot: string

vi.mock('../config.js', async (orig) => {
  const actual = await orig<typeof import('../config.js')>()
  return {
    ...actual,
    get PROJECT_ROOT() { return tmpRoot },
    MAIN_AGENT_ID: 'main-agent',
  }
})

const { mainAgentBlockBody, hasMainCompact } = await import('../web/main-compact-blocks.js')

describe('mainAgentBlockBody', () => {
  beforeEach(() => { tmpRoot = mkdtempSync(join(tmpdir(), 'compact-')) })
  afterEach(() => { rmSync(tmpRoot, { recursive: true, force: true }) })

  it('leaves a sub-agent body untouched and writes no reference file', () => {
    expect(mainAgentBlockBody('sub', 'evidence-rule', 'FULL')).toBe('FULL')
    expect(existsSync(join(tmpRoot, 'docs', 'tudas', 'generalt'))).toBe(false)
  })

  it('leaves an unknown key untouched for the main agent', () => {
    expect(hasMainCompact('fleet-roster')).toBe(false)
    expect(mainAgentBlockBody('main-agent', 'fleet-roster', 'FULL')).toBe('FULL')
  })

  it('compacts the main agent body, keeps the heading and points at the full text', () => {
    const out = mainAgentBlockBody('main-agent', 'system-directive-auth', '## Rendszer\n\nFULL BODY')
    expect(out.startsWith('## Rendszer-direktíva hitelesítés')).toBe(true)
    expect(out).toContain('to_agent="main-agent"')
    expect(out).not.toContain('{{AGENT}}')
    expect(out).toContain('docs/tudas/generalt/system-directive-auth.md')
    const ref = readFileSync(join(tmpRoot, 'docs', 'tudas', 'generalt', 'system-directive-auth.md'), 'utf-8')
    expect(ref).toContain('FULL BODY')
  })

  it('does not rewrite an identical reference file', () => {
    mainAgentBlockBody('main-agent', 'fleet-auth', 'SAME')
    const p = join(tmpRoot, 'docs', 'tudas', 'generalt', 'fleet-auth.md')
    const first = statSync(p).mtimeMs
    mainAgentBlockBody('main-agent', 'fleet-auth', 'SAME')
    expect(statSync(p).mtimeMs).toBe(first)
  })
})
