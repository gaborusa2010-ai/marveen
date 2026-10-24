import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { detectsAutocompactSuggestion } from '../pane-state.js'

// Captured verbatim from cody's pre-restart snapshot on 2026-10-24 07:05
// (store/context-guard-last-pane-cody.txt); anorak's 2026-10-22 23:07 snapshot
// has the identical tail. Both panes sat on it after a finished turn until the
// hard threshold restarted the agent.
const REAL_PANE = [
  " We recommend compacting earlier",
  "",
  "   Each message re-sends the whole conversation, so long conversations use up",
  "   your plan's usage limits faster. With your current settings, Claude Code",
  "   autocompacts (replaces older messages with a summary) at 1m tokens",
  "   (default via auto setting). Compacting earlier can reduce usage, but it",
  "   happens more often, and some details can be lost each time.",
  "",
  "   Want to autocompact at 400k tokens instead? Your model doesn't change.",
  "   Switch back any time with /autocompact.",
  "",
  "   ❯ Autocompact at ‹ 400k › tokens",
  "     Keep auto (1m tokens)",
  "",
  "   ↑/↓ choose · ←/→ change size · enter confirm · esc not now",
  "",
  "",
].join('\n')

describe('detectsAutocompactSuggestion', () => {
  it('detects the real dialog', () => {
    expect(detectsAutocompactSuggestion(REAL_PANE)).toBe(true)
  })

  it('ignores empty and blank panes', () => {
    expect(detectsAutocompactSuggestion('')).toBe(false)
    expect(detectsAutocompactSuggestion('\n\n  \n')).toBe(false)
  })

  it('ignores a quote of the dialog in scrollback with the prompt box below it', () => {
    const quoted = REAL_PANE + '\n' + [
      '',
      '✻ Cooked for 3s · done 7:10 AM',
      '',
      '──────────────────────────────────────────────────────────────── Cody ─',
      '❯ ',
      '────────────────────────────────────────────────────────────────────────',
      '  ⏵⏵ bypass permissions on (shift+tab to cycle)',
    ].join('\n')
    expect(detectsAutocompactSuggestion(quoted)).toBe(false)
  })

  it('needs the headline above the footer, not the footer alone', () => {
    expect(detectsAutocompactSuggestion('some text\n  ↑/↓ choose · enter confirm · esc not now')).toBe(false)
  })

  it('ignores a headline that is far above the footer', () => {
    const far = ['We recommend compacting earlier', ...Array(30).fill('filler'), '  enter confirm · esc not now'].join('\n')
    expect(detectsAutocompactSuggestion(far)).toBe(false)
  })
})

// Cody's review of #642: the dialog comes up when a turn ENDS, so it can appear
// during sendPromptToSession's idle wait, after the early dismiss. The
// busy-timeout 'send' would then type into it and its Enter confirms 400k.
// The dismiss must therefore also run inside emitToPane, before any keystroke.
describe('emitToPane dismisses the suggestion before typing', () => {
  const AGENT_PROCESS = readFileSync(join(__dirname, '../web/agent-process.ts'), 'utf-8')

  it('calls the dismiss in emitToPane before the first literal chunk', () => {
    const start = AGENT_PROCESS.indexOf('const emitToPane = async (')
    expect(start).toBeGreaterThan(0)
    const body = AGENT_PROCESS.slice(start)
    const dismiss = body.indexOf('await dismissAutocompactSuggestionIfPresent(session, host)')
    const firstChunk = body.indexOf("'-l', chunk")
    expect(dismiss).toBeGreaterThan(0)
    expect(firstChunk).toBeGreaterThan(dismiss)
  })
})
