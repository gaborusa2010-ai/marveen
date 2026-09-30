import { readFileSync, statSync, openSync, readSync, closeSync } from 'node:fs'

// The subscription quota the whole fleet draws from, as the statusLine command
// last saw it. scripts/statusline-ratelimit.sh writes the block to
// store/.claude-rate-limits.json on every render, costing no tokens; this is
// the read side, so the dashboard shows the same numbers the monitor alerts on.
//
// Reading it has one rule, and it is why this module exists instead of a few
// lines in the route: a quota reading is only worth showing while it is fresh.
// A number from six hours ago looks exactly like a number from six seconds ago
// and reassures just as much, so age travels with the data and the caller is
// made to deal with it. Same for a window whose reset time has passed: the
// block only changes when an API response brings new numbers, so after a
// rollover the old percentage sits there describing a window that no longer
// exists (measured at the 2026-08-18 22:00 rollover, and the reason
// scripts/lib/quota-check.py skips those too).

/** Same default as QUOTA_MAX_AGE_SEC in scripts/limit-monitor.sh. */
export const DEFAULT_MAX_AGE_SEC = 21600

// The Fable/Opus weekly window is not part of the statusLine block above --
// it only shows up there while a session is actively using Fable, which is
// rarely true. scripts/usage-collect.py is the authoritative source instead,
// writing store/usage-latest.json on its own schedule (a heartbeat task,
// every 15 minutes). Its freshness bar has to be much tighter than the
// statusLine's 6h: a missed run here means the collector itself is down, not
// just "no session lately". ~3x the collection interval, the same margin
// usage-collect.py's own alert refire windows use.
export const DEFAULT_FABLE_MAX_AGE_SEC = 2700

export interface QuotaWindow {
  /** Percentage of the window already spent, 0-100. */
  usedPercentage: number
  /** Unix seconds when the window rolls over, null when the payload had none. */
  resetsAt: number | null
  /** The reset time has passed: this reading describes a window that is gone. */
  expired: boolean
}

export interface QuotaSnapshot {
  /** ok: fresh reading. stale: too old to trust. missing: no reading at all. */
  status: 'ok' | 'stale' | 'missing'
  /** Seconds since the statusLine wrote the file, null when there is no file. */
  ageSec: number | null
  maxAgeSec: number
  fiveHour: QuotaWindow | null
  sevenDay: QuotaWindow | null
  /** Why there is nothing to show; only set when status is 'missing'. */
  reason?: 'no-file' | 'unreadable' | 'no-rate-limits'
}

function readWindow(raw: unknown, nowSec: number): QuotaWindow | null {
  if (!raw || typeof raw !== 'object') return null
  const w = raw as { used_percentage?: unknown; resets_at?: unknown }
  if (typeof w.used_percentage !== 'number' || !Number.isFinite(w.used_percentage)) return null
  const resetsAt = typeof w.resets_at === 'number' && Number.isFinite(w.resets_at) ? w.resets_at : null
  return {
    usedPercentage: w.used_percentage,
    resetsAt,
    expired: resetsAt !== null && resetsAt <= nowSec,
  }
}

/**
 * Read the quota snapshot the statusLine last wrote.
 *
 * Never throws: a missing or corrupt file is an answer ('missing'), not an
 * error, because the dashboard must still render. The caller is expected to
 * say WHY the strip is absent rather than drop it silently.
 */
export function readQuotaSnapshot(
  file: string,
  nowSec: number = Math.floor(Date.now() / 1000),
  maxAgeSec: number = DEFAULT_MAX_AGE_SEC,
): QuotaSnapshot {
  const empty = { ageSec: null, maxAgeSec, fiveHour: null, sevenDay: null }

  let parsed: unknown
  try {
    parsed = JSON.parse(readFileSync(file, 'utf-8'))
  } catch (err) {
    const code = (err as NodeJS.ErrnoException)?.code
    return { status: 'missing', reason: code === 'ENOENT' ? 'no-file' : 'unreadable', ...empty }
  }
  if (!parsed || typeof parsed !== 'object') {
    return { status: 'missing', reason: 'unreadable', ...empty }
  }

  const d = parsed as { written_at?: unknown; rate_limits?: unknown }
  const fiveHour = readWindow((d.rate_limits as Record<string, unknown>)?.five_hour, nowSec)
  const sevenDay = readWindow((d.rate_limits as Record<string, unknown>)?.seven_day, nowSec)
  if (!fiveHour && !sevenDay) {
    // An API-key account never gets a rate_limits block, and neither does a
    // subscription session before its first API response.
    return { status: 'missing', reason: 'no-rate-limits', ...empty }
  }

  const writtenAt = typeof d.written_at === 'number' && Number.isFinite(d.written_at) ? d.written_at : 0
  const ageSec = Math.max(0, nowSec - writtenAt)
  return {
    status: ageSec > maxAgeSec ? 'stale' : 'ok',
    ageSec,
    maxAgeSec,
    fiveHour,
    sevenDay,
  }
}

// The MONTHLY extra-usage spend. Unlike the two windows above it never reaches
// the status line: only the oauth/usage endpoint reports it, and
// scripts/usage-collect.py stores it as claude.windows.monthly_spend. That
// endpoint needs an interactive-login token, so there can be long gaps with no
// fresh reading (2026-09-28 onwards: 403 for the setup-token). The last reading
// is still the best answer, as long as its date travels with it -- hence the
// fallback to the history file and the measuredAt field.

export interface MonthlySpend {
  usedMinor: number | null
  /** null: no cap set, so a percentage would mean nothing. */
  limitMinor: number | null
  currency: string | null
  exponent: number
  /** Only set when there is a cap. */
  percent: number | null
  enabled: boolean
  limitReached: boolean
  /** Unix seconds of the reading this came from. */
  measuredAt: number
  /** true when it came from usage-latest.json, false when from history. */
  fromLatest: boolean
}

function parseMonthly(raw: unknown, measuredAt: number, fromLatest: boolean): MonthlySpend | null {
  if (!raw || typeof raw !== 'object') return null
  const m = raw as Record<string, unknown>
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  const usedMinor = num(m.used_minor)
  const limitMinor = num(m.limit_minor)
  if (usedMinor === null && limitMinor === null) return null
  const percent = limitMinor !== null && limitMinor > 0 && usedMinor !== null
    ? (100 * usedMinor) / limitMinor
    : null
  return {
    usedMinor,
    limitMinor,
    currency: typeof m.currency === 'string' ? m.currency : null,
    exponent: num(m.exponent) ?? 2,
    percent,
    enabled: m.enabled === true,
    limitReached: m.spend_limit_reached === true,
    measuredAt,
    fromLatest,
  }
}

function snapshotEpoch(d: Record<string, unknown>): number {
  const t = typeof d.generated_at === 'string' ? Date.parse(d.generated_at) : NaN
  return Number.isFinite(t) ? Math.floor(t / 1000) : 0
}

function monthlyOf(d: unknown): unknown {
  const c = (d as { claude?: { windows?: { monthly_spend?: unknown } } })?.claude
  return c?.windows?.monthly_spend
}

/** Newest monthly spend reading, from usage-latest.json or else the history tail. Never throws. */
export function readMonthlySpend(latestFile: string, historyFile: string): MonthlySpend | null {
  try {
    const d = JSON.parse(readFileSync(latestFile, 'utf-8'))
    const m = parseMonthly(monthlyOf(d), snapshotEpoch(d), true)
    if (m) return m
  } catch { /* fall through to history */ }
  let fd: number | null = null
  try {
    // The history grows by a line every 15 minutes; the tail is enough, and
    // reading 1.3 MB on every overview load is not.
    const size = statSync(historyFile).size
    const len = Math.min(size, 2 * 1024 * 1024)
    const buf = Buffer.alloc(len)
    fd = openSync(historyFile, 'r')
    readSync(fd, buf, 0, len, size - len)
    const lines = buf.toString('utf-8').split('\n')
    for (let i = lines.length - 1; i >= 0; i--) {
      if (!lines[i].includes('monthly_spend')) continue
      try {
        const d = JSON.parse(lines[i])
        const m = parseMonthly(monthlyOf(d), snapshotEpoch(d), false)
        if (m) return m
      } catch { /* a cut first line */ }
    }
  } catch { /* no history */ } finally {
    if (fd !== null) closeSync(fd)
  }
  return null
}

/**
 * The Fable/Opus weekly window, read separately because it comes from a
 * different writer (scripts/usage-collect.py -> store/usage-latest.json)
 * with its own shape and its own freshness rule. Missing/stale/unreadable
 * all degrade to 'missing' or 'stale' rather than throwing, same rule as
 * readQuotaSnapshot: the dashboard must still render.
 */
export interface FableSnapshot {
  status: 'ok' | 'stale' | 'missing'
  ageSec: number | null
  window: QuotaWindow | null
}

/** usage-collect.py's window shape: `used_percent`, not the statusLine's `used_percentage`. */
function readOpusWindow(raw: unknown, nowSec: number): QuotaWindow | null {
  if (!raw || typeof raw !== 'object') return null
  const w = raw as { used_percent?: unknown; resets_at?: unknown }
  if (typeof w.used_percent !== 'number' || !Number.isFinite(w.used_percent)) return null
  const resetsAt = typeof w.resets_at === 'number' && Number.isFinite(w.resets_at) ? w.resets_at : null
  return {
    usedPercentage: w.used_percent,
    resetsAt,
    expired: resetsAt !== null && resetsAt <= nowSec,
  }
}

export function readFableSnapshot(
  file: string,
  nowSec: number = Math.floor(Date.now() / 1000),
  maxAgeSec: number = DEFAULT_FABLE_MAX_AGE_SEC,
): FableSnapshot {
  let parsed: unknown
  try {
    parsed = JSON.parse(readFileSync(file, 'utf-8'))
  } catch {
    return { status: 'missing', ageSec: null, window: null }
  }
  if (!parsed || typeof parsed !== 'object') return { status: 'missing', ageSec: null, window: null }

  const d = parsed as { generated_at?: unknown; claude?: unknown }
  const claude = d.claude && typeof d.claude === 'object' ? (d.claude as Record<string, unknown>) : null
  const windows = claude?.windows && typeof claude.windows === 'object' ? (claude.windows as Record<string, unknown>) : null
  // Non-tiered accounts report this window as null forever, same as the
  // collector's own "skip gracefully" handling -- that's a permanent
  // "nothing to show", not a freshness problem, so it stays 'missing'.
  // NOTE: usage-collect.py's field is `used_percent`, not the statusLine's
  // `used_percentage` -- readWindow() above is the wrong shape for this source.
  const window = windows ? readOpusWindow(windows.seven_day_opus, nowSec) : null
  if (!window) return { status: 'missing', ageSec: null, window: null }

  // A missing generated_at counts as maximally old, matching how
  // readQuotaSnapshot treats a missing written_at above.
  let generatedAtSec = 0
  if (typeof d.generated_at === 'string') {
    const ms = Date.parse(d.generated_at)
    if (!Number.isNaN(ms)) generatedAtSec = Math.floor(ms / 1000)
  }
  const ageSec = Math.max(0, nowSec - generatedAtSec)
  return { status: ageSec > maxAgeSec ? 'stale' : 'ok', ageSec, window }
}
