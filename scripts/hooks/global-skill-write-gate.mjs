#!/usr/bin/env node
// PreToolUse hook: a technical brake on the SHARED skill folder.
//
// GLOBALSKILL901. The 2026-08-31 security review's second finding: an autonomy
// level is a sentence in a prompt. The API refuses to RAISE a level, but
// nothing stops an agent from acting above the level it has. It was not a
// theory that day -- Cody rewrote a global skill, a level-2 (approval-required)
// category, without approval. He put it back himself once he read the config.
// Nothing except his own attention stopped him.
//
// ~/.claude/skills is the sharpest case, because it is not one agent's mistake:
// every file in there loads for the WHOLE fleet. The project's own CLAUDE.md
// already warns that <workdir>/.claude-config/skills is a symlink to it, so an
// agent can land in the shared folder while believing it is writing its own.
//
// WHAT THIS DOES
//   - Write / Edit / MultiEdit whose target is inside the GLOBAL skill folder:
//     denied, with the approval procedure in the message.
//   - Bash commands that would WRITE there (cp, mv, rm, tee, sed -i, a `>`
//     redirect, ...): denied. Read-only commands (ls, cat, grep, diff) are not
//     touched -- looking is not changing.
//   - An agent's OWN skills, <workdir>/.claude/skills/, are untouched. That is
//     where an agent is supposed to put its own work, and a gate that blocked
//     it would just push people back to the global folder.
//
// THE ESCAPE HATCH IS THE POINT
// Level 2 means "with approval", not "never". A hook cannot wait for an
// approval, so the approval is turned into a fact on disk: store/skill-write-
// grant.json with an expiry. Write it after /api/approvals returns approved,
// do the edit, let it lapse. Every write that passes under a grant is logged,
// so a grant cannot be quiet.
//
// LIMITS, said out loud: a write from a script this hook cannot see, or from a
// process started outside the tool layer, is not covered. This raises the cost
// of an accident and makes a deliberate act leave a trace. It is not a
// permission system.

import { readFileSync, appendFileSync, mkdirSync, realpathSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { basename, dirname, join, resolve } from 'node:path'
import { homedir } from 'node:os'
import { readCommand, tokenizeDetailed, splitSegments, verbIndex, readsProgramFromStdin } from './lib/shell-command.mjs'
import { logSafe } from './lib/redact.mjs'

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const AUDIT_LOG = join(REPO_ROOT, 'store', 'global-skill-writes.log')
const GRANT_PATH = join(REPO_ROOT, 'store', 'skill-write-grant.json')
// Standing, owner-approved, agent- and skill-scoped. Separate file from the
// ad-hoc one on purpose: one is a decision, the other is a moment.
const STANDING_GRANTS_PATH = join(REPO_ROOT, 'store', 'skill-write-grants.json')

/** The one folder this gate protects: the fleet-wide skill directory. */
export const GLOBAL_SKILL_DIR = join(homedir(), '.claude', 'skills')

// Textual forms of the protected path that can appear in a Bash command.
// `.claude-config/skills` is included because it is a SYMLINK to the global
// folder: the trap the project's CLAUDE.md names by hand.
const PATH_PATTERNS = [
  GLOBAL_SKILL_DIR,
  '~/.claude/skills',
  '$HOME/.claude/skills',
  '${HOME}/.claude/skills',
  '.claude-config/skills',
]

// GLOBALSKILL901-FP: this gate produced the same false positive TWICE, and the
// second time is why the command reading now lives in lib/shell-command.mjs.
//   1st: `ls ~/.claude/skills | head` chained with an unrelated `mkdir` was
//        refused -- "mentions the folder" and "has a write verb" were tested
//        against the WHOLE command, and belonged to different halves of a chain.
//   2nd: a curl POST whose JSON BODY quoted the protected path was refused. The
//        body was split on whitespace like command structure, and the fragment
//        `<workdir>/.claude-config/skills` read as a `>` redirect.
// Both are the same mistake: reading a command as a bag of words. The shared
// reader tokenises quote-aware (a quoted blob is ONE token and is data, never
// structure), drops what value-flags carry, and splits into real segments.

// Verbs where ANY protected path among the arguments is being changed.
const DESTRUCTIVE_VERBS = new Set([
  'rm', 'rmdir', 'unlink', 'mkdir', 'touch', 'tee', 'truncate',
  'chmod', 'chown', 'shred',
])
// Verbs where only the LAST argument is the destination; a protected path in
// any earlier position is a SOURCE, i.e. a read.
const COPY_VERBS = new Set(['cp', 'mv', 'rsync', 'install', 'ln'])

function mentionsProtected(token) {
  return PATH_PATTERNS.some((pat) => String(token).includes(pat))
}

// INTERPWRITE901 (Forgacs, 2026-09-01, found on the FIRST use of his new grant).
// He patched a granted skill with `python3 - <<'PY' ... PY`. The write SUCCEEDED
// and left NO line in the audit log -- not even an allowed one. The same edit
// through the Edit tool logged immediately. Two independent causes:
//   1. The write-verb lists name shells and file utilities. An interpreter that
//      opens the file ITSELF is on no list: python3, node, perl, ruby, awk -i.
//   2. The path lived inside the program text -- a heredoc body, which the
//      shared reader strips, and a quoted string, which the reader deliberately
//      treats as data. Both rules are right for a JSON payload and wrong for a
//      program, because a program body is not data: it is code that writes.
//
// What makes this the day's worst finding is not the gap itself but WHERE it
// points: this project's own standing instruction is to edit files from Bash
// with sed, heredocs or short scripts, and to reach for Write/Edit only as a
// fallback. The house style routes the whole fleet down the one path the gate
// could not see. So the log we had was never "attempts" -- it was "attempts
// that happened to use Write/Edit".
//
// FIX, deliberately blunt: do not try to work out what a program does. If an
// interpreter takes its program from INLINE CODE, a HEREDOC or STDIN, and the
// raw command mentions the protected folder anywhere at all -- quotes and
// heredoc bodies included -- treat it as a write and make it ask.
//
// THE COST -- and the first way I wrote this down was too narrow, so read the
// second sentence, not the first. It is NOT only "a one-liner that reads a
// SKILL.md gets refused too". The rule does not look at what the program DOES:
// it looks at whether the COMMAND TEXT mentions the protected path in ANY role.
// An inline script that merely has the path as a search string, a log message,
// a comment or a quoted example is refused as well, even when it never touches
// the folder.
//
// Forgacs hit exactly that within the hour (SEARCHSTRING901): a heredoc that
// read an inter-agent message back over HTTP and looked for a substring -- and
// the substring happened to be this path, because the message was ABOUT this
// gate. Nothing in that command went near the folder. It is a recursive little
// trap: the commands most likely to mention the path are the ones written while
// discussing or verifying the gate itself.
//
// The workaround is cheap (use `cat`/`grep`/the Read tool, or pick another
// search string) and the alternative -- parsing program text to tell a read
// from a write -- is what produced every false positive this gate has had. So
// the decision stands. But anyone who reads only the narrow version will blame
// the gate for a block that is working as designed, which is why this paragraph
// is longer than the rule it explains.
//
// NOT covered, and consistent with the existing stated gap: `python3 script.py`
// or `bash script.sh`, where the program is a file in the repo. That is reviewed
// code, and treating it as suspect would flag every helper the fleet runs.
const INTERPRETERS = new Set([
  'python', 'python3', 'node', 'nodejs', 'perl', 'ruby', 'sh', 'bash', 'zsh', 'awk', 'gawk', 'ed', 'ex',
])
const INLINE_PROGRAM_FLAGS = new Set(['-c', '-e', '-i', '--inplace', '-pe', '-ne', '-i.bak'])

/**
 * Does this segment hand a program to an interpreter through inline code, a
 * heredoc, or stdin? Those are the forms whose body this gate cannot read.
 */
function isInlineInterpreter(structuralTokens) {
  // WRAPFLAG901: shared wrapper table. This branch used to know only sudo /
  // command / timeout, so `env python3 -c "<write to the shared folder>"` was
  // not even read as an interpreter call.
  const vi = verbIndex(structuralTokens)
  const verb = (structuralTokens[vi] || '').split('/').pop()
  if (!INTERPRETERS.has(verb)) return false
  const args = structuralTokens.slice(vi + 1)
  // An explicit inline-program flag (`-c`, `-e`) is unambiguous.
  if (args.some((a) => INLINE_PROGRAM_FLAGS.has(a))) return true
  // STDINPROG901, 2nd occurrence -- this gate, the same evening (Cody isolated
  // it): a bare `-` or a heredoc marker was read as "the interpreter's program
  // comes from stdin". In `bash scripts/agent-msg.sh <to> - <<EOF` the `-` is
  // the SCRIPT'S argument and the body is the SCRIPT'S input; the interpreter
  // runs a reviewed repo file. Treating it as an inline program widened the
  // scope to the whole command (see the `<<` branch below), so a data heredoc
  // that merely MENTIONED the protected folder blocked the fleet's own
  // messaging idiom. Two harmless halves passed alone and were refused together.
  // The bash-egress-gate already drew this line; the shared helper is what
  // keeps the two from drifting apart a third time.
  if (!readsProgramFromStdin(args)) return false
  return args.some((a) => a === '-' || a.includes('<<'))
}

/** Does a Bash command look like a WRITE aimed at the protected folder? */
export function bashTouchesGlobalSkills(command) {
  const cmd = String(command ?? '')
  if (!cmd) return false
  if (!PATH_PATTERNS.some((pat) => cmd.includes(pat))) return false

  // The interpreter check runs on the RAW segments -- before value-flag values
  // are dropped. `node -e "<program>"` would otherwise lose its program to the
  // `-e` entry in that list (curl's referer flag), and the check would see a
  // bare `node`. A detection that depends on an unrelated flag table is a
  // detection waiting to break.
  for (const rawSeg of splitSegments(tokenizeDetailed(cmd))) {
    if (!isInlineInterpreter(rawSeg.map((t) => t.text))) continue
    // GLOBALSKILL901-FP, 3rd occurrence (Forgacs, 2026-09-01): this rule used to
    // fire on the WHOLE command, so an inline `python3 -c` in ONE segment blocked
    // a plain `grep <protected path>` sitting in a completely different segment.
    // Same shape as the first two false positives: reading the command as a bag
    // of words instead of as segments. The write-verb branch below was moved to
    // per-segment for exactly this reason; this branch had quietly kept the old
    // behaviour. The deliberate over-reach STAYS -- inside the interpreter's own
    // segment every token counts, quoted ones included, because the gate cannot
    // read an inline program -- it just no longer leaks into its neighbours.
    // A heredoc body is stripped before tokenising, so it belongs to no segment
    // any more -- and that body is exactly where INTERPWRITE901 hid the path.
    // When the command has one, this branch therefore keeps the OLD, deliberately
    // over-wide whole-command match; it is the only reading that still sees the
    // body. Without a heredoc the segment's own tokens are the honest scope.
    const scope = cmd.includes('<<') ? cmd : rawSeg.map((t) => t.text).join(' ')
    if (PATH_PATTERNS.some((pat) => scope.includes(pat))) return true
  }

  for (const seg of readCommand(cmd)) {
    // A QUOTED token is data, never a destination: it is the argument text, not
    // a path the shell will act on. This is what stops a JSON body that quotes
    // the folder from reading as a write to it.
    const structural = seg.filter((t) => !t.quoted).map((t) => t.text)

    // QUOTEDDEST901: the early exit used to run on the UNQUOTED view, so a command
    // whose only mention of the folder was quoted never reached the checks below.
    if (!seg.some((t) => mentionsProtected(t.text))) continue

    // A redirect writes to the token that follows it. Only an UNQUOTED token
    // that is a bare `>`/`>>`, or ends with one, is a redirect operator.
    // QUOTEDDEST901: the OPERATOR must be unquoted (a `>` inside quotes is text),
    // but its TARGET may be quoted -- `echo x > '<protected>/x'` is a real write.
    const opTexts = seg.map((t) => (t.quoted ? '' : t.text))
    const allForRedirect = seg.map((t) => t.text)
    for (let i = 0; i < opTexts.length; i++) {
      const tok = opTexts[i]
      if (tok === '>' || tok === '>>') {
        if (mentionsProtected(allForRedirect[i + 1] ?? '')) return true
        continue
      }
      const m = tok.match(/^>>?(.+)$/)
      if (m && mentionsProtected(m[1])) return true
    }

    // The verb is the first token that is not an env assignment or a wrapper.
    // QUOTEDDEST901 (Cody, 2026-09-01, measured): the verb comes from the
    // UNQUOTED view, but a DESTINATION may perfectly well be quoted -- a path
    // with a space, or just habit. `cp /tmp/x '<protected>/x/SKILL.md'` was not
    // seen at all. Cody also MEASURED that the quoted-token filter is not what
    // stops the JSON-body false positive it was added for: dropFlagValues does
    // that, by dropping `-d`/`-H` values. So destinations are read from ALL
    // tokens, and the JSON-body case stays allowed (there is a test for it).
    const vi = verbIndex(structural)
    const verb = (structural[vi] || '').split('/').pop()
    const allTexts = seg.map((t) => t.text)
    const allVi = verbIndex(allTexts)
    // REDIRHIDE901 (Cody, same round): `cp a <protected> > /tmp/log` slipped by
    // because only the BARE `>` token was dropped -- the log path stayed in the
    // positional list and became the "destination". Drop the operator AND what
    // it writes to, in both the spaced and the glued spelling.
    const args = []
    const raw = allTexts.slice(allVi + 1)
    for (let i = 0; i < raw.length; i++) {
      const t = raw[i]
      if (t === '>' || t === '>>' || t === '<' || t === '<<') { i++; continue }
      if (/^>>?[^>]/.test(t) || /^\d*>&?/.test(t)) continue
      args.push(t)
    }

    if (DESTRUCTIVE_VERBS.has(verb)) {
      if (args.some(mentionsProtected)) return true
      continue
    }
    if (COPY_VERBS.has(verb)) {
      const positional = args.filter((a) => !a.startsWith('-'))
      const dest = positional[positional.length - 1] || ''
      if (mentionsProtected(dest)) return true
      continue
    }
    if (verb === 'sed' && args.some((a) => /^-[a-z]*i$/.test(a) || a === '--in-place')) {
      if (args.some(mentionsProtected)) return true
      continue
    }
  }
  return false
}

/**
 * Resolve symlinks as far as the path actually exists, then re-attach the rest.
 * realpathSync alone throws on a file that is not created yet -- and the file a
 * Write is about to create is exactly that case.
 */
function realpathish(p) {
  let cur = resolve(p)
  const tail = []
  for (;;) {
    try {
      return tail.length ? join(realpathSync(cur), ...tail.slice().reverse()) : realpathSync(cur)
    } catch {
      const parent = dirname(cur)
      if (parent === cur) return resolve(p)
      tail.push(basename(cur))
      cur = parent
    }
  }
}

/**
 * Is `p` inside the protected folder? Path-segment aware, so a sibling
 * directory named `skills-old` does not match.
 *
 * SYMLINKMISS901 (Roxy, 2026-09-01): a REAL miss, not a false positive. This
 * used resolve(), which normalises `..` but does NOT follow symlinks, so a
 * Write to <workdir>/.claude-config/skills/x/SKILL.md sailed straight through
 * into the shared folder. The Bash branch caught the same path only because it
 * matches TEXT. The header of this file names that symlink as the trap it
 * exists to close, so the intent was there and the code did not carry it out.
 */
export function isGlobalSkillPath(p) {
  if (!p) return false
  try {
    const expanded = String(p).replace(/^~(?=\/|$)/, homedir())
    // Compare on real paths at BOTH ends: the protected folder may itself be a
    // symlink on some installs.
    const target = realpathish(expanded)
    const guarded = realpathish(GLOBAL_SKILL_DIR)
    const hit = (abs, dir) => abs === dir || abs.startsWith(dir + '/')
    return hit(target, guarded) || hit(resolve(expanded), GLOBAL_SKILL_DIR)
  } catch {
    return false
  }
}

/**
 * An approval turned into a fact on disk.
 * Shape: { "until": <unix seconds>, "reason": "...", "approval_id": "..." }
 * Missing, malformed or expired -> no grant. Never throws: a broken grant file
 * must read as "no permission", never as an error that lets the write through.
 */
export function readGrant(now = Math.floor(Date.now() / 1000), path = GRANT_PATH) {
  try {
    const g = JSON.parse(readFileSync(path, 'utf-8'))
    const until = Number(g?.until)
    if (!Number.isFinite(until) || until <= now) return null
    return { until, reason: String(g?.reason ?? ''), approvalId: String(g?.approval_id ?? '') }
  } catch {
    return null
  }
}

/**
 * STANDING grants: an owner decision that does not expire, narrowed to ONE agent
 * and a NAMED set of skills.
 *
 * STANDING901, approved 2026-09-01: Forgacs patches the four workshop skills far
 * more than anyone, and an approval round per patch is real friction. The owner
 * granted him standing authority for exactly those four. The grant is narrow on
 * purpose: it is not "Forgacs may write global skills", it is "Forgacs may write
 * these four".
 *
 * HONEST LIMIT, and it must not be dressed up: the agent identity comes from the
 * process's working directory. That stops the accident this gate exists to stop
 * -- an agent editing the shared folder while believing it edits its own -- but
 * it is NOT proof of identity, and an agent that changed directory would read as
 * another. This is a brake, not an authentication system, and the fleet already
 * shares one dashboard token anyway (the known residual risk in the review).
 */
export function readStandingGrants(path = STANDING_GRANTS_PATH) {
  try {
    const d = JSON.parse(readFileSync(path, 'utf-8'))
    const list = Array.isArray(d?.grants) ? d.grants : []
    return list
      .filter((g) => g && typeof g.agent === 'string' && Array.isArray(g.skills))
      .map((g) => ({
        agent: g.agent,
        skills: g.skills.filter((s) => typeof s === 'string'),
        reason: String(g.reason ?? ''),
        approvedBy: String(g.approved_by ?? ''),
      }))
  } catch {
    return []   // missing or malformed reads as NO permission, never as an error
  }
}

/** Which agent is calling, inferred from the working directory. See the note above. */
export function agentFromCwd(cwd = process.cwd()) {
  const parts = String(cwd).split('/')
  const i = parts.lastIndexOf('agents')
  if (i >= 0 && parts[i + 1]) return parts[i + 1]
  return 'marveen'
}

/** The skill a path belongs to: the first segment under the shared folder. */
export function skillOfPath(p) {
  if (!p) return null
  const abs = realpathish(String(p).replace(/^~(?=\/|$)/, homedir()))
  const guarded = realpathish(GLOBAL_SKILL_DIR)
  for (const dir of [guarded, GLOBAL_SKILL_DIR]) {
    if (abs === dir) return null
    if (abs.startsWith(dir + '/')) return abs.slice(dir.length + 1).split('/')[0] || null
  }
  return null
}

/** Every skill name a Bash command names under the shared folder. */
export function skillsInCommand(command) {
  const out = new Set()
  for (const tok of String(command ?? '').split(/\s+/)) {
    for (const pat of PATH_PATTERNS) {
      const at = tok.indexOf(pat)
      if (at === -1) continue
      const rest = tok.slice(at + pat.length).replace(/^\//, '')
      const first = rest.split('/')[0]
      if (first) out.add(first)
    }
  }
  return [...out]
}

function standingAllows(grants, agent, skills) {
  if (skills.length === 0) return null           // cannot name the skill -> no standing grant
  const mine = grants.filter((g) => g.agent === agent)
  if (mine.length === 0) return null
  const covered = skills.every((s) => mine.some((g) => g.skills.includes(s)))
  return covered ? { agent, skills, reason: mine[0].reason, approvedBy: mine[0].approvedBy } : null
}

/** Pure decision, no I/O beyond the objects handed to it. */
export function skillWriteDecision(toolName, toolInput, grant = null, standing = [], agent = 'marveen') {
  const name = String(toolName ?? '')
  let hit = false
  let skills = []
  if (name === 'Write' || name === 'Edit' || name === 'MultiEdit' || name === 'NotebookEdit') {
    const p = toolInput?.file_path ?? toolInput?.notebook_path
    hit = isGlobalSkillPath(p)
    if (hit) {
      const s = skillOfPath(p)
      skills = s ? [s] : []
    }
  } else if (name === 'Bash') {
    hit = bashTouchesGlobalSkills(toolInput?.command)
    if (hit) skills = skillsInCommand(toolInput?.command)
  }
  if (!hit) return { blocked: false, tier: 'not-global-skill' }

  const standingHit = standingAllows(standing, agent, skills)
  if (standingHit) return { blocked: false, tier: 'standing', standing: standingHit, skills }

  if (grant) return { blocked: false, tier: 'granted', grant, skills }
  return { blocked: true, tier: 'no-grant', skills }
}

const BLOCK_MESSAGE =
  'TILTOTT iras a KOZOS skill-mappaba (global-skill-write-gate hook). ' +
  'A ~/.claude/skills a TELJES flottara hat: ami ide kerul, az MINDEN agensnek betoltodik. ' +
  'Ezert ide irni tudatos, FLOTTA-SZINTU dontes legyen, ne alapertelmezes -- a kapu nem a ' +
  'szandekot nezi, hanem egy nevesitett engedelyt. Ez FUGGETLEN az autonomia-szintektol: ez a ' +
  'hook nem olvassa a store/autonomy-config.json-t, mert egy szint egy mondat a promptban, a ' +
  'kozos mappa viszont mindenkire hat (GLOBALSKILL901). Egy magasabb szint NEM nyitja meg ezt ' +
  'az utat. Ugyanez all a <workdir>/.claude-config/skills utra is, mert az SYMLINK ugyanide.\n' +
  'HA A SAJAT SKILLED: tedd a munkakonyvtarad .claude/skills/ mappajaba, az szabad.\n' +
  'HA A PARANCSOD NEM IS NYUL A MAPPAHOZ, csak EMLITI: ez akkor is elakad, es ez szandekos. ' +
  'Egy inline script (heredoc, python3 -c, node -e) torzset a kapu nem tudja elolvasni, ezert ' +
  'ha UGYANAZ a szegmens hasznal inline interpretert ES emliti a vedett utat -- kereso-' +
  'stringkent, kommentkent, idezett peldakent --, jovahagyast ker. Egy MASIK szegmens ' +
  'inline scriptje nem szamit bele -- KIVEVE ha egy INTERPRETER (python/node/perl/ruby, ' +
  'illetve sh/bash) heredocbol vagy stdinrol kapja a programjat: annak a torzse mar egyik ' +
  'szegmenshez sem rendelheto, ezert ott a kapu az egesz parancsot nezi. Egy sima ' +
  '`cat > fajl <<EOF` heredoc NEM ilyen: az adat, nem program, es szabadon emlitheti az utat. ' +
  'Megoldas: olvasashoz cat/grep vagy a Read tool, ' +
  'kereseshez valassz mas needle-t, egyebkent kerj jovahagyast.\n' +
  'HA TENYLEG A FLOTTANAK SZOL, KET UT VAN, ES EZ A HOOK MINDKETTOT OLVASSA:\n' +
  'A) ESETI, LEJARATOS -- store/skill-write-grant.json (EGYES szam). (1) kerj jovahagyast a ' +
  '/api/approvals vegponton (category: skill_patch), (2) ha approved lett, ird ki a fajlt ' +
  '{"until": <unix ido>, "reason": "...", "approval_id": "..."} tartalommal, (3) vegezd el a ' +
  'modositast, (4) hagyd lejarni.\n' +
  'B) ALLANDO, AGENS- ES SKILL-NEVRE SZUKITVE -- store/skill-write-grants.json (TOBBES szam). ' +
  'Nem jar le, mert ez dontes es nem pillanat; ezert kizarolag a gazda hozhatja letre. Ha a ' +
  'neved ES a szoban forgo skill neve szerepel benne, ez a hook at is enged, es a naplosorod ' +
  'ALLOWED_STANDING lesz.\n' +
  'A KET FAJLNEV EGY BETUBEN TER EL. Mielott azt allitanad, hogy nincs engedelyed, nezd meg ' +
  'MINDKETTOT: ls store/skill-write-grant*.json -- egy letezo, olvashato, de rossz fajl ' +
  'ugyanugy nez ki, mint egy sikeres meres.\n' +
  'Minden ilyen iras naplosort kap a store/global-skill-writes.log fajlban.'

const SELFTEST = process.env.SKILL_GATE_SELFTEST === '1'

// LOGSCOPE901 (Cody, 2026-09-01): this log's coverage was over-claimed, and the
// honest fix is not a bigger gate but a stated limit at the top of the file the
// reader actually opens. A write performed by a script FILE never reaches this
// hook at all -- the gate reads the COMMAND, not the disk -- so a missing
// BLOCKED line is not evidence that no write happened.
//
// UPDATED 2026-09-04 (card 7e6d6dc0), because the sentence above outlived the
// thing it described and a stale limitation note is read as a current one. A
// SECOND writer now appends here: global-skill-write-audit.mjs, a PostToolUse
// hook that compares the protected tree against a snapshot and logs every
// difference whatever route produced it. Measured live the same day, on the
// exact route this comment is about: `python3 <file>.py` writing into the tree
// produced TREE_CHANGE_UNGRANTED at 13:07:55Z, and its removal another line
// eight seconds later. So a missing BLOCKED line still proves nothing, but a
// missing TREE_CHANGE line now means the tree matched the snapshot.
// store/global-skill-writes.log carries the full version of this in its header,
// including what neither half sees; keep the two in step.
function logLine(kind, detail) {
  try {
    mkdirSync(join(REPO_ROOT, 'store'), { recursive: true })
    appendFileSync(AUDIT_LOG, `${new Date().toISOString()} ${SELFTEST ? 'SELFTEST_' : ''}${kind} ${detail}\n`, 'utf-8')
  } catch {
    // A failed log must never turn into a failed agent.
  }
}

function allow() { process.exit(0) }

function deny(reason) {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  }))
  process.exit(0)
}

function isInvokedDirectly() {
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === (process.argv[1] ? realpathSync(process.argv[1]) : '')
  } catch {
    return false
  }
}

if (isInvokedDirectly()) {
  let payload
  try {
    payload = JSON.parse(readFileSync(0, 'utf-8'))
  } catch {
    allow()  // malformed or empty input must never block the agent
  }
  const agent = agentFromCwd()
  const decision = skillWriteDecision(
    payload?.tool_name, payload?.tool_input, readGrant(), readStandingGrants(), agent,
  )
  // One event, one line, with anything credential-shaped removed. See
  // lib/redact.mjs (SECRETLOG901): this log records whole commands so a
  // reviewer can see what was attempted, and a typed-in literal must not ride
  // along into a file people read casually.
  const target = logSafe(payload?.tool_input?.file_path ?? payload?.tool_input?.command ?? '')
  const agentType = String(payload?.agent_type ?? '')
  if (decision.blocked) {
    logLine('BLOCKED', `tool="${payload?.tool_name}" agent_type="${agentType}" target="${target}"`)
    deny(BLOCK_MESSAGE)
  }
  if (decision.tier === 'standing') {
    // A standing grant is still a write to the shared folder: logged like any
    // other, so "who changed this and under what authority" stays answerable.
    logLine('ALLOWED_STANDING',
      `tool="${payload?.tool_name}" agent="${agent}" skills="${(decision.skills || []).join(',')}" ` +
      `approved_by="${decision.standing.approvedBy}" target="${target}"`)
  }
  if (decision.tier === 'granted') {
    // A grant that leaves no trace is indistinguishable from no gate at all.
    logLine('ALLOWED_BY_GRANT',
      `tool="${payload?.tool_name}" agent_type="${agentType}" approval_id="${decision.grant.approvalId}" ` +
      `until=${decision.grant.until} target="${target}"`)
  }
  allow()
}
