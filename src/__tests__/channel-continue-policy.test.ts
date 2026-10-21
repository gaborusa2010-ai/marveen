import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { decideContinueFlag, estimateResumeReattachPct, verifyContinueLaunch, CONTINUE_MIN_CLI, type ContinueDecisionInput } from '../web/channel-continue-policy.js'

// CONTRESUME922 narrowing (approved 2026-09-23 with three conditions). The
// measurement: on Claude Code 2.1.280 a hand-launched --continue session of a
// telegram agent on the fleet-token path brought the plugin up, sent a real
// Telegram message and kept its context. The provider-key agent could not be
// measured because its key is an ephemeral launch-secret (a resume starts
// with an empty key -> 401), so that branch is excluded in CODE.

const ROOT = join(__dirname, '..', '..')
const PROCESS_SRC = readFileSync(join(ROOT, 'src', 'web', 'agent-process.ts'), 'utf-8')

const measured: ContinueDecisionInput = {
  hasPriorSession: true, fresh: false, hasChannel: true, isMainAgent: false, provider: 'telegram',
  usesLaunchSecret: false, fleetTokenLaunch: true, useMcpJsonForChannel: false, installedCli: '2.1.280',
}

describe('decideContinueFlag', () => {
  it('#416: a prior session at/above the guard handoff threshold is not resumed (channel or not)', () => {
    // 2026-09-30: Margo fresh-restarted at 297k, fleet stop 7 s later, the
    // next launch resumed the abandoned 297k transcript and ran to 329k.
    expect(decideContinueFlag({ ...measured, priorContextPct: 1.485, guardActPct: 0.9 })).toMatchObject({ useContinue: false, reason: expect.stringContaining('handoff threshold') })
    expect(decideContinueFlag({ ...measured, priorContextPct: 0.9, guardActPct: 0.9 }).useContinue).toBe(false)
    expect(decideContinueFlag({ ...measured, hasChannel: false, priorContextPct: 1.1, guardActPct: 0.9 }).useContinue).toBe(false)
    // below the threshold, or unmeasured / guard off: unchanged
    expect(decideContinueFlag({ ...measured, priorContextPct: 0.6, guardActPct: 0.9 }).useContinue).toBe(true)
    expect(decideContinueFlag({ ...measured, priorContextPct: null, guardActPct: 0.9 }).useContinue).toBe(true)
    expect(decideContinueFlag({ ...measured, priorContextPct: 1.2, guardActPct: null }).useContinue).toBe(true)
  })
  it('#251: a resume must leave first-turn headroom below the threshold', () => {
    // anorak 2026-10-07 23:00: resumed at 85.6%, guard handoff two minutes later.
    expect(decideContinueFlag({ ...measured, priorContextPct: 0.856, guardActPct: 0.9 })).toMatchObject({ useContinue: false, reason: expect.stringContaining('first-turn headroom') })
    expect(decideContinueFlag({ ...measured, priorContextPct: 0.89155, guardActPct: 0.9 }).useContinue).toBe(false)
    // forgacs 2026-10-09 03:00 (78.5%) and cody 2026-10-08 03:00 (79.2%) ran for hours: still resumed
    expect(decideContinueFlag({ ...measured, priorContextPct: 0.78471, guardActPct: 0.9 }).useContinue).toBe(true)
    expect(decideContinueFlag({ ...measured, priorContextPct: 0.79155, guardActPct: 0.9 }).useContinue).toBe(true)
  })
  it('#251: a changed instruction block counts against the headroom', () => {
    // anorak 2026-10-08 23:00: resumed at 68.5%, the re-attached block (~36%) pushed it past 100%.
    expect(decideContinueFlag({ ...measured, priorContextPct: 0.685, guardActPct: 0.9, resumeReattachPct: 0.33 })).toMatchObject({ useContinue: false, reason: expect.stringContaining('re-attached instructions') })
    expect(decideContinueFlag({ ...measured, priorContextPct: 0.685, guardActPct: 0.9, resumeReattachPct: 0 }).useContinue).toBe(true)
    expect(decideContinueFlag({ ...measured, priorContextPct: 0.3, guardActPct: 0.9, resumeReattachPct: 0.33 }).useContinue).toBe(true)
    // unmeasured fill: the estimate alone never forces a fresh launch
    expect(decideContinueFlag({ ...measured, priorContextPct: null, guardActPct: 0.9, resumeReattachPct: 0.9 }).useContinue).toBe(true)
  })
  it('the measured shape resumes: telegram + fleet token + no launch-secret + CLI >= floor', () => {
    expect(decideContinueFlag(measured).useContinue).toBe(true)
    expect(decideContinueFlag({ ...measured, installedCli: '2.1.300' }).useContinue).toBe(true)
  })
  it('condition 1: below the floor, or unmeasured, is fresh', () => {
    expect(decideContinueFlag({ ...measured, installedCli: '2.1.278' })).toMatchObject({ useContinue: false, reason: expect.stringContaining(CONTINUE_MIN_CLI) })
    expect(decideContinueFlag({ ...measured, installedCli: '2.1.193' }).useContinue).toBe(false)
    expect(decideContinueFlag({ ...measured, installedCli: null })).toMatchObject({ useContinue: false, reason: expect.stringContaining('unmeasured') })
  })
  it('condition 2: a launch that reads an ephemeral launch-secret never resumes (mechanism, not a gap)', () => {
    expect(decideContinueFlag({ ...measured, usesLaunchSecret: true })).toMatchObject({ useContinue: false, reason: expect.stringContaining('launch-secret') })
  })
  it('scope: only telegram, only the fleet-token path, not the mcp.json path, never the main agent', () => {
    expect(decideContinueFlag({ ...measured, provider: 'slack' }).useContinue).toBe(false)
    expect(decideContinueFlag({ ...measured, fleetTokenLaunch: false }).useContinue).toBe(false)
    expect(decideContinueFlag({ ...measured, useMcpJsonForChannel: true }).useContinue).toBe(false)
    expect(decideContinueFlag({ ...measured, isMainAgent: true }).useContinue).toBe(false)
  })
  it('unchanged: no prior session / fresh requested -> no; channel-less agents keep --continue regardless of CLI', () => {
    expect(decideContinueFlag({ ...measured, hasPriorSession: false }).useContinue).toBe(false)
    expect(decideContinueFlag({ ...measured, fresh: true }).useContinue).toBe(false)
    expect(decideContinueFlag({ ...measured, hasChannel: false, installedCli: null, usesLaunchSecret: true, fleetTokenLaunch: false }).useContinue).toBe(true)
  })
})

describe('verifyContinueLaunch (condition 3)', () => {
  it('alive on a later poll ends the wait early', async () => {
    let t = 0
    const seq: Array<'alive' | 'down' | 'unknown'> = ['unknown', 'down', 'alive']
    const r = await verifyContinueLaunch({ probe: () => seq.shift() ?? 'alive', windowMs: 90_000, intervalMs: 3_000, now: () => t, sleep: async (ms) => { t += ms } })
    expect(r).toMatchObject({ outcome: 'alive', polls: 3, elapsedMs: 6_000 })
  })
  it('never alive -> timeout at the window, not before', async () => {
    let t = 0
    const r = await verifyContinueLaunch({ probe: () => 'down', windowMs: 90_000, intervalMs: 3_000, now: () => t, sleep: async (ms) => { t += ms } })
    expect(r.outcome).toBe('timeout')
    expect(r.elapsedMs).toBeGreaterThanOrEqual(90_000)
    expect(r.polls).toBe(31)
  })
  it('#607: a stop during the window cancels the wait instead of timing out', async () => {
    let t = 0
    let stopped = false
    const r = await verifyContinueLaunch({
      probe: () => { if (t >= 6_000) stopped = true; return 'unknown' },
      cancelled: () => stopped,
      windowMs: 90_000, intervalMs: 3_000, now: () => t, sleep: async (ms) => { t += ms },
    })
    expect(r).toMatchObject({ outcome: 'cancelled', polls: 3, elapsedMs: 9_000 })
  })
  it('#607: cancelled before the first probe never probes', async () => {
    let probes = 0
    const r = await verifyContinueLaunch({ probe: () => { probes += 1; return 'alive' }, cancelled: () => true })
    expect(r.outcome).toBe('cancelled')
    expect(probes).toBe(0)
  })
})

describe('the launch path is wired to the policy', () => {
  it('startAgentProcess decides with decideContinueFlag from the measured inputs and no longer hardcodes !hasChannel', () => {
    expect(PROCESS_SRC).not.toContain("(hasPriorSession && !opts.fresh && !hasChannel) ? '--continue ' : ''")
    expect(PROCESS_SRC).toContain('const usesLaunchSecret = providerEnv !== \'\' || apiKeyEnv !== \'\'')
    expect(PROCESS_SRC).toContain('fleetTokenLaunch: oauthTokenEnv !== \'\'')
    expect(PROCESS_SRC).toContain('installedCli = hasAnyChannel ? (await measureClaudeCliVersion()).version : null')
    expect(PROCESS_SRC).toContain("const continueFlag = continueDecision.useContinue ? '--continue ' : ''")
  })
  it('a resumed channel launch is verified with the plugin-liveness probe and falls back to a FRESH start', () => {
    const at = PROCESS_SRC.indexOf('if (continueFlag && hasAnyChannel && name !== MAIN_AGENT_ID) {')
    expect(at).toBeGreaterThan(-1)
    const block = PROCESS_SRC.slice(at, at + 2500)
    expect(block).toContain('verifyContinueLaunch({')
    expect(block).toContain('probeChannelPluginLiveness(pid, agentProvider, name)')
    expect(block).toContain("['kill-session', '-t', exactTmuxTarget(session)]")
    expect(block).toContain('startAgentProcess(name, { fresh: true })')
  })
  it('#607: a stop bumps the launch generation and the fallback skips a stopped agent', () => {
    const at = PROCESS_SRC.indexOf('if (continueFlag && hasAnyChannel && name !== MAIN_AGENT_ID) {')
    const block = PROCESS_SRC.slice(at, at + 3000)
    expect(block).toContain('cancelled: () => launchGeneration(name) !== generation')
    const skip = block.indexOf("if (v.outcome === 'cancelled' || launchGeneration(name) !== generation) {")
    expect(skip).toBeGreaterThan(-1)
    expect(skip).toBeLessThan(block.indexOf('startAgentProcess(name, { fresh: true })'))
    const stop = PROCESS_SRC.slice(PROCESS_SRC.indexOf('export async function stopAgentProcess('))
    expect(stop.slice(0, stop.indexOf('isAgentRunning(name)'))).toContain('bumpLaunchGeneration(name)')
  })
})

describe('estimateResumeReattachPct (#251)', () => {
  const files: Record<string, { mtimeMs: number; size: number }> = {
    '/t/a.jsonl': { mtimeMs: 1000, size: 1 },
    '/t/b.jsonl': { mtimeMs: 2000, size: 1 },
    '/root/CLAUDE.md': { mtimeMs: 1500, size: 60_000 },
    '/agent/CLAUDE.md': { mtimeMs: 1800, size: 40_000 },
  }
  const stat = (p: string) => files[p] ?? null
  const listJsonl = () => ['/t/a.jsonl', '/t/b.jsonl']
  const base = { instructionFiles: ['/root/CLAUDE.md', '/agent/CLAUDE.md', '/missing/MEMORY.md'], transcriptDir: '/t', limitTokens: 200_000, stat, listJsonl }

  it('is 0 when every instruction file is older than the newest transcript', () => {
    expect(estimateResumeReattachPct(base)).toBe(0)
  })
  it('counts the WHOLE block when any one file changed after the last turn', () => {
    const changed: Record<string, { mtimeMs: number; size: number }> = { ...files, '/agent/CLAUDE.md': { mtimeMs: 2500, size: 40_000 } }
    expect(estimateResumeReattachPct({ ...base, stat: (p: string) => changed[p] ?? null })).toBeCloseTo(100_000 / 2 / 200_000)
  })
  it('cannot tell without a transcript or a limit', () => {
    expect(estimateResumeReattachPct({ ...base, listJsonl: () => [] })).toBeNull()
    expect(estimateResumeReattachPct({ ...base, limitTokens: null })).toBeNull()
  })
  it('is wired into the launch decision', () => {
    expect(PROCESS_SRC).toContain('resumeReattachPct = estimateResumeReattachPct(')
    expect(PROCESS_SRC).toMatch(/decideContinueFlag\(\{[\s\S]*?resumeReattachPct/)
  })
})
