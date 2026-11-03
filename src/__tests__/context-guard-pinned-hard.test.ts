import { describe, it, expect } from 'vitest'
import {
  decideGuard,
  DEFAULT_CONTEXT_GUARD,
  INITIAL_GUARD_STATE,
  STALE_REFRESH_REASON_PREFIX,
  HANDOFF_RESEND_REASON_PREFIX,
  type GuardInputs,
  type GuardState,
} from '../context-guard.js'

// #641: await-handoff under a `limitTokens` pin. The main agent runs a 150k pin
// on a 1M window (owner decision, tg 4039). On the pin the act tier asks at 85%
// and the hard line sits at 97%, ~18k apart; one tool output has measured 12k,
// so the agent crossed the hard line while writing its handoff and was
// restarted without one (2026-11-02 18:02, comment 3723 on the card). Under a
// pin the hard line is a budget, not a wedge: when the REAL window is measurably
// below hardPct, await-handoff waits for the handoff, at most until the handoff
// deadline. Unknown real window, a full real window or no pin: unchanged.

const CFG = { ...DEFAULT_CONTEXT_GUARD, enabled: true, saturationRestart: true, limitTokens: 150_000 }
const UNPINNED = { ...CFG, limitTokens: null }
const NOW = 1_000_000_000
const DEADLINE = NOW + 3 * 60_000

const AWAITING: GuardState = {
  ...INITIAL_GUARD_STATE,
  phase: 'await-handoff',
  handoffMtimeAtRequest: 100,
  deadlineMs: DEADLINE,
  handoffRequestReason: 'act threshold (86% >= 85%)',
}

function inputs(overrides: Partial<GuardInputs> = {}): GuardInputs {
  return {
    nowMs: NOW,
    pct: 0.99,
    windowPct: 0.15,
    running: true,
    paneIdle: true,
    paneBusy: false,
    paneBackgroundWork: false,
    sessionReady: false,
    handoffMtime: 100,
    paneSaturated: false,
    dailyHandoffDue: false,
    contextTokens: null,
    idleMs: null,
    usageLimited: false,
    pendingResumeExpiresAtMs: null,
    ...overrides,
  }
}

const WAITING = 'pinned hard threshold during await-handoff (99% of the 150000 pin) -- real window at 15%, waiting for the handoff until the deadline'

describe('decideGuard: await-handoff hard tier under a limitTokens pin (#641)', () => {
  it('waits for the handoff while the real window has room and the deadline is ahead', () => {
    const d = decideGuard(AWAITING, inputs(), CFG)
    expect(d.action).toBe('none')
    expect(d.reason).toBe(WAITING)
    expect(d.nextState).toEqual(AWAITING)
  })

  it('waits on a busy pane too, without the busy deferral wording', () => {
    const d = decideGuard(AWAITING, inputs({ paneBusy: true, paneIdle: false }), CFG)
    expect(d.action).toBe('none')
    expect(d.reason).toBe(WAITING)
  })

  it('a full real window restarts as before', () => {
    const d = decideGuard(AWAITING, inputs({ windowPct: 0.98 }), CFG)
    expect(d.action).toBe('restart')
    expect(d.reason).toBe('hard threshold during await-handoff')
  })

  it('an unknown real window grants nothing: null and absent both restart', () => {
    expect(decideGuard(AWAITING, inputs({ windowPct: null }), CFG).reason).toBe('hard threshold during await-handoff')
    const absent = inputs()
    delete absent.windowPct
    expect(decideGuard(AWAITING, absent, CFG).reason).toBe('hard threshold during await-handoff')
  })

  it('without a pin nothing changes, whatever windowPct says', () => {
    const d = decideGuard(AWAITING, inputs(), UNPINNED)
    expect(d.action).toBe('restart')
    expect(d.reason).toBe('hard threshold during await-handoff')
  })

  it('the deadline bounds the wait: past it the hard line applies as before (and still never cuts a busy turn)', () => {
    const late = inputs({ nowMs: DEADLINE })
    expect(decideGuard(AWAITING, late, CFG).reason).toBe('hard threshold during await-handoff')
    const lateBusy = inputs({ nowMs: DEADLINE, paneBusy: true, paneIdle: false })
    expect(decideGuard(AWAITING, lateBusy, CFG).reason).toBe('hard threshold during await-handoff but agent mid-turn -- deferring restart')
  })

  it('a handoff written in time ships as before', () => {
    const d = decideGuard(AWAITING, inputs({ handoffMtime: 200 }), CFG)
    expect(d.action).toBe('restart')
    expect(d.reason).toBe('handoff written')
  })

  it('a stale handoff may still be refreshed past the pin line while the real window has room', () => {
    // last activity 10 minutes after the handoff write
    const stale = { handoffMtime: NOW - 11 * 60_000, idleMs: 60_000 }
    const pinned = decideGuard({ ...AWAITING, handoffMtimeAtRequest: 0 }, inputs(stale), CFG)
    expect(pinned.action).toBe('request-handoff')
    expect(pinned.reason.startsWith(STALE_REFRESH_REASON_PREFIX)).toBe(true)
    const full = decideGuard({ ...AWAITING, handoffMtimeAtRequest: 0 }, inputs({ ...stale, windowPct: 0.98 }), CFG)
    expect(full.action).toBe('restart')
    expect(full.reason.startsWith('handoff written but STALE')).toBe(true)
  })

  it('after a usage-limit park the request is re-sent past the pin line while the real window has room', () => {
    const parked = { ...AWAITING, handoffParkedByLimit: true }
    const pinned = decideGuard(parked, inputs(), CFG)
    expect(pinned.action).toBe('request-handoff')
    expect(pinned.reason.startsWith(HANDOFF_RESEND_REASON_PREFIX)).toBe(true)
    const full = decideGuard(parked, inputs({ windowPct: 0.98 }), CFG)
    expect(full.action).toBe('restart')
    expect(full.reason).toBe('hard threshold during await-handoff')
  })

})

// #641 part 3: the idle-phase hard tier under a pin. A pinned agent past the
// pin's hardPct with the real window measurably below hardPct is not wedged by
// a full window, so it is asked for the handoff first; await-handoff (above)
// then waits for it at most until the deadline. Unpinned, unknown or full real
// window: the restart as before.
const PINNED_HARD = 'pinned hard threshold (99% of the 150000 pin, real window at 15%) -- requesting handoff first'

describe('decideGuard: idle-phase hard tier under a limitTokens pin (#641 part 3)', () => {
  it('asks for the handoff first, and enters await-handoff with a deadline', () => {
    const d = decideGuard(INITIAL_GUARD_STATE, inputs(), CFG)
    expect(d.action).toBe('request-handoff')
    expect(d.reason).toBe(PINNED_HARD)
    expect(d.nextState.phase).toBe('await-handoff')
    expect(d.nextState.deadlineMs).toBe(NOW + CFG.handoffTimeoutMinutes * 60_000)
    expect(d.nextState.handoffMtimeAtRequest).toBe(100)
  })

  it('the next sweep waits for that handoff, and the deadline still bounds the wait', () => {
    const first = decideGuard(INITIAL_GUARD_STATE, inputs(), CFG)
    const wait = decideGuard(first.nextState, inputs({ nowMs: NOW + 60_000 }), CFG)
    expect(wait.action).toBe('none')
    const late = decideGuard(first.nextState, inputs({ nowMs: first.nextState.deadlineMs }), CFG)
    expect(late.action).toBe('restart')
  })

  it('a full real window restarts as before', () => {
    const d = decideGuard(INITIAL_GUARD_STATE, inputs({ windowPct: 0.98 }), CFG)
    expect(d.action).toBe('restart')
    expect(d.reason).toBe('hard threshold (99% >= 97%)')
  })

  it('an unknown real window restarts as before', () => {
    expect(decideGuard(INITIAL_GUARD_STATE, inputs({ windowPct: null }), CFG).reason).toBe('hard threshold (99% >= 97%)')
    const absent = inputs()
    delete absent.windowPct
    expect(decideGuard(INITIAL_GUARD_STATE, absent, CFG).reason).toBe('hard threshold (99% >= 97%)')
  })

  it('without a pin nothing changes', () => {
    const d = decideGuard(INITIAL_GUARD_STATE, inputs(), UNPINNED)
    expect(d.action).toBe('restart')
    expect(d.reason).toBe('hard threshold (99% >= 97%)')
  })

  it('a busy pane keeps its own handoff request wording', () => {
    const d = decideGuard(INITIAL_GUARD_STATE, inputs({ paneBusy: true, paneIdle: false }), CFG)
    expect(d.action).toBe('request-handoff')
    expect(d.reason).toBe('hard threshold (99% >= 97%) but agent mid-turn -- requesting handoff, the restart waits for the turn')
  })
})
