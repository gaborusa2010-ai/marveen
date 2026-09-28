// Contract tests for the EFFECT-side half of the shared-skill-folder guard.
//
// The PreToolUse gate reads the command; this hook reads the disk. Card
// 7e6d6dc0 measured the gap it closes: `python3 /tmp/patch.py` changed a shared
// skill and left NO line in the audit log, while the same intent written as
// `python3 - <<'PY'` was blocked and logged. Text-judging cannot see a program
// that lives in a file, so this half judges the effect instead.
//
// These tests pin both directions, because a gate can fail two ways and the
// second one is the one that gets forgotten (kapu-javitas-tullo-nelkul):
//   - UNDER-shooting: a real change goes unreported.
//   - OVER-shooting: an unchanged tree, or a change already authorised, raises
//     an alert. An audit that cries wolf is switched off, and then the real
//     change is silent again.

import { describe, it, expect } from 'vitest'
import { homedir } from 'node:os'
import { join } from 'node:path'
// @ts-expect-error -- plain .mjs hook script, no types
import { diffTrees, hasChange, skillOfRelPath, skillsInDiff, grantCovers, agentFromCwd, isTreeShaped, readTree, GLOBAL_SKILL_DIR } from '../../scripts/hooks/global-skill-write-audit.mjs'

const f = (sha: string, size = 10) => ({ sha, size })

describe('skill audit: what counts as a change', () => {
  it('reports nothing when the tree is identical', () => {
    const t = { 'a/SKILL.md': f('aaa'), 'b/SKILL.md': f('bbb') }
    const d = diffTrees(t, { ...t })
    expect(hasChange(d)).toBe(false)
    expect(d).toEqual({ added: [], modified: [], deleted: [] })
  })

  it('sees a modified file by content, not by timestamp', () => {
    // Content is the yardstick on purpose: a touch that rewrites the same bytes
    // is not a change, and a change made a second after the snapshot still is.
    const before = { 'a/SKILL.md': f('aaa') }
    const d = diffTrees(before, { 'a/SKILL.md': f('zzz') })
    expect(d.modified).toEqual(['a/SKILL.md'])
    expect(hasChange(d)).toBe(true)
  })

  it('sees an added and a deleted file', () => {
    const d = diffTrees({ 'a/SKILL.md': f('aaa') }, { 'b/SKILL.md': f('bbb') })
    expect(d.added).toEqual(['b/SKILL.md'])
    expect(d.deleted).toEqual(['a/SKILL.md'])
  })

  it('a same-size but different-content edit is still a change', () => {
    // The cheap check (size) would miss this one. The measured incident was a
    // duplicate-paragraph removal plus an addition, which can land on the same
    // byte count.
    const d = diffTrees({ 'a/SKILL.md': f('aaa', 100) }, { 'a/SKILL.md': f('bbb', 100) })
    expect(d.modified).toEqual(['a/SKILL.md'])
  })
})

describe('skill audit: naming the affected skill', () => {
  it('takes the first path segment as the skill name', () => {
    expect(skillOfRelPath('google-hozzaferes/SKILL.md')).toBe('google-hozzaferes')
    expect(skillOfRelPath('munkanaplo/references/x.md')).toBe('munkanaplo')
  })

  it('collects every skill touched, de-duplicated and sorted', () => {
    const d = diffTrees(
      { 'b/SKILL.md': f('1'), 'a/SKILL.md': f('2') },
      { 'b/SKILL.md': f('9'), 'a/SKILL.md': f('9'), 'a/extra.md': f('3') },
    )
    expect(skillsInDiff(d)).toEqual(['a', 'b'])
  })

  it('a stray file at the root names no skill rather than inventing one', () => {
    expect(skillOfRelPath('.skill-index.md')).toBe('.skill-index.md')
    expect(skillOfRelPath('..')).toBe(null)
  })
})

describe('skill audit: an authorised change is NOT an alert', () => {
  // The expiry clock lives in the PreToolUse gate's readGrant (it returns null
  // for a lapsed grant) and is pinned by that gate's own tests. This half must
  // not own a second copy of it -- so here a non-null grant simply means "an
  // authority the shared reader already accepted".
  it('a live ad-hoc grant covers the change', () => {
    expect(grantCovers(['munkanaplo'], { until: 1, approvalId: 'abc' }, [], 'marveen').covered).toBe(true)
  })

  it('no grant at all is not coverage', () => {
    expect(grantCovers(['munkanaplo'], null, [], 'marveen').covered).toBe(false)
  })

  it('a standing grant covers its SKILLS, whoever happened to observe the change', () => {
    // Not narrowed to the observing agent, and this test pins the reason rather
    // than the convenience. Measured 2026-09-04, twenty minutes after the hook
    // went live: Forgacs edited butor-tervezes (named in HIS standing grant),
    // my session's next Bash call ran the audit, and asking "does MARVEEN hold a
    // grant for butor-tervezes?" turned a legitimate edit into an alert. A
    // PostToolUse tree diff cannot know the author -- the agent name is the
    // observer -- so matching on it is asking the wrong question.
    const standing = [{ agent: 'forgacs', skills: ['munkanaplo'], approvedBy: 'Fejes Istvan Gabor' }]
    expect(grantCovers(['munkanaplo'], null, standing, 'forgacs').covered).toBe(true)
    expect(grantCovers(['munkanaplo'], null, standing, 'marveen').covered).toBe(true)
    // A skill NO grant names is still uncovered -- the weakening stops there.
    expect(grantCovers(['google-hozzaferes'], null, standing, 'forgacs').covered).toBe(false)
    // A change spanning one covered and one uncovered skill is NOT covered.
    expect(grantCovers(['munkanaplo', 'google-hozzaferes'], null, standing, 'forgacs').covered).toBe(false)
  })

  it('missing or malformed authority files read as NO permission, never as a crash', () => {
    // Measured 2026-09-04: the first version of this hook threw here, because a
    // private reader handed the raw {grants: [...]} OBJECT where a list was
    // expected -- and a PostToolUse hook that throws makes a successful command
    // look failed. Coverage must degrade to "no", never to an exception.
    expect(grantCovers(['x'], null, null, 'marveen').covered).toBe(false)
    expect(() => grantCovers(['x'], null, { grants: [] } as never, 'marveen')).not.toThrow()
    expect(grantCovers(['x'], null, { grants: [] } as never, 'marveen').covered).toBe(false)
    expect(grantCovers([], null, [], 'marveen').covered).toBe(false)
    expect(grantCovers(null as never, null, [], 'marveen').covered).toBe(false)
  })
})

describe('skill audit: the pieces the runtime depends on', () => {
  it('watches the same folder the PreToolUse gate protects', () => {
    expect(GLOBAL_SKILL_DIR).toBe(join(homedir(), '.claude', 'skills'))
  })

  it('reads the agent name from the working directory, defaulting to the main agent', () => {
    expect(agentFromCwd('/home/gabor/marveen/agents/forgacs')).toBe('forgacs')
    expect(agentFromCwd('/home/gabor/marveen')).toBe('marveen')
  })
})

describe('skill audit: the instrument checks itself', () => {
  // Forgacs, the hour this hook shipped: "the snapshot comparison is ITSELF a
  // measurement, and it can fail consistently in the direction nobody checks."
  // He was right, and it happened within that same hour -- twice, to me, from my
  // own half-finished edit. readTree()'s return shape changed mid-edit while the
  // call site still stored whatever came back, so `{files: {...}, rootOk: true}`
  // was saved AS the snapshot; the next run reported 40 deletions and two new
  // "skills" named `files` and `rootOk`, and the run after that reported all 40
  // back as additions. The folder was untouched the whole time.
  it('recognises a real tree reading', () => {
    expect(isTreeShaped({ 'a/SKILL.md': { size: 3, sha: 'abc' } })).toBe(true)
    expect(isTreeShaped({})).toBe(true)   // an empty folder is a valid reading
  })

  it('rejects the exact shape that corrupted the snapshot', () => {
    expect(isTreeShaped({ files: { 'a/SKILL.md': { size: 3, sha: 'abc' } }, rootOk: true })).toBe(false)
  })

  it('rejects everything that is not a map of fingerprints', () => {
    for (const x of [null, undefined, [], 'x', 42, { 'a': 'b' }, { 'a': { size: 1 } }]) {
      expect(isTreeShaped(x as never)).toBe(false)
    }
  })

  it('reports whether the ROOT was readable, separately from finding nothing', () => {
    // An unreadable root and an empty folder both produce {}. Only one of them
    // means "nothing changed" -- the other means the instrument is blind, and a
    // baseline written from it would make every later run report calm forever.
    const missing = readTree('/nonexistent-path-for-this-test')
    expect(missing.rootOk).toBe(false)
    expect(missing.files).toEqual({})

    const real = readTree()
    expect(real.rootOk).toBe(true)
    // Positive control, Forgacs's point applied to the test itself: asserting
    // "no error" would pass just as happily against a broken reader.
    expect(Object.keys(real.files).length).toBeGreaterThan(0)
    expect(isTreeShaped(real.files)).toBe(true)
  })
})
