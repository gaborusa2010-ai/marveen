// #797 (Cody msg 17582): from the project root or the store dir an unresolved `$i` is read as a
// relative `*`, which matches local-hooks / egress-allowlist.json, so a plain message-close loop was
// denied as an allowlist write. A `for NAME in <literal words>` variable is now read as its values.
// Both directions are pinned: the loop forms pass, every protected write and every loop the
// resolution must NOT trust stays denied.
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
// @ts-expect-error -- plain .mjs hook script, no types
import { gateDecision, literalLoopVars } from '../../scripts/self-pace-gate.mjs'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

let root: string
let store: string
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'loop-var-'))
  store = join(root, 'store')
  mkdirSync(store)
  mkdirSync(join(root, 'local-hooks'))
  mkdirSync(join(root, 'agents', 'cody'), { recursive: true })
  writeFileSync(join(store, 'egress-allowlist.json'), '{}')
})
afterEach(() => { rmSync(root, { recursive: true, force: true }) })

const decide = (command: string) =>
  gateDecision('Bash', { command: command.replaceAll('ROOT', root) },
    { allowlistPath: join(store, 'egress-allowlist.json'), cwd: join(root, 'agents', 'cody'), selfAgent: 'cody' })

const PASS = [
  // the exact repro
  'cd ROOT; for i in 17569 17570; do bash scripts/marveen-api.sh message-close "$i" --no-notify; done',
  'cd ROOT; for i in 1 2; do bash scripts/x.sh "$i"; done',
  'cd ROOT; for i in 1 2; do echo "$i"; done',
  'cd ROOT/store; for i in 1 2; do echo "$i"; done',
  'cd ROOT; for i in 1; do cp /tmp/a "store/$i"; done',
  'cd ROOT\nfor i in 17569 17570\ndo\n  bash scripts/marveen-api.sh message-close "$i" --no-notify\ndone',
  'cd ROOT && for id in 1 2 3; do python3 scripts/tabla.py --ki cody kartya "$id" | head -5; done',
  'cd ROOT/store; for f in a.json b.json; do cat "${f}"; done',
  'for i in 17569 17570; do bash scripts/marveen-api.sh message-close "$i" --no-notify; done',
]
const DENY = [
  'cd ROOT; cp /tmp/a store/egress-allowlist.json',
  'cd ROOT/store; for f in egress-allowlist.json; do cp /tmp/a "$f"; done',
  'cd ROOT; for f in store/egress-allowlist.json; do echo x > "$f"; done',
  'cd ROOT; for f in store/extra-hooks.json; do cp /tmp/a "$f"; done',
  'cd ROOT; for i in local-hooks; do cp /tmp/a "$i/x"; done',
  'cd ROOT; for i in 1; do cp /tmp/a "local-hooks/$i.sh"; done',
  // unbound, rebound or non-literal: stays `*`
  'cd ROOT; bash scripts/x.sh "$i"',
  'cd ROOT/store; for i in 1 2; do cp /tmp/a "$k"; done',
  'cd ROOT; cp /tmp/a "$j"; for j in 1; do :; done',
  'cd ROOT; for i in 1; do i=local-hooks; cp /tmp/a "$i"; done',
  'cd ROOT; for i in 1; do read i; cp /tmp/a "$i"; done',
  'cd ROOT; for i in 1; do cp /tmp/a "${i:-local-hooks}/x"; done',
  'cd ROOT; for i in 1; do :; done; for i in local-hook?; do cp /tmp/a "$i/x"; done',
  'cd ROOT; for i in $(ls); do cp /tmp/a "$i"; done',
  'cd ROOT; for i in *; do cp /tmp/a "$i"; done',
  'cd ROOT; for i in 1; do eval "i=x"; cp /tmp/a "$i"; done',
]

describe('literal for-loop variables (#797)', () => {
  for (const c of PASS) it(`passes: ${c}`, () => { expect(decide(c).deny).toBe(false) })
  for (const c of DENY) it(`denies: ${c}`, () => { expect(decide(c).deny).toBe(true) })

  it('resolves only a sole literal binding', () => {
    expect([...literalLoopVars('for i in 1 2\ndo x "$i"\ndone')]).toEqual([['i', ['1', '2']]])
    expect(literalLoopVars('for i in $(ls)\ndo x\ndone').size).toBe(0)
    expect(literalLoopVars('for i in 1\ndo i=2\ndone').size).toBe(0)
    expect(literalLoopVars('for i in 1\ndo x\ndone\nsource f').size).toBe(0)
    expect(literalLoopVars(`for i in ${Array.from({ length: 65 }, (_, k) => k).join(' ')}\ndo x\ndone`).size).toBe(0)
  })
})
