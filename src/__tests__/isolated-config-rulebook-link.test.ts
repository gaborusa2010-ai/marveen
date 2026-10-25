// #647: a rebuilt sub-agent .claude-config must load the fleet rulebook.
//
// A sub-agent does not load the repo root CLAUDE.md (owner decision, Telegram
// msg 7844); it loads docs/flotta/kozos-szabalyok.md through a CLAUDE.md
// symlink in its isolated config dir. Both halves were made by hand, so a
// rebuilt dir (Brigid, 2026-10-21) came back with the root excluded and no
// link: no rulebook at all for three days.
//
// Pinned here:
//   - a fresh provision links CLAUDE.md to the rulebook, even when the
//     user-global ~/.claude/CLAUDE.md exists (that one is the main agent's);
//   - claudeMdExcludes carries the root CLAUDE.md, keeps existing entries,
//     adds no duplicate;
//   - ensureFleetRulebookLink keeps a regular file, relinks a dangling link,
//     and creates no link when the rulebook itself is missing.
//
// docs/flotta/ is not tracked in git: in a worktree the rulebook may not
// exist. The test creates it only when absent and removes only what it made,
// so the real file on the prod tree is never written.
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync, existsSync, lstatSync, readlinkSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { ensureIsolatedChannelConfigDir, ensureFleetRulebookLink, FLEET_RULEBOOK } from '../web/agent-process.js'
import { PROJECT_ROOT } from '../config.js'

const PROBE = 'rulebook-probe'
const probeDir = join(PROJECT_ROOT, 'agents', PROBE)
const cfg = join(probeDir, '.claude-config')
const link = join(cfg, 'CLAUDE.md')
const ROOT_CLAUDE_MD = join(PROJECT_ROOT, 'CLAUDE.md')

let fakeHome: string
let realHome: string | undefined
let scratch: string
let createdRulebook = false

function readIsolated(): Record<string, unknown> {
  return JSON.parse(readFileSync(join(cfg, 'settings.json'), 'utf-8')) as Record<string, unknown>
}

beforeEach(() => {
  realHome = process.env.HOME
  fakeHome = mkdtempSync(join(tmpdir(), 'rulebook-'))
  scratch = mkdtempSync(join(tmpdir(), 'rulebook-scratch-'))
  process.env.HOME = fakeHome
  mkdirSync(join(fakeHome, '.claude'), { recursive: true })
  writeFileSync(join(fakeHome, '.claude', 'settings.json'), JSON.stringify({ statusLine: { type: 'command', command: 'echo shared' } }))
  // The main agent's user-global rules: must NOT become a sub-agent's CLAUDE.md.
  writeFileSync(join(fakeHome, '.claude', 'CLAUDE.md'), '# main agent rules\n')

  if (!existsSync(FLEET_RULEBOOK)) {
    mkdirSync(dirname(FLEET_RULEBOOK), { recursive: true })
    writeFileSync(FLEET_RULEBOOK, '# test rulebook\n')
    createdRulebook = true
  }

  if (existsSync(join(probeDir, 'HANDOFF.md'))) {
    throw new Error(`refusing: agents/${PROBE} looks like a live agent`)
  }
  rmSync(probeDir, { recursive: true, force: true })
  mkdirSync(probeDir, { recursive: true })
})

afterEach(() => {
  process.env.HOME = realHome
  rmSync(fakeHome, { recursive: true, force: true })
  rmSync(scratch, { recursive: true, force: true })
  rmSync(probeDir, { recursive: true, force: true })
  if (createdRulebook) {
    rmSync(FLEET_RULEBOOK, { force: true })
    createdRulebook = false
  }
})

describe('#647: provisioning a sub-agent config dir', () => {
  it('links CLAUDE.md to the fleet rulebook, not the user-global one', () => {
    expect(ensureIsolatedChannelConfigDir(PROBE, null)).not.toBeNull()
    expect(lstatSync(link).isSymbolicLink()).toBe(true)
    expect(readlinkSync(link)).toBe(FLEET_RULEBOOK)
    // Control: the rest of the clone still happened.
    expect(readIsolated().statusLine).toEqual({ type: 'command', command: 'echo shared' })
  })

  it('excludes the root CLAUDE.md on a fresh dir', () => {
    ensureIsolatedChannelConfigDir(PROBE, null)
    expect(readIsolated().claudeMdExcludes).toEqual([ROOT_CLAUDE_MD])
  })

  it('keeps existing excludes and adds no duplicate on re-provision', () => {
    mkdirSync(cfg, { recursive: true })
    writeFileSync(join(cfg, 'settings.json'), JSON.stringify({ claudeMdExcludes: ['/elsewhere/CLAUDE.md', ROOT_CLAUDE_MD] }))
    ensureIsolatedChannelConfigDir(PROBE, null)
    ensureIsolatedChannelConfigDir(PROBE, null)
    expect(readIsolated().claudeMdExcludes).toEqual(['/elsewhere/CLAUDE.md', ROOT_CLAUDE_MD])
  })

  it('keeps an agent-written regular CLAUDE.md', () => {
    mkdirSync(cfg, { recursive: true })
    writeFileSync(link, '# own rules\n')
    ensureIsolatedChannelConfigDir(PROBE, null)
    expect(lstatSync(link).isSymbolicLink()).toBe(false)
    expect(readFileSync(link, 'utf-8')).toBe('# own rules\n')
  })
})

describe('#647: ensureFleetRulebookLink', () => {
  it('relinks a dangling CLAUDE.md', () => {
    const rulebook = join(scratch, 'rules.md')
    writeFileSync(rulebook, '# rules\n')
    symlinkSync(join(scratch, 'gone.md'), join(scratch, 'CLAUDE.md'))
    ensureFleetRulebookLink(scratch, PROBE, rulebook)
    expect(readlinkSync(join(scratch, 'CLAUDE.md'))).toBe(rulebook)
  })

  it('leaves a live link to another target alone', () => {
    const rulebook = join(scratch, 'rules.md')
    const other = join(scratch, 'other.md')
    writeFileSync(rulebook, '# rules\n')
    writeFileSync(other, '# other\n')
    symlinkSync(other, join(scratch, 'CLAUDE.md'))
    ensureFleetRulebookLink(scratch, PROBE, rulebook)
    expect(readlinkSync(join(scratch, 'CLAUDE.md'))).toBe(other)
  })

  it('creates no link when the rulebook is missing', () => {
    ensureFleetRulebookLink(scratch, PROBE, join(scratch, 'missing.md'))
    expect(existsSync(join(scratch, 'CLAUDE.md'))).toBe(false)
    expect(() => lstatSync(join(scratch, 'CLAUDE.md'))).toThrow()
  })
})
