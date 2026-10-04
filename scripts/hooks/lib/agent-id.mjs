// Which agent is this hook running for? (card 44f54620, #95)
//
// WHY: store/egress-blocked.log said WHAT was blocked but never WHO tried it.
// Six agents share one log, and on 2026-10-03 a BLOCKED_BASH line for a Sheets
// URL could only be attributed by asking around. The quarantine lines carried
// agent_type="quarantine-reader", which names the tier, not the parent agent.
//
// Identity comes from where the session was STARTED: CLAUDE_PROJECT_DIR, which
// the harness sets for hooks and which a `cd` does not move. The payload cwd is
// the fallback. Same rule as scripts/hooks/provenance-gate.py derive_agent_id
// (card 494ae005): <install> -> main agent, <install>/agents/<name>/... -> name.
//
// This is a LOG LABEL, not an access decision: an unknown layout yields '?',
// never an error, and nothing is allowed or denied on it.
import { realpathSync } from 'node:fs'
import { sep } from 'node:path'

export function sessionAgentName(payload, repoRoot, env = process.env) {
  try {
    const start = env.CLAUDE_PROJECT_DIR || payload?.cwd || ''
    if (!start) return '?'
    const here = realpathSync(start)
    const root = realpathSync(repoRoot)
    if (here === root) return env.MAIN_AGENT_ID || 'marveen'
    const agentsDir = root + sep + 'agents' + sep
    if (here.startsWith(agentsDir)) {
      const name = here.slice(agentsDir.length).split(sep)[0]
      if (name) return name
    }
    return '?'
  } catch {
    return '?'
  }
}
