// #102 (owner tg 8305): a sub-agent with its own Telegram bot gets the same four
// conversation-ledger hooks as the main agent, so its inbound is recorded and the
// lost-inbound drain can find it. Main is refused (its hooks are repo-shipped),
// an agent without a bot is skipped, and a re-run is a no-op.
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ensureAgentLedgerHooks, agentSettingsPath } from '../web/agent-scaffold.js'
import { MAIN_AGENT_ID, PROJECT_ROOT } from '../config.js'

const PROBE = 'ledger102-probe'
const probeDir = join(PROJECT_ROOT, 'agents', PROBE)
let fakeHome: string
let realHome: string | undefined

function events(): Record<string, { matcher?: string; hooks: { command: string }[] }[]> {
  return JSON.parse(readFileSync(agentSettingsPath(PROBE), 'utf-8')).hooks
}
function has(event: string, script: string): boolean {
  return (events()[event] || []).some((e) => e.hooks.some((h) => h.command.includes(script)))
}

beforeEach(() => {
  realHome = process.env.HOME
  fakeHome = mkdtempSync(join(tmpdir(), 'ledger102-'))
  process.env.HOME = fakeHome
  mkdirSync(join(fakeHome, '.claude'), { recursive: true })
  if (existsSync(join(probeDir, 'HANDOFF.md'))) throw new Error(`refusing: agents/${PROBE} looks live`)
  rmSync(probeDir, { recursive: true, force: true })
  mkdirSync(join(probeDir, '.claude'), { recursive: true })
})

afterEach(() => {
  process.env.HOME = realHome
  rmSync(fakeHome, { recursive: true, force: true })
  rmSync(probeDir, { recursive: true, force: true })
})

describe('ensureAgentLedgerHooks', () => {
  it('main agent is refused', () => {
    expect(ensureAgentLedgerHooks(MAIN_AGENT_ID)).toBe(false)
  })

  it('an agent without its own bot is skipped', () => {
    expect(ensureAgentLedgerHooks(PROBE)).toBe(false)
    expect(existsSync(agentSettingsPath(PROBE))).toBe(false)
  })

  it('an agent with a bot gets all four hooks, keeps its own, and a re-run is a no-op', () => {
    mkdirSync(join(probeDir, '.claude', 'channels', 'telegram'), { recursive: true })
    writeFileSync(join(probeDir, '.claude', 'channels', 'telegram', '.env'), 'X=1\n')
    writeFileSync(agentSettingsPath(PROBE), JSON.stringify({
      hooks: { UserPromptSubmit: [{ hooks: [{ type: 'command', command: 'echo keep-me' }] }] },
    }))
    expect(ensureAgentLedgerHooks(PROBE)).toBe(true)
    expect(has('UserPromptSubmit', 'ledger-capture.py')).toBe(true)
    expect(has('UserPromptSubmit', 'keep-me')).toBe(true)
    expect(has('PostToolUse', 'ledger-outbound.py')).toBe(true)
    expect(events().PostToolUse[0].matcher).toBe('mcp__plugin_[^_]+_[^_]+__reply')
    expect(has('Stop', 'ledger-midturn-capture.py')).toBe(true)
    expect(has('SessionStart', 'ledger-replay.py')).toBe(true)
    const before = readFileSync(agentSettingsPath(PROBE), 'utf-8')
    expect(ensureAgentLedgerHooks(PROBE)).toBe(false)
    expect(readFileSync(agentSettingsPath(PROBE), 'utf-8')).toBe(before)
  })
})
