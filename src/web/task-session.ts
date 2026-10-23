// The main agent's TASK SESSION (#638 rebuild 2): a second, channel-less tmux
// session in which the main agent's scheduled tasks run, so their turns stop
// filling the conversation the owner talks to.
//
// WHY. Measured 2026-10-23 over 7 days of the main transcripts: scheduled tasks
// were 12.6% of the main session's text (295 turns), and every one of them
// pulls the main session closer to a context-guard restart. A task that never
// talks to the owner does not need the channel session at all.
//
// OPT-IN PER TASK. A task moves here only when its task-config.json says
// `"targetSession": "<main>-tasks"`; nothing else changes. The first rollout
// step is the heartbeats that sent nothing to the owner in 14 days (measured:
// store/hb_reply-20261023.py), because this session has NO channel tool.
//
// ISOLATION, MEASURED BEFORE WRITING (2026-10-23 14:32, card #638 comment
// 2129). The project .claude/settings.json enables the telegram plugin by cwd,
// so a clean CLAUDE_CONFIG_DIR alone would start a second poller and take the
// bot's single getUpdates slot from the main session. Three gates, the same
// ones scripts/morning-briefing.sh uses (MORNINGPOLLER1770): a --settings
// overlay with every channel plugin false, `env -u` on the bot tokens, and
// every *_STATE_DIR pointed at an EMPTY dir (never unset: unset falls back to
// the shared default, which may hold a token). With those, an interactive
// session in the repo cwd left the main bot.pid untouched and spawned no bun.
//
// FALLBACK. When this session cannot be had (no usable auth, provisioning
// failed, a launch failed within the backoff), resolveTaskTarget() sends the
// task to the main session as before. A task that cannot reach its own session
// must still run; it must never sit in the retry queue forever.
//
// OWN CONFIG DIR (.tasks-config). Its transcripts must NOT land in the main
// session's projects dir: the keepalive / deafness checks read the NEWEST
// transcript there as proof that the main session is alive, and a task turn
// would read as owner traffic. The auto-memory dir is symlinked to the main
// one, so the task session reads and writes the same memory.
import { execFileSync } from 'node:child_process'
import { existsSync, lstatSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { makeLazyBinResolver } from '../platform.js'
import { MAIN_AGENT_ID, PROJECT_ROOT } from '../config.js'
import { logger } from '../logger.js'
import { channelStateDirEnvVar, type ChannelProviderType } from '../channel-provider.js'
import { CHANNEL_PLUGIN_IDS } from './plugin-ids.js'
import {
  FLEET_OAUTH_TOKEN_PATH,
  ensureMainTaskConfigDir,
  fleetVenvPathPrefix,
  hasFleetOauthToken,
  mainAgentConfigDirIfSeparate,
  readExtraChannelPluginIds,
  resolveMainAgentConfigDir,
  resolveMainAgentRotatedConfigDir,
  resolveMainAgentRotatedTokenSecretId,
  shSingleQuote,
} from './agent-process.js'
import { readConfiguredMainModel } from './channel-monitor.js'
import { ROOT_SANDBOX_ENV } from './root-sandbox-env.js'
import { projectsDirFor } from './active-model.js'

export const TASK_SESSION = `${MAIN_AGENT_ID}-tasks`

export function taskConfigDir(): string {
  return join(PROJECT_ROOT, '.tasks-config')
}

const tmuxBin = makeLazyBinResolver('tmux')
const claudeBin = makeLazyBinResolver('claude')

const RESOLVE_PLAN_TOKEN_PATH = join(PROJECT_ROOT, 'scripts', 'resolve-plan-token-env.mjs')
const CHANNELS_FAILURES_LOG_PATH = join(PROJECT_ROOT, 'store', 'channels-failures.log')
const OVERLAY_PATH = join(PROJECT_ROOT, 'store', '.task-session.settings.json')
const EMPTY_STATE_DIR = join(PROJECT_ROOT, 'store', '.task-session-no-channel')
const BOT_TOKEN_VARS = ['TELEGRAM_BOT_TOKEN', 'SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN', 'DISCORD_BOT_TOKEN']

// A fresh session per this many hours or deliveries. The tasks are
// self-contained, so dropping the conversation costs nothing, and it keeps the
// session far from the context-guard (which does not watch this session).
export const TASK_SESSION_MAX_AGE_MS = 3 * 60 * 60 * 1000
export const TASK_SESSION_MAX_DELIVERIES = 12
// After a failed launch, the main session takes the tasks for this long.
const LAUNCH_BACKOFF_MS = 10 * 60 * 1000

/** How the task session authenticates, or why it cannot. */
export type TaskSessionAuth =
  | { kind: 'fleet' }
  | { kind: 'plan'; tokenSecretId: string }
  | { kind: 'none'; reason: string }

// The same precedence as resolveMainConfigDecision(), read WITHOUT its report:
// that report is the main session's launch trace, and a task-session launch
// written there would read as a main restart. An own-credential dir (explicit
// MAIN_AGENT_CONFIG_DIR or a configDir-mode plan) is a login this session
// cannot share without copying its credential file, which the fleet auth rule
// forbids -- so in that case the tasks stay on the main session.
export function resolveTaskSessionAuth(): TaskSessionAuth {
  if (resolveMainAgentConfigDir()) return { kind: 'none', reason: 'main agent runs on an explicit config dir (own login)' }
  if (resolveMainAgentRotatedConfigDir()) return { kind: 'none', reason: 'main agent runs on a configDir-mode plan (own login)' }
  const tokenSecretId = resolveMainAgentRotatedTokenSecretId()
  if (tokenSecretId) return { kind: 'plan', tokenSecretId }
  if (hasFleetOauthToken()) return { kind: 'fleet' }
  return { kind: 'none', reason: 'no fleet OAuth token' }
}

/** Every channel plugin off, plus the main agent's co-listen extras. */
export function taskSessionOverlay(extraPluginIds: string[]): { enabledPlugins: Record<string, boolean> } {
  const enabledPlugins: Record<string, boolean> = {}
  for (const id of [...Object.values(CHANNEL_PLUGIN_IDS), ...extraPluginIds]) enabledPlugins[id] = false
  return { enabledPlugins }
}

// Pure, so the test can lock every gate into the string.
export function buildTaskSessionCmd(opts: {
  claudePath: string
  model: string
  configDir: string
  overlayPath: string
  emptyStateDir: string
  auth: Exclude<TaskSessionAuth, { kind: 'none' }>
}): string {
  const providers = Object.keys(CHANNEL_PLUGIN_IDS) as ChannelProviderType[]
  return [
    `export PATH="${fleetVenvPathPrefix()}/opt/homebrew/bin:$HOME/.bun/bin:/home/linuxbrew/.linuxbrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"`,
    `&& unset ${BOT_TOKEN_VARS.join(' ')}`,
    `&& export ${providers.map((p) => `${channelStateDirEnvVar(p)}=${shSingleQuote(opts.emptyStateDir)}`).join(' ')}`,
    '&& export CLAUDE_CODE_DISABLE_AGENT_VIEW=1',
    `&& ${ROOT_SANDBOX_ENV}`,
    `&& export CLAUDE_CONFIG_DIR=${shSingleQuote(opts.configDir)}`,
    opts.auth.kind === 'plan'
      ? `&& _plan_token="$(node '${RESOLVE_PLAN_TOKEN_PATH}' '${opts.auth.tokenSecretId}' '${FLEET_OAUTH_TOKEN_PATH}' '${CHANNELS_FAILURES_LOG_PATH}')" && export CLAUDE_CODE_OAUTH_TOKEN="$_plan_token"`
      : `&& export CLAUDE_CODE_OAUTH_TOKEN="$(cat '${FLEET_OAUTH_TOKEN_PATH}')"`,
    '&&', opts.claudePath,
    '--dangerously-skip-permissions',
    '--settings', shSingleQuote(opts.overlayPath),
    ...(opts.model ? ['--model', shSingleQuote(opts.model)] : []),
  ].join(' ')
}

// The main agent's auto-memory dir, shared into the task config dir.
function linkMainMemory(cfg: string): void {
  const mainRoot = mainAgentConfigDirIfSeparate() ?? join(homedir(), '.claude')
  const target = join(projectsDirFor(PROJECT_ROOT, mainRoot), 'memory')
  const ownProject = projectsDirFor(PROJECT_ROOT, cfg)
  const link = join(ownProject, 'memory')
  mkdirSync(ownProject, { recursive: true })
  mkdirSync(target, { recursive: true })
  try {
    if (lstatSync(link).isSymbolicLink() && existsSync(link)) return
    rmSync(link, { recursive: true, force: true })
  } catch { /* absent -> create */ }
  symlinkSync(target, link)
}

let lastLaunchFailureAt = 0
let deliveriesSinceLaunch = 0

export function taskSessionExists(): boolean {
  try {
    execFileSync(tmuxBin(), ['has-session', '-t', `=${TASK_SESSION}`], { timeout: 3000, stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

/** Can a task aimed here be delivered here (now or after a launch)? */
export function taskSessionUsable(now: number = Date.now()): boolean {
  if (taskSessionExists()) return true
  if (now - lastLaunchFailureAt < LAUNCH_BACKOFF_MS) return false
  return resolveTaskSessionAuth().kind !== 'none'
}

export function launchTaskSession(now: number = Date.now()): { ok: true } | { ok: false; error: string } {
  const auth = resolveTaskSessionAuth()
  if (auth.kind === 'none') {
    lastLaunchFailureAt = now
    return { ok: false, error: auth.reason }
  }
  try {
    const cfg = ensureMainTaskConfigDir()
    if (!cfg) throw new Error('task config dir could not be provisioned')
    linkMainMemory(cfg)
    mkdirSync(EMPTY_STATE_DIR, { recursive: true })
    writeFileSync(OVERLAY_PATH, JSON.stringify(taskSessionOverlay(readExtraChannelPluginIds())) + '\n')
    const cmd = buildTaskSessionCmd({
      claudePath: claudeBin(),
      model: readConfiguredMainModel(),
      configDir: cfg,
      overlayPath: OVERLAY_PATH,
      emptyStateDir: EMPTY_STATE_DIR,
      auth,
    })
    execFileSync(tmuxBin(), ['new-session', '-d', '-s', TASK_SESSION, '-c', PROJECT_ROOT, cmd], { timeout: 10000, stdio: ['ignore', 'ignore', 'pipe'] })
    deliveriesSinceLaunch = 0
    logger.info({ session: TASK_SESSION, auth: auth.kind }, 'task-session: launched')
    return { ok: true }
  } catch (err) {
    lastLaunchFailureAt = now
    logger.warn({ err, session: TASK_SESSION }, 'task-session: launch failed, tasks fall back to the main session')
    return { ok: false, error: err instanceof Error ? err.message : String(err) }
  }
}

function sessionCreatedAtMs(): number | null {
  try {
    const out = execFileSync(tmuxBin(), ['display-message', '-p', '-t', `=${TASK_SESSION}:`, '#{session_created}'], { timeout: 3000, encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'] })
    const sec = Number(out.trim())
    return Number.isFinite(sec) && sec > 0 ? sec * 1000 : null
  } catch {
    return null
  }
}

export function shouldRecycleTaskSession(
  now: number,
  createdAtMs: number | null = sessionCreatedAtMs(),
  deliveries: number = deliveriesSinceLaunch,
): boolean {
  if (deliveries >= TASK_SESSION_MAX_DELIVERIES) return true
  return createdAtMs != null && now - createdAtMs >= TASK_SESSION_MAX_AGE_MS
}

/** Kill the (idle) session and start a fresh one. The caller checked idleness. */
export function recycleTaskSession(now: number = Date.now()): { ok: true } | { ok: false; error: string } {
  try {
    execFileSync(tmuxBin(), ['kill-session', '-t', `=${TASK_SESSION}`], { timeout: 5000, stdio: 'ignore' })
  } catch { /* already gone */ }
  logger.info({ session: TASK_SESSION, deliveries: deliveriesSinceLaunch }, 'task-session: recycling for a fresh context')
  return launchTaskSession(now)
}

export function noteTaskSessionDelivery(): void {
  deliveriesSinceLaunch++
}

// Prepended to every prompt delivered here: the session has no channel tool,
// and a task that has something for the owner must hand it to the main session.
export const TASK_SESSION_PREAMBLE =
  `[Feladat-munkamenet (${TASK_SESSION}): itt NINCS Telegram-eszkozod. Ha a gazdanak szolni kell, ` +
  `kuldd a fo-munkamenetnek: bash scripts/agent-msg.sh ${MAIN_AGENT_ID} ${MAIN_AGENT_ID} "..." -- az dont a kuldesrol.]`

export function resetTaskSessionStateForTests(): void {
  lastLaunchFailureAt = 0
  deliveriesSinceLaunch = 0
}
