// The egress gate's own prune must not take the Bash group with it.
//
// SUBSTR994 (Roxy, 2026-09-21). injectEgressGate removed stale copies of itself with
// `!JSON.stringify(e).includes('egress-gate.mjs')`. That string is a SUBSTRING of
// `bash-egress-gate.mjs`, so the filter also matched the template's `matcher: "Bash"`
// group -- and because it filtered whole ENTRIES rather than hooks, the group's other
// hook, the shared skill-tree gate, was carried out with it.
//
// Two protections, on five agents, removed by one name collision. Measured the same
// morning: the dashboard boot wrote the Bash group into all five at 17:20, and each
// agent lost it again in the minute of its own next start.
//
// The check that ties this test to the fix: restore the old one-line filter and the
// first case below goes RED. It did, when measured.
import { describe, it, expect } from 'vitest'
import { hookCommandNames } from '../web/agent-scaffold.js'

// The shape injectEgressGate operates on, taken from templates/settings.json.template.
const BASH_CSOPORT = {
  matcher: 'Bash',
  hooks: [
    { type: 'command', command: 'node "/r/scripts/hooks/bash-egress-gate.mjs"' },
    { type: 'command', command: 'node "/r/scripts/hooks/global-skill-write-gate.mjs"' },
  ],
}
const REGI_SAJAT = {
  matcher: 'WebFetch',
  hooks: [{ type: 'command', command: 'node "/regi-worktree/scripts/hooks/egress-gate.mjs"' }],
}

describe('hookCommandNames: a rovidebb nev nem illeszkedik a hosszabb testverre', () => {
  it('megkulonbozteti az egress-gate-et a bash-egress-gate-tol', () => {
    expect(hookCommandNames(BASH_CSOPORT.hooks[0].command)).toContain('bash-egress-gate.mjs')
    // EZ a regresszio magva: a regi szuro itt IGAZAT adott, es ezert vitte el a csoportot.
    expect(hookCommandNames(BASH_CSOPORT.hooks[0].command)).not.toContain('egress-gate.mjs')
  })

  it('a sajat, regi bejegyzest viszont felismeri, barmelyik checkoutbol', () => {
    expect(hookCommandNames(REGI_SAJAT.hooks[0].command)).toContain('egress-gate.mjs')
  })

  it('a masik ket kapu neve sem csuszik ossze', () => {
    expect(hookCommandNames('node "/r/scripts/hooks/global-skill-write-gate.mjs"'))
      .toEqual(['global-skill-write-gate.mjs'])
    expect(hookCommandNames('python3 "/r/scripts/hooks/require-checked.py"'))
      .toEqual(['require-checked.py'])
  })

  it('ures es ertelmetlen bemenetre ures lista, nem kivetel', () => {
    expect(hookCommandNames(undefined)).toEqual([])
    expect(hookCommandNames('')).toEqual([])
    expect(hookCommandNames('echo nincs-benne-szkript')).toEqual([])
  })
})
