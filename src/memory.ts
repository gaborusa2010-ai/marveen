import { mkdirSync, writeFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { homedir, tmpdir } from 'node:os'
import {
  searchMemories,
  recentMemories,
  touchMemory,
  saveMemory,
  decayMemories as dbDecay,
  pruneAuditLogs,
  pruneTokenUsage,
  getMemoriesCreatedSince,
  appendDailyLog,
  type Memory,
} from './db.js'
import { MAIN_AGENT_ID } from './config.js'
import { runAgent } from './agent.js'
import { logger } from './logger.js'
import { wrapUntrusted, UNTRUSTED_PREAMBLE } from './prompt-safety.js'

// Dedicated cwd for the daily-digest sub-agent. We can't reuse PROJECT_ROOT
// here -- the Marveen Telegram channels session runs claude --continue in
// PROJECT_ROOT, and the SDK's per-cwd session/lock state collides with it
// when two Claude Code processes share the same project dir, dropping the
// channels plugin every night at 23:00. A throwaway dir under the user's
// `~/.claude/projects/` tree avoids the collision while keeping the SDK
// happy (it expects a writable cwd to place its session jsonl into).
//
// We honor TMPDIR via os.tmpdir() as a last-resort fallback so a hardened
// host with a read-only home still has somewhere to land.
function ensureDigestCwd(): string {
  const candidates = [join(homedir(), '.claude', 'tmp', 'marveen-digest'), join(tmpdir(), 'marveen-digest')]
  for (const dir of candidates) {
    try {
      mkdirSync(dir, { recursive: true })
      return dir
    } catch { /* try next */ }
  }
  // Last resort: tmpdir itself. Worst case we share with whatever else is
  // in /tmp, but that still doesn't collide with the Marveen project dir.
  return tmpdir()
}

// The daily digest sub-agent inherits the host's CLAUDE_CONFIG_DIR
// (~/.claude/) by default, which means it loads the user's globally
// enabled plugins -- including telegram@claude-plugins-official. The
// Telegram Bot API only allows ONE active getUpdates connection per
// token, so the sub-agent's plugin steals the connection from the
// long-running marveen-channels session, which then logs as
// "plugin lecsatlakozott" at 23:00 every night when runDailyDigest
// fires. Workaround: hand the sub-agent a private CLAUDE_CONFIG_DIR
// with `enabledPlugins: {}` so it never spawns the Telegram MCP. The
// dir is created idempotently on first use; the settings.json is
// written only if missing so the user can edit it later if needed.
function ensureDigestConfigDir(): string {
  const candidates = [
    join(homedir(), '.claude', 'tmp', 'marveen-digest-config'),
    join(tmpdir(), 'marveen-digest-config'),
  ]
  for (const dir of candidates) {
    try {
      mkdirSync(dir, { recursive: true })
      const settingsPath = join(dir, 'settings.json')
      if (!existsSync(settingsPath)) {
        writeFileSync(settingsPath, JSON.stringify({ enabledPlugins: {} }, null, 2))
      }
      return dir
    } catch { /* try next */ }
  }
  return tmpdir()
}

// Semantic: user preferences, facts about themselves, persistent info
const SEMANTIC_PATTERN =
  /\b(my|i am|i'm|i prefer|remember|always|never|az en|nekem|szeretem|nem szeretem|mindig|soha|emlekezzel|en|kedvenc|utokalok|fontos|ne felejtsd|jegyezd meg)\b/i

// Skip: trivial messages not worth remembering
const SKIP_PATTERN = /^(ok|igen|nem|koszi|kosz|hello|szia|hi|hey|thx|thanks|jo|oke|persze|rendben|ja|aha|\.+|!+|\?+)$/i

export async function buildMemoryContext(
  chatId: string,
  userMessage: string
): Promise<string> {
  // Relaxed kept for the conversational recall path: this is a suggestion
  // surfaced next to a live message, not an answer to "do we know this".
  // Changing it is a separate decision from the endpoint's strict default.
  const ftsResults = searchMemories(userMessage, chatId, 3, true)
  const recent = recentMemories(chatId, 5)

  const seen = new Set<number>()
  const combined: Memory[] = []

  for (const m of [...ftsResults, ...recent]) {
    if (!seen.has(m.id)) {
      seen.add(m.id)
      combined.push(m)
    }
  }

  if (combined.length === 0) return ''

  for (const m of combined) {
    touchMemory(m.id)
  }

  const lines = combined.map((m) => `- ${m.content} (${m.sector})`)
  return `[Memoria kontextus]\n${lines.join('\n')}`
}

// buildKanbanContext used to live here: it rendered EVERY unarchived card's
// FULL title into a [Kanban tabla] block (no limit, no truncation -- ~1 MB of
// titles on the live board, KANBANCTXDEAD824). Nothing ever called it, in src,
// dist or any plugin. Removed rather than fixed: an unbounded full-board dump
// is the wrong contract for agent context, and whoever wires kanban context in
// the future should start from the heartbeat-summary shape (counts first,
// titles truncated server-side -- see buildHeartbeatSummaryResponse).

export async function saveConversationTurn(
  chatId: string,
  userMsg: string,
  assistantMsg: string
): Promise<void> {
  // Skip trivial, short, or command messages
  if (userMsg.length <= 20 || userMsg.startsWith('/')) return
  if (SKIP_PATTERN.test(userMsg.trim())) return

  // Only save semantic memories (user preferences, facts) automatically
  // Episodic memories come from daily digest and session checkpoints
  if (SEMANTIC_PATTERN.test(userMsg)) {
    const content = `Felhasznalo: ${userMsg.slice(0, 500)}\nAsszisztens: ${assistantMsg.slice(0, 500)}`
    saveMemory(chatId, content, 'semantic')
    logger.debug({ chatId }, 'Szemantikus emlek mentve')
  }
  // Non-semantic turns are NOT saved individually -- they go into daily digest
}

export function runDecaySweep(): void {
  dbDecay()
  pruneAuditLogs()
  const tokenRowsPruned = pruneTokenUsage()
  logger.info({ tokenRowsPruned }, 'Memoria leepulesi sopres vegrehajtva')
}

// --- Daily digest ---

export async function runDailyDigest(chatId: string): Promise<string | null> {
  // Collect today's episodic memories (last 24h).
  //
  // ORDER MATTERS, and it was wrong until 2026-09-04. The old form asked for
  // the 50 most recently ACCESSED memories and only then kept the ones from
  // today -- so the limit trimmed a TIME SLICE rather than a count, and which
  // slice survived depended on what happened to be read lately. Measured by
  // Anorak that night: 12 of the day's 37 memories reached the digest, 10 of
  // them from a single agent, and the day's two most consequential events
  // never appeared in the fleet's only daily summary.
  const oneDayAgo = Math.floor(Date.now() / 1000) - 86400
  const todayMemories = getMemoriesCreatedSince(chatId, oneDayAgo, 200)

  if (todayMemories.length < 2) {
    logger.info({ chatId, count: todayMemories.length }, 'Napi naplo: tul keves emlek, kihagyjuk')
    return null
  }

  // Each memory is wrapped individually: the stored content originated in
  // Telegram messages that could have come through the assistant from a third
  // party (a forwarded message, a quoted email). Treat every record as data.
  // EVERY LINE CARRIES ITS AUTHOR. Without this the prompt received six
  // agents' memories as if they were one assistant's, and the model wrote the
  // summary in the first person from whichever agent supplied most of the
  // sample. Measured 2026-09-04 23:00:36 on Marveen's own log: the entry said
  // "I measured the Hungarian subtitle timing (agents/roxy/scripts/...)" and
  // referred to Marveen in the third person. The work was Roxy's; the log was
  // Marveen's. Same family as the second-person circular letter: the speaker
  // is invented by the surrounding text when nothing states who acted.
  const memoryLines = todayMemories
    .map((m) => `- [${m.agent_id || 'ismeretlen'}] ${wrapUntrusted('memory-record', m.content.slice(0, 200))}`)
    .join('\n')

  const prompt = `${UNTRUSTED_PREAMBLE}
Az alabbi egy TOBB AGENSBOL allo csapat mai emlekei. Minden sor elejen szogletes
zarojelben all, MELYIK AGENS irta.

Irj egy tomor napi osszefoglalot (max 5-8 mondat), ami megragadja:
1. Milyen feladatokon dolgoztak
2. Milyen fontos dontesek szulettek
3. Mi maradt nyitva / mi a kovetkezo lepes

KOTELEZO FORMAI SZABALYOK:
- HARMADIK SZEMELYBEN irj. Soha ne hasznalj egyes szam elso szemelyt ("kimertem",
  "megirtam"), mert az emlekek hat kulonbozo agenstol szarmaznak, es nem tudod
  eldonteni, ki az "en".
- Minden allitas mellett NEVEZD MEG, melyik agens csinalta, a sor eleji jeloles
  szerint. Ha egy allitas tobb agenshez kotheto, mondd ki mind a kettot.
- Ne hasznalj gondolatjelet. Kettospont, zarojel vagy uj mondat helyette.

Csak az osszefoglalot add vissza, semmi mast. Magyarul irj.

Mai emlekek:
${memoryLines}`

  try {
    const digestCwd = ensureDigestCwd()
    const digestConfigDir = ensureDigestConfigDir()
    const { text } = await runAgent(prompt, undefined, undefined, false, digestCwd, {
      CLAUDE_CONFIG_DIR: digestConfigDir,
    })
    if (!text) return null

    const digest = text.trim()
    // ONE place for "what happened today", and it is daily_logs.
    //
    // DIARYSPLIT901 (2026-09-04). This used to write the digest into the
    // `memories` table with a `[Napi naplo ...]` prefix, while every agent --
    // and the dashboard, and /api/daily-log/dates -- used the `daily_logs`
    // table for the same question. Two records, one name. It produced a false
    // statement the same night: the endpoint reported ZERO diary entries for
    // marveen on 09-03, an agent reported that as "the manual fix did not last a
    // day", and I passed it on confirmed. The entry existed. It was 1500
    // characters long. It was simply in the other table.
    //
    // Measured then: 13 nights of digests in `memories` (all marveen, all
    // ~23:00, since 08-22) against 117 rows in `daily_logs`. The digest was the
    // only thing writing to the wrong side, so the digest is what moves.
    //
    // The old rows are LEFT WHERE THEY ARE on purpose. Copying them across
    // would put every one of those nights into the recall index twice -- recall
    // reads both tables -- and duplicated history is the same class of bug as
    // the split itself. The gap is stated on card ef4e9830 instead.
    appendDailyLog(MAIN_AGENT_ID, digest)
    logger.info({ chatId, agentId: MAIN_AGENT_ID, digestCwd, digestConfigDir }, 'Napi naplo mentve a daily_logs tablaba')
    return digest
  } catch (err) {
    logger.error({ err }, 'Napi naplo generalas hiba')
    return null
  }
}
