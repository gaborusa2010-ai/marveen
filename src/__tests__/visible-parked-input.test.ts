import { describe, it, expect } from 'vitest'
import { visibleParkedInput } from '../pane-state.js'
import { matchesInjectedPrompt } from '../web/injected-prompt-registry.js'

const SEP = '─'.repeat(80)
const FOOTER = '  ⏵⏵ bypass permissions on (shift+tab to cycle)'

// Captured 2026-10-11 on the 80x24 marveen-worker pane (Claude Code 2.1.293):
// the box is height-capped, the ❯ glyph is re-drawn on the first VISIBLE row,
// and the head of the prompt is scrolled out of the box.
const CAPPED = [
  '  Get to finished work sooner with Opus 5.5. Update Claude Code to try it.',
  '❯ /clear',
  SEP,
  '❯  kiron, margo, merlin, prospero, roxy  --- OUTPUT INSTRUCTIONS (delivery',
  '  mechanism, not part of the task): 1. Write your COMPLETE response -- and',
  '  nothing else, no commentary, no code fences around it unless the task itself',
  '  asks -- to this exact file using the Write tool:',
  SEP,
  FOOTER,
].join('\n')

const SENT = 'Break this card down. Available team members: cody, kiron, margo, merlin, prospero, roxy  --- OUTPUT INSTRUCTIONS (delivery mechanism, not part of the task): 1. Write your COMPLETE response -- and nothing else, no commentary, no code fences around it unless the task itself asks -- to this exact file using the Write tool:'

describe('visibleParkedInput', () => {
  it('returns the visible tail of a height-capped box, and it matches the sent prompt', () => {
    const tail = visibleParkedInput(CAPPED)
    expect(tail).toMatch(/^kiron, margo/)
    expect(matchesInjectedPrompt(tail, { text: SENT, at: 0 })).toBe(true)
  })

  it('does not match someone else\'s parked text', () => {
    const other = CAPPED.replace('kiron, margo, merlin', 'a human draft here')
    expect(matchesInjectedPrompt(visibleParkedInput(other), { text: SENT, at: 0 })).toBe(false)
  })

  it('is null for an empty box', () => {
    expect(visibleParkedInput(['❯ /clear', SEP, '❯ ', SEP, FOOTER].join('\n'))).toBeNull()
  })

  it('is null without an idle footer', () => {
    expect(visibleParkedInput(CAPPED.replace(FOOTER, ''))).toBeNull()
  })
  it('drops the TUI overflow marker so the tail still matches', () => {
    const marked = CAPPED.replace('the Write tool:\n', 'the Write tool:+;=\n')
    const tail = visibleParkedInput(marked)
    expect(tail).toMatch(/the Write tool:$/)
    expect(matchesInjectedPrompt(tail, { text: SENT, at: 0 })).toBe(true)
  })
})
