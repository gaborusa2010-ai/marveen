// The ONE transformation a prompt undergoes between the caller and the tmux
// pane (DIREKTIVASORTORES920, 2026-09-20). sendPromptToSession() types the
// text into the pane as literal send-keys chunks, and a newline there would
// submit the prompt early, so every line break becomes a single space FIRST.
//
// Kept in its own tiny module because a second reader depends on knowing it
// exactly: scripts/hooks/provenance-gate.py verifies a system directive by
// comparing the pane-delivered body with the queue row, and it must apply this
// same mapping to the row. Measured 2026-09-20: the gate expected the caller's
// `header + "\n" + body` shape, the pane received `header + " " + body`, and
// every real directive since #1411 (3 of 3) was flagged forged over that one
// character. The gate's test was green throughout because it measured the
// pre-delivery artefact. If this mapping ever changes, change it HERE, and the
// hook test that feeds the gate through this function goes red with it.
//
// Every OTHER control character (tab, \x01, \x02, DEL, ...) becomes one space
// too. Typed with send-keys -l they reach the TUI as KEYS, not text: Ctrl-A
// jumps to the line start, Ctrl-B moves back one character, Tab triggers
// completion. Measured 2026-10-11: an idea description extracted from a PDF
// carried \x01/\x02 and tabs, the worker's input box rendered the prompt
// scrambled, so the post-send retry never recognised it as ours and the
// Enter was never re-pressed (idea c2232aac, 20 minute timeout).
export function paneOneLine(text: string): string {
  return text.replace(/\r?\n/g, ' ').replace(/[\x00-\x1f\x7f]/g, ' ')
}
