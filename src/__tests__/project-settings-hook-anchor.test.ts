// #1305 (ISSUE1305HOOKSCOPE): the tracked .claude/settings.json is the
// AUTHORITATIVE registration surface for the main agent's fleet hooks. The
// scaffold refuses to write them anywhere else (hook-scope-main-refusal
// pins that), so if an entry silently falls out of this file, nothing
// re-adds it -- the hook just stops firing. This anchor makes such a drop
// loud: the full expected set is spelled out, per event.
//
// Portability is part of the contract: every command must resolve through
// $CLAUDE_PROJECT_DIR (the file ships to every install), and the WebFetch
// egress gate must keep its fail-CLOSED preamble -- the user-global variant
// pinned a machine-local node path, which is exactly what broke in the
// owner's own sessions (#1305) and what a "simplifying" edit would
// reintroduce.
import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = join(__dirname, '..', '..')
const SETTINGS_PATH = join(ROOT, '.claude', 'settings.json')

interface HookEntry { matcher?: string; hooks?: Array<{ type?: string; command?: string; timeout?: number }> }
type Hooks = Record<string, HookEntry[]>

const settings = JSON.parse(readFileSync(SETTINGS_PATH, 'utf-8')) as { hooks?: Hooks }
const hooks: Hooks = settings.hooks ?? {}

// The full expected registration: event -> script basenames (order-free).
// A change here is a REVIEWED decision about what runs in the main session,
// never a side effect.
//
// Both channel providers' progress hooks are listed unconditionally. That is
// deliberate: each one is provider-scoped internally and no-ops on the other
// provider's turns, so the cost of the idle set is a process spawn. Only the
// watchdog DAEMON -- which polls on a timer whether or not a turn is in
// flight -- is gated to the active CHANNEL_PROVIDER, by the two
// install-*-progress-hook.sh scripts.
const EXPECTED: Record<string, string[]> = {
  UserPromptSubmit: [
    'ledger-capture.py', 'inbox-drain.py', 'telegram-reply-directive.py',
    'provenance-gate.py', 'staleness-guard.py', 'memory-lookup-nudge.py', 'channel-inbox-drain.py',
    'voice-reply-directive.py', 'telegram_progress.py', 'slack_progress.py',
    'marveen-commands.py',
  ],
  PostToolUse: [
    'ledger-outbound.py', 'tool-log-capture.py',
    'telegram_progress_reply_clear.py', 'slack_progress_reply_clear.py',
    'skill-usage-capture.py',
    // Local addition on this install: global-skill-write-audit.mjs is the
    // AFTER-the-fact, effect-based check on the shared skill tree (it diffs the
    // protected tree against a snapshot on every tool call). It cannot prevent a
    // write -- it runs after the tool -- so it is deliberately a PostToolUse /
    // PostToolUseFailure pair rather than a gate.
    'global-skill-write-audit.mjs',
    'memory-frontmatter-bash-gate.py',
    // The persona guard watches the main agent's own CLAUDE.md/SOUL.md and
    // every agents/*/ pair, and it must fire on EVERY tool call: the first
    // version filtered on the Write/Edit file_path and a python3 heredoc
    // walked straight past it. PostToolUse is the event that cannot be
    // dodged by choosing a different tool.
    'persona-change-notify.py',
    // Local (owner, Telegram msg 7441, 2026-10-11): line-cap-gate.py re-counts
    // the capped files (SKILL.md, hand-written CLAUDE.md) after a Bash call,
    // because a shell write cannot be predicted by the PreToolUse half.
    'line-cap-gate.py',
  ],
  // A failed tool call fires PostToolUseFailure, never PostToolUse
  // (TOOLLOGVAKSIKER921): without this entry tool_call_log cannot hold a 0.
  // global-skill-write-audit.mjs rides both events for the same reason (see
  // PostToolUse below): a write that made the tool call FAIL still changed the
  // protected tree, so the snapshot diff has to run on the failure path too.
  // KAPUEGYUT918: a failed Bash call can still have written a memory file.
  PostToolUseFailure: ['tool-log-capture.py', 'global-skill-write-audit.mjs', 'memory-frontmatter-bash-gate.py'],
  PreToolUse: [
    'outgoing-copy-gate.py', 'email-approval-gate.py',
    'channel-image-resize.sh', 'egress-gate.mjs', 'memory-frontmatter-gate.py',
    'memory-frontmatter-bash-gate.py',
    // Local additions on this install, both BEFORE-the-fact gates (the audit
    // hook above is the after-the-fact half): bash-egress-gate.mjs checks the
    // network destinations a Bash command reaches for, and
    // global-skill-write-gate.mjs stops a write aimed at the shared skill tree.
    'bash-egress-gate.mjs', 'global-skill-write-gate.mjs',
    // Local (owner, Telegram msg 5850, 2026-10-04): the Checked block has to
    // exist BEFORE a Telegram reply leaves, not only at the end of the turn.
    'check-before-send.py',
    // Local (owner, Telegram msg 7441): a Write/Edit that would push a SKILL.md
    // or a hand-written CLAUDE.md past 500 lines is denied before it lands.
    'line-cap-gate.py',
  ],
  // Local addition on this install (reviewed 2026-09-16, v1.38.0 merge):
  // ledger-midturn-capture.py backfills mid-turn inbound rows into the
  // conversation ledger (MIDTURN901). The PostToolUseFailure entry that used to
  // sit here too is upstream's own since v1.39.0 (TOOLLOGVAKSIKER921, above) --
  // the auto-merge left both copies, which is a silent duplicate key in JS and
  // an error only typecheck catches.
  //
  // Local addition on this install (owner's approval, Telegram msg 2369,
  // 2026-09-10; moved into this file 2026-09-26): require-checked.py blocks a
  // turn whose final text carries no "Checked:" block. It used to be
  // registered by an absolute path into ~/.claude/hooks, which the portability
  // test below rightly refused; the script now lives in scripts/hooks like
  // every other registered hook.
  Stop: [
    'marveen-commands.py', 'telegram-reply-guard.py', 'telegram_progress_clear.py', 'slack_progress_clear.py',
    'ledger-midturn-capture.py', 'require-checked.py',
  ],
  SessionStart: ['ledger-replay.py', 'taskstate-replay.py', 'clear-replay.py', 'marveen-commands.py'],
  SessionEnd: ['clear-capture.py'],
}

function commands(event: string): string[] {
  return (hooks[event] ?? []).flatMap((e) => e.hooks ?? []).map((h) => h.command ?? '')
}

function scriptNames(event: string): Set<string> {
  return new Set(
    commands(event)
      .map((c) => c.match(/scripts\/hooks\/([^/\s'"]+?\.(?:py|sh|mjs))/)?.[1] ?? '')
      .filter(Boolean),
  )
}

describe('tracked .claude/settings.json hook anchor (#1305)', () => {
  it('registers exactly the expected script set per event', () => {
    expect(Object.keys(hooks).sort()).toEqual(Object.keys(EXPECTED).sort())
    for (const [event, expected] of Object.entries(EXPECTED)) {
      expect([...scriptNames(event)].sort(), `event ${event}`).toEqual([...new Set(expected)].sort())
    }
  })

  it('every referenced script exists in scripts/hooks/', () => {
    for (const event of Object.keys(EXPECTED)) {
      for (const name of scriptNames(event)) {
        expect(existsSync(join(ROOT, 'scripts', 'hooks', name)), `missing scripts/hooks/${name}`).toBe(true)
      }
    }
  })

  it('every command is portable: $CLAUDE_PROJECT_DIR, no machine-local paths', () => {
    for (const event of Object.keys(hooks)) {
      for (const cmd of commands(event)) {
        expect(cmd, `event ${event}`).toContain('$CLAUDE_PROJECT_DIR')
        expect(cmd).not.toMatch(/\/(Users|home|opt|var)\//)
      }
    }
  })

  it('the WebFetch egress gate keeps its fail-closed preamble', () => {
    const webfetch = (hooks.PreToolUse ?? []).filter((e) => e.matcher === 'WebFetch')
    expect(webfetch).toHaveLength(1)
    const cmd = webfetch[0].hooks?.[0]?.command ?? ''
    // No node -> BLOCK (exit 2), never fail-open silence.
    expect(cmd).toMatch(/command -v node[^;]*\|\|\s*\{[^}]*exit 2/)
    expect(cmd).toContain('egress-gate.mjs')
  })
})
