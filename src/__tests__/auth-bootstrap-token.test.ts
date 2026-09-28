import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { requiresAuth } from '../web/auth-gate.js'

// GET /api/auth/bootstrap-token hands out the MASTER dashboard token, so the
// thing worth pinning is not that it works -- it is that it stays narrower than
// the gate that lets callers reach it. resolveAuth() accepts five principals;
// only two of them may have this answer.
//
// The endpoint is an inline route in src/web.ts (next to /api/network-info, for
// the same reason: both need `auth` in scope, which the route modules do not
// get). That makes it source-contract testable and not much else -- so the
// assertions below read the file. A source test is weaker than a live request,
// which is why the live check ran separately against the restarted service; this
// file exists to stop a later "simplification" from deleting the kind check.
const WEB_TS = readFileSync(join(process.cwd(), 'src', 'web.ts'), 'utf-8')

describe('GET /api/auth/bootstrap-token', () => {
  it('is gated: requiresAuth() demands a principal for it', () => {
    // If this ever returns false the endpoint becomes a public token dispenser.
    expect(requiresAuth('/api/auth/bootstrap-token', 'GET')).toBe(true)
  })

  it('is NOT in the public-probe list next to /api/auth/status and /login', () => {
    const gate = readFileSync(join(process.cwd(), 'src', 'web', 'auth-gate.ts'), 'utf-8')
    const requiresAuthBody = gate.slice(gate.indexOf('export function requiresAuth'))
    expect(requiresAuthBody).not.toContain('bootstrap-token')
  })

  it('exists as a route in src/web.ts', () => {
    expect(WEB_TS).toContain("path === '/api/auth/bootstrap-token' && method === 'GET'")
  })

  it('refuses every principal except session and token', () => {
    const start = WEB_TS.indexOf("path === '/api/auth/bootstrap-token'")
    expect(start).toBeGreaterThan(-1)
    const block = WEB_TS.slice(start, start + 1200)
    // The allow-list form is what matters: an explicit deny-list would silently
    // admit any principal kind added to AuthResult later.
    expect(block).toContain("auth.kind !== 'session' && auth.kind !== 'token'")
    expect(block).toContain('403')
  })

  it('returns the 403 BEFORE it can return the token', () => {
    const start = WEB_TS.indexOf("path === '/api/auth/bootstrap-token'")
    const block = WEB_TS.slice(start, start + 1200)
    const refusal = block.indexOf('403')
    const handout = block.indexOf('DASHBOARD_TOKEN')
    expect(refusal).toBeGreaterThan(-1)
    expect(handout).toBeGreaterThan(-1)
    // Ordering, not just presence: a device key must hit the refusal first.
    expect(refusal).toBeLessThan(handout)
  })

  it('logs the refusal, so an excluded principal trying is visible', () => {
    const start = WEB_TS.indexOf("path === '/api/auth/bootstrap-token'")
    const block = WEB_TS.slice(start, start + 1200)
    expect(block).toContain('logger.warn')
  })
})

describe('the mobile-login QR consumes it', () => {
  // The whole point of the endpoint: the QR modal used to tell an owner who
  // signed in through the browser form to "log in" -- the one caller who could
  // not act on that advice, because a form login leaves localStorage empty.
  const APP_JS = readFileSync(join(process.cwd(), 'web', 'app.js'), 'utf-8')

  it('the QR render falls back to the endpoint when localStorage is empty', () => {
    const start = APP_JS.indexOf('=== Mobile login')
    expect(start).toBeGreaterThan(-1)
    const block = APP_JS.slice(start, start + 2500)
    expect(block).toContain('/api/auth/bootstrap-token')
    // `const token` would make the fallback assignment a TypeError at runtime,
    // and this file is not type-checked -- so the declaration IS the contract.
    expect(block).toContain("let token = localStorage.getItem('marveen-dashboard-token')")
  })

  it('still shows the no-token message when the fallback also fails', () => {
    const start = APP_JS.indexOf('=== Mobile login')
    const block = APP_JS.slice(start, start + 2500)
    expect(block).toContain("mobile_login.no_token")
  })
})
