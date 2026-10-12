// Whether a CHANNEL-having agent may be relaunched with `--continue`
// (CONTRESUME922, narrowing approved by Marveen 2026-09-23, msg 28581).
//
// HISTORY: Claude Code 2.1.193 stopped re-initialising the `--channels` plugin
// MCP server on a `--continue` resume (no /mcp entry, no bun poller, no
// bot.pid -> a bot that is permanently deaf on its channel). Since then every
// channel agent was launched FRESH and paid with its whole conversation
// context on every restart.
//
// MEASURED 2026-09-23 on Claude Code 2.1.280 (three agents, one morning): a
// hand-launched `--continue` session brought the Telegram plugin up (tool
// schema loaded, bun grandchild, bot.pid rewritten), sent a REAL Telegram
// message from the resumed session (jumanji, message_id 23) and kept its
// context (probamcp and jumanji both remembered the previous round). The
// provider-key agent (deeper, DeepSeek) could NOT be measured because its key
// is an ephemeral launch-secret file that the stop removes: a resume of that
// launch command starts with an empty key (401). That is a mechanism, so it is
// a CODE condition here, not a comment.
//
// The three conditions of the approval:
//   1. the installed CLI is measured and >= 2.1.280 (below, or unmeasured: fresh);
//   2. the provider-key branch is excluded in code (usesLaunchSecret);
//   3. after a `--continue` launch the system verifies gates (b) bun poller and
//      (c) bot.pid within a window, and falls back to a fresh launch when they
//      do not appear -- a silently deaf channel agent costs more than a lost context.
import { readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { compareVersions } from '../claude-cli-support.js'

/** The measured floor: the regression was observed on 2.1.193 and absent on 2.1.280. */
export const CONTINUE_MIN_CLI = '2.1.280'
/** How long a resumed session gets to bring its plugin up before the fresh fallback. */
export const CONTINUE_VERIFY_WINDOW_MS = 90_000
export const CONTINUE_VERIFY_INTERVAL_MS = 3_000
/**
 * Headroom a resume must leave below the guard handoff threshold. The first
 * resumed turn is not free: the wake prompt, the briefing and its tool output
 * land on top of the old context. MEASURED (anorak, Anorak's #251 retro,
 * 2026-10-08): resumed at 85.6% on 10-07 23:00, at 97% two minutes later, so
 * the guard handed it off with a stale HANDOFF.md. Resumes at 78-79%
 * (forgacs 10-09 03:00, cody 10-08 03:00) ran for hours.
 */
export const RESUME_HEADROOM_PCT = 0.10
/**
 * Bytes of instruction files per context token when Claude Code re-attaches
 * them on a resume. Calibrated on the one measured re-attach (anorak 10-08
 * 23:01, `instructions` attachment, `changed: true`): ~131k chars of
 * CLAUDE.md + agent CLAUDE.md + MEMORY.md moved the context by ~72k tokens.
 * Rounded DOWN so the estimate errs towards a fresh launch.
 */
export const REATTACH_BYTES_PER_TOKEN = 2

export interface ContinueDecisionInput {
  hasPriorSession: boolean
  fresh: boolean
  hasChannel: boolean
  isMainAgent: boolean
  /** The agent's channel provider type ('telegram', 'slack', ...). Only telegram was measured. */
  provider: string
  /** The launch command reads a provider key / API key from a launch-secret file (ephemeral). */
  usesLaunchSecret: boolean
  /** The launch exports the persistent fleet OAuth token (the measured auth path). */
  fleetTokenLaunch: boolean
  /** The channel plugin is loaded through mcp.json + tee instead of --channels (unmeasured path). */
  useMcpJsonForChannel: boolean
  /** Measured installed Claude Code version, null when unmeasured. */
  installedCli: string | null
  /** Context fill of the newest prior transcript (0..1), null when unmeasured
   *  or the agent has no enabled context-guard. */
  priorContextPct?: number | null
  /** The guard's handoff threshold (actPct) for this agent, null when the
   *  guard is not enabled for it. */
  guardActPct?: number | null
  /** Estimated extra fill (0..1 of the guard limit) a resume adds because an
   *  instruction file changed after the prior session's last turn and Claude
   *  Code re-attaches the whole instruction block. 0/null when nothing changed. */
  resumeReattachPct?: number | null
  /**
   * Co-listen providers loaded next to the primary (AGENTEXTRACH1006). With a
   * channel-having primary they ride its decision (and the post-launch verify
   * covers them). With NO primary channel they are the only plugins loaded,
   * so the measured gates are evaluated on THEM: a slack-only agent must not
   * resume where a slack-primary one would not (Dani, #1710 review).
   */
  extraProviders?: readonly string[]
}

export interface ContinueDecision { useContinue: boolean; reason: string }

/** Pure decision. Every "no" names its reason so the launch log says why a context was dropped. */
export function decideContinueFlag(i: ContinueDecisionInput): ContinueDecision {
  if (!i.hasPriorSession) return { useContinue: false, reason: 'no prior session to continue' }
  if (i.fresh) return { useContinue: false, reason: 'fresh launch requested' }
  // #416 (8a285b66): `--continue` resumes the NEWEST transcript. When the guard
  // fresh-restarted an agent and the fresh session died before writing a turn
  // (2026-09-30 18:41: Margo restarted at 297k, fleet stop 7 s later), the
  // newest transcript is still the abandoned heavy one, and the next launch
  // resumed it (to 329k). A session already past the handoff threshold is one
  // the guard would hand off on its next sweep anyway, so do not resume it.
  if (i.priorContextPct != null && i.guardActPct != null && i.priorContextPct >= i.guardActPct) {
    return { useContinue: false, reason: `prior session at ${Math.round(i.priorContextPct * 100)}% context, at/above the guard handoff threshold ${Math.round(i.guardActPct * 100)}% -- resuming it would re-adopt a session the guard abandons` }
  }
  // #251 retro: below the threshold is not enough. A resume that lands within
  // a few minutes of the threshold (first-turn growth, or the re-attached
  // instruction block after a CLAUDE.md change) is handed off by the guard
  // with a stale HANDOFF.md -- a fresh launch is cheaper and loses less.
  if (i.priorContextPct != null && i.guardActPct != null) {
    const reattach = i.resumeReattachPct ?? 0
    const projected = i.priorContextPct + RESUME_HEADROOM_PCT + reattach
    if (projected >= i.guardActPct) {
      const parts = `${Math.round(i.priorContextPct * 100)}% + ${Math.round(RESUME_HEADROOM_PCT * 100)}% first-turn headroom` + (reattach > 0 ? ` + ${Math.round(reattach * 100)}% re-attached instructions (changed since the last turn)` : '')
      return { useContinue: false, reason: `prior session would resume at ${parts} = ${Math.round(projected * 100)}%, at/above the guard handoff threshold ${Math.round(i.guardActPct * 100)}%` }
    }
  }
  const extras = i.extraProviders ?? []
  if (!i.hasChannel && extras.length === 0) return { useContinue: true, reason: 'channel-less agent keeps its context' }
  if (i.isMainAgent) return { useContinue: false, reason: 'main agent lifecycle is service-managed; not in scope' }
  // The plugin(s) the gates are about: the primary when it has a channel,
  // otherwise the extras, which are then the only plugins this launch loads.
  const gated = i.hasChannel ? [i.provider] : extras
  const unmeasured = gated.find(p => p !== 'telegram')
  if (unmeasured !== undefined) return { useContinue: false, reason: `provider '${unmeasured}' not measured; only telegram was (CONTRESUME922)` }
  if (i.useMcpJsonForChannel) return { useContinue: false, reason: 'mcp.json+tee channel path not measured' }
  if (i.usesLaunchSecret) return { useContinue: false, reason: 'launch reads an ephemeral launch-secret (provider/API key); a resume starts without it (measured 401)' }
  if (!i.fleetTokenLaunch) return { useContinue: false, reason: 'launch is not on the fleet OAuth token path (the measured auth path)' }
  if (!i.installedCli) return { useContinue: false, reason: 'installed Claude Code version unmeasured' }
  if (compareVersions(i.installedCli, CONTINUE_MIN_CLI) < 0) return { useContinue: false, reason: `installed Claude Code ${i.installedCli} is below the measured floor ${CONTINUE_MIN_CLI}` }
  return { useContinue: true, reason: `telegram + fleet token + Claude Code ${i.installedCli} >= ${CONTINUE_MIN_CLI} (measured 2026-09-23)` }
}

export type ContinueVerifyOutcome = 'alive' | 'timeout'

/**
 * Condition 3: poll the plugin-liveness probe until it says 'alive' or the
 * window closes. 'unknown' (a failed ps) and 'down' both keep polling: only a
 * positive verdict ends the wait early. Injected clock/sleep for tests.
 */
export async function verifyContinueLaunch(deps: {
  probe: () => 'alive' | 'down' | 'unknown'
  windowMs?: number
  intervalMs?: number
  now?: () => number
  sleep?: (ms: number) => Promise<void>
}): Promise<{ outcome: ContinueVerifyOutcome; polls: number; elapsedMs: number }> {
  const windowMs = deps.windowMs ?? CONTINUE_VERIFY_WINDOW_MS
  const intervalMs = deps.intervalMs ?? CONTINUE_VERIFY_INTERVAL_MS
  const now = deps.now ?? (() => Date.now())
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
  const started = now()
  let polls = 0
  for (;;) {
    polls += 1
    if (deps.probe() === 'alive') return { outcome: 'alive', polls, elapsedMs: now() - started }
    if (now() - started >= windowMs) return { outcome: 'timeout', polls, elapsedMs: now() - started }
    await sleep(intervalMs)
  }
}

/**
 * #251: estimate how much a `--continue` resume adds because an instruction
 * file changed after the prior session's last turn. Claude Code then
 * re-attaches the WHOLE instruction block (every loaded CLAUDE.md and the
 * MEMORY.md index), not just the changed file -- measured once, anorak
 * 2026-10-08 23:01 (`instructions` attachment, `changed: true`). That trigger
 * is an inference from the attachment, not read from Claude Code's source,
 * so the estimate only ever pushes towards a fresh launch.
 *
 * `instructionFiles` are the files the agent loads; missing ones are skipped.
 * Returns 0 when nothing changed after the newest transcript, null when it
 * cannot tell (no transcript, no limit).
 */
export function estimateResumeReattachPct(i: {
  instructionFiles: readonly string[]
  transcriptDir: string
  limitTokens: number | null
  stat?: (p: string) => { mtimeMs: number; size: number } | null
  listJsonl?: (dir: string) => string[]
}): number | null {
  if (!i.limitTokens || i.limitTokens <= 0) return null
  const stat = i.stat ?? ((p: string) => { try { const s = statSync(p); return { mtimeMs: s.mtimeMs, size: s.size } } catch { return null } })
  const listJsonl = i.listJsonl ?? ((dir: string) => { try { return readdirSync(dir).filter(f => f.endsWith('.jsonl')).map(f => join(dir, f)) } catch { return [] } })
  let lastTurn: number | null = null
  for (const f of listJsonl(i.transcriptDir)) {
    const s = stat(f)
    if (s && (lastTurn === null || s.mtimeMs > lastTurn)) lastTurn = s.mtimeMs
  }
  if (lastTurn === null) return null
  let changed = false
  let bytes = 0
  for (const f of i.instructionFiles) {
    const s = stat(f)
    if (!s) continue
    bytes += s.size
    if (s.mtimeMs > lastTurn) changed = true
  }
  if (!changed) return 0
  return bytes / REATTACH_BYTES_PER_TOKEN / i.limitTokens
}
