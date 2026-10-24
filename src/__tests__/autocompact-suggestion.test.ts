import { describe, it, expect } from 'vitest'
import { detectsAutocompactSuggestion } from '../pane-state.js'

// Captured verbatim from cody's pre-restart snapshot on 2026-10-24 07:05
// (store/context-guard-last-pane-cody.txt); anorak's 2026-10-22 23:07 snapshot
// has the identical tail. The guard's handoff directive waited behind it until
// the hard threshold restarted the agent without a handoff.
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
