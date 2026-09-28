// The SAME script under TWO matchers must land twice, not once.
//
// MATCHERKEY901 (Anorak, 2026-09-20). templates/settings.json.template
// deliberately registers `global-skill-write-gate.mjs` under PreToolUse twice:
// once on `Bash` and once on `Write|Edit|MultiEdit|NotebookEdit`. The command
// strings are BYTE-IDENTICAL, because it is the same gate -- the two tool
// families are simply two different ways to reach the protected tree.
//
// ensureAgentHooks deduped on the command string alone, per event. So whichever
// matcher landed first made the other look "already present" forever, and the
// second was never installed. Measured that day on all five sub-agents: the gate
// sat on `Write|Edit|MultiEdit|NotebookEdit` and was missing on `Bash`, so a
// shell command reaching the same tree ran ungated.
//
// The check that this test is wired to the fix: revert the key back to the
// command alone and the first case below must go RED. It did, when measured.
//
// What this does NOT cover, and must not be read as covering: the same template
// also ships `bash-egress-gate.mjs` on `Bash`, whose command string is unique,
// and that one was missing everywhere too. A unique string cannot collide, so
// this bug is not its cause -- that one is still open (card e2632440).
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ensureAgentHooks, agentSettingsPath } from '../web/agent-scaffold.js'
import { PROJECT_ROOT } from '../config.js'

const NAME = '__matcherkey_probe__'
const GATE = 'node "/abs/scripts/hooks/global-skill-write-gate.mjs"'

let root: string
let projectScope: string

describe('ensureAgentHooks keys on matcher AND command', () => {
  const dir = join(PROJECT_ROOT, 'agents', NAME)
  const settings = agentSettingsPath(NAME)

  beforeEach(() => {
    if (existsSync(join(dir, 'HANDOFF.md'))) {
      throw new Error(`refusing: agents/${NAME} looks like a live agent`)
    }
    rmSync(dir, { recursive: true, force: true })
    mkdirSync(join(dir, '.claude'), { recursive: true })
    root = mkdtempSync(join(tmpdir(), 'matcherkey-'))
    projectScope = join(root, 'repo', '.claude', 'settings.json')
    mkdirSync(join(root, 'repo', '.claude'), { recursive: true })
    writeFileSync(projectScope, JSON.stringify({ hooks: {} }, null, 2))
  })
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true })
    rmSync(root, { recursive: true, force: true })
  })

  /** Every PreToolUse matcher that carries the skill-write gate, deduped. */
  function gateMatchers(): string[] {
    const parsed = JSON.parse(readFileSync(settings, 'utf-8')) as {
      hooks?: { PreToolUse?: Array<{ matcher?: string; hooks?: Array<{ command?: string }> }> }
    }
    const out = new Set<string>()
    for (const entry of parsed.hooks?.PreToolUse ?? []) {
      for (const h of entry.hooks ?? []) {
        if ((h.command ?? '').includes('global-skill-write-gate.mjs')) out.add(entry.matcher ?? '')
      }
    }
    return [...out].sort()
  }

  it('installs the Bash matcher even when the identical command already sits on another matcher', () => {
    // Pre-seed ONLY the write-tool matcher, exactly the state the five live
    // sub-agents were found in on 2026-09-20.
    writeFileSync(settings, JSON.stringify({
      hooks: {
        PreToolUse: [{ matcher: 'Write|Edit|MultiEdit|NotebookEdit', hooks: [{ type: 'command', command: GATE }] }],
      },
    }, null, 2))

    ensureAgentHooks(NAME, { user: settings, project: projectScope })

    const matchers = gateMatchers()
    expect(matchers).toContain('Write|Edit|MultiEdit|NotebookEdit')
    expect(matchers).toContain('Bash')
  })

  it('still does not duplicate within the SAME matcher (idempotent)', () => {
    // The negative control. Without it a "fix" that simply appends everything
    // on every boot would pass the case above and grow the file without bound.
    ensureAgentHooks(NAME, { user: settings, project: projectScope })
    const after1 = JSON.parse(readFileSync(settings, 'utf-8')) as {
      hooks?: { PreToolUse?: Array<{ matcher?: string; hooks?: Array<{ command?: string }> }> }
    }
    const count = (parsed: typeof after1, matcher: string): number =>
      (parsed.hooks?.PreToolUse ?? [])
        .filter((e) => (e.matcher ?? '') === matcher)
        .flatMap((e) => e.hooks ?? [])
        .filter((h) => (h.command ?? '').includes('global-skill-write-gate.mjs')).length

    ensureAgentHooks(NAME, { user: settings, project: projectScope })
    const after2 = JSON.parse(readFileSync(settings, 'utf-8')) as typeof after1

    for (const m of ['Bash', 'Write|Edit|MultiEdit|NotebookEdit']) {
      expect(count(after2, m)).toBe(count(after1, m))
      expect(count(after2, m)).toBeLessThanOrEqual(1)
    }
  })
})
