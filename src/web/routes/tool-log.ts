import { logToolCall, analyzeWorkflowCandidates, getRecentToolCalls, pruneToolCallLog } from '../../db.js'
import { readBody, json, rejectUnknownQueryParams } from '../http-helpers.js'
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
  //   2. Not every agent registers the capture hook, so an agent missing from
  //      the rows may simply not be logged. (Measured 2026-10-06: the last
  //      hour held marveen, cody and roxy rows -- no longer the main agent only.)
  // Rows tagged `[interrupt] ` in input_summary are user stops, not tool errors.
  //
  // #185: until 2026-10-06 only `since` was read. `?agent=roxy`, `?limit=5` and
  // a misspelt `?agnet=` all answered 200 with the same unfiltered rows, so a
  // caller read every agent's calls as one agent's. `agent` (alias `agent_id`)
  // and `limit` now filter, and any other parameter is a 400.
  if (path === '/api/tool-log' && method === 'GET') {
    if (rejectUnknownQueryParams(res, url, ['since', 'agent', 'agent_id', 'limit'])) return true
    const since = parseInt(url.searchParams.get('since') || '3600')
    const agent = url.searchParams.get('agent')
    const agentIdParam = url.searchParams.get('agent_id')
    if (agent !== null && agentIdParam !== null && agent !== agentIdParam) {
      json(res, { error: 'agent and agent_id name different agents; send one of them' }, 400)
      return true
    }
    const agentId = agent ?? agentIdParam ?? undefined
    if (agentId !== undefined && !agentId.trim()) { json(res, { error: 'agent must not be empty' }, 400); return true }
    const rawLimit = url.searchParams.get('limit')
    const limit = rawLimit === null ? undefined : Number(rawLimit)
    if (limit !== undefined && (!Number.isInteger(limit) || limit <= 0)) {
      json(res, { error: 'limit must be a positive integer' }, 400)
      return true
    }
    json(res, getRecentToolCalls(since, { agentId, limit }))
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
