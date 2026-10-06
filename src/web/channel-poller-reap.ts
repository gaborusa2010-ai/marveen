// Reap orphaned channel-plugin pollers (bun/node processes that survived a
// tmux kill-session or are left over from a previous agent crash).
//
// The bug we close (2026-06-01 incident, channel-disconnect roundtrip):
//   - stopAgentProcess used `pkill -f TELEGRAM_STATE_DIR=<dir>`, but the
//     plugin process argv is just `bun run --cwd .../telegram/0.0.6 start`
//     - the env var lives in /proc-equivalent environment storage, not argv,
//     so `pkill -f` never matches and the orphan keeps polling getUpdates
//     with the same bot token until SIGTERM by hand.
//   - startAgentProcess only killed the tmux session pre-launch and did NOT
//     reap orphans at all. After a restart the old poller raced the new one
//     and Telegram returned 409 Conflict in a loop.
//   - The plugin writes bot.pid in <chanDir>/bot.pid. That works on the
//     happy path but if a new poller crashed and a later one overwrote the
//     file, the older orphan is no longer in bot.pid - we miss it.
//
// Strategy: combine two identifiers.
//   1. bot.pid (cheap, works for the supervised process).
//   2. `ps eww -e` scan for the *_STATE_DIR=<chanDir> env-var match. This
//      catches orphans whose pid is no longer in bot.pid - any process that
//      was started against this channel state dir is in scope, regardless
//      of how its argv was rendered. macOS BSD ps emits each process's full
//      environment when invoked with `e`; we grep that.

import { execFileSync, execSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { ChannelProviderType } from '../channel-provider.js'
import { channelStateDir } from '../channel-provider.js'
import { logger } from '../logger.js'

const STATE_ENV_VAR: Record<ChannelProviderType, string> = {
  telegram: 'TELEGRAM_STATE_DIR',
  slack: 'SLACK_STATE_DIR',
  discord: 'DISCORD_STATE_DIR',
  googlechat: 'GOOGLECHAT_STATE_DIR',
  teams: 'TEAMS_STATE_DIR',
}

// Parse `ps eww -e` output and return every PID whose process environment
// contains `<envVar>=<value>`. Exported for testability.
//
// `ps eww -e` rows on macOS look like:
//   90798 s000  S+   0:00.01 bun run --cwd ... HOME=/Users/... TELEGRAM_STATE_DIR=/path... ...
// The match must be precise: substring `TELEGRAM_STATE_DIR=/path` against
// `TELEGRAM_STATE_DIR=/path-elsewhere` is acceptable because the value is an
// absolute path, but we still anchor on the env-var literal to avoid
// matching a row that just *mentions* the path string in its argv.
export function parsePollerPidsFromPs(
  psOutput: string,
  envVar: string,
  value: string,
): number[] {
  const needle = `${envVar}=${value}`
  const out: number[] = []
  for (const line of psOutput.split('\n')) {
    if (!line.includes(needle)) continue
    const m = line.match(/^\s*(\d+)\s/)
    if (!m) continue
    const pid = parseInt(m[1]!, 10)
    if (pid > 1) out.push(pid)
  }
  return out
}

function listPollerPidsByStateDir(envVar: string, chanDir: string): number[] {
  try {
    const out = execSync('/bin/ps eww -e', { timeout: 5000, encoding: 'utf-8', maxBuffer: 8 * 1024 * 1024 })
    return parsePollerPidsFromPs(out, envVar, chanDir)
  } catch (err) {
    logger.warn({ err, chanDir }, 'channel-poller-reap: ps scan failed')
    return []
  }
}

function readBotPid(chanDir: string): number | null {
  const path = join(chanDir, 'bot.pid')
  if (!existsSync(path)) return null
  try {
    const pid = parseInt(readFileSync(path, 'utf-8').trim(), 10)
    return Number.isFinite(pid) && pid > 1 ? pid : null
  } catch {
    return null
  }
}

export interface ReapResult {
  reaped: number[]
  source: { fromBotPid: number | null; fromEnvScan: number[] }
  // Candidates that matched bot.pid/env-scan but were spared because they ARE a
  // live tmux pane's own leader process right now. Non-empty here is the exact
  // signature of the 2026-09-19 bug (card 08a02137): env-var inheritance makes a
  // pane's own claude process match its own *_STATE_DIR export, and killing it
  // here (instead of leaving it to the caller's imminent `respawn-pane -k`)
  // collapsed the pane before respawn-pane could run. Logged whenever non-empty
  // so a recurrence is visible instead of silently "just working".
  skippedLivePane: number[]
  // True when the tmux server itself matched the candidate sources and was
  // spared (TMUXSERVERREAP929). Killing it takes every agent session down.
  skippedTmuxServer?: boolean
}

// ---------------------------------------------------------------------------
// Down-verdict forensics (2026-07-14).
//
// The watchdog restarted agents ~10x/day on a "channel plugin down" verdict,
// and the restart DESTROYS the evidence: the poller is reaped, the session is
// respawned, and the post-mortem log says only "down -- auto-restarting". So
// the interesting question -- did the poller really die, or did the tree-walk
// lose a live one? -- could not be answered from the logs at all.
//
// This captures the state at the MOMENT the verdict is formed, before anything
// is torn down: is a poller process for this chanDir alive at all, is it in the
// claude process tree, and does bot.pid still point at it. One WARN per
// down-spell, so it costs a ps per spell, not per sweep.

export interface PollerEvidenceRow {
  pid: number
  ppid: number
  // Whether claudePid is an ancestor of this pid. FALSE with a live pid is the
  // interesting case: the poller exists but hangs outside the tree the liveness
  // probe walks (reparented / attached to a previous claude).
  inClaudeTree: boolean
}

export interface PollerEvidence {
  botPid: number | null
  botPidAlive: boolean
  // Pollers found by env-var scan, i.e. every process started against this
  // channel state dir regardless of parentage.
  envScanPids: number[]
  rows: PollerEvidenceRow[]
  // The verdict this evidence supports, spelled out so the log line is readable
  // without re-deriving it:
  //   'no-poller'      -> nothing alive: the plugin really did die.
  //   'orphaned'       -> a live poller exists but is NOT under claude.
  //   'in-tree'        -> a live poller IS under claude: the probe was WRONG.
  interpretation: 'no-poller' | 'orphaned' | 'in-tree'
}

// Pure core: exported for tests (no ps, no fs).
export function buildPollerEvidence(
  procs: ProcRow[],
  botPid: number | null,
  envScanPids: number[],
  claudePid: number,
): PollerEvidence {
  const byPid = new Map<number, ProcRow>()
  for (const p of procs) byPid.set(p.pid, p)

  const isUnderClaude = (pid: number): boolean => {
    let cur = pid
    const seen = new Set<number>()
    for (let hops = 0; hops < 8; hops++) {
      if (cur === claudePid) return true
      if (seen.has(cur)) break
      seen.add(cur)
      const next = byPid.get(cur)?.ppid
      if (next === undefined || next === cur || next <= 1) break
      cur = next
    }
    return false
  }

  const candidates = new Set<number>(envScanPids)
  if (botPid != null) candidates.add(botPid)

  const rows: PollerEvidenceRow[] = []
  for (const pid of candidates) {
    const row = byPid.get(pid)
    if (!row) continue // not in the ps snapshot -> dead
    rows.push({ pid, ppid: row.ppid, inClaudeTree: isUnderClaude(pid) })
  }

  const interpretation: PollerEvidence['interpretation'] = rows.length === 0
    ? 'no-poller'
    : rows.some((r) => r.inClaudeTree) ? 'in-tree' : 'orphaned'

  return {
    botPid,
    botPidAlive: botPid != null && byPid.has(botPid),
    envScanPids,
    rows,
    interpretation,
  }
}

// Collect the evidence for one agent. Call this ONCE per down-spell, at the
// first down observation, BEFORE any teardown.
export function collectPollerEvidence(
  provider: ChannelProviderType,
  agentDirPath: string,
  claudePid: number,
): PollerEvidence {
  const chanDir = channelStateDir(provider, agentDirPath)
  return buildPollerEvidence(
    snapshotProcs(),
    readBotPid(chanDir),
    listPollerPidsByStateDir(STATE_ENV_VAR[provider], chanDir),
    claudePid,
  )
}

/**
 * Split env-scan candidates into the ones that belong to a LIVE session and the
 * ones that are safe to kill. Pure: exported for tests.
 *
 * WHY THIS EXISTS (2026-09-19 incident, main session suicide loop).
 * The env scan below was written when channels.sh launched the main `claude
 * --channels` with NO *_STATE_DIR export -- so the scan physically could not
 * see the main session, and killing everything it found was safe. #915
 * (2026-09-09) started exporting TELEGRAM_STATE_DIR for the main session too,
 * and that assumption silently died with it: the scan then matched the tmux
 * client, the main `claude` ITSELF and its poller. The scheduled auto-restart
 * reaps before respawning, so every due tick shot the live session in the head,
 * the follow-up `respawn-pane` failed with "no server running", the due flag
 * was never cleared, and it retried a minute later. 77 kills in one morning.
 *
 * THE LINE, and why it is drawn exactly here: the caller's intent is to kill a
 * leftover POLLER, because `tmux respawn-pane -k` tears down the pane's claude
 * but not its bun grandchild. So:
 *   - a pid that IS a live pane leader        -> spare (that is the session)
 *   - a `claude` binary under a live pane     -> spare (sub-agents run claude as
 *                                                a child of the pane's `sh -c`)
 *   - a `tmux` binary                         -> spare (server/client, never a
 *                                                poller; its argv merely quotes
 *                                                the launch command)
 *   - anything else                           -> killable (this is the poller)
 * A poller whose claude IS live stays killable on purpose: that is the whole
 * point of reaping before a respawn.
 */
export function partitionLiveSessionPids(
  candidates: number[],
  procs: ProcRow[],
  livePanePids: Set<number>,
): { spared: number[]; killable: number[] } {
  const byPid = new Map<number, ProcRow>()
  for (const p of procs) byPid.set(p.pid, p)

  const underLivePane = (pid: number): boolean => {
    let cur = pid
    const seen = new Set<number>()
    for (let hops = 0; hops < 8; hops++) {
      if (livePanePids.has(cur)) return true
      if (seen.has(cur)) break
      seen.add(cur)
      const next = byPid.get(cur)?.ppid
      if (next === undefined || next === cur || next <= 1) break
      cur = next
    }
    return false
  }

  const spared: number[] = []
  const killable: number[] = []
  for (const pid of candidates) {
    const row = byPid.get(pid)
    const argv0 = (row?.command ?? '').trim().split(/\s+/, 1)[0] ?? ''
    const base = argv0.split('/').pop() ?? ''
    if (livePanePids.has(pid)) { spared.push(pid); continue }
    if (base === 'tmux') { spared.push(pid); continue }
    if (base === 'claude' && underLivePane(pid)) { spared.push(pid); continue }
    killable.push(pid)
  }
  return { spared, killable }
}

/**
 * Can this pid actually BE a channel poller? Pure: exported for tests.
 *
 * The env scan matches every process that INHERITED the state dir, not just
 * pollers. Measured on the live tree 2026-09-19: alongside the real bun poller
 * it returned a `sleep 5` (the channels.sh watchdog) and a `bash -c` that was a
 * Bash tool call in progress. Killing those truncates someone else's work with
 * no error anywhere, so the kill is restricted to a bun/node/deno runtime --
 * or to whatever bot.pid names, since that one is the supervised poller by
 * definition, whatever it happens to be written in.
 *
 * A pid missing from the snapshot counts as killable: it is already dead, so
 * the kill is a no-op, and treating it as live would only mask a stale bot.pid.
 */
export function looksLikePoller(pid: number, procs: ProcRow[], botPid: number | null): boolean {
  if (botPid != null && pid === botPid) return true
  const row = procs.find((p) => p.pid === pid)
  if (!row) return true
  const base = (row.command.trim().split(/\s+/, 1)[0] ?? '').split('/').pop() ?? ''
  return base === 'bun' || base === 'node' || base === 'deno'
}

// Live pane pids, distinguishing "tmux answered, nothing is live" from "the
// query itself failed". `no server running` is a real answer (no panes exist),
// so it returns an empty set; anything else returns null and the caller must
// fail safe rather than guess.
function livePanePidsOrNull(tmuxPath: string): Set<number> | null {
  try {
    const out = execSync(`${tmuxPath} list-panes -a -F '#{pane_pid}'`, { timeout: 5000, encoding: 'utf-8', stdio: ['ignore', 'pipe', 'pipe'] })
    const s = new Set<number>()
    for (const line of out.split('\n')) {
      const n = parseInt(line.trim(), 10)
      if (Number.isFinite(n) && n > 1) s.add(n)
    }
    return s
  } catch (err) {
    const e = err as { stderr?: unknown; message?: string }
    const msg = `${String(e?.stderr ?? '')}\n${e?.message ?? ''}`
    if (/no server running|no such file or directory/i.test(msg)) return new Set()
    logger.warn({ err }, 'channel-poller-reap: live-pane query failed (not a "no server" answer)')
    return null
  }
}

/**
 * Reap every channel-plugin poller process associated with this agent.
 * Combines bot.pid (cheap, supervised pid) with a `ps eww -e` env-var scan
 * (catches orphans whose pid is no longer in bot.pid). SIGTERM first; after
 * a short grace period, SIGKILL any survivor. Safe to call multiple times
 * (process.kill on a missing pid is caught).
 *
 * Since 2026-09-19 it never kills a live session's claude or tmux -- see
 * partitionLiveSessionPids for why the env scan alone stopped being safe.
 */
export function reapChannelOrphans(
  provider: ChannelProviderType,
  agentDirPath: string,
  opts: { tmuxPath?: string } = {},
): ReapResult {
  const chanDir = channelStateDir(provider, agentDirPath)
  const envVar = STATE_ENV_VAR[provider]

  const fromBotPid = readBotPid(chanDir)
  const fromEnvScan = listPollerPidsByStateDir(envVar, chanDir)

  // Deduplicate while preserving order so the bot.pid path is logged first.
  const candidates: number[] = []
  const seen = new Set<number>()
  for (const pid of [fromBotPid, ...fromEnvScan]) {
    if (pid && !seen.has(pid)) {
      seen.add(pid)
      candidates.push(pid)
    }
  }

  // TWO NARROWINGS, and they are not alternatives -- this is the union of the
  // upstream fix and ours, which were written against the same 2026-09-19
  // measurement (card 08a02137) from opposite ends.
  //
  // (1) Never kill a pid that IS a live tmux pane's own leader process right
  // now. `export VAR=x && exec claude` makes VAR visible in claude's OWN
  // environment too, not just a spawned poller child's -- so the env-var scan
  // (and, for the main session, even bot.pid) can match the pane's own claude
  // process. Killing it here, instead of leaving it to the caller's imminent
  // `tmux respawn-pane -k`, races the respawn and can collapse the pane first
  // (no remain-on-exit -> pane death takes the whole session with it).
  //
  // (2) The env scan matches every process that INHERITED the state dir, not
  // just pollers -- on the live tree that included a `sleep 5` (the channels.sh
  // watchdog) and a `bash -c` that was a Bash tool call in progress. Killing
  // those is a silent truncation of someone else's work, so the kill is
  // restricted to what a poller can actually be: a bun/node runtime, or
  // whatever bot.pid names.
  //
  // Fail-SAFE, not fail-open: an UNRESOLVED live-pane set aborts the kill
  // entirely. livePanePidsOrNull is used rather than livePanePids because the
  // two failure modes are not the same claim -- "no tmux server running" is a
  // real, empty answer, while a failed query is no answer at all, and only the
  // second one may stand the reap down.
  let all = candidates
  let spared: number[] = []
  let skippedLivePane: number[] = []
  let skippedTmuxServer = false
  if (candidates.length > 0) {
    const live = livePanePidsOrNull(opts.tmuxPath ?? 'tmux')
    if (live === null) {
      logger.warn({ provider, chanDir, candidates }, 'channel-poller-reap: live-pane set unavailable, skipping reap (fail-safe)')
      return { reaped: [], source: { fromBotPid, fromEnvScan }, skippedLivePane: [], skippedTmuxServer: false }
    }
    // Never kill the tmux SERVER either (TMUXSERVERREAP929). When channels.sh
    // creates the server itself (after a host reboot it usually does: it runs
    // `tmux start-server` with the state-dir var already exported), the server
    // process carries TELEGRAM_STATE_DIR in its own environment, so the env scan
    // matches it. Killing it tears down EVERY agent's pane on the host, not just
    // this one's poller. Measured 2026-09-28 12:47, 14:47 and 2026-09-29 04:31:
    // the reap's `reaped` list held the server pid (the parent of every pane),
    // the next `tmux list-panes` answered "no server running", and the whole
    // fleet died and came back only through the service manager.
    // Fail-safe like the live-pane guard: an unresolved server pid refuses the
    // reap instead of guessing, because the guess is exactly the fleet kill.
    //
    // Local merge note (v1.41.0): an EMPTY live set is the real "no tmux server
    // running" answer here (livePanePidsOrNull returns null for a failed query),
    // and with no server there is no server pid to protect, so the lookup is
    // only required when at least one pane exists.
    const serverPid = live.size > 0 ? tmuxServerPid(opts.tmuxPath ?? 'tmux') : null
    if (live.size > 0 && serverPid === null) {
      logger.warn({ provider, chanDir, candidates },
        'channel-poller-reap: could not resolve the tmux server pid, refusing to reap (fail-safe)')
      return { reaped: [], source: { fromBotPid, fromEnvScan }, skippedLivePane: [], skippedTmuxServer: false }
    }
    const procs = snapshotProcs()
    const split = partitionLiveSessionPids(candidates, procs, live)
    const isPoller = (pid: number): boolean => looksLikePoller(pid, procs, fromBotPid)
    skippedTmuxServer = serverPid !== null && candidates.includes(serverPid) && !live.has(serverPid)
    all = split.killable.filter((p) => isPoller(p) && p !== serverPid)
    // skippedLivePane is the narrower, reportable set the ReapResult contract
    // describes: pane LEADERS only. `spared` is everything the reap declined to
    // kill, which also covers narrowing (2) and belongs in the log, not the
    // result.
    skippedLivePane = split.spared
    spared = [...split.spared, ...split.killable.filter((p) => !isPoller(p) || p === serverPid)]
    if (skippedTmuxServer) {
      logger.warn({ provider, chanDir, serverPid, fromBotPid, fromEnvScan },
        'channel-poller-reap: candidate IS the tmux server, sparing it (killing it would take down every agent session)')
    }
  }

  // SIGTERM, give bun/node ~300ms to flush, then SIGKILL stragglers.
  for (const pid of all) {
    try { process.kill(pid, 'SIGTERM') } catch { /* already gone */ }
  }
  if (all.length > 0) {
    try { execFileSync('/bin/sleep', ['0.3'], { timeout: 2000 }) } catch { /* ignore */ }
    for (const pid of all) {
      try { process.kill(pid, 0) /* probe */; process.kill(pid, 'SIGKILL') } catch { /* gone */ }
    }
  }

  if (all.length > 0 || spared.length > 0) {
    logger.info({ provider, chanDir, reaped: all, spared, fromBotPid, fromEnvScan }, 'channel-poller-reap: orphans killed')
  }
  if (skippedLivePane.length > 0) {
    logger.warn({ provider, chanDir, skippedLivePane, fromBotPid, fromEnvScan },
      'channel-poller-reap: candidate IS a live pane leader, sparing it (respawn-pane will replace it)')
  }
  return { reaped: all, source: { fromBotPid, fromEnvScan }, skippedLivePane, skippedTmuxServer }
}

// ---------------------------------------------------------------------------
// Detached channel CLAUDE reaper (the parent-process leak, 2026-06-03).
//
// reapChannelOrphans (above) kills bun/node POLLERS by env-var scan + bot.pid.
//
// OUTDATED AS OF #915 (2026-09-09), kept because the rest of the rationale
// still holds: this used to say the env scan "MISSES the main channels session
// entirely", because channels.sh launched the main `claude --channels` with no
// *_STATE_DIR export. channels.sh now exports it (measured 2026-09-19 on the
// live process tree: the tmux client, the main claude AND its bun poller all
// carry TELEGRAM_STATE_DIR), so the env scan DOES see the main session -- which
// is exactly why reapChannelOrphans had to grow the live-pane guard.
//
// The detached reaper below is still needed for a different reason: it finds a
// detached claude that no longer matches any state dir at all. When a
// --continue respawn (channel-monitor
// respawn-pane / agent-process start) fails to tear down the prior claude, the
// detached claude survives -- reparented to the tmux server -- and keeps a bun
// poller hitting getUpdates on the SHARED bot token. 5 such orphans accumulated
// over 13 days, each 409-racing the live poller (token churn + a self-feeding
// agent thrash-restart loop). See project_channels_continue_respawn_leak.
//
// Identification is by tmux-pane attribution, NOT env/argv heuristics (cmdline
// alone cannot tell a live agent claude from a detached one -- see
// feedback_verify_session_before_kill): a `claude --channels` process is an
// orphan iff neither its pid nor any ancestor pid is a LIVE tmux pane pid.
//   - main session: tmux runs claude as the pane leader, so claudePid == panePid.
//   - sub-agents:   tmux runs `sh -c "...claude..."`, so the pane pid is the sh
//                   and claude is its child -> ancestor walk catches it.
// The tmux SERVER process is excluded up front: its argv embeds the full
// `new-session ... claude --channels ...` string, a false positive, but argv[0]
// is tmux, not claude.

export interface ProcRow { pid: number; ppid: number; command: string }

// argv[0] basename === 'claude' (the binary), so the tmux server row whose argv
// merely *contains* the claude command string is excluded.
function isClaudeBinary(command: string): boolean {
  const argv0 = command.trim().split(/\s+/, 1)[0] ?? ''
  const base = argv0.split('/').pop() ?? ''
  return base === 'claude'
}

/**
 * Pure: return the pids of `claude --channels` processes that are NOT attached
 * to any live tmux pane (orphans). `livePanePids` is the set of pane pids from
 * `tmux list-panes -a`. `channelNeedle` optionally restricts to one plugin
 * (e.g. 'plugin:telegram@...'); when omitted, every channel plugin is in scope.
 * Exported for testability.
 */
export function findOrphanChannelClaudes(
  procs: ProcRow[],
  livePanePids: Set<number>,
  channelNeedle?: string,
): number[] {
  const byPid = new Map<number, ProcRow>()
  for (const p of procs) byPid.set(p.pid, p)

  const attachedToLivePane = (pid: number): boolean => {
    let cur = pid
    const seen = new Set<number>()
    for (let hops = 0; hops < 8; hops++) {
      if (livePanePids.has(cur)) return true
      if (seen.has(cur)) break
      seen.add(cur)
      const next = byPid.get(cur)?.ppid
      if (next === undefined || next === cur || next <= 1) break
      cur = next
    }
    return false
  }

  const orphans: number[] = []
  for (const p of procs) {
    if (!p.command.includes('--channels')) continue
    if (!isClaudeBinary(p.command)) continue
    if (channelNeedle && !p.command.includes(channelNeedle)) continue
    if (attachedToLivePane(p.pid)) continue
    orphans.push(p.pid)
  }
  return orphans
}

function snapshotProcs(): ProcRow[] {
  try {
    const out = execSync('/bin/ps -axww -o pid=,ppid=,command=', { timeout: 5000, encoding: 'utf-8', maxBuffer: 8 * 1024 * 1024 })
    const rows: ProcRow[] = []
    for (const line of out.split('\n')) {
      const m = line.match(/^\s*(\d+)\s+(\d+)\s+(.*)$/)
      if (!m) continue
      rows.push({ pid: parseInt(m[1]!, 10), ppid: parseInt(m[2]!, 10), command: m[3]! })
    }
    return rows
  } catch (err) {
    logger.warn({ err }, 'channel-poller-reap: ps -axww snapshot failed')
    return []
  }
}

// The tmux server's own pid, or null when it cannot be resolved.
function tmuxServerPid(tmuxPath: string): number | null {
  try {
    const out = execSync(`${tmuxPath} display-message -p '#{pid}'`, { timeout: 5000, encoding: 'utf-8' })
    const n = parseInt(out.trim().split('\n')[0] ?? '', 10)
    return Number.isFinite(n) && n > 1 ? n : null
  } catch (err) {
    logger.warn({ err }, 'channel-poller-reap: tmux display-message failed')
    return null
  }
}

function livePanePids(tmuxPath: string): Set<number> {
  try {
    const out = execSync(`${tmuxPath} list-panes -a -F '#{pane_pid}'`, { timeout: 5000, encoding: 'utf-8' })
    const s = new Set<number>()
    for (const line of out.split('\n')) {
      const n = parseInt(line.trim(), 10)
      if (Number.isFinite(n) && n > 1) s.add(n)
    }
    return s
  } catch (err) {
    logger.warn({ err }, 'channel-poller-reap: tmux list-panes failed')
    return new Set()
  }
}

function killBunChildren(claudePid: number): void {
  try {
    const out = execSync(`/usr/bin/pgrep -P ${claudePid} bun`, { timeout: 3000, encoding: 'utf-8' })
    for (const line of out.split('\n')) {
      const pid = parseInt(line.trim(), 10)
      if (Number.isFinite(pid) && pid > 1) {
        try { process.kill(pid, 'SIGTERM') } catch { /* gone */ }
      }
    }
  } catch { /* no bun children (pgrep exits 1) */ }
}

/**
 * Reap detached `claude --channels` orphans (parent-process leak). SAFE to call
 * before any (re)spawn: it spares every claude attached to a live tmux pane, so
 * it never kills the active session or a live sibling agent -- only truly
 * detached leftovers. Kills each orphan's bun poller children first, then the
 * claude (SIGTERM, ~300ms grace, SIGKILL stragglers). Returns reaped pids.
 *
 * tmuxPath defaults to a bare `tmux` (resolved on PATH); callers that already
 * hold an absolute path should pass it.
 */
export function reapDetachedChannelClaudes(opts: { channelNeedle?: string; tmuxPath?: string } = {}): number[] {
  const tmuxPath = opts.tmuxPath ?? 'tmux'
  const procs = snapshotProcs()
  const live = livePanePids(tmuxPath)
  // No live panes resolved (tmux query failed) -> refuse to reap: without the
  // live set we cannot tell orphans from the active session. Fail safe.
  if (live.size === 0) {
    logger.warn('channel-poller-reap: no live panes resolved, skipping detached-claude reap (fail-safe)')
    return []
  }
  const orphans = findOrphanChannelClaudes(procs, live, opts.channelNeedle)
  for (const pid of orphans) {
    killBunChildren(pid)
    try { process.kill(pid, 'SIGTERM') } catch { /* gone */ }
  }
  if (orphans.length > 0) {
    try { execFileSync('/bin/sleep', ['0.3'], { timeout: 2000 }) } catch { /* ignore */ }
    for (const pid of orphans) {
      try { process.kill(pid, 0); process.kill(pid, 'SIGKILL') } catch { /* gone */ }
    }
    logger.info({ reaped: orphans, channelNeedle: opts.channelNeedle ?? '(all)' }, 'channel-poller-reap: detached channel claudes killed')
  }
  return orphans
}

// ---------------------------------------------------------------------------
// Foreign MAIN-token poller reaper (2026-07-18 incident: the main bot went
// silent for ~half an afternoon).
//
// The two reapers above share a blind spot that this one closes:
//
//   reapChannelOrphans          -> matches by <PROVIDER>_STATE_DIR=<chanDir>.
//     The MAIN channels session is launched by channels.sh with NO
//     TELEGRAM_STATE_DIR export (the plugin falls back to its default dir,
//     ~/.claude/channels/<provider>), so the main poller carries no state-dir
//     needle and is invisible to that env scan.
//   reapDetachedChannelClaudes  -> matches `claude --channels` processes not
//     attached to a live pane. A THIEF here is NOT a `--channels` session: it
//     is a plain local-agent-mode / CLI `claude` running in the project cwd,
//     which AUTO-LOADS the telegram plugin because the PROJECT settings.json
//     has enabledPlugins.telegram=true. Its argv has no `--channels`, so that
//     reaper never even considers it.
//
// Net effect: a local-agent-mode subagent (e.g. one the main session's own
// Agent/Task tool spawns) loads the plugin with the DEFAULT state dir, grabs
// the MAIN bot token, and long-polls getUpdates alongside the legit poller ->
// 409 Conflict -> the main bot silently drops inbound. The existing
// down-recovery restarts the VICTIM (the legit session), never the THIEF, so
// the outage persists until the thief happens to exit.
//
// This reaper targets exactly that class: a poller bound to the MAIN (default)
// state dir -- i.e. WITHOUT a <PROVIDER>_STATE_DIR override, which cleanly
// excludes every sub-agent -- whose owning `claude` process is NOT the pane
// leader of the main channels session. The legit main poller's nearest claude
// ancestor IS the channels pane pid; a thief's nearest claude ancestor is the
// local-agent-mode claude (a DESCENDANT of the pane, but not the pane leader).
// So pane-pid EQUALITY -- not mere descent -- is the discriminator (the thief
// is a descendant of the channels pane too, so an "ancestor includes pane"
// test would wrongly spare it).

// argv[0] basename of the CLAUDE_PLUGIN_ROOT plugin dir per provider. The
// telegram plugin cache path ends in `.../telegram/<ver>`; slack-channel in
// `.../slack-channel/<ver>`; etc.
const PLUGIN_ROOT_NEEDLE: Record<ChannelProviderType, string> = {
  telegram: '/telegram',
  slack: '/slack-channel',
  discord: '/discord',
  googlechat: '/googlechat',
  teams: '/teams',
}

// Candidate = a poller bound to the MAIN default state dir: its env carries
// CLAUDE_PLUGIN_ROOT=.../<provider>/<ver> but NO <PROVIDER>_STATE_DIR override
// (the override is exactly what every sub-agent sets, so its absence isolates
// the main-dir pollers). Exported for testability.
export function parseMainDirPollerPids(
  psEwwOutput: string,
  pluginRootNeedle: string, // e.g. '/telegram'
  stateEnvVar: string,      // e.g. 'TELEGRAM_STATE_DIR'
): number[] {
  const escaped = pluginRootNeedle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  // Anchor on the CLAUDE_PLUGIN_ROOT env literal, then the provider dir segment
  // ending on a path/version/space boundary so `/telegram` does not match a
  // longer sibling like `/telegram-inline`.
  const rootRe = new RegExp(`CLAUDE_PLUGIN_ROOT=\\S*${escaped}(?:[/@ ]|$)`)
  const out: number[] = []
  for (const line of psEwwOutput.split('\n')) {
    if (!rootRe.test(line)) continue
    if (line.includes(`${stateEnvVar}=`)) continue // sub-agent override -> not main dir
    const m = line.match(/^\s*(\d+)\s/)
    if (!m) continue
    const pid = parseInt(m[1]!, 10)
    if (pid > 1) out.push(pid)
  }
  return out
}

// Nearest ancestor whose argv[0] basename is `claude`. Null if none is found
// within the hop budget (a cycle-guarded 8-hop walk). Exported for testability.
export function nearestClaudeAncestor(pid: number, byPid: Map<number, ProcRow>): number | null {
  const seen = new Set<number>()
  let cur = byPid.get(pid)?.ppid
  for (let hops = 0; hops < 8; hops++) {
    if (cur === undefined || cur <= 1 || seen.has(cur)) break
    seen.add(cur)
    const row = byPid.get(cur)
    if (row && isClaudeBinary(row.command)) return cur
    cur = row?.ppid
  }
  return null
}

/**
 * Pure: from the candidate MAIN-dir poller pids, return those whose owning
 * claude (nearest claude ancestor) is NOT a legit main-session pane leader.
 *
 * Fail-safe on two fronts:
 *   - legitClaudePids empty (the main channels session could not be resolved)
 *     -> return [] : without the legit set we cannot tell the real poller from
 *     a thief, and killing the real one would take the bot down.
 *   - a candidate whose owning claude cannot be resolved -> skipped : we never
 *     kill on an ambiguous parent chain.
 * Exported for testability.
 */
export function findForeignMainPollers(
  candidatePollerPids: number[],
  procs: ProcRow[],
  legitClaudePids: Set<number>,
): number[] {
  if (legitClaudePids.size === 0) return []
  const byPid = new Map<number, ProcRow>()
  for (const p of procs) byPid.set(p.pid, p)
  const out: number[] = []
  for (const pid of candidatePollerPids) {
    const owner = nearestClaudeAncestor(pid, byPid)
    if (owner == null) continue
    if (legitClaudePids.has(owner)) continue
    out.push(pid)
  }
  return out
}

function mainSessionPanePids(session: string, tmuxPath: string): Set<number> {
  try {
    const out = execSync(`${tmuxPath} list-panes -t ${session} -F '#{pane_pid}'`, { timeout: 5000, encoding: 'utf-8' })
    const s = new Set<number>()
    for (const line of out.split('\n')) {
      const n = parseInt(line.trim(), 10)
      if (Number.isFinite(n) && n > 1) s.add(n)
    }
    return s
  } catch {
    // Session absent OR tmux query failed -> empty set -> caller fails safe.
    return new Set()
  }
}

/**
 * Reap foreign pollers contending for the MAIN bot token (see the block comment
 * above). SIGTERM -> ~300ms grace -> SIGKILL stragglers. Kills only the poller
 * process, never its owning claude (a real Agent/Task subagent may still be
 * doing legit work -- it just must not hold the main channel's poller). Returns
 * the pids killed. Fail-safe: does nothing when the main session can't be
 * resolved or the ps/tmux snapshot fails.
 */
export function reapForeignMainPollers(opts: {
  provider: ChannelProviderType
  mainSession: string
  tmuxPath?: string
}): number[] {
  const tmuxPath = opts.tmuxPath ?? 'tmux'
  const legit = mainSessionPanePids(opts.mainSession, tmuxPath)
  if (legit.size === 0) return [] // fail-safe: cannot distinguish legit from thief

  let psEww: string
  try {
    psEww = execSync('/bin/ps eww -e', { timeout: 5000, encoding: 'utf-8', maxBuffer: 8 * 1024 * 1024 })
  } catch (err) {
    logger.warn({ err }, 'channel-poller-reap: ps eww scan failed (foreign-main reap skipped)')
    return []
  }
  const candidates = parseMainDirPollerPids(psEww, PLUGIN_ROOT_NEEDLE[opts.provider], STATE_ENV_VAR[opts.provider])
  if (candidates.length === 0) return []

  const foreign = findForeignMainPollers(candidates, snapshotProcs(), legit)
  for (const pid of foreign) {
    try { process.kill(pid, 'SIGTERM') } catch { /* gone */ }
  }
  if (foreign.length > 0) {
    try { execFileSync('/bin/sleep', ['0.3'], { timeout: 2000 }) } catch { /* ignore */ }
    for (const pid of foreign) {
      try { process.kill(pid, 0); process.kill(pid, 'SIGKILL') } catch { /* gone */ }
    }
    logger.info(
      { provider: opts.provider, mainSession: opts.mainSession, reaped: foreign, legit: [...legit] },
      'channel-poller-reap: foreign main-token poller(s) killed (thief contending for the main bot token)',
    )
  }
  return foreign
}
