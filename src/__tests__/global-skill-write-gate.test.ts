// Contract tests for the shared-skill-folder brake (GLOBALSKILL901).
//
// The 2026-08-31 security review's second finding: an autonomy level is a
// sentence in a prompt, and nothing stops an agent acting above its level. It
// was not hypothetical -- Cody rewrote a global skill (level 2, approval
// required) without approval and put it back himself. The shared skill folder
// is the sharpest case because every file in it loads for the WHOLE fleet.
//
// These tests pin three things:
//   1. Writes to the shared folder are refused, through Write/Edit AND Bash.
//   2. The symlinked <workdir>/.claude-config/skills path is refused too -- the
//      trap the project's own CLAUDE.md names.
//   3. An agent's OWN .claude/skills stays open, and reading anything is fine.
//      A gate that blocks legitimate work gets switched off within the day.

import { describe, it, expect } from 'vitest'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { tmpdir } from 'node:os'
import { mkdirSync, mkdtempSync, symlinkSync } from 'node:fs'
// @ts-expect-error -- plain .mjs hook script, no types
import { skillWriteDecision, isGlobalSkillPath, bashTouchesGlobalSkills, readGrant, skillOfPath, skillsInCommand, agentFromCwd, GLOBAL_SKILL_DIR, REPO_SKILL_DIR } from '../../scripts/hooks/global-skill-write-gate.mjs'

const GLOBAL = join(homedir(), '.claude', 'skills')
const OWN = '/home/gabor/marveen/.claude/skills'

describe('global skill gate: path recognition', () => {
  it('recognises the shared folder and files under it', () => {
    expect(GLOBAL_SKILL_DIR).toBe(GLOBAL)
    expect(isGlobalSkillPath(GLOBAL)).toBe(true)
    expect(isGlobalSkillPath(`${GLOBAL}/skill-factory/SKILL.md`)).toBe(true)
    expect(isGlobalSkillPath('~/.claude/skills/foo/SKILL.md')).toBe(true)
  })

  it("does NOT claim an agent's own skills folder", () => {
    expect(isGlobalSkillPath(`${OWN}/mine/SKILL.md`)).toBe(false)
    expect(isGlobalSkillPath('/home/gabor/marveen/agents/roxy/.claude/skills/x/SKILL.md')).toBe(false)
  })

  it('is segment-aware, so a lookalike sibling folder does not match', () => {
    expect(isGlobalSkillPath(`${homedir()}/.claude/skills-old/x`)).toBe(false)
    expect(isGlobalSkillPath(`${homedir()}/.claude/skillsomething`)).toBe(false)
  })

  it('resolves traversal back into the protected folder', () => {
    expect(isGlobalSkillPath(`${GLOBAL}/../skills/sneaky/SKILL.md`)).toBe(true)
  })
})

describe('global skill gate: the write is refused', () => {
  it('blocks a Write into the shared folder', () => {
    const d = skillWriteDecision('Write', { file_path: `${GLOBAL}/new/SKILL.md` }, null)
    expect(d.blocked).toBe(true)
    expect(d.tier).toBe('no-grant')
  })

  it('blocks an Edit into the shared folder', () => {
    expect(skillWriteDecision('Edit', { file_path: `${GLOBAL}/x/SKILL.md` }, null).blocked).toBe(true)
  })

  it('blocks the symlinked .claude-config/skills path in Bash (the CLAUDE.md trap)', () => {
    const cmd = 'cp /tmp/SKILL.md /home/gabor/marveen/agents/roxy/.claude-config/skills/new/SKILL.md'
    expect(bashTouchesGlobalSkills(cmd)).toBe(true)
    expect(skillWriteDecision('Bash', { command: cmd }, null).blocked).toBe(true)
  })

  it('blocks the shell write verbs', () => {
    for (const cmd of [
      `cp x ${GLOBAL}/y`,
      `mv x ${GLOBAL}/y`,
      `rm -rf ${GLOBAL}/y`,
      `tee ${GLOBAL}/y`,
      `sed -i s/a/b/ ${GLOBAL}/y/SKILL.md`,
      `echo hi > ${GLOBAL}/y/SKILL.md`,
      `mkdir -p ${GLOBAL}/y`,
      `ln -s /tmp/evil ${GLOBAL}/y`,
    ]) {
      expect(bashTouchesGlobalSkills(cmd), cmd).toBe(true)
    }
  })
})

describe('global skill gate: what must keep working', () => {
  it("leaves an agent's own skills folder alone", () => {
    expect(skillWriteDecision('Write', { file_path: `${OWN}/mine/SKILL.md` }, null).blocked).toBe(false)
    expect(bashTouchesGlobalSkills(`cp x ${OWN}/y`)).toBe(false)
  })

  it('does not block READING the shared folder', () => {
    for (const cmd of [
      `ls ${GLOBAL}`,
      `cat ${GLOBAL}/skill-factory/SKILL.md`,
      `grep -r trigger ${GLOBAL}`,
      `find ${GLOBAL} -name SKILL.md`,
      `diff ${GLOBAL}/a/SKILL.md /tmp/b`,
      `wc -l ${GLOBAL}/a/SKILL.md`,
    ]) {
      expect(bashTouchesGlobalSkills(cmd), cmd).toBe(false)
    }
  })

  it('ignores unrelated tools and unrelated paths', () => {
    expect(skillWriteDecision('Read', { file_path: `${GLOBAL}/x/SKILL.md` }, null).blocked).toBe(false)
    expect(skillWriteDecision('Write', { file_path: '/home/gabor/marveen/docs/x.md' }, null).blocked).toBe(false)
    expect(skillWriteDecision('Bash', { command: 'npm run build' }, null).blocked).toBe(false)
  })

  it('does not fire on a write verb that has nothing to do with the folder', () => {
    expect(bashTouchesGlobalSkills('rm -rf /tmp/junk')).toBe(false)
  })
})

describe('global skill gate: the approval turned into a fact on disk', () => {
  const now = 1_800_000_000

  it('an unexpired grant opens the gate, and says so', () => {
    const grant = { until: now + 600, reason: 'approved patch', approvalId: 'abc' }
    const d = skillWriteDecision('Write', { file_path: `${GLOBAL}/x/SKILL.md` }, grant)
    expect(d.blocked).toBe(false)
    expect(d.tier).toBe('granted')
  })

  it('an expired grant does not', () => {
    expect(readGrant(now, '/nonexistent/grant.json')).toBeNull()
    const d = skillWriteDecision('Write', { file_path: `${GLOBAL}/x/SKILL.md` }, null)
    expect(d.blocked).toBe(true)
  })

  it('a missing or malformed grant file reads as NO permission, never as an error', () => {
    expect(readGrant(now, '/nonexistent/grant.json')).toBeNull()
    expect(readGrant(now, '/etc/hostname')).toBeNull()
  })
})

describe('global skill gate: a segment is not the whole command (GLOBALSKILL901-FP)', () => {
  // The first live wiring refused this within a minute: a READ of the shared
  // folder chained with an unrelated write elsewhere in the same command. The
  // gate had tested "mentions the folder" and "has a write verb" against the
  // whole string, and they belonged to different segments.
  // #606: since #261 a relative `.claude/skills` is judged by the caller's cwd,
  // and from the repo root it IS the protected repo folder. These cases were
  // written for a sub-agent's own folder, so they run from a sub-agent's cwd.
  // The absolute repo-root mkdir that used to stand here is a protected write
  // now (tg 8271); it only passed when the repo root was not /home/gabor/marveen.
  const REPO_ROOT = dirname(dirname(REPO_SKILL_DIR))
  const AGENT_CWD = join(REPO_ROOT, 'agents', 'cody')
  const SHOULD_PASS = [
    'ls ~/.claude/skills | head -3',
    'ls ~/.claude/skills | head -3 && mkdir -p .claude/skills/proba && rmdir .claude/skills/proba',
    `cat ${GLOBAL}/skill-factory/SKILL.md`,
    'grep -rl pinned ~/.claude/skills > /tmp/out.txt',
    `cp ${GLOBAL}/x/SKILL.md /tmp/masolat.md`,
    `diff ${GLOBAL}/a/SKILL.md /tmp/b`,
    'find ~/.claude/skills -name SKILL.md | wc -l',
    'rm -rf /tmp/junk && ls ~/.claude/skills',
  ]
  it.each(SHOULD_PASS)('allows: %s', (cmd) => {
    expect(bashTouchesGlobalSkills(cmd, AGENT_CWD)).toBe(false)
  })

  it('#261: the same relative mkdir from the repo root is a write to the protected repo folder', () => {
    const cmd = 'ls ~/.claude/skills | head -3 && mkdir -p .claude/skills/proba && rmdir .claude/skills/proba'
    expect(bashTouchesGlobalSkills(cmd, REPO_ROOT)).toBe(true)
    expect(bashTouchesGlobalSkills(`mkdir -p ${REPO_SKILL_DIR}/sajat`, AGENT_CWD)).toBe(true)
  })

  const SHOULD_BLOCK = [
    'cp /tmp/SKILL.md ~/.claude/skills/uj/SKILL.md',
    `mv /tmp/x ${GLOBAL}/y`,
    `rm -rf ${GLOBAL}/regi`,
    'mkdir -p ~/.claude/skills/uj',
    'echo hi > ~/.claude/skills/x/SKILL.md',
    `sed -i s/a/b/ ${GLOBAL}/x/SKILL.md`,
    'cp x /home/gabor/marveen/agents/cody/.claude-config/skills/y',
    'tee ~/.claude/skills/x/SKILL.md',
    `cat /tmp/a > ${GLOBAL}/b`,
  ]
  it.each(SHOULD_BLOCK)('blocks: %s', (cmd) => {
    expect(bashTouchesGlobalSkills(cmd)).toBe(true)
  })

  it('reads a copy OUT of the folder as a read, not a write', () => {
    // The destination is what matters for cp/mv: the shared folder appearing as
    // a SOURCE is someone taking a copy, which is allowed.
    expect(bashTouchesGlobalSkills(`cp ${GLOBAL}/a/SKILL.md /tmp/b`)).toBe(false)
    expect(bashTouchesGlobalSkills(`cp /tmp/b ${GLOBAL}/a/SKILL.md`)).toBe(true)
  })
})

describe('global skill gate: the REAL miss, not a false positive (SEPSPLIT901)', () => {
  // Everything else on this page is about not blocking honest work. This one is
  // the opposite: the gate let a real write THROUGH, and the directory was
  // actually created on disk. `cd /repo; mkdir -p <protected>` tokenised the
  // semicolon into the previous word, so no segment boundary existed and the
  // verb read as `cd`. Found by running the gate against itself, not by a test.
  it('blocks a write that follows a semicolon with no space', () => {
    expect(bashTouchesGlobalSkills(`cd /home/gabor/marveen; mkdir -p ${GLOBAL}/x`)).toBe(true)
  })

  it('blocks it with a redirect suffix attached too', () => {
    expect(bashTouchesGlobalSkills(`cd /repo; mkdir -p ${GLOBAL}/x 2>&1 | head -2`)).toBe(true)
  })

  it('still lets the read half of the same chain through', () => {
    expect(bashTouchesGlobalSkills(`cd /repo; ls ${GLOBAL} | wc -l`)).toBe(false)
  })

  it('does not treat a protected path quoted inside a payload as a write', () => {
    // The second false positive: a curl POST documenting the gate itself. The
    // fragment `<workdir>/.claude-config/skills` even looked like a redirect.
    const body = `{"content":"Vedi a ${GLOBAL} mappat es a <workdir>/.claude-config/skills symlinket"}`
    expect(bashTouchesGlobalSkills(`curl -s -X POST http://localhost:3420/api/x -d '${body}'`)).toBe(false)
  })

  it('DOES fire on a heredoc body that mentions the folder -- reversed on purpose', () => {
    // This test used to assert the opposite, and that expectation was wrong.
    // INTERPWRITE901 (Forgacs) showed a python heredoc writing a skill file with
    // no audit line at all: inside a program body, "merely mentions" cannot be
    // told from "writes". The gate now fails closed there, and the cost -- a
    // read-only one-liner being refused too -- is accepted and documented.
    // Kept as a reversed assertion rather than deleted, so the change of mind
    // stays visible instead of quietly vanishing from the suite.
    const cmd = ["python3 - <<'PY'", `# a ${GLOBAL} vedett mappa`, 'print(1)', 'PY'].join('\n')
    expect(bashTouchesGlobalSkills(cmd)).toBe(true)
  })
})

describe('global skill gate: the symlink actually resolves (SYMLINKMISS901, found by Roxy)', () => {
  // The second REAL miss of the day, and the more serious one: a Write through
  // <workdir>/.claude-config/skills went straight into the shared folder. The
  // path check used resolve(), which normalises `..` but does not follow a
  // symlink. The Bash branch caught the same path only because it matches TEXT,
  // which is why the gap was invisible from the Bash tests.
  // Built per run, so the test holds on a machine without the fleet layout (CI).
  mkdirSync(GLOBAL, { recursive: true })
  const ROXY = mkdtempSync(join(tmpdir(), 'symlinkmiss901-'))
  mkdirSync(join(ROXY, '.claude-config'), { recursive: true })
  symlinkSync(GLOBAL, join(ROXY, '.claude-config', 'skills'))
  const VIA_LINK = join(ROXY, '.claude-config', 'skills', 'handoff', 'SKILL.md')

  it('recognises the symlinked path as the shared folder', () => {
    expect(isGlobalSkillPath(VIA_LINK)).toBe(true)
  })

  it('blocks a Write through the symlink', () => {
    expect(skillWriteDecision('Write', { file_path: VIA_LINK }, null).blocked).toBe(true)
  })

  it('blocks an Edit through the symlink', () => {
    expect(skillWriteDecision('Edit', { file_path: VIA_LINK }, null).blocked).toBe(true)
  })

  it('still allows a Write to the agent OWN skills folder next to it', () => {
    const own = join(ROXY, '.claude', 'skills', 'sajat', 'SKILL.md')
    expect(skillWriteDecision('Write', { file_path: own }, null).blocked).toBe(false)
  })

  it('handles a path that does not exist yet (the file a Write is about to create)', () => {
    expect(isGlobalSkillPath(`${GLOBAL}/meg-nem-letezik/melyebben/SKILL.md`)).toBe(true)
  })
})

describe('global skill gate: standing, scoped grants (STANDING901)', () => {
  // The owner granted Forgacs standing authority for the four workshop skills on
  // 2026-09-01, because he patches them most and a per-patch approval round was
  // real friction. Relocating them was rejected: all four trigger on an OWNER
  // MESSAGE, which can arrive at any agent, so they stay shared. The grant is
  // therefore narrow: not "Forgacs may write global skills", but "these four".
  const FOUR = ['munkanaplo', 'butor-tervezes', 'uk-sole-trader-szamla', 'nyugta-rogzites']
  const STANDING = [{ agent: 'forgacs', skills: FOUR, reason: 'workshop owner', approvedBy: 'Fejes Istvan Gabor' }]

  it('lets the granted agent write the granted skills', () => {
    for (const skill of FOUR) {
      const d = skillWriteDecision('Write', { file_path: `${GLOBAL}/${skill}/SKILL.md` }, null, STANDING, 'forgacs')
      expect(d.blocked, skill).toBe(false)
      expect(d.tier).toBe('standing')
    }
  })

  it('does NOT extend to any other skill', () => {
    const d = skillWriteDecision('Write', { file_path: `${GLOBAL}/skill-factory/SKILL.md` }, null, STANDING, 'forgacs')
    expect(d.blocked).toBe(true)
  })

  it('does NOT extend to another agent', () => {
    for (const who of ['cody', 'roxy', 'marveen']) {
      const d = skillWriteDecision('Write', { file_path: `${GLOBAL}/munkanaplo/SKILL.md` }, null, STANDING, who)
      expect(d.blocked, who).toBe(true)
    }
  })

  it('covers the Bash path too, with the same narrowing', () => {
    expect(skillWriteDecision('Bash', { command: `cp /tmp/x ${GLOBAL}/munkanaplo/SKILL.md` }, null, STANDING, 'forgacs').blocked).toBe(false)
    expect(skillWriteDecision('Bash', { command: `cp /tmp/x ${GLOBAL}/skill-factory/SKILL.md` }, null, STANDING, 'forgacs').blocked).toBe(true)
  })

  it('refuses a command that touches a granted AND an ungranted skill', () => {
    // Partial cover is no cover: the whole command is refused, not half of it.
    const cmd = `rm -rf ${GLOBAL}/munkanaplo ${GLOBAL}/skill-factory`
    expect(skillWriteDecision('Bash', { command: cmd }, null, STANDING, 'forgacs').blocked).toBe(true)
  })

  it('refuses when the skill cannot be named at all', () => {
    // A write to the folder root names no skill, so no standing grant covers it.
    const d = skillWriteDecision('Bash', { command: `rm -rf ${GLOBAL}` }, null, STANDING, 'forgacs')
    expect(d.blocked).toBe(true)
  })

  it('reads the skill name out of a path and a command', () => {
    expect(skillOfPath(`${GLOBAL}/munkanaplo/SKILL.md`)).toBe('munkanaplo')
    expect(skillOfPath(`${GLOBAL}`)).toBeNull()
    expect(skillsInCommand(`cp a ${GLOBAL}/munkanaplo/SKILL.md`)).toEqual(['munkanaplo'])
  })

  it('infers the calling agent from the working directory (a brake, not an identity)', () => {
    expect(agentFromCwd('/home/gabor/marveen/agents/forgacs')).toBe('forgacs')
    expect(agentFromCwd('/home/gabor/marveen')).toBe('marveen')
  })
})

describe('global skill gate: an interpreter writes too (INTERPWRITE901, found by Forgacs)', () => {
  // Found on the FIRST use of the standing grant: Forgacs patched a granted
  // skill with a python heredoc. The write succeeded and left NO audit line at
  // all -- not even an allowed one. The same edit via the Edit tool logged
  // immediately. The write-verb lists knew shells and file utilities; an
  // interpreter that opens the file itself was on no list, and the path lived
  // inside a heredoc body the reader strips.
  //
  // The reason it matters more than a missing verb: this project's own standing
  // instruction is to edit files from Bash with sed, heredocs or short scripts.
  // The house style routes the fleet down exactly the blind path, so the audit
  // log was never "attempts", only "attempts that used Write/Edit".
  const HEREDOC = [
    "python3 - <<'PY'",
    'import io',
    `p='${GLOBAL}/uk-sole-trader-szamla/SKILL.md'`,
    "io.open(p,'w').write(x)",
    'PY',
  ].join('\n')

  it('catches the heredoc that went through unlogged', () => {
    expect(bashTouchesGlobalSkills(HEREDOC)).toBe(true)
  })

  it('catches inline programs in every common interpreter', () => {
    expect(bashTouchesGlobalSkills(`python3 -c "open('${GLOBAL}/x/SKILL.md','w')"`)).toBe(true)
    expect(bashTouchesGlobalSkills(`node -e "fs.writeFileSync('${GLOBAL}/x','y')"`)).toBe(true)
    expect(bashTouchesGlobalSkills(`perl -i -pe 's/a/b/' ${GLOBAL}/x/SKILL.md`)).toBe(true)
    expect(bashTouchesGlobalSkills(`cat f | python3 - # ${GLOBAL}`)).toBe(true)
  })

  it('the grant still decides who may do it', () => {
    const FOUR = ['munkanaplo', 'butor-tervezes', 'uk-sole-trader-szamla', 'nyugta-rogzites']
    const STANDING = [{ agent: 'forgacs', skills: FOUR, reason: '', approvedBy: 'owner' }]
    expect(skillWriteDecision('Bash', { command: HEREDOC }, null, STANDING, 'forgacs').blocked).toBe(false)
    expect(skillWriteDecision('Bash', { command: HEREDOC }, null, STANDING, 'cody').blocked).toBe(true)
  })

  it('does NOT flag an interpreter running a repo script (the stated gap)', () => {
    // `bash scripts/x.sh` and `python3 scripts/x.py` are reviewed code. Flagging
    // them would catch every helper the fleet runs -- including the inter-agent
    // messenger, whose ARGUMENT text often quotes the protected path.
    expect(bashTouchesGlobalSkills(`bash scripts/agent-msg.sh marveen forgacs "a ${GLOBAL} vedett"`)).toBe(false)
    expect(bashTouchesGlobalSkills('python3 scripts/muhely/invoice.py --list')).toBe(false)
  })

  it('does not turn reading into a write for non-interpreter commands', () => {
    expect(bashTouchesGlobalSkills(`cat ${GLOBAL}/munkanaplo/SKILL.md`)).toBe(false)
    expect(bashTouchesGlobalSkills(`grep -rl trigger ${GLOBAL}`)).toBe(false)
  })
})

describe('global skill gate: the interpreter rule is per-segment too (GLOBALSKILL901-FP, 3rd)', () => {
  // Forgacs, 2026-09-01. He ran a plain skill search chained with an unrelated
  // inline script and was refused. The grep half was allowed on its own; the
  // python half never mentioned the folder. The gate glued the two together --
  // the same "read the command as a bag of words" shape that produced the first
  // two false positives in this file, surviving on the one branch that had not
  // been moved to segments yet.
  it('lets a clean grep through when an UNRELATED segment has an inline script', () => {
    expect(bashTouchesGlobalSkills(`grep -rl "PIPESTATUS" ${GLOBAL} | head -3`)).toBe(false)
    expect(
      bashTouchesGlobalSkills(`grep -rl "PIPESTATUS" ${GLOBAL} | head -3; echo ---; python3 -c "print('fuggetlen')"`),
    ).toBe(false)
  })

  it('still fires when the SAME segment carries the interpreter and the path', () => {
    expect(bashTouchesGlobalSkills(`python3 -c "open('${GLOBAL}/x/SKILL.md','w')"`)).toBe(true)
    expect(bashTouchesGlobalSkills(`ls /tmp; node -e "require('fs').writeFileSync('${GLOBAL}/x/SKILL.md','')"`)).toBe(true)
  })

  it('keeps the whole-command reading when a heredoc is present', () => {
    // A heredoc body belongs to no segment once stripped, and that body is where
    // INTERPWRITE901 hid the path. Narrowing to segments there would re-open the
    // exact hole this gate was extended to close.
    const heredoc = `python3 - <<'PY'\nopen('${GLOBAL}/x/SKILL.md','w')\nPY`
    expect(bashTouchesGlobalSkills(heredoc)).toBe(true)
  })
})

describe('a newline does not hide a skill write either (NEWLINE901)', () => {
  // The write that exposed this was my own: a `cd` line, then a heredoc patch of a
  // shared skill. It succeeded under a valid grant and left NO audit line -- the
  // gate never saw it as a write at all, which is the one thing this gate exists
  // to guarantee.
  it('sees the interpreter when it is not on the FIRST line', () => {
    const oneLine = `python3 - <<'PY'\nopen('${GLOBAL}/x/SKILL.md','w')\nPY`
    expect(bashTouchesGlobalSkills(oneLine)).toBe(true)
    expect(bashTouchesGlobalSkills(`cd /home/gabor/marveen\n${oneLine}`)).toBe(true)
    expect(bashTouchesGlobalSkills(`cd /home/gabor/marveen\nS=x\n${oneLine}`)).toBe(true)
  })

  it('does not start blocking harmless multi-line reads', () => {
    expect(bashTouchesGlobalSkills(`cd /home/gabor/marveen\ncat ${GLOBAL}/x/SKILL.md`)).toBe(false)
    expect(bashTouchesGlobalSkills(`cd /home/gabor/marveen\ngrep -rn trigger ${GLOBAL}\necho done`)).toBe(false)
  })
})

describe('the skill gate knows the same wrappers as the egress gate (WRAPFLAG901)', () => {
  // Found by probing my own same-day change: the two gates each carried their
  // OWN wrapper table, with different contents. `env python3 -c "<write>"` was
  // not even recognised as an interpreter call here, so it wrote to the shared
  // folder unlogged. The table now lives in lib/shell-command.mjs and both gates
  // call verbIndex(), so they cannot drift apart again.
  it('sees the interpreter behind env / nohup / stdbuf', () => {
    const write = `python3 -c "open('${GLOBAL}/x/SKILL.md','w')"`
    expect(bashTouchesGlobalSkills(write)).toBe(true)
    expect(bashTouchesGlobalSkills(`env ${write}`)).toBe(true)
    expect(bashTouchesGlobalSkills(`nohup ${write}`)).toBe(true)
    expect(bashTouchesGlobalSkills(`stdbuf -oL ${write}`)).toBe(true)
  })

  it('still lets reads and own-folder writes through', () => {
    expect(bashTouchesGlobalSkills(`env cat ${GLOBAL}/x/SKILL.md`)).toBe(false)
    expect(bashTouchesGlobalSkills(`nohup grep -rn trigger ${GLOBAL}`)).toBe(false)
  })
})

describe('a quoted destination is still a destination (QUOTEDDEST901 / REDIRHIDE901)', () => {
  // Cody, 2026-09-01, six measured bypasses in one round. Quoting a path is
  // ordinary habit, not evasion -- and it made the gate blind. He also MEASURED
  // that the quoted-token filter is not what stops the JSON-body false positive
  // it was added for: dropping `-d`/`-H` values does that. Both are asserted here.
  it('sees a quoted target for cp / mv / redirect', () => {
    expect(bashTouchesGlobalSkills(`cp /tmp/x '${GLOBAL}/proba/SKILL.md'`)).toBe(true)
    expect(bashTouchesGlobalSkills(`mv /tmp/x "${GLOBAL}/proba/SKILL.md"`)).toBe(true)
    expect(bashTouchesGlobalSkills(`echo x > '${GLOBAL}/proba/SKILL.md'`)).toBe(true)
  })

  it('is not fooled when a redirect follows the real destination', () => {
    // The log path used to become the "destination" and the protected path was
    // never looked at.
    expect(bashTouchesGlobalSkills(`cp /tmp/x ${GLOBAL}/proba/SKILL.md > /tmp/log.txt`)).toBe(true)
    expect(bashTouchesGlobalSkills(`cp /tmp/x ${GLOBAL}/proba/SKILL.md >/tmp/log.txt`)).toBe(true)
  })

  it('still does not fire on a JSON body that merely quotes the path', () => {
    expect(bashTouchesGlobalSkills(`curl -s -d '{"c":"${GLOBAL}/x"}' http://localhost:3420/x`)).toBe(false)
    expect(bashTouchesGlobalSkills(`cat ${GLOBAL}/x/SKILL.md`)).toBe(false)
  })
})
