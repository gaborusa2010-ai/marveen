// Kanban calendar view (#451 part B). The pure month-grid / day-bucket helpers
// are sliced out of web/app.js (no module boundary) and run in a vm under
// Europe/London, the zone the dashboard's browser runs in. The wiring checks
// pin the feature flag, the view button and the i18n keys.
process.env.TZ = 'Europe/London'

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const APP_JS = readFileSync(join(ROOT, 'web', 'app.js'), 'utf-8')
const INDEX_HTML = readFileSync(join(ROOT, 'web', 'index.html'), 'utf-8')
const LANG = {
  en: readFileSync(join(ROOT, 'web', 'lang', 'en.js'), 'utf-8'),
  hu: readFileSync(join(ROOT, 'web', 'lang', 'hu.js'), 'utf-8'),
}

function slice(src: string, startMarker: string, endMarker: string): string {
  const a = src.indexOf(startMarker)
  const b = src.indexOf(endMarker, a)
  if (a < 0 || b < 0) throw new Error(`marker not found: ${startMarker}`)
  return src.slice(a, b)
}

type Cell = { key: string; day: number; inMonth: boolean }
type Card = { id: string; status?: string; priority?: string; seq?: number; due_date?: unknown }
const ctx: Record<string, unknown> = {}
vm.createContext(ctx)
vm.runInContext(
  slice(APP_JS, '// === Kanban calendar helpers (pure) ===', '// === end kanban calendar helpers ===') +
    '\nthis.h = { kanbanCalDayKey, kanbanCalDueKey, kanbanCalMonthGrid, kanbanCalBucket }',
  ctx,
)
const h = ctx.h as {
  kanbanCalDueKey: (v: unknown) => string | null
  kanbanCalMonthGrid: (y: number, m: number) => Cell[][]
  kanbanCalBucket: (cards: Card[], showDone: boolean) => Map<string, Card[]>
}
const sec = (iso: string) => Date.parse(iso) / 1000

describe('kanbanCalMonthGrid', () => {
  it('starts on Monday and pads to whole weeks (October 2026 starts on a Thursday)', () => {
    const weeks = h.kanbanCalMonthGrid(2026, 9)
    expect(weeks).toHaveLength(5)
    expect(weeks.every((w) => w.length === 7)).toBe(true)
    expect(weeks[0][0]).toEqual({ key: '2026-09-28', day: 28, inMonth: false })
    expect(weeks[0][3]).toEqual({ key: '2026-10-01', day: 1, inMonth: true })
    expect(weeks[4][6]).toEqual({ key: '2026-11-01', day: 1, inMonth: false })
    expect(weeks.flat().filter((c) => c.inMonth)).toHaveLength(31)
  })

  it('needs 4 rows for a Monday-starting February and 6 for a Saturday-starting 31-day month', () => {
    const feb = h.kanbanCalMonthGrid(2021, 1)
    expect(feb).toHaveLength(4)
    expect(feb[0][0].key).toBe('2021-02-01')
    expect(feb[3][6].key).toBe('2021-02-28')
    const aug = h.kanbanCalMonthGrid(2026, 7)
    expect(aug).toHaveLength(6)
    expect(aug[0][5].key).toBe('2026-08-01')
  })

  it('keeps one cell per calendar day across the BST change (25 Oct 2026, 29 Mar 2026)', () => {
    for (const [y, m] of [[2026, 9], [2026, 2]]) {
      const keys = h.kanbanCalMonthGrid(y, m).flat().map((c) => c.key)
      expect(new Set(keys).size).toBe(keys.length)
      for (let i = 1; i < keys.length; i++) {
        const prev = new Date(keys[i - 1] + 'T12:00:00Z').getTime()
        const cur = new Date(keys[i] + 'T12:00:00Z').getTime()
        expect(cur - prev).toBe(86400000)
      }
    }
  })
})

describe('kanbanCalDueKey', () => {
  it('uses the London day, not the UTC one', () => {
    // 23:30 UTC on 30 June is 00:30 BST on 1 July
    expect(h.kanbanCalDueKey(sec('2026-06-30T23:30:00Z'))).toBe('2026-07-01')
    // in winter London is UTC
    expect(h.kanbanCalDueKey(sec('2026-12-31T23:30:00Z'))).toBe('2026-12-31')
  })

  it('returns null for a missing or unusable deadline, and accepts a numeric string', () => {
    for (const v of [null, undefined, '', 0, 'abc', NaN, -5]) expect(h.kanbanCalDueKey(v)).toBeNull()
    expect(h.kanbanCalDueKey(String(sec('2026-10-17T10:00:00Z')))).toBe('2026-10-17')
  })
})

describe('kanbanCalBucket', () => {
  const day = sec('2026-10-20T09:00:00Z')
  const cards: Card[] = [
    { id: 'nodl', status: 'planned', priority: 'urgent', seq: 1 },
    { id: 'done', status: 'done', priority: 'urgent', seq: 2, due_date: day },
    { id: 'low', status: 'planned', priority: 'low', seq: 3, due_date: day },
    { id: 'hi9', status: 'in_progress', priority: 'high', seq: 9, due_date: day },
    { id: 'hi4', status: 'waiting', priority: 'high', seq: 4, due_date: day },
    { id: 'other', status: 'planned', priority: 'normal', seq: 5, due_date: sec('2026-10-21T09:00:00Z') },
  ]

  it('skips cards without a deadline and hides done cards unless asked', () => {
    const hidden = h.kanbanCalBucket(cards, false)
    expect([...hidden.keys()].sort()).toEqual(['2026-10-20', '2026-10-21'])
    expect(hidden.get('2026-10-20')!.map((c) => c.id)).toEqual(['hi4', 'hi9', 'low'])
  })

  it('shows done cards last within the day, then priority, then seq', () => {
    const shown = h.kanbanCalBucket(cards, true)
    expect(shown.get('2026-10-20')!.map((c) => c.id)).toEqual(['hi4', 'hi9', 'low', 'done'])
    expect(shown.get('2026-10-21')!.map((c) => c.id)).toEqual(['other'])
  })

  it('tolerates a non-array input', () => {
    expect(h.kanbanCalBucket(undefined as unknown as Card[], true).size).toBe(0)
  })
})

describe('calendar view wiring', () => {
  it('the view button starts hidden and the container exists', () => {
    expect(INDEX_HTML).toMatch(/<button[^>]*id="kanbanViewCalendar"[^>]*data-i18n="kanban\.view\.calendar"[^>]*hidden>/)
    expect(INDEX_HTML).toContain('id="kanbanCalendarView"')
  })

  it('is gated on the calendar feature flag', () => {
    expect(APP_JS).toContain("const on = kanbanFeatureOn('calendar')")
  })

  it('hides done cards by default (only a stored "1" shows them)', () => {
    expect(APP_JS).toContain('let calShowDone = false')
    expect(APP_JS).toContain("localStorage.getItem('marveen.kanbanCalShowDone') === '1'")
  })

  it('inserts card text only through textContent (no innerHTML with card data)', () => {
    const fn = slice(APP_JS, '  function renderKanbanCalendar() {', '  // --- View switcher init')
    const html = fn.match(/innerHTML\s*=\s*[^\n]*/g) || []
    expect(html).toEqual(["innerHTML = ''"])
  })

  it('has every calendar string in both languages, Hungarian without dashes', () => {
    const keys = ['kanban.view.calendar', 'kanban.cal.today', 'kanban.cal.show_done', 'kanban.cal.empty_month']
    for (const lang of ['en', 'hu'] as const) {
      const win: { _i18n?: Record<string, Record<string, string>> } = {}
      vm.runInNewContext(LANG[lang], { window: win })
      const dict = win._i18n![lang]
      for (const k of keys) {
        expect(typeof dict[k], `${lang} ${k}`).toBe('string')
        expect(dict[k]).not.toMatch(/—| -- /)
      }
    }
  })
})
