import { logToolCall, analyzeWorkflowCandidates, getRecentToolCalls, pruneToolCallLog } from '../../db.js'
import { readBody, json } from '../http-helpers.js'
import type { RouteContext } from './types.js'

export async function tryHandleToolLog(ctx: RouteContext): Promise<boolean> {
  const { req, res, path, method, url } = ctx

  // POST /api/tool-log -- log a tool call (from PostToolUse hook)
  if (path === '/api/tool-log' && method === 'POST') {
    const body = await readBody(req)
    const data = JSON.parse(body.toString()) as {
      session_id: string
      tool_name: string
      input_summary?: string
      success?: boolean
      agent_id?: string
      trace_id?: string
      duration_ms?: number
    }
    if (!data.session_id || !data.tool_name) { json(res, { error: 'session_id and tool_name required' }, 400); return true }
    logToolCall(data.session_id, data.tool_name, data.input_summary ?? null, data.success !== false, data.agent_id ?? null, data.trace_id ?? null, data.duration_ms ?? null)
    json(res, { ok: true })
    return true
  }

  // GET /api/tool-log -- recent tool calls
  //
  // WHAT `success` DOES AND DOES NOT MEASURE (measured 2026-09-06, card 93d5d846).
  // Until today the column was all 1s across 691 rows, because a tool call that
  // errored never reached the PostToolUse hook. The PostToolUseFailure event now
  // feeds the failures in, so 0 is reachable -- verified live with a bare
  // `exit 7` (Bash) and a missing-file Read.
  //
  // Two gaps remain, and they both bias an error rate computed from this table
  // DOWNWARD, so do not present one as a health figure without saying so:
  //   1. A call refused by a PreToolUse gate produces NO row at all -- not a
  //      failure row, nothing. Measured the same afternoon: an egress-gate block
  //      landed in store/egress-blocked.log and left this table untouched.
  //   2. Only the main agent registers the capture hook; sub-agent tool calls,
  //      successes included, are absent.
  // Rows tagged `[interrupt] ` in input_summary are user stops, not tool errors.
  if (path === '/api/tool-log' && method === 'GET') {
    const since = parseInt(url.searchParams.get('since') || '3600')
    json(res, getRecentToolCalls(since))
    return true
  }

  // GET /api/tool-log/analyze -- workflow candidates
  if (path === '/api/tool-log/analyze' && method === 'GET') {
    const since = parseInt(url.searchParams.get('since') || '3600')
    const minCalls = parseInt(url.searchParams.get('min_calls') || '5')
    const gapSecs = parseInt(url.searchParams.get('gap') || '300')
    const candidates = analyzeWorkflowCandidates(since, minCalls, gapSecs)
    // Return summarized form (without full tool_calls array to keep response small)
    const summary = candidates.map(c => ({
      session_id: c.session_id,
      tool_count: c.tool_calls.length,
      duration_minutes: c.duration_minutes,
      start_ts: c.start_ts,
      end_ts: c.end_ts,
      tools: [...new Set(c.tool_calls.map(t => t.tool_name))],
      steps_preview: c.tool_calls.slice(0, 10).map(t => ({
        tool: t.tool_name,
        description: t.input_summary || t.tool_name,
      })),
    }))
    json(res, summary)
    return true
  }

  // POST /api/tool-log/prune -- cleanup old entries
  if (path === '/api/tool-log/prune' && method === 'POST') {
    const body = await readBody(req)
    const data = JSON.parse(body.toString()) as { older_than_secs?: number }
    pruneToolCallLog(data.older_than_secs ?? 86400)
    json(res, { ok: true })
    return true
  }

  return false
}
