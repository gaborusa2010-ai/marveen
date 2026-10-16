// Recurring kanban cards (card #452): the rule shape, its validation, and the
// "when is the next one due" computation. Pure and dependency-free (the zone
// is a parameter) so the month-end and DST edges can be pinned by tests
// without a database or a clock.
//
// A rule is stored on the card as canonical JSON text (kanban_cards.recurrence):
//   {"freq":"daily"}
//   {"freq":"weekly","weekday":1..7}     ISO weekday, 1 = Monday, 7 = Sunday
//   {"freq":"monthly","day":1..31}       clamped to the month's length
//   {"freq":"interval","days":1..365}    every N days
//
// Two due-date shapes live on this board and the computation keeps each one:
//   - DATE-ONLY: the dashboard's date field sends "YYYY-MM-DD", stored as UTC
//     midnight (see normaliseDueDate in db.ts). The next due date is again a
//     UTC midnight, so the edit modal reads back the intended calendar day.
//   - TIMED: anything else (an agent that set a real time). Stepped in the
//     install's local zone (APP_TZ / SCHEDULER_TZ), keeping the local wall-clock
//     time across a DST change instead of drifting an hour.

export type KanbanRecurrence =
  | { freq: 'daily' }
  | { freq: 'weekly'; weekday: number }
  | { freq: 'monthly'; day: number }
  | { freq: 'interval'; days: number }

const RULE_KEYS: Record<KanbanRecurrence['freq'], string[]> = {
  daily: [],
  weekly: ['weekday'],
  monthly: ['day'],
  interval: ['days'],
}

const INT_BOUNDS: Record<string, [number, number]> = {
  weekday: [1, 7],
  day: [1, 31],
  days: [1, 365],
}

/**
 * Validate a rule coming from a caller (an object, or the stored JSON text).
 * `null` / `''` mean "not recurring". An unknown key or an out-of-range value
 * is an error with a message that names the accepted shape -- the same "a
 * silently dropped field teaches guessing" rule as PUT /api/kanban/:id.
 */
export function parseKanbanRecurrence(input: unknown):
  { ok: true; rule: KanbanRecurrence | null } | { ok: false; error: string } {
  if (input === null || input === undefined || input === '') return { ok: true, rule: null }
  let raw: unknown = input
  if (typeof input === 'string') {
    try { raw = JSON.parse(input) } catch { return { ok: false, error: 'recurrence: invalid JSON' } }
  }
  if (raw === null) return { ok: true, rule: null }
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, error: 'recurrence must be an object like {"freq":"weekly","weekday":1} or null' }
  }
  const obj = raw as Record<string, unknown>
  const freq = obj.freq
  if (typeof freq !== 'string' || !(freq in RULE_KEYS)) {
    return { ok: false, error: 'recurrence.freq must be one of: daily, weekly, monthly, interval' }
  }
  const allowed = new Set(['freq', ...RULE_KEYS[freq as KanbanRecurrence['freq']]])
  const unknown = Object.keys(obj).filter((k) => !allowed.has(k))
  if (unknown.length) {
    return { ok: false, error: `recurrence: unknown field(s) for freq=${freq}: ${unknown.join(', ')}. Accepted: ${[...allowed].join(', ')}` }
  }
  const out: Record<string, unknown> = { freq }
  for (const key of RULE_KEYS[freq as KanbanRecurrence['freq']]) {
    const v = typeof obj[key] === 'string' ? Number(obj[key]) : obj[key]
    const [min, max] = INT_BOUNDS[key]
    if (typeof v !== 'number' || !Number.isInteger(v) || v < min || v > max) {
      return { ok: false, error: `recurrence.${key} must be an integer ${min}..${max}` }
    }
    out[key] = v
  }
  return { ok: true, rule: out as KanbanRecurrence }
}

/** Canonical stored text of a rule (fixed key order), or null. */
export function serializeKanbanRecurrence(rule: KanbanRecurrence | null): string | null {
  return rule ? JSON.stringify(rule) : null
}

// --- calendar helpers (proleptic Gregorian, month is 1-based) ---------------

function daysInMonth(y: number, m: number): number {
  return new Date(Date.UTC(y, m, 0)).getUTCDate()
}

type Ymd = { y: number; m: number; d: number }

function addDays(date: Ymd, n: number): Ymd {
  const t = new Date(Date.UTC(date.y, date.m - 1, date.d + n))
  return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() }
}

function isoWeekday(date: Ymd): number {
  const wd = new Date(Date.UTC(date.y, date.m - 1, date.d)).getUTCDay()
  return wd === 0 ? 7 : wd
}

function cmp(a: Ymd, b: Ymd): number {
  return (a.y - b.y) || (a.m - b.m) || (a.d - b.d)
}

/** The first date STRICTLY after `base` that the rule produces. */
export function nextKanbanOccurrenceDate(rule: KanbanRecurrence, base: Ymd): Ymd {
  switch (rule.freq) {
    case 'daily':
      return addDays(base, 1)
    case 'interval':
      return addDays(base, rule.days)
    case 'weekly': {
      const delta = ((rule.weekday - isoWeekday(base)) + 7) % 7 || 7
      return addDays(base, delta)
    }
    case 'monthly': {
      // Same month first (a rule changed to a later day this month), then the
      // next month. The clamp is per candidate month, so a "31st" rule goes
      // Jan 31 -> Feb 28/29 -> Mar 31: the rule keeps its day, the date does not drift.
      const sameMonth = { y: base.y, m: base.m, d: Math.min(rule.day, daysInMonth(base.y, base.m)) }
      if (cmp(sameMonth, base) > 0) return sameMonth
      const y = base.m === 12 ? base.y + 1 : base.y
      const m = base.m === 12 ? 1 : base.m + 1
      return { y, m, d: Math.min(rule.day, daysInMonth(y, m)) }
    }
  }
}

// --- zone helpers ------------------------------------------------------------

type LocalParts = Ymd & { h: number; mi: number; s: number }

function localParts(epochSec: number, tz: string): LocalParts {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hourCycle: 'h23',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
  const p: Record<string, number> = {}
  for (const part of fmt.formatToParts(new Date(epochSec * 1000))) {
    if (part.type !== 'literal') p[part.type] = Number(part.value)
  }
  return { y: p.year, m: p.month, d: p.day, h: p.hour % 24, mi: p.minute, s: p.second }
}

function zoneOffsetSec(epochSec: number, tz: string): number {
  const l = localParts(epochSec, tz)
  return Date.UTC(l.y, l.m - 1, l.d, l.h, l.mi, l.s) / 1000 - epochSec
}

/** Local wall-clock time in `tz` -> unix seconds (two-pass offset fix-up across DST). */
function localToEpoch(l: LocalParts, tz: string): number {
  const naive = Date.UTC(l.y, l.m - 1, l.d, l.h, l.mi, l.s) / 1000
  const first = naive - zoneOffsetSec(naive, tz)
  const second = naive - zoneOffsetSec(first, tz)
  return second
}

/** Today's calendar date in `tz`. */
export function localDateOf(epochSec: number, tz: string): Ymd {
  const l = localParts(epochSec, tz)
  return { y: l.y, m: l.m, d: l.d }
}

/**
 * The next instance's due date (unix seconds) for a card completed with
 * `prevDue` (null = the card had no deadline: step from today in `tz`).
 */
export function nextKanbanDueDate(rule: KanbanRecurrence, prevDue: number | null | undefined, nowSec: number, tz: string): number {
  const utcMidnight = (d: Ymd) => Date.UTC(d.y, d.m - 1, d.d) / 1000
  if (prevDue === null || prevDue === undefined || !Number.isFinite(prevDue)) {
    return utcMidnight(nextKanbanOccurrenceDate(rule, localDateOf(nowSec, tz)))
  }
  if (prevDue % 86400 === 0) {
    const t = new Date(prevDue * 1000)
    const base = { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() }
    return utcMidnight(nextKanbanOccurrenceDate(rule, base))
  }
  const l = localParts(prevDue, tz)
  const next = nextKanbanOccurrenceDate(rule, l)
  return localToEpoch({ ...next, h: l.h, mi: l.mi, s: l.s }, tz)
}

/** "YYYY-MM-DD" of a due date as the dashboard shows it (date-only = UTC day). */
export function formatKanbanDue(due: number, tz: string): string {
  const d = due % 86400 === 0
    ? (() => { const t = new Date(due * 1000); return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() } })()
    : localDateOf(due, tz)
  return `${d.y}-${String(d.m).padStart(2, '0')}-${String(d.d).padStart(2, '0')}`
}
