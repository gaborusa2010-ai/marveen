import { appendDailyLog, getDailyLog, getDailyLogDates, localCalendarDay } from '../../db.js'
import { MAIN_AGENT_ID } from '../../config.js'
import { logger } from '../../logger.js'
import { readBody, json, rejectUnknownQueryParams } from '../http-helpers.js'
import { detectHomoglyphs, formatHomoglyphWarning } from '../../homoglyph.js'
import { containsSuspiciousContent } from './memories.js'
import type { RouteContext } from './types.js'

export async function tryHandleDailyLog(ctx: RouteContext): Promise<boolean> {
  const { req, res, path, method, url } = ctx

  if (path === '/api/daily-log' && method === 'POST') {
    const body = await readBody(req)
    const data = JSON.parse(body.toString()) as { agent_id?: string; content: string }
    if (!data.content?.trim()) { json(res, { error: 'Content required' }, 400); return true }
    const agentId = data.agent_id || MAIN_AGENT_ID
    // The memory content filter decides whether the entry is ALSO mirrored into
    // the searchable memories table. A refusal never loses the log row (the
    // diary has always accepted this text): it only skips the mirror, and the
    // response says so.
    const filtered = containsSuspiciousContent(data.content)
    if (filtered) logger.warn({ agent: agentId }, 'daily-log entry saved without a memory mirror: suspicious pattern')
    // A DB error rolls the log row and the mirror back together and throws.
    const { memoryId } = appendDailyLog(agentId, data.content.trim(), { mirror: !filtered })
    const memory_mirror = memoryId !== null ? memoryId : 'skipped:content-filter'
    // Warn-only homoglyph check (GATEHOMOGLIFSWEEP816) -- see memories.ts.
    const homoglyphs = detectHomoglyphs(data.content)
    if (homoglyphs.length > 0) {
      const warning = formatHomoglyphWarning(homoglyphs)
      logger.warn({ agent: data.agent_id }, `daily-log entry saved with ${warning}`)
      json(res, { ok: true, memory_mirror, homoglyph_warning: warning })
      return true
    }
    json(res, { ok: true, memory_mirror })
    return true
  }

  if (path === '/api/daily-log' && method === 'GET') {
    // The alias below fixed the `agent` side of this; `date` had the same hole.
    // A typo there (`dat=2026-08-30`) silently fell back to TODAY and answered
    // 200 with a plausible log for the wrong day. (Cody, 2026-09-01.)
    if (rejectUnknownQueryParams(res, url, ['agent', 'agent_id', 'date'],
      'filters are "agent" ("agent_id" is an alias) and "date" (YYYY-MM-DD)')) return true
    // `agent_id` is accepted as an alias for `agent`. WHY: the POST body on this
    // very route takes `agent_id`, so a caller naturally reuses that name on the
    // GET -- and an unknown query param used to be dropped in silence, falling
    // back to MAIN_AGENT_ID. The call returned 200 with SOMEONE ELSE'S log, which
    // is the worst shape of failure: it looks like an answer. (Margo, 2026-09-01.)
    const agent = url.searchParams.get('agent') || url.searchParams.get('agent_id') || MAIN_AGENT_ID
    // DAYSPLIT901 (Cody, 2026-09-01, found reviewing the day's diff): the WRITER
    // (src/db.ts) stamps the entry with the LOCAL calendar day, and says why in
    // its own comment -- an entry written just after midnight must not land on
    // yesterday. The reader here computed the UTC day instead. In BST that means
    // between 00:00 and 01:00 a plain GET returns the PREVIOUS day, while the
    // entries written in that hour are already filed under the new one. That is
    // Anorak's night shift, and it answers 200 with the wrong data.
    const date = url.searchParams.get('date') || localCalendarDay()
    json(res, getDailyLog(agent, date))
    return true
  }

  if (path === '/api/daily-log/dates' && method === 'GET') {
    if (rejectUnknownQueryParams(res, url, ['agent', 'agent_id'],
      'the agent filter is "agent" ("agent_id" is an alias)')) return true
    const agent = url.searchParams.get('agent') || url.searchParams.get('agent_id') || MAIN_AGENT_ID
    json(res, getDailyLogDates(agent))
    return true
  }

  return false
}
