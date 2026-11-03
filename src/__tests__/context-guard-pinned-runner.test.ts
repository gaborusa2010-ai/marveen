// #641, the BINDING: the runner must feed measurePcts' windowPct into
// GuardInputs.windowPct. The decision-level rules are pinned in
// context-guard-pinned-hard.test.ts; this file drives the real runner (same
// harness as context-guard-idle-flush-background-veto.test.ts) with a 150k pin
// on a 1M model, so a runner that drops windowPct (review 3739: setting it to
// null kept every decision test green) restarts here instead of asking.

import { describe, it, expect, vi, beforeEach, afterEach, afterAll } from 'vitest'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

const ROOT = mkdtempSync(join(homedir(), '.cgpinwire-test-'))
const STORE = join(ROOT, 'store')

vi.mock('../config.js', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  MAIN_AGENT_ID: 'marveen',
  PROJECT_ROOT: ROOT,
}))
vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

const T0 = 1_790_000_000_000
const MIN = 60_000

// A healthy idle pane: input box and the bypass footer, no saturation banner.
const IDLE_PANE = ['  done.', '', '─'.repeat(40), '❯ ', '─'.repeat(40), '  ⏵⏵ bypass permissions on (shift+tab to cycle)'].join('\n')

const h = vi.hoisted(() => ({
  worksource: true,
  messages: [] as string[],
  pane: null as string | null,
  tokens: 148_000,
  model: 'claude-opus-5-5[1m]' as string | null,
  restarts: 0,
}))

vi.mock('../web/context-guard-store.js', async () => {
  const { normalizeContextGuardConfig } = await import('../context-guard.js')
  return { readContextGuardConfig: () => normalizeContextGuardConfig({ enabled: true, limitTokens: 150_000 }) }
})
vi.mock('../web/agent-process.js', () => ({
  agentRunState: () => 'running',
  agentSessionName: (n: string) => `agent-${n}`,
  restartAgentProcess: async () => { h.restarts++; return { ok: true } },
  capturePane: (s: string) => (s.startsWith('agent-') ? h.pane : null),
  isSessionReadyForPrompt: async () => false,
  noteSaturationBannerUntrusted: () => {},
  clearSaturationBannerOverride: () => {},
}))
vi.mock('../web/system-directive.js', () => ({ sendSystemDirective: async () => 'sent' }))
vi.mock('../web/channel-monitor.js', () => ({
  hardRestartMarveenChannels: () => ({ ok: true }),
  lastMainRespawnAt: () => null,
  MARVEEN_POST_RESPAWN_GRACE_MS: 0,
  markAgentRestartPending: () => {},
}))
vi.mock('../web/stuck-tool-call-watcher.js', () => ({ shouldDeferForRecentRespawn: () => false }))
vi.mock('../web/agent-config.js', () => ({
  listAgentNames: () => ['boni'],
  listAllAgentNames: () => ['boni'],
  agentDir: (n: string) => join(ROOT, 'agents', n),
  readAgentModel: () => h.model,
  readAgentRemoteHost: () => null,
  readAgentWorksourceChannel: () => h.worksource,
}))
vi.mock('../web/main-transcript-root.js', () => ({ configDirFor: () => undefined }))
vi.mock('../web/active-model.js', () => ({
  readContextTokensFromProjectDir: () => h.tokens,
  readActiveModelFromProjectDir: () => null,
  readTranscriptMtimeFromProjectDir: () => T0 - MIN,
  readTranscriptTailFromProjectDir: () => null,
}))
vi.mock('../web/main-agent.js', () => ({ MAIN_CHANNELS_SESSION: 'marveen-channels' }))
vi.mock('../notify.js', () => ({ notifyChannel: async () => {} }))
vi.mock('../db.js', () => ({
  createAgentMessage: (_from: string, _to: string, content: string, tag?: string) => {
    if (tag === 'context-guard restart notice') h.messages.push(content)
    return { id: 1 }
  },
  countRestartQueueState: () => ({ pending: 0, failed: 0 }),
}))

type Runner = typeof import('../web/context-guard-runner.js')
let timers: NodeJS.Timeout[] = []

async function boot(): Promise<Runner> {
  vi.resetModules()
  const runner = await import('../web/context-guard-runner.js')
  timers.push(runner.startContextGuardRunner())
  return runner
}

beforeEach(() => {
  rmSync(STORE, { recursive: true, force: true })
  mkdirSync(STORE, { recursive: true })
  mkdirSync(join(ROOT, 'agents', 'boni'), { recursive: true })
  rmSync(join(ROOT, 'agents', 'boni', 'HANDOFF.md'), { force: true })
  vi.useFakeTimers()
  vi.setSystemTime(new Date(T0))
  h.worksource = true
  h.messages = []
  h.pane = IDLE_PANE
  h.tokens = 148_000
  h.model = 'claude-opus-5-5[1m]'
  h.restarts = 0
})

afterEach(() => {
  for (const t of timers) clearInterval(t)
  timers = []
  vi.clearAllTimers()
  vi.useRealTimers()
})

afterAll(() => {
  rmSync(ROOT, { recursive: true, force: true })
})

describe('pinned hard tier, through the real runner (#641)', () => {
  it('past the pin with the real window at ~15%: asks for the handoff, no restart', async () => {
    const runner = await boot()
    await vi.advanceTimersByTimeAsync(4.5 * MIN)
    expect(runner.getHardGuardPhase('boni')).toBe('await-handoff')
    expect(h.restarts).toBe(0)
    expect(runner.getContextGuardStatus().find(r => r.agent === 'boni')?.windowPct).toBeCloseTo(0.148, 3)
  })

  it('a full real window (200k model, 196k used) restarts without asking, as before', async () => {
    h.model = null
    h.tokens = 196_000
    const runner = await boot()
    await vi.advanceTimersByTimeAsync(4.5 * MIN)
    expect(runner.getHardGuardPhase('boni')).not.toBe('await-handoff')
    expect(h.restarts).toBe(1)
  })
})
