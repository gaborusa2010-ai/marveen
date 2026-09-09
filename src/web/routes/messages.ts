import {
  createAgentMessage, getPendingMessages, listAgentMessages,
  getAgentConversation, getAgentConversationThreads,
  getKanbanSeqByIdPrefix,
  markMessageDone, markMessageFailed, getAgentMessage,
  closeOtelSpan,
  getPendingBacklogByAgent,
  COMPLETION_REPORT_PREFIX,
  type AgentMessage,
} from '../../db.js'
import { logger } from '../../logger.js'
import { COORDINATOR_AGENT_ID } from '../../channel-coordinator/ingest.js'
import { sanitizeAgentIdent } from '../../prompt-safety.js'
import { isKnownAgent } from '../agent-config.js'
import { OWNER_NAME, SYSTEM_SENDER_IDS, parseSystemSenderIds } from '../../config.js'
import { readBody, json, jsonMaybeGzip, rejectUnknownQueryParams } from '../http-helpers.js'
import { normalizeKanbanRefs } from '../kanban-ref-normalize.js'
import { parseQualifiedId, formatQualifiedId } from '../federation/address.js'
import { getFederationConfig } from '../federation/config.js'
import { DEFAULT_STALE_CUTOFF_MS } from '../../context-restart-gate.js'
import type { RouteContext } from './types.js'

// Should closing a message produce a reverse "[Eredmény]" notification to its sender?
//
// Exported and tested directly, rather than inlined in the PUT handler: the previous
// tests re-implemented this condition inside the test file, so they passed no matter
// what the route actually did.
//
// Three senders get no notification:
//   1. self-messages -- the sender already knows;
//   2. senders that are not addressable agents. `system` posts the [session-stuck] and
//      [handoff-failure] notices but owns no tmux session, so a reply to it can never be
//      delivered: it fails, the retry window expires, and the resulting [handoff-failure]
//      wakes the main agent -- which, once closed, produced the next one. Measured on the
//      Acrobot install 2026-08-17: 44 of the last 200 rows were `-> system`, all failed.
//      `isKnownAgent` is the right test here because it accepts MAIN_AGENT_ID as well as
//      the sub-agent directories; a plain registry lookup would have suppressed every
//      notification back to the MAIN agent, which is the case this feature exists for.
//   3. contents that are themselves completion reports -- breaks ping-pong chains.
//   4. messages already older than the staleness window -- see below.
//
// RULE 4, measured 2026-09-01 (Roxy, Forgacs, Cody, all independently): closing
// a BACKLOG row is not the same event as finishing a freshly delegated task, and
// notifying for it is worse than useless. A closure creates a new 'pending' row
// back to the sender, which wakes that agent for a three-word receipt -- and the
// new row then sits unclosed itself. Measured on 37 closures: the fleet's
// unclosed rows went UP, 405 -> 425, and one agent was woken ten times in the
// same second. The cleanup produced more mess than it removed.
//
// The cutoff is the one the context-restart gate already uses to call dispatched
// work stale (DEFAULT_STALE_CUTOFF_MS, 2h): past it the system has already
// stopped treating the delegation as live, so the delegator is not waiting for
// this answer. Inside it, the report is real information and still goes out.
export function shouldNotifyDelegator(
  fromAgent: string,
  toAgent: string,
  content: string,
  createdAtEpoch?: number,
  nowMs: number = Date.now(),
): boolean {
  if (fromAgent === toAgent) return false
  if (!isKnownAgent(fromAgent)) return false
  if (content.startsWith(COMPLETION_REPORT_PREFIX)) return false
  if (createdAtEpoch !== undefined && Number.isFinite(createdAtEpoch)
      && nowMs - createdAtEpoch * 1000 > DEFAULT_STALE_CUTOFF_MS) return false
  return true
}

// Frozen at module load, like the config constant it derives from.
const SYSTEM_SENDERS = parseSystemSenderIds(SYSTEM_SENDER_IDS, sanitizeAgentIdent)

export async function tryHandleMessages(ctx: RouteContext): Promise<boolean> {
  const { req, res, path, method, url } = ctx

  if (path === '/api/messages' && method === 'POST') {
    const body = await readBody(req)
    const { from, to, content, origin_note } = JSON.parse(body.toString()) as
      { from: string; to: string; content: string; origin_note?: string }
    if (!from?.trim() || !to?.trim() || !content?.trim()) {
      json(res, { error: 'from, to, and content are required' }, 400)
      return true
    }
    // Security: the channel-coordinator id grants channel-inbound delivery
    // (verbatim <channel> + reply-expected framing) in the message-router. The
    // ONLY legitimate writer of that id is the in-process coordinator, which
    // inserts directly into the DB -- it never POSTs here. The dashboard token
    // is readable by every sub-agent, so without this guard any sub-agent could
    // forge a reply-expected message addressed at the main agent. Reject it.
    //
    // CRITICAL: normalize with the EXACT function the router matches on
    // (sanitizeAgentIdent), NOT from.trim(). The router does
    // CHANNEL_COORDINATOR_AGENTS.has(sanitizeAgentIdent(from)), and
    // sanitizeAgentIdent STRIPS [^a-zA-Z0-9_-] rather than trimming. A bypass
    // like from="@telegram-coordinator" / "telegram-coordinator." survives
    // .trim() (!= the constant) yet sanitizes to "telegram-coordinator" in the
    // router -> channel-inbound with an attacker-controlled body. Matching the
    // router's normalization here closes that asymmetry.
    if (sanitizeAgentIdent(from) === COORDINATOR_AGENT_ID) {
      logger.warn({ from: from.trim(), to: to.trim() }, 'Rejected /api/messages POST forging channel-coordinator id')
      json(res, { error: 'from is reserved for the in-process channel coordinator' }, 403)
      return true
    }
    // Federation spoof guard: a slash-qualified from ("teodor/teodor") is the
    // provenance mark of a REMOTE sender and may only ever be written by the
    // token-authenticated /api/federation/inbox. Accepting it here would let
    // any dashboard-token holder (i.e. every local sub-agent) impersonate a
    // federation peer toward another local agent.
    if (from.includes('/')) {
      logger.warn({ from: from.trim(), to: to.trim() }, 'Rejected /api/messages POST with qualified from (federation impersonation guard)')
      json(res, { error: 'from must be a local agent id without "/" -- federated senders are only accepted via /api/federation/inbox' }, 403)
      return true
    }
    // From-authentication: accept messages only from registered fleet agents.
    // The shared Bearer token is readable by any sub-agent, so without this
    // check any process with the token could inject messages as an arbitrary
    // sender ("from": "zack" from an external attacker who obtained the token).
    // Server-side validation: the `from` claim must match a known agent on the
    // filesystem (agents/<id>/ directory, or MAIN_AGENT_ID). This is not
    // impersonation-proof between fleet agents (they share the same token) but
    // it closes the "unknown sender" injection path without per-agent secrets.
    //
    // The human OWNER is a legitimate sender too: the dashboard "Messages" page
    // composes with from=OWNER_NAME (resolveOwnerName -> the owner assignee), so
    // without this exemption the operator's own dashboard messages 403 with
    // "unknown agent". The owner is not a fleet agent (no agents/<id>/ dir), so
    // isKnownAgent alone rejects it. Match on the router's normalization to stay
    // symmetric with the other guards above.
    //
    // Neighbouring SYSTEMS are legitimate senders too, on the same reasoning as
    // the owner: they are token-authenticated and only notify an agent, but they
    // have no agents/<id>/ directory. Opt-in via SYSTEM_SENDER_IDS in .env
    // (empty by default). Without this, such a system silently loses its push
    // channel the moment this guard ships -- observed here: an external case
    // manager pushed 4182 messages, then every call 403'd for nine days while
    // its fail-soft caller logged nothing.
    const isOwnerSender = sanitizeAgentIdent(from) === sanitizeAgentIdent(OWNER_NAME)
    const isSystemSender = SYSTEM_SENDERS.has(sanitizeAgentIdent(from))
    if (!isOwnerSender && !isSystemSender && !isKnownAgent(sanitizeAgentIdent(from))) {
      logger.warn({ from: from.trim(), to: to.trim() }, 'Rejected /api/messages POST from unregistered agent')
      json(res, { error: `unknown agent '${from.trim()}' -- from must be a registered fleet agent id` }, 403)
      return true
    }
    // Qualified to ("peer/agent"): validate at creation time so the sender
    // gets an actionable error NOW instead of a silent 1h abandon. Local
    // (slash-free) recipients are untouched.
    let storedTo = to.trim()
    if (storedTo.includes('/')) {
      const target = parseQualifiedId(storedTo)
      if (!target) {
        json(res, { error: 'Invalid federated address in to (expected "<system>/<agent>")' }, 400)
        return true
      }
      const cfg = getFederationConfig()
      if (!cfg.enabled) {
        json(res, { error: 'Federation is disabled on this system' }, 400)
        return true
      }
      // System ids are case-insensitive (stored lowercase in the config).
      // Normalize the STORED prefix too: the per-peer purge SQL and the
      // bridge's peer lookup key on it, and thread grouping in the UI should
      // not split 'Teodor/x' from 'teodor/x'. The agent segment is the
      // PEER's namespace -- leave its case alone.
      const targetSystem = target.system.toLowerCase()
      if (targetSystem === cfg.systemId) {
        json(res, { error: `'${target.system}' is this system -- address the agent locally as '${target.agent}'` }, 400)
        return true
      }
      if (!cfg.peers.some((p) => p.id === targetSystem)) {
        json(res, { error: `Unknown federation peer '${target.system}'` }, 400)
        return true
      }
      storedTo = formatQualifiedId(targetSystem, target.agent)
    } else if (storedTo.includes(':')) {
      // A colon-form 'to' ("federation:teodor:teodor", copied from an
      // <untrusted source> attribute) is NOT a valid address: it has no '/',
      // so it would be treated as a LOCAL recipient, never match a session,
      // and silently sit pending until the 1h abandon window. Reject it now
      // with the correct form. Safe: sanitizeAgentIdent strips ':', so no
      // legitimate local agent id can contain one, and the channel
      // coordinator inserts directly into the DB, bypassing this endpoint.
      json(res, { error: 'Invalid recipient: use "<system>/<agent>" (slash) for a federated address, not the "federation:x:y" source form' }, 400)
      return true
    }
    // Code-side enforcement of the kanban-ref convention: rewrite any
    // `#<hex8>` token that maps to a real kanban_cards row into its
    // human-facing `#<seq>` form before persistence, so the dashboard and
    // every downstream consumer sees the canonical reference even when a
    // sub-agent forgets the CLAUDE.md rule (#75 Cuzcoo dispatch).
    const normalizedContent = normalizeKanbanRefs(content.trim(), getKanbanSeqByIdPrefix)
    // Card 06f062e4: optional attributability tag, self-declared like `from`
    // itself -- capped short so it stays a label, not a second content field.
    const trimmedOriginNote = origin_note?.trim().slice(0, 120) || null
    const msg = createAgentMessage(from.trim(), storedTo, normalizedContent, trimmedOriginNote)
    logger.info({ id: msg.id, from: msg.from_agent, to: msg.to_agent, originNote: msg.origin_note }, 'Agent message created')
    json(res, msg)
    return true
  }

  // Sidebar threads: one row per conversation peer (system agents excluded),
  // each with its count + most-recent message, recency computed per-peer.
  if (path === '/api/messages/threads' && method === 'GET') {
    json(res, getAgentConversationThreads())
    return true
  }

  // Backlog per agent: count + how long the oldest has been waiting. Cheap
  // enough to curl on a schedule; the point is that a growing queue behind a
  // busy agent becomes visible BEFORE someone mistakes it for lost messages.
  if (path === '/api/messages/backlog' && method === 'GET') {
    json(res, getPendingBacklogByAgent())
    return true
  }

  if (path === '/api/messages' && method === 'GET') {
    // An UNKNOWN filter param used to fall through to the global list: a typo,
    // or the plausible-but-wrong `agent_id`, silently returned the fleet's last
    // N messages instead of one agent's mailbox. Not an error, not an empty
    // list -- MORE than asked for, which is the expensive direction to be wrong
    // in: a caller acting in good faith on that answer reads other agents'
    // traffic as its own. Fail loudly instead.
    if (rejectUnknownQueryParams(res, url, ['agent', 'status', 'limit', 'before'],
      'the mailbox filter is "agent"; "agent_id" and "to" are not read')) return true
    const agent = url.searchParams.get('agent') || ''
    const status = url.searchParams.get('status') || ''
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '50', 10), 200)
    const beforeRaw = url.searchParams.get('before')
    const before = beforeRaw !== null ? parseInt(beforeRaw, 10) : undefined

    // A KNOWN param name with an UNKNOWN value was the same lie one level down:
    // only 'pending' was ever honoured, so ?status=unread (a status this schema
    // has never had) fell through to the unfiltered list and returned a full
    // 200. An agent checking its inbox read that as "dozens waiting" while the
    // real pending count was zero. Values come from the agent_messages CHECK
    // constraint; anything else is a caller mistake, not an empty result.
    // (Roxy, 2026-09-01.)
    const VALID_STATUSES = ['pending', 'delivered', 'done', 'failed'] as const
    if (status && !(VALID_STATUSES as readonly string[]).includes(status)) {
      // The hint is per-VALUE, not one canned line. A caller who typed
      // "zoldbeka" and is told "there is no unread state" spends a moment
      // believing it typed something it did not -- and the whole point of the
      // 400 is to stop the guessing, not to relocate it. (Roxy, 2026-09-01.)
      const INBOX_WORDS = new Set(['unread', 'read', 'new', 'waiting', 'open'])
      json(res, {
        error: 'unknown status value',
        unknown: status,
        known: [...VALID_STATUSES],
        hint: INBOX_WORDS.has(status)
          ? `this mailbox has no "${status}" state; a message waiting to be picked up is "pending"`
          : `"${status}" is not a message state; pick one of the listed values`,
      }, 400)
      return true
    }
    const statusFilter = status ? (status as AgentMessage['status']) : undefined

    let messages: AgentMessage[]
    if (status === 'pending' && agent) {
      messages = getPendingMessages(agent)
    } else if (status === 'pending') {
      messages = getPendingMessages()
    } else if (agent) {
      // SQL-filtered to THIS agent's last N (+ before-cursor pagination), not
      // global-last-N-then-JS-filter which starved rarely-active threads.
      messages = getAgentConversation(agent, limit, Number.isFinite(before as number) ? before : undefined, statusFilter)
    } else {
      messages = listAgentMessages(limit, statusFilter, Number.isFinite(before as number) ? before : undefined)
    }

    // A CAP IS NOT A BUG; ITS SILENCE IS. (Card c1fa4c60, 2026-09-04.)
    //
    // `limit` is clamped to 200 above, and ?limit=1000 therefore answers 200
    // rows with HTTP 200 and no hint that anything was left out. Anorak counted
    // a day's traffic that way on 2026-09-03 and lost 18 of 218 rows -- 8.3%,
    // every one of them from before 08:12, because the cap always eats the
    // OLDEST end. A short list looks exactly like a quiet day.
    //
    // The body stays a bare array: every caller in the fleet indexes it
    // directly, and wrapping it in an envelope to add one flag would break all
    // of them to fix none. Headers are additive, so the answer says so on the
    // side. `X-Next-Before` is the id to page from -- the same cursor the
    // per-agent path has always honoured, and now this one too.
    //
    // The test is `length >= limit`, which flags a page that is full but happens
    // to be the last one. That false positive costs a caller one extra request
    // returning an empty array; the opposite error costs it a silently short
    // answer it will never question. Counting the remaining rows to be exact
    // would mean a second COUNT query on every list call to remove a harmless
    // extra fetch -- the wrong trade for the wrong direction.
    if (messages.length >= limit && messages.length > 0) {
      const oldest = messages[messages.length - 1]
      res.setHeader('X-Result-Truncated', 'true')
      res.setHeader('X-Result-Limit', String(limit))
      res.setHeader('X-Next-Before', String(oldest.id))
    }
    jsonMaybeGzip(req, res, messages)
    return true
  }

  // One message by id. Added 2026-09-02, and the reason is worth keeping: the
  // truncated-receipt marker written that morning ended with
  // "GET /api/messages/<id>" -- an endpoint that did not exist. Cody measured it
  // two ways (a live call returned 404, and the id route was bound to PUT only),
  // so a fix for a SILENT failure was pointing readers at a closed door.
  // The honest repair was to make the sentence true, not to soften it: the
  // receipt says where the rest is, so the rest has to be there.
  const msgGetMatch = path.match(/^\/api\/messages\/(\d+)$/)
  if (msgGetMatch && method === 'GET') {
    const one = getAgentMessage(parseInt(msgGetMatch[1], 10))
    if (!one) { json(res, { error: 'message not found' }, 404); return true }
    json(res, one)
    return true
  }

  const msgUpdateMatch = path.match(/^\/api\/messages\/(\d+)$/)
  if (msgUpdateMatch && method === 'PUT') {
    const id = parseInt(msgUpdateMatch[1], 10)
    const body = await readBody(req)
    const { status: newStatus, result } = JSON.parse(body.toString()) as { status: string; result?: string }

    let ok = false
    if (newStatus === 'done') ok = markMessageDone(id, result)
    else if (newStatus === 'failed') ok = markMessageFailed(id, result)

    if (ok) {
      const done = getAgentMessage(id)
      // Close the OTel span now that the message has a terminal status.
      if (done?.trace_id && done?.span_id) {
        closeOtelSpan(done.trace_id, done.span_id, Date.now(), newStatus === 'done' ? 'ok' : 'error')
      }
      // Notify the delegator: create a reverse message from executor → delegator so
      // they learn the result without polling. See shouldNotifyDelegator for which
      // senders are skipped and why.
      if (done && shouldNotifyDelegator(done.from_agent, done.to_agent, done.content, done.created_at)) {
        // The receipt carries at most 500 characters of the result -- but it must
        // SAY SO. Until 2026-09-02 it sliced silently, so the recipient read a
        // sentence that simply stopped, with nothing to show something was missing.
        // Measured that day (Cody found it): two receipts were both exactly 535
        // characters -- a 35-character prefix plus a 500-character slice -- and both
        // ended mid-word. The stored `result` is NOT truncated (511 and 506 characters
        // in those two cases), so nothing is lost; only the receipt is short. Saying
        // that is the whole fix, and it is why the marker carries the real length and
        // the endpoint: a reader who needs the rest now knows it exists and where.
        const RECEIPT_RESULT_LIMIT = 500
        let summary: string
        if (!result) {
          summary = '(nincs eredmény)'
        } else if (result.length <= RECEIPT_RESULT_LIMIT) {
          summary = result
        } else {
          summary = `${result.slice(0, RECEIPT_RESULT_LIMIT)}\n[...levágva; a teljes eredmény ${result.length} karakter: GET /api/messages/${id}]`
        }
        createAgentMessage(
          done.to_agent,
          done.from_agent,
          `${COMPLETION_REPORT_PREFIX} msg_id:${id} status:${newStatus}\n\n${summary}`,
        )
      }
      json(res, { ok: true }); return true
    }
    json(res, { error: 'Message not found or invalid status' }, 404)
    return true
  }

  return false
}
