// The main agent's transcript is read from the dir it is WRITTEN to.
//
// MAINCFG920 (2026-09-20). schedule-runner, context-guard-runner and
// context-restart-gate-runner each resolved the main agent's config root as
// `undefined`, which sends projectsDirFor() to ~/.claude/projects/<encoded>.
// With main-agent isolation the session writes under
// <root>/.channels-config/projects/<encoded>. The shared root is not missing --
// it is a real directory full of OLD sessions -- so the read never errored, it
// just returned a nine-hour-stale mtime (measured on the live install: 07:36:10
// against a live transcript being written at 16:48:21).
//
// The damage was in schedule-runner, which uses that mtime as the `sawTurn`
// evidence that an injected task started a turn. Stale mtime => never newer
// than injectedAt => the only remaining evidence is catching the pane 'busy' on
// a 30-second sweep. Long turns are caught; a SHORT one is not, so the silent
// heartbeat round -- the one designed to cost nothing -- was declared 'lost'
// and re-injected every ~30s. 38 false-lost re-injections on 2026-09-20.
//
// The first case below is the regression: with `undefined` it reads the stale
// directory, which is precisely the old behaviour, so it is its own negative
// control.
import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, utimesSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  mainAgentConfigDirForRead,
  readTranscriptMtimeFromProjectDir,
  projectsDirFor,
} from '../web/active-model.js'

const WORKDIR = '/srv/marveen'
const ENCODED = WORKDIR.replace(/[/.]/g, '-')
const STALE = new Date('2026-09-20T07:36:10Z')
const FRESH = new Date('2026-09-20T16:48:21Z')

let root: string
let fakeHome: string

function seed(dir: string, when: Date): void {
  mkdirSync(dir, { recursive: true })
  const f = join(dir, 'session.jsonl')
  writeFileSync(f, '{}\n')
  utimesSync(f, when, when)
}

describe('mainAgentConfigDirForRead', () => {
  beforeAll(() => {
    root = mkdtempSync(join(tmpdir(), 'maincfg920-'))
    fakeHome = mkdtempSync(join(tmpdir(), 'maincfg920-home-'))
    // The shared root: real, populated, and NINE HOURS BEHIND.
    seed(join(fakeHome, '.claude', 'projects', ENCODED), STALE)
    // The isolated root: where the session actually writes.
    seed(join(root, '.channels-config', 'projects', ENCODED), FRESH)
  })
  afterAll(() => {
    rmSync(root, { recursive: true, force: true })
    rmSync(fakeHome, { recursive: true, force: true })
  })

  it('a configDir of undefined reads the STALE shared root -- the bug, kept as its own control', () => {
    const shared = projectsDirFor(WORKDIR, undefined, fakeHome)
    expect(readTranscriptMtimeFromProjectDir(shared === '' ? '' : WORKDIR, join(fakeHome, '.claude')))
      .toBe(STALE.getTime())
  })

  it('returns the isolated root when its projects dir exists, and that reads FRESH', () => {
    const cfg = mainAgentConfigDirForRead(root)
    expect(cfg).toBe(join(root, '.channels-config'))
    expect(readTranscriptMtimeFromProjectDir(WORKDIR, cfg)).toBe(FRESH.getTime())
  })

  it('falls back to undefined when the install never ran isolated', () => {
    const plain = mkdtempSync(join(tmpdir(), 'maincfg920-plain-'))
    try {
      expect(mainAgentConfigDirForRead(plain)).toBeUndefined()
    } finally {
      rmSync(plain, { recursive: true, force: true })
    }
  })

  it('the two roots really are different directories (guards a symlinked install)', () => {
    expect(projectsDirFor(WORKDIR, mainAgentConfigDirForRead(root)))
      .not.toBe(projectsDirFor(WORKDIR, undefined, fakeHome))
  })
})
