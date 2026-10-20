#!/usr/bin/env node
// PostToolUse hook: the EFFECT-side half of the shared skill folder's guard.
//
// WHY A SECOND HOOK AND NOT A STRICTER FIRST ONE
// global-skill-write-gate.mjs reads the COMMAND. That is the right thing for a
// PreToolUse gate -- it can refuse before anything happens -- but it means the
// gate judges what a command SAYS, and a program handed to an interpreter from
// a FILE says nothing at all. Measured 2026-09-03 (card 7e6d6dc0), twice, on
// the same target file by the same agent:
//   `python3 /tmp/.../patch2.py`   -> the shared skill changed, and the audit
//                                     log has NO line about it. Not ALLOWED,
//                                     not BLOCKED: the gate never saw it.
//   `python3 - <<'PY' ... PY`      -> BLOCKED and logged, same intent.
// The only difference was where the program text lived.
//
// The tempting repair is to teach the gate to open script files and scan them.
// That is the same mistake one level deeper: it still judges TEXT, so it keeps
// producing false positives (a script that merely MENTIONS the path) while a
// script that builds the path at runtime still walks through. The project's own
// kapu-javitas-tullo-nelkul skill states the rule this file follows: do not
// tighten the pattern, change the YARDSTICK. The Write/Edit branch of the
// PreToolUse gate is correct precisely because the destination is visible in
// the event itself -- it judges the effect.
//
// So this hook asks the only question that cannot be talked around: DID THE
// PROTECTED TREE CHANGE? It runs after the tool, compares the folder against a
// stored snapshot, and writes a line for every difference it finds, whatever
// route produced it.
//
// WHAT IT DOES NOT DO, said plainly: it cannot prevent the write -- by the time
// it runs, the file is already changed. It converts a SILENT change into a
// LOGGED one, and an ungranted change into an alert the owner sees. That is a
// smaller promise than the PreToolUse gate makes, and it is the honest one for
// a hook that arrives after the fact. The two together mean: the visible route
// is refused, and the invisible route is no longer invisible.
//
// COST: the tree is 40 files / 556 kB (measured 2026-09-04). A full read with
// sha256 is milliseconds. If the folder ever grows to where this is felt, the
// fix is to compare size+mtime first and hash only what differs -- not to run
// less often, because a check that skips is a check that misses.

import { readFileSync, writeFileSync, appendFileSync, mkdirSync, readdirSync, realpathSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'
import { homedir } from 'node:os'
import { execFileSync } from 'node:child_process'
// The authority files are read through the PreToolUse gate's OWN readers, not
// through a second copy here. Measured 2026-09-04, within a minute of writing
// this file: a private reader crashed on `skill-write-grants.json` because the
// grants live under a `grants` key and the field is `approved_by`, not
// `approvedBy`. Two readers for one file is how two halves of a gate start
// disagreeing about who is allowed what -- the project's own gate-repair skill
// says to unify them rather than keep them in step by hand.
import { readGrant, readStandingGrants, REPO_SKILL_DIR } from './global-skill-write-gate.mjs'
import { localStamp } from './lib/local-time.mjs'

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const AUDIT_LOG = join(REPO_ROOT, 'store', 'global-skill-writes.log')
const SNAPSHOT = join(REPO_ROOT, 'store', '.global-skill-tree.json')
// #261: the repo-root skill folder gets its own snapshot, so neither tree's
// baseline can be mistaken for the other's.
const REPO_SNAPSHOT = join(REPO_ROOT, 'store', '.repo-skill-tree.json')
const TOKEN_PATH = join(REPO_ROOT, 'store', '.dashboard-token')

export const GLOBAL_SKILL_DIR = join(homedir(), '.claude', 'skills')

/**
 * Every file under the protected folder: relative path -> content fingerprint.
 *
 * `rootOk` is separate from an empty result ON PURPOSE. Forgacs raised this the
 * morning the hook shipped, and he is right: the snapshot comparison is ITSELF a
 * measurement, and it can fail consistently in the one direction nobody checks.
 * If the ROOT is ever unreadable -- wrong path, permissions, the folder briefly
 * gone -- the walk returns {}. On its own that is survivable. But if it happens
 * on a run that WRITES the baseline, every later comparison is {} against {},
 * which reads as "nothing changed" forever. A blind instrument and a quiet
 * folder look exactly alike, and the blind one never says so.
 *
 * So the caller gets to know whether the root was readable, and refuses to
 * store a baseline it does not trust.
 */
export function readTree(root = GLOBAL_SKILL_DIR) {
  const out = {}
  let rootOk = true
  const walk = (dir, isRoot = false) => {
    let entries
    try {
      entries = readdirSync(dir, { withFileTypes: true })
    } catch {
      // A sub-directory that cannot be read is not a change; the ROOT failing is
      // a different animal and has to travel back up.
      if (isRoot) rootOk = false
      return
    }
    for (const e of entries) {
      const full = join(dir, e.name)
      if (e.isDirectory()) { walk(full); continue }
      if (!e.isFile()) continue  // symlinks out of the tree are not this gate's business
      try {
        const buf = readFileSync(full)
        out[relative(root, full)] = {
          size: buf.length,
          sha: createHash('sha256').update(buf).digest('hex').slice(0, 16),
        }
      } catch {
        // Skip what cannot be read rather than reporting a phantom deletion.
      }
    }
  }
  walk(root, true)
  return { files: out, rootOk }
}

/**
 * Is this actually a tree reading, or something that merely sits where one goes?
 *
 * Added the same hour the hook shipped, after it produced TWO false alarms
 * against itself. Mid-edit, readTree()'s return shape changed from a bare map to
 * `{files, rootOk}` while the CALL SITE still stored whatever came back. The
 * next tool call ran the half-edited file, saved `{files: {...}, rootOk: true}`
 * AS the snapshot, and reported 40 deletions plus two new "skills" called
 * `files` and `rootOk`. The run after that saw the corrupt snapshot and reported
 * all 40 back as additions. The tree was untouched throughout.
 *
 * Forgacs had raised the general form of this an hour earlier -- the comparison
 * is itself a measurement, and can fail consistently in the direction nobody
 * checks. His example was an unreadable root; this is the same thing through a
 * different door, and the door I walked through was my own edit. A hook that
 * watches a folder runs DURING its own development, on whatever half-written
 * state is on disk at that second.
 *
 * So both ends are checked: nothing is stored that is not tree-shaped, and
 * nothing is compared against a stored value that is not either.
 */
export function isTreeShaped(x) {
  if (!x || typeof x !== 'object' || Array.isArray(x)) return false
  return Object.values(x).every((v) => v && typeof v === 'object' && typeof v.sha === 'string')
}

/**
 * What changed between two tree readings.
 * Pure: the whole decision this hook makes is testable without touching disk.
 */
export function diffTrees(before, after) {
  const added = [], modified = [], deleted = []
  for (const p of Object.keys(after)) {
    if (!(p in before)) added.push(p)
    else if (before[p].sha !== after[p].sha) modified.push(p)
  }
  for (const p of Object.keys(before)) {
    if (!(p in after)) deleted.push(p)
  }
  return { added: added.sort(), modified: modified.sort(), deleted: deleted.sort() }
}

export function hasChange(d) {
  return d.added.length > 0 || d.modified.length > 0 || d.deleted.length > 0
}

/** The skill a path belongs to: the first path segment under the folder. */
export function skillOfRelPath(rel) {
  const seg = String(rel).split('/')[0]
  return seg && seg !== '..' ? seg : null
}

export function skillsInDiff(d) {
  const s = new Set()
  for (const p of [...d.added, ...d.modified, ...d.deleted]) {
    const k = skillOfRelPath(p)
    if (k) s.add(k)
  }
  return [...s].sort()
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf-8'))
  } catch {
    return null
  }
}

/**
 * Is this change covered by SOME authority on disk?
 *
 * NOT narrowed to the observing agent, and that is a deliberate weakening --
 * measured within twenty minutes of this hook going live. Forgacs patched
 * butor-tervezes, a skill his standing grant explicitly names. My session's
 * next Bash call ran the audit, saw the difference, and asked "does MARVEEN
 * have a grant for butor-tervezes?" -- no -- and fired an ungranted alert at a
 * completely legitimate edit.
 *
 * The mistake was asking the wrong question. A PostToolUse tree diff CANNOT
 * know who wrote the file: the tool call it runs after is merely the next one
 * anywhere in the fleet, so the agent name here identifies the OBSERVER, never
 * the author. Checking a grant against the observer therefore produces a false
 * alert every time one agent edits under its own authority while another agent
 * is working -- and an alert that cries wolf gets muted, which puts the silent
 * change right back where it started.
 *
 * So: any standing grant that names the affected skills counts. The honest
 * price, stated rather than hidden: if agent A writes to a skill that only
 * agent B is authorised for, this half will not catch it. The PreToolUse gate
 * still will, on the visible route -- and this hook's job was never to identify
 * the author, only to make sure no change to the shared tree passes unnoticed.
 */
export function grantCovers(skills, grant, standing, _observedBy) {
  // `grant` arrives already expiry-checked by readGrant (it returns null for an
  // expired one), so this function never re-implements the clock.
  if (grant) return { covered: true, how: 'grant', id: grant.approvalId ?? '' }
  const all = (Array.isArray(standing) ? standing : []).filter(Boolean)
  const list = Array.isArray(skills) ? skills : []
  if (list.length > 0 && all.length > 0 && list.every((s) => all.some((g) => (g.skills || []).includes(s)))) {
    const holder = all.find((g) => (g.skills || []).some((s) => list.includes(s)))
    return { covered: true, how: 'standing', id: holder?.agent ?? '' }
  }
  return { covered: false, how: '', id: '' }
}

export function agentFromCwd(cwd = process.cwd()) {
  const parts = String(cwd).split('/')
  const i = parts.lastIndexOf('agents')
  return i >= 0 && parts[i + 1] ? parts[i + 1] : 'marveen'
}

/**
 * #261, owner tg 8276: in the REPO folder the main agent may fix an EXISTING
 * skill without a grant. So a change there is covered when every touched skill
 * was already in the baseline, nothing was removed wholesale, and the observer
 * is the main agent.
 *
 * Why the observer counts HERE when grantCovers says it must not: the main
 * agent's own writes -- Write/Edit or a script it ran -- are followed at once
 * by ITS OWN PostToolUse run, so in practice it observes its own change first.
 * A sub-agent observing a change to an existing repo skill means either the
 * sub-agent wrote it (exactly what this folder is protected against) or it
 * caught the main agent's write in the few milliseconds before the main
 * agent's hook ran. The second is a rare false alarm, to the main agent itself;
 * the first is the case this exists for. Cheap noise for a real signal.
 */
export function repoChangeCovered(d, beforeFiles, observedBy) {
  if (observedBy !== 'marveen') return false
  const existing = new Set(Object.keys(beforeFiles || {}).map(skillOfRelPath).filter(Boolean))
  const afterSkills = new Set()
  for (const p of [...d.added, ...d.modified]) afterSkills.add(skillOfRelPath(p))
  for (const k of skillsInDiff(d)) if (!existing.has(k)) return false
  // A skill whose every file disappeared is a deletion of the skill, not a fix.
  for (const p of d.deleted) {
    const k = skillOfRelPath(p)
    const stillHas = Object.keys(beforeFiles).some((q) => skillOfRelPath(q) === k && !d.deleted.includes(q))
    if (!stillHas && !afterSkills.has(k)) return false
  }
  return true
}

function logLine(kind, detail) {
  try {
    mkdirSync(join(REPO_ROOT, 'store'), { recursive: true })
    appendFileSync(AUDIT_LOG, `${localStamp()} ${kind} ${detail}\n`, 'utf-8')
  } catch {
    // A failed log must never turn into a failed agent.
  }
}

function saveSnapshot(tree, path = SNAPSHOT) {
  try {
    mkdirSync(join(REPO_ROOT, 'store'), { recursive: true })
    writeFileSync(path, JSON.stringify({ at: Date.now(), files: tree }), 'utf-8')
  } catch {
    // If the snapshot cannot be written the next run re-baselines. Noisy once,
    // never wrong.
  }
}

/**
 * Tell the main agent, so an ungranted change reaches a human.
 *
 * Short timeout and every failure swallowed: this runs inside a tool call, and
 * an alert that stalls the agent would be its own kind of damage. The log line
 * is written FIRST and does not depend on this succeeding.
 */
function alertMainAgent(text, from = 'marveen') {
  let token
  try {
    token = readFileSync(TOKEN_PATH, 'utf-8').trim()
  } catch {
    return false
  }
  if (!token) return false
  try {
    // `from` must be a REGISTERED fleet agent id -- the API refuses anything
    // else with `unknown agent '...'`, and curl still exits 0, so the first
    // version of this alert failed silently while the log line looked fine.
    // Measured 2026-09-04 on the first end-to-end probe: a made-up sender
    // ('skill-audit') was rejected, the real agent id went through. So the
    // alert is sent AS the agent whose tool call moved the tree.
    const body = JSON.stringify({ from, to: 'marveen', content: text })
    const out = execFileSync('curl', [
      '-s', '-m', '4', '-X', 'POST', 'http://localhost:3420/api/messages',
      '-H', 'Content-Type: application/json',
      '-H', `Authorization: Bearer ${token}`,
      '--data-binary', '@-',
    ], { input: body, encoding: 'utf-8', timeout: 6000 })
    // curl exits 0 even when the server refuses, so read the answer for an id.
    // The project's own inter-agent rule, applied to a hook.
    return Boolean(JSON.parse(out || '{}').id)
  } catch {
    return false
  }
}

function isInvokedDirectly() {
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === (process.argv[1] ? realpathSync(process.argv[1]) : '')
  } catch {
    return false
  }
}

/**
 * The whole run, wrapped so that NOTHING here can break a tool call.
 *
 * A PostToolUse hook that throws prints a stack trace into the agent's session
 * and makes an otherwise successful command look failed. That happened on this
 * file's first end-to-end probe (a private grants reader crashed on the real
 * file's shape), and it is exactly the failure mode an audit must not have: the
 * audit exists to be quietly present, not to become a second thing to debug.
 * An audit that cannot run must fail silent, because the alternative is that
 * someone switches it off -- and then the silent change is silent again.
 */
const TREES = [
  { name: 'global', root: GLOBAL_SKILL_DIR, snapshot: SNAPSHOT, tag: '', label: 'KOZOS SKILL-MAPPA' },
  { name: 'repo', root: REPO_SKILL_DIR, snapshot: REPO_SNAPSHOT, tag: 'tree="repo" ', label: 'REPO SKILL-MAPPA (.claude/skills)' },
]

function run() {
  let payload = null
  try {
    payload = JSON.parse(readFileSync(0, 'utf-8'))
  } catch {
    payload = null  // a malformed payload still deserves a tree check
  }
  for (const cfg of TREES) {
    // One tree failing must not hide the other.
    try {
      runTree(cfg, payload)
    } catch (e) {
      logLine('AUDIT_ERROR', `${cfg.tag}msg="${String(e && e.message ? e.message : e).slice(0, 200).replace(/"/g, "'")}"`)
    }
  }
}

function runTree(cfg, payload) {
  const { files: after, rootOk } = readTree(cfg.root)
  const snap = readJson(cfg.snapshot)

  if (!rootOk) {
    // Say it, and change NOTHING. Writing a baseline from an unreadable root is
    // how a blind check becomes a permanent one: {} stored now means every later
    // run compares {} to {} and reports calm. Leaving the old snapshot alone
    // means the next healthy run picks the real difference up.
    logLine('AUDIT_BLIND', `${cfg.tag}reason="protected root unreadable" path="${cfg.root}"`)
    return
  }

  if (!isTreeShaped(after)) {
    // Refuse to store a reading we do not recognise. See isTreeShaped: this is
    // the guard that stops one bad reading from becoming a bad baseline.
    logLine('AUDIT_SUSPECT', `${cfg.tag}reason="readTree returned a non-tree shape"`)
    return
  }

  if (!snap || !snap.files || !isTreeShaped(snap.files)) {
    // No baseline yet -- or one that is not tree-shaped, which is the same
    // situation with a worse disguise. Re-baseline quietly: a corrupt snapshot
    // is not evidence that anyone touched the folder, and reporting 40
    // "deletions" from it is noise that buries the real signal.
    const why = snap && snap.files && !isTreeShaped(snap.files) ? 'corrupt-snapshot' : 'first-run'
    saveSnapshot(after, cfg.snapshot)
    logLine('TREE_BASELINE', `${cfg.tag}files=${Object.keys(after).length} why="${why}"`)
    return
  }

  // An empty tree where the snapshot had files is a WIPE if true and a broken
  // measurement if not -- and it is far more often the second. Report it as its
  // own thing rather than as N deletions, and do not overwrite the baseline, so
  // the state survives for whoever looks.
  if (Object.keys(after).length === 0 && Object.keys(snap.files).length > 0) {
    logLine('AUDIT_SUSPECT', `${cfg.tag}reason="tree read as empty but snapshot had files" had=${Object.keys(snap.files).length}`)
    alertMainAgent(
      `[${cfg.label}] A vedett fa URESNEK olvasodott, pedig a pillanatkep ` +
      `${Object.keys(snap.files).length} fajlt tartalmazott. Ez VAGY egy valodi torles, VAGY -- sokkal ` +
      `valoszinubben -- a meres bukott el. A pillanatkepet NEM irtam felul, tehat a kovetkezo ` +
      `ep futas meg latni fogja a kulonbseget. Nezd meg, letezik-e es olvashato-e a mappa.`,
      agentFromCwd(),
    )
    return
  }

  const d = diffTrees(snap.files, after)
  if (!hasChange(d)) return

  const skills = skillsInDiff(d)
  const agent = agentFromCwd()
  // Standing grants are scoped to the GLOBAL folder (see the gate), so the repo
  // tree takes an ad-hoc grant or the main agent's existing-skill rule.
  let cov = cfg.name === 'repo'
    ? grantCovers(skills, readGrant(), [], agent)
    : grantCovers(skills, readGrant(), readStandingGrants(), agent)
  if (!cov.covered && cfg.name === 'repo' && repoChangeCovered(d, snap.files, agent)) {
    cov = { covered: true, how: 'main-existing', id: agent }
  }
  const tool = String(payload?.tool_name ?? '')
  const parts = []
  if (d.added.length) parts.push(`added=${d.added.join(',')}`)
  if (d.modified.length) parts.push(`modified=${d.modified.join(',')}`)
  if (d.deleted.length) parts.push(`deleted=${d.deleted.join(',')}`)
  // `observed_by`, not `agent`: see grantCovers -- this hook runs after SOME
  // tool call, not necessarily the one that moved the file.
  const detail = `${cfg.tag}tool="${tool}" observed_by="${agent}" skills="${skills.join(',')}" ${parts.join(' ')}`

  // The log line goes down BEFORE the alert, and the snapshot only after both.
  // If the process dies in between, the next run sees the same difference and
  // reports it again: a repeated line is cheap, a lost one is the whole bug.
  if (cov.covered) {
    logLine('TREE_CHANGE_GRANTED', `${detail} via="${cov.how}"`)
  } else {
    logLine('TREE_CHANGE_UNGRANTED', detail)
    alertMainAgent(
      `[${cfg.label}] A vedett fa megvaltozott ERVENYES ENGEDELY NELKUL. ` +
      `Erintett skill(ek): ${skills.join(', ') || 'ismeretlen'}. ${parts.join(' | ')}. ` +
      `Eszlelte: ${agent} (${tool || 'ismeretlen eszkoz'}) -- de NEM biztos, hogy o irta: ez az ellenorzes ` +
      `a kovetkezo tool-hivas utan fut, barmelyik agensé is az, tehat a nevet eszlelokent olvasd, ne szerzokent. ` +
      `Ezt a PostToolUse audit fogta meg, nem a PreToolUse kapu -- vagyis a valtoztatas a kapu latoteren KIVUL tortent ` +
      `(tipikusan fajlbol futtatott szkript). Nezd meg, szandekos volt-e, es ha igen, utolag ratifikald vagy allitsd vissza.`,
      agent,
    )
  }
  saveSnapshot(after, cfg.snapshot)
}

if (isInvokedDirectly()) {
  try {
    run()
  } catch (e) {
    try {
      logLine('AUDIT_ERROR', `msg="${String(e && e.message ? e.message : e).slice(0, 200).replace(/"/g, "'")}"`)
    } catch {
      // Even the error log is best-effort.
    }
  }
  process.exit(0)
}
