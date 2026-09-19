// Redaction for the gate audit logs.
//
// SECRETLOG901 (Forgacs, 2026-09-01, raised as a hunch and labelled as one).
// The skill gate records the whole command in `target=` so a reviewer can see
// what was attempted. That is right for an audit trail and wrong for a secret:
// if a command ever mentions the protected folder AND carries a typed-in
// credential, the credential lands in a file people read casually.
//
// He checked the day's existing lines and found none -- the fleet's own pattern
// is `$(cat store/.dashboard-token)`, which the hook sees BEFORE the shell
// expands it, so only the call text is logged, never the value. The exposure
// needs someone to paste a literal, which is already a bad habit. That makes
// this cheap insurance rather than a fix for a live leak, and it is written up
// that way on purpose.
//
// Over-redaction is the safe direction here: a log entry that hides one token
// too many is still readable; one that reveals a real key is not.

const PATTERNS = [
  // Authorization headers of every common shape.
  [/\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}/gi, '$1 <redacted>'],
  // Query parameters that name themselves.
  [/([?&](?:api[_-]?key|access[_-]?token|token|secret|password|passwd|pwd|sig|signature)=)[^&\s'"]+/gi, '$1<redacted>'],
  // Assignments in a command: KEY=..., --api-key ..., -u user:pass
  [/\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*=)[^\s'"]+/g, '$1<redacted>'],
  [/(--(?:api[_-]?key|token|secret|password)[= ])[^\s'"]+/gi, '$1<redacted>'],
  [/(-u\s+)[^\s'"]+:[^\s'"]+/g, '$1<redacted>'],
  // A long opaque run is a credential far more often than it is anything else.
  [/\b[A-Za-z0-9_-]{40,}\b/g, '<redacted>'],
]

/** Return `text` with anything that looks like a credential replaced. */
export function redactSecrets(text) {
  let out = String(text ?? '')
  for (const [re, to] of PATTERNS) out = out.replace(re, to)
  return out
}

/**
 * One event, one line: collapse whitespace, neutralise quotes, redact, truncate.
 * A command carries newlines, and pasting one raw turns an audit log into
 * something no grep can read -- measured on this log's own first day.
 */
export function logSafe(text, max = 200) {
  return redactSecrets(String(text ?? '').replace(/\s+/g, ' ').replace(/"/g, "'")).slice(0, max)
}
