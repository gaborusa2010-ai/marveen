import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  TASK_SESSION,
  TASK_SESSION_MAX_AGE_MS,
  TASK_SESSION_MAX_DELIVERIES,
  buildTaskSessionCmd,
  shouldRecycleTaskSession,
  taskSessionOverlay,
} from '../web/task-session.js'
import { CHANNEL_PLUGIN_IDS } from '../web/plugin-ids.js'

// #638 rebuild 2: the main agent's channel-less task session. The isolation
// gates below were measured on a live host before this was written (card #638
// comment 2129): without any one of them a second session in the repo cwd can
// start a poller and take the bot from the main session.

const base = {
  claudePath: '/usr/bin/claude',
  model: 'claude-opus-5-5[1m]',
  configDir: '/repo/.tasks-config',
  overlayPath: '/repo/store/.task-session.settings.json',
  emptyStateDir: '/repo/store/.task-session-no-channel',
}

describe('buildTaskSessionCmd', () => {
  const cmd = buildTaskSessionCmd({ ...base, auth: { kind: 'fleet' } })

  it('drops every bot token', () => {
    for (const v of ['TELEGRAM_BOT_TOKEN', 'SLACK_BOT_TOKEN', 'SLACK_APP_TOKEN', 'DISCORD_BOT_TOKEN']) {
      expect(cmd).toMatch(new RegExp(`unset [^&]*\\b${v}\\b`))
    }
  })

  it('points every provider state dir at the empty dir, never unsets it', () => {
    for (const p of Object.keys(CHANNEL_PLUGIN_IDS)) {
      expect(cmd).toContain(`${p.toUpperCase()}_STATE_DIR='/repo/store/.task-session-no-channel'`)
      expect(cmd).not.toMatch(new RegExp(`unset [^&]*${p.toUpperCase()}_STATE_DIR`))
    }
  })

  it('carries the plugin-off overlay and no --channels flag', () => {
    expect(cmd).toContain("--settings '/repo/store/.task-session.settings.json'")
    expect(cmd).not.toContain('--channels')
  })

  it('runs on its own config dir with the fleet token read at launch', () => {
    expect(cmd).toContain("export CLAUDE_CONFIG_DIR='/repo/.tasks-config'")
    expect(cmd).toMatch(/CLAUDE_CODE_OAUTH_TOKEN="\$\(cat '[^']+\.claude-oauth-token'\)"/)
  })

  it('quotes the model id', () => {
    expect(cmd).toContain("--model 'claude-opus-5-5[1m]'")
  })

  it('uses the plan token for a token-mode plan', () => {
    const plan = buildTaskSessionCmd({ ...base, auth: { kind: 'plan', tokenSecretId: 'plan-b' } })
    expect(plan).toContain("'plan-b'")
    expect(plan).toContain('export CLAUDE_CODE_OAUTH_TOKEN="$_plan_token"')
  })
})

describe('taskSessionOverlay', () => {
  it('switches off every channel plugin and every co-listen extra', () => {
    const o = taskSessionOverlay(['telegram-reaction@marveen-telegram-reaction'])
    for (const id of Object.values(CHANNEL_PLUGIN_IDS)) expect(o.enabledPlugins[id]).toBe(false)
    expect(o.enabledPlugins['telegram-reaction@marveen-telegram-reaction']).toBe(false)
    expect(Object.values(o.enabledPlugins).every((v) => v === false)).toBe(true)
  })
})

describe('shouldRecycleTaskSession', () => {
  const now = 10 * TASK_SESSION_MAX_AGE_MS
  it('keeps a young session with few deliveries', () => {
    expect(shouldRecycleTaskSession(now, now - 60_000, 1)).toBe(false)
  })
  it('recycles by age', () => {
    expect(shouldRecycleTaskSession(now, now - TASK_SESSION_MAX_AGE_MS, 0)).toBe(true)
  })
  it('recycles by delivery count', () => {
    expect(shouldRecycleTaskSession(now, now, TASK_SESSION_MAX_DELIVERIES)).toBe(true)
  })
  it('an unknown creation time alone never recycles', () => {
    expect(shouldRecycleTaskSession(now, null, 0)).toBe(false)
  })
})

describe('schedule-runner wiring', () => {
  const SRC = readFileSync(join(__dirname, '../web/schedule-runner.ts'), 'utf-8')
  const resolve = SRC.slice(SRC.indexOf('export function resolveTaskTarget'), SRC.indexOf('async function attemptFireTask'))
  const fire = SRC.slice(SRC.indexOf('async function attemptFireTask'))

  it('the session is not the channels session', () => {
    expect(TASK_SESSION).not.toMatch(/-channels$/)
    expect(TASK_SESSION).not.toMatch(/^agent-/)
  })

  it('falls back to the main session when the task session is unusable', () => {
    expect(resolve).toMatch(/session === TASK_SESSION && !taskSessionUsable\(\)/)
    expect(resolve).toMatch(/session = MAIN_CHANNELS_SESSION/)
  })

  it('launches a missing task session BEFORE the main-agent "starting" branch', () => {
    const missing = fire.slice(fire.indexOf('if (!sessionExistsOnHost(host, session))'))
    expect(missing.indexOf('launchTaskSession(')).toBeGreaterThan(-1)
    expect(missing.indexOf('launchTaskSession(')).toBeLessThan(missing.indexOf('if (isMainAgent)'))
  })

  it('recycles only after the readiness gate', () => {
    expect(fire.indexOf('shouldRecycleTaskSession(')).toBeGreaterThan(fire.indexOf('isSessionReadyForPrompt('))
  })

  it('looks for the task turn under the task config dir, not the main roots', () => {
    expect(fire).toMatch(/taskInflightMap\.set\([^\n]*\n(\s*\/\/[^\n]*\n)*\s*if \(session === TASK_SESSION\) inflightEntry\.configDirs = \[taskConfigDir\(\)\]/)
  })
})
