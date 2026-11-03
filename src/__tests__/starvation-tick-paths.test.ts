import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { ScheduledTask } from '../web/scheduled-tasks-io.js'

// #243 review follow-up: the starvation watchdog's two tick-level paths, driven
// through the REAL startScheduleRunner tick (harness from
// schedule-runner-fire-current-def.test.ts) against the REAL task_runs table
// (in-memory db) and the real countConsecutiveSkips:
//   * the catch-up 'missed' branch runs the starvation check, and alerts at the
//     right count (2nd consecutive skip once the task is 8+ days silent);
//   * a quiet preCheck round ('skipped-precheck') does NOT run the check, so a
//     streak that sits on an alert point is not re-paged on every quiet round.
// Only the alert sink (createAgentMessage) is spied; it still writes for real.

type RealDb = typeof import('../db.js')
// vi.resetModules() re-evaluates every module per tick; the db module (and its
// in-memory connection) must survive that, so the real one is pinned here.
const g = globalThis as unknown as { __starveTickRealDb?: RealDb }
const mockCreateAgentMessage = vi.fn()

vi.mock('../db.js', async (importOriginal) => {
  if (!g.__starveTickRealDb) {
    const real = await importOriginal<RealDb>()
    real.initDatabase(':memory:')
    g.__starveTickRealDb = real
  }
  const real = g.__starveTickRealDb
  return {
    ...real,
    createAgentMessage: (...a: Parameters<RealDb['createAgentMessage']>) => {
      mockCreateAgentMessage(...a)
      return real.createAgentMessage(...a)
    },
  }
})

vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
}))

vi.mock('../web/atomic-write.js', () => ({
  atomicWriteFileSync: vi.fn(),
}))

vi.mock('../channel-provider.js', async (importOriginal) => {
  const real = await importOriginal<typeof import('../channel-provider.js')>()
  return {
    ...real,
    getProvider: (type: Parameters<typeof real.getProvider>[0]) => ({
      ...real.getProvider(type),
      sendMessage: vi.fn(async () => {}),
      sendPhoto: vi.fn(async () => {}),
    }),
  }
})

const mockListScheduledTasks = vi.fn(() => [] as ScheduledTask[])
vi.mock('../web/scheduled-tasks-io.js', () => ({
  listScheduledTasks: () => mockListScheduledTasks(),
  readScheduledTask: (n: string) => mockListScheduledTasks().find(t => t.name === n) ?? null,
  SCHEDULED_TASKS_DIR: '/tmp/marveen-starvation-tick-no-tasks-dir',
  SCHEDULED_TASK_INLINE_MAX_CHARS: 1_500,
  SCHEDULED_TASK_BODY_WARN_CHARS: 20_000,
  MAX_SCHEDULED_TASK_PROMPT_LEN: 50_000,
}))

const sentPrompts: string[] = []
const sessionReady = { v: true }
vi.mock('../web/agent-process.js', () => ({
  clearFeedbackModalAndRecheck: () => false,
  agentSessionName: (name: string) => `agent-${name}`,
  isAgentRunning: () => true,
  isSessionReadyForPrompt: () => sessionReady.v,
  sendPromptToSession: async (_s: string, prompt: string) => { sentPrompts.push(prompt); return 'sent' },
  startAgentProcess: vi.fn(() => ({ ok: false, error: 'not in tests' })),
  sessionExistsOnHost: () => true,
  capturePane: () => null,
  sendEnterToSession: vi.fn(),
  clearStaleParkedInput: vi.fn(() => false),
  resolveAgentProvider: () => 'telegram',
}))

const AGENT = 'curagent'
const H = 3_600_000
const D = 24 * H
// 10:20 UTC; the startup tick runs at 10:20:05.
const NOW = new Date('2026-07-31T10:20:00.000Z').getTime()

let preCheckDir = ''
let preCheckSkip = ''

function task(overrides: Partial<ScheduledTask> & { name: string }): ScheduledTask {
  return {
    description: 'starvation tick fixture',
    prompt: 'Do the thing.',
    schedule: '* * * * *',
    agent: AGENT,
    enabled: true,
    createdAt: 0,
    type: 'task',
    ...overrides,
  }
}

// Seed task_runs with explicit timestamps (appendTaskRun stamps Date.now()).
function seed(name: string, rows: Array<[status: string, ts: number]>): void {
  const db = g.__starveTickRealDb!
  for (const [status, ts] of rows) {
    const id = db.appendTaskRun(name, AGENT, status, status === 'fired' ? null : 'busy')
    db.getDb().prepare('UPDATE task_runs SET ts = ? WHERE id = ?').run(ts, id)
  }
}

function statuses(name: string): string[] {
  return (g.__starveTickRealDb!.getDb()
    .prepare('SELECT status FROM task_runs WHERE name = ? ORDER BY id').all(name) as Array<{ status: string }>)
    .map(r => r.status)
}

const starvationAlerts = () => mockCreateAgentMessage.mock.calls.filter(c => c[3] === 'scheduler:starvation')

async function runOneTick(extraWait?: () => boolean) {
  vi.resetModules()
  const { startScheduleRunner } = await import('../web/schedule-runner.js')
  const stop = startScheduleRunner()
  await vi.advanceTimersByTimeAsync(6_000)
  // The preCheck is a real child process: its exit is real I/O, not a fake timer.
  if (extraWait) await vi.waitFor(() => { if (!extraWait()) throw new Error('tick not done') }, { timeout: 5_000, interval: 20 })
  clearInterval(stop)
}

describe('#243 starvation check on the tick paths', () => {
  beforeAll(async () => {
    preCheckDir = mkdtempSync(join(tmpdir(), 'starve-tick-'))
    preCheckSkip = join(preCheckDir, 'skip.sh')
    writeFileSync(preCheckSkip, '#!/bin/bash\necho SKIP\n')
    // Load the db mock once so the in-memory connection exists before seeding.
    await import('../db.js')
  })
  afterAll(() => { rmSync(preCheckDir, { recursive: true, force: true }) })

  beforeEach(() => {
    vi.stubEnv('SCHEDULER_TZ', 'Europe/Budapest')
    vi.clearAllMocks()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
    vi.setSystemTime(NOW)
    sentPrompts.length = 0
    sessionReady.v = true
    g.__starveTickRealDb!.getDb().prepare('DELETE FROM task_runs').run()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
  })

  // Hourly, due at 10:00 UTC: 20 min late on the startup tick, over its 1-minute
  // catch-up budget, so the tick records it as 'missed'.
  const STALE = task({ name: 'missed-weekly-ish', schedule: '0 * * * *', catchUpMaxAgeMinutes: 1 })

  it("missed branch: the 'missed' row is the 2nd skip of an 8+ day silence -> one starvation alert", async () => {
    seed(STALE.name, [['fired', NOW - 10 * D], ['skipped', NOW - 3 * D]])
    mockListScheduledTasks.mockReturnValue([STALE])
    await runOneTick()
    expect(statuses(STALE.name)).toEqual(['fired', 'skipped', 'missed'])
    expect(sentPrompts).toHaveLength(0)
    const alerts = starvationAlerts()
    expect(alerts).toHaveLength(1)
    expect(alerts[0][0]).toBe(AGENT)
    expect(alerts[0][2]).toContain(`"${STALE.name}"`)
    expect(alerts[0][2]).toContain('2 EGYMAS UTANI')
    // The task name is in the text too, so match 'missed' inside the reasons part.
    expect(alerts[0][2]).toMatch(/Okok a naplo szerint: [^.]*\bmissed\b/)
  })

  it("missed branch control: the 'missed' row is only the 1st skip -> no alert yet", async () => {
    seed(STALE.name, [['fired', NOW - 10 * D]])
    mockListScheduledTasks.mockReturnValue([STALE])
    await runOneTick()
    expect(statuses(STALE.name)).toEqual(['fired', 'missed'])
    expect(starvationAlerts()).toHaveLength(0)
  })

  // A streak sitting exactly on the 10-skip alert point, last fire 7 h ago
  // (past the 6 h silence floor). A check on the quiet round would page it again.
  const QUIET = task({ name: 'precheck-gated' })

  it('a quiet preCheck round (skipped-precheck) does not run the check -> no alert', async () => {
    const t = { ...QUIET, preCheck: preCheckSkip }
    seed(t.name, [['fired', NOW - 7 * H], ...Array.from({ length: 10 }, (_, i) => ['skipped', NOW - 6 * H + i * 60_000] as [string, number])])
    mockListScheduledTasks.mockReturnValue([t])
    await runOneTick(() => statuses(t.name).length === 12)
    expect(statuses(t.name).at(-1)).toBe('skipped-precheck')
    expect(sentPrompts).toHaveLength(0)
    expect(starvationAlerts()).toHaveLength(0)
  })

  it('control: the same streak, the round dropped by skipIfBusy instead -> the check runs and alerts', async () => {
    // Proves the seeded streak is one quiet-round-away from a page, so the
    // silence above is the skipped-precheck guard, not the arithmetic.
    // The busy round adds the 10th skip; the quiet round left it at 10.
    const t = { ...QUIET, skipIfBusy: true }
    seed(t.name, [['fired', NOW - 7 * H], ...Array.from({ length: 9 }, (_, i) => ['skipped', NOW - 6 * H + i * 60_000] as [string, number])])
    mockListScheduledTasks.mockReturnValue([t])
    sessionReady.v = false
    await runOneTick()
    expect(statuses(t.name).at(-1)).toBe('skipped')
    expect(statuses(t.name)).toHaveLength(11)
    const alerts = starvationAlerts()
    expect(alerts).toHaveLength(1)
    expect(alerts[0][2]).toContain('10 EGYMAS UTANI')
  })
})
