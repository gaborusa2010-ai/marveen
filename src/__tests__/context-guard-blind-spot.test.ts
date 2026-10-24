import { describe, it, expect } from 'vitest'
import {
  decideGuard,
  DEFAULT_CONTEXT_GUARD,
  INITIAL_GUARD_STATE,
  READY_TIMEOUT_MS,
  AWAIT_READY_MEASURE_GRACE_MS,
  type ContextGuardConfig,
  type GuardInputs,
  type GuardState,
} from '../context-guard.js'

// The two blind windows measured on 2026-10-23 (Anorak, flotta-retro): a busy
// fresh session grew past 100% while the guard sat in await-ready (never
// ready, so never measured) or in the sweep right after an elapsed cooldown.
// 4 of that day's 20 restarts were hard restarts with no handoff request.

const CFG: ContextGuardConfig = { ...DEFAULT_CONTEXT_GUARD, enabled: true }
const NOW = 1_000_000_000

function inputs(overrides: Partial<GuardInputs> = {}): GuardInputs {
  return {
    nowMs: NOW,
    pct: null,
    running: true,
    paneIdle: false,
    paneBusy: true,
    sessionReady: false,
    handoffMtime: null,
    paneSaturated: false,
    dailyHandoffDue: false,
    contextTokens: null,
    idleMs: null,
    ...overrides,
  }
}

describe('decideGuard: an elapsed cooldown decides in the same sweep', () => {
  const cooling: GuardState = { ...INITIAL_GUARD_STATE, phase: 'cooldown', cooldownUntilMs: NOW - 1 }

  it('pct past actPct -> request-handoff right away', () => {
    const d = decideGuard(cooling, inputs({ pct: 0.95, paneIdle: true, paneBusy: false }), CFG)
    expect(d.action).toBe('request-handoff')
    expect(d.nextState.phase).toBe('await-handoff')
  })

  it('pct below the thresholds -> back to idle, no action', () => {
    const d = decideGuard(cooling, inputs({ pct: 0.3, paneIdle: true, paneBusy: false }), CFG)
    expect(d.action).toBe('none')
    expect(d.nextState).toEqual(INITIAL_GUARD_STATE)
  })

  it('a cooldown still running suppresses even a huge pct', () => {
    const d = decideGuard({ ...cooling, cooldownUntilMs: NOW + 60_000 }, inputs({ pct: 1.2 }), CFG)
    expect(d.action).toBe('none')
    expect(d.nextState.phase).toBe('cooldown')
  })
})

describe('decideGuard: a busy fresh session in await-ready is measured', () => {
  const restartAt = NOW - 4 * 60_000
  const awaitingReady: GuardState = {
    ...INITIAL_GUARD_STATE,
    phase: 'await-ready',
    deadlineMs: restartAt + READY_TIMEOUT_MS,
  }
  // The fresh session's own transcript, written 10 s ago (after the restart).
  const fresh = { pct: 0.95, idleMs: 10_000 }

  it('past actPct, past the grace, fresh transcript -> request-handoff', () => {
    expect(NOW - restartAt).toBeGreaterThanOrEqual(AWAIT_READY_MEASURE_GRACE_MS)
    const d = decideGuard(awaitingReady, inputs(fresh), CFG)
    expect(d.action).toBe('request-handoff')
    expect(d.nextState.phase).toBe('await-handoff')
  })

  it('inside the grace -> no request', () => {
    const early: GuardState = { ...awaitingReady, deadlineMs: NOW - 60_000 + READY_TIMEOUT_MS }
    const d = decideGuard(early, inputs(fresh), CFG)
    expect(d.action).toBe('none')
    expect(d.nextState.phase).toBe('await-ready')
  })

  it('newest transcript older than the restart (the replaced session) -> no request', () => {
    const d = decideGuard(awaitingReady, inputs({ pct: 1.0, idleMs: 5 * 60_000 }), CFG)
    expect(d.action).toBe('none')
    expect(d.nextState.phase).toBe('await-ready')
  })

  it('transcript age unmeasurable -> no request (fails closed)', () => {
    const d = decideGuard(awaitingReady, inputs({ pct: 1.0, idleMs: null }), CFG)
    expect(d.action).toBe('none')
  })

  it('pct below actPct -> keeps waiting', () => {
    const d = decideGuard(awaitingReady, inputs({ ...fresh, pct: 0.5 }), CFG)
    expect(d.action).toBe('none')
    expect(d.nextState.phase).toBe('await-ready')
  })

  it('guard disabled -> no request', () => {
    const d = decideGuard(awaitingReady, inputs(fresh), { ...CFG, enabled: false })
    expect(d.action).not.toBe('request-handoff')
  })

  it('a ready session still gets its resume, even past actPct', () => {
    const d = decideGuard(awaitingReady, inputs({ ...fresh, sessionReady: true, paneIdle: true, paneBusy: false }), CFG)
    expect(d.action).toBe('inject-resume')
  })
})
