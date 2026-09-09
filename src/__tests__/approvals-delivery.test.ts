// APPROVALVAK821: the approval gate was a closed loop -- three independent
// legs, each silently non-delivering:
//   (1) creation never sent anything to the owner (telegram_message_id was
//       only writable via PATCH, which nothing called),
//   (2) notifyMainAgent delivered the notification back to the REQUESTER
//       when the requester was the main agent itself,
//   (3) timeout_at was always NULL (no category carries timeout_minutes and
//       the request's timeout_seconds was never read), so the sweeper's
//       'timeout' status was structurally unreachable.
// These tests pin the fix for all three legs: the pure pieces behaviorally,
// the wiring as string contracts (house idiom of approvals-prompt-contract).
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  computeTimeoutAt,
  buildOwnerApprovalText,
  DEFAULT_TIMEOUT_MINUTES,
  MAX_TIMEOUT_SECONDS,
} from '../web/routes/approvals.js'
import type { Approval } from '../db.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ROUTE = readFileSync(join(__dirname, '../../src/web/routes/approvals.ts'), 'utf-8')
const TELEGRAM = readFileSync(join(__dirname, '../../src/web/telegram.ts'), 'utf-8')

const NOW_MS = 1_787_300_000_000

function approval(over: Partial<Approval> = {}): Approval {
  return {
    id: 'test-id-1234',
    agent_id: 'samu',
    category: 'skill_patch',
    action_description: 'Skill frissítése a mérési eredmény alapján',
    action_payload: null,
    status: 'pending',
    timeout_at: Math.floor(NOW_MS / 1000) + 3600,
    telegram_message_id: null,
    requested_at: Math.floor(NOW_MS / 1000),
    resolved_at: null,
    resolved_by: null,
    ...over,
  }
}

describe('computeTimeoutAt (leg 3: the timeout state must be reachable)', () => {
  const now = Math.floor(NOW_MS / 1000)

  it('the request timeout_seconds wins', () => {
    expect(computeTimeoutAt('nonexistent-category', 3600, NOW_MS)).toBe(now + 3600)
  })

  it('caps a timeout past a week (it would equal the old "never")', () => {
    expect(computeTimeoutAt('x', 10 * 24 * 3600, NOW_MS)).toBe(now + MAX_TIMEOUT_SECONDS)
  })

  it('ignores junk timeout_seconds and falls through', () => {
    for (const junk of ['3600', -5, 0, NaN, Infinity, null, undefined, {}]) {
      expect(computeTimeoutAt('nonexistent-category', junk, NOW_MS)).toBe(now + DEFAULT_TIMEOUT_MINUTES * 60)
    }
  })

  it('NEVER returns null: with no param and no category value the default applies', () => {
    expect(computeTimeoutAt('nonexistent-category', undefined, NOW_MS)).toBe(now + DEFAULT_TIMEOUT_MINUTES * 60)
  })
})

describe('buildOwnerApprovalText (leg 1: what the owner reads)', () => {
  it('carries the id, requester, category and description', () => {
    const text = buildOwnerApprovalText(approval())
    expect(text).toContain('samu')
    expect(text).toContain('skill_patch')
    expect(text).toContain('Skill frissítése a mérési eredmény alapján')
    expect(text).toContain('test-id-1234')
    expect(text).toContain('Jóváhagyások')
    expect(text).not.toContain('undefined')
  })

  it('renders the expiry in local (Budapest) time, or "nincs" without one', () => {
    expect(buildOwnerApprovalText(approval())).toContain('Lejárat: 2026.')
    expect(buildOwnerApprovalText(approval({ timeout_at: null }))).toContain('Lejárat: nincs')
  })
})

describe('wiring contracts on the route source', () => {
  it('the POST handler notifies the OWNER, not only the main agent', () => {
    // Scoped to the ASKING path. Since 2026-09-01 the handler has a second
    // branch (`pre_approved_by`) that records an audit row for an action a
    // standing permission already covers -- that one notifies the main agent
    // and deliberately NOT the owner, so a plain first-indexOf would read the
    // wrong call. Cut the pre-approved branch off before measuring order.
    // The asking path is the handler's tail, so lastIndexOf pins it without
    // having to guess where the pre-approved branch ends -- an earlier attempt
    // sliced at its first `return true` (the 403 guard's) and still swallowed
    // the branch's own notifyMainAgent call.
    const post = ROUTE.slice(ROUTE.indexOf("path === '/api/approvals' && method === 'POST'"))
    const owner = post.lastIndexOf('notifyOwner(approval)')
    const main = post.lastIndexOf('notifyMainAgent(approval)')
    expect(owner).toBeGreaterThan(0)
    expect(main).toBeGreaterThan(owner)
  })

  it('the pre-approved branch does NOT notify the owner', () => {
    // The whole point of the branch: the owner already gave this permission,
    // so re-pinging him is the noise he asked us to stop. If a later edit adds
    // notifyOwner here, this fails.
    const post = ROUTE.slice(ROUTE.indexOf("path === '/api/approvals' && method === 'POST'"))
    const start = post.indexOf('if (typeof pre_approved_by')
    expect(start).toBeGreaterThan(0)
    const branch = post.slice(start, post.indexOf('notifyOwner(approval)', start))
    expect(branch).toContain('resolveApproval(id')
    expect(branch).toContain('notifyMainAgent(approval)')
    expect(branch).not.toContain('notifyOwner(')
  })

  it('a successful owner send stamps telegram_message_id onto the row', () => {
    expect(ROUTE).toContain('setApprovalTelegramMessageId(approval.id, messageId)')
  })

  it('leg 2: the main-agent notify short-circuits when the requester IS the main agent', () => {
    const fn = ROUTE.slice(ROUTE.indexOf('function notifyMainAgent('))
    const guard = fn.indexOf('approval.agent_id === MAIN_AGENT_ID')
    const send = fn.indexOf('createAgentMessage')
    expect(guard).toBeGreaterThan(0)
    expect(send).toBeGreaterThan(guard)
  })

  it('the POST handler reads timeout_seconds from the body', () => {
    expect(ROUTE).toContain('computeTimeoutAt(category, timeout_seconds)')
  })

  it('a failed owner send is loud, not silent', () => {
    expect(ROUTE).toContain('approval owner notification FAILED')
  })

  it('sendTelegramMessage exposes the message_id for the stamp', () => {
    expect(TELEGRAM).toContain('Promise<number | null>')
    expect(TELEGRAM).toContain('message_id')
  })
})

describe('degraded-delivery fallback (review finding on #1026)', () => {
  it('every owner-send failure path falls back to an in-band message', () => {
    const fn = ROUTE.slice(ROUTE.indexOf('function notifyOwner('))
    for (const reason of ["'no-token'", "'no-owner-chat'", "'send-failed'"]) {
      expect(fn).toContain(`fallbackInBand(approval, ${reason})`)
    }
  })

  it('the fallback reaches the main agent for EVERY requester, marked either way (OWNERUNREACHED901)', () => {
    // It used to bail out for sub-agent requesters. The main agent then saw the
    // plain `[APPROVAL_REQUEST]` from notifyMainAgent() with no hint that the
    // owner never got it, and the sub-agent waited out its 24h timeout. The
    // warning was missing precisely for the party that cannot read the dashboard.
    const fn = ROUTE.slice(ROUTE.indexOf('function fallbackInBand('), ROUTE.indexOf('function notifyOwner('))
    expect(fn).not.toContain('if (approval.agent_id !== MAIN_AGENT_ID) return')
    expect(fn).toContain('createAgentMessage')
    expect(fn).toContain('[OWNER_UNREACHED')
    expect(fn).toContain('[APPROVAL_OWNER_UNREACHED')
  })
})
