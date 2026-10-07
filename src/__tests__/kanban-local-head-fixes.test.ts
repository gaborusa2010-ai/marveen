// Local fixes for regressions the fork's own changes introduced (none of them exist
// on the upstream base), one describe each:
//  - a bare "YYYY-MM-DD" due/start date is stored as UTC midnight, so the edit modal
//    (which reads it back with toISOString) shows the same day in any time zone;
//  - POST /api/kanban accepts start_date without logging it as an unknown field;
//  - the aging rule paints the left edge with --card-aging-color again;
//  - static layout: the view switcher wraps, and the card modal's Project field has
//    its own row instead of being the third field in a two-column row.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { Readable } from 'node:stream'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type http from 'node:http'

const warn = vi.fn()
vi.mock('../logger.js', () => ({
  logger: { info: vi.fn(), warn: (...a: unknown[]) => warn(...a), debug: vi.fn(), error: vi.fn() },
}))

vi.mock('../db.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../db.js')>()
  return { ...actual, createAgentMessage: vi.fn() }
})

import { initDatabase, createKanbanCard, getKanbanCard, updateKanbanCard } from '../db.js'
import { tryHandleKanban } from '../web/routes/kanban.js'

async function call(method: string, path: string, payload?: unknown): Promise<{ status: number; body: any }> {
  const text = payload === undefined ? '' : JSON.stringify(payload)
  const req = Readable.from([Buffer.from(text)]) as unknown as http.IncomingMessage
  let status = 200
  let chunk = ''
  const res = {
    writeHead: (s: number) => { status = s; return res },
    end: (c?: unknown) => { if (c !== undefined) chunk = String(c) },
    setHeader: () => {},
  } as unknown as http.ServerResponse
  const handled = await tryHandleKanban({ req, res, path, method, url: new URL(`http://localhost${path}`) } as never)
  expect(handled).toBe(true)
  return { status, body: chunk ? JSON.parse(chunk) : undefined }
}

// What the edit modal does with a stored epoch (web/app.js, openEditCardModal).
const modalDay = (epoch: number | null | undefined) => new Date(Number(epoch) * 1000).toISOString().split('T')[0]

const savedTz = process.env.TZ
beforeEach(() => {
  initDatabase(':memory:')
  warn.mockClear()
})
afterEach(() => {
  if (savedTz === undefined) delete process.env.TZ
  else process.env.TZ = savedTz
})

describe('bare date strings are stored as UTC midnight', () => {
  // East of UTC is where local midnight read back as the previous day.
  for (const tz of ['Europe/London', 'Asia/Tokyo', 'America/New_York']) {
    it(`create + edit round-trip keeps the day in ${tz}`, () => {
      process.env.TZ = tz
      createKanbanCard({ id: 'c1', title: 'Dated', due_date: '2026-07-15' as never, start_date: '2026-07-01' as never })
      let card = getKanbanCard('c1')!
      expect(card.due_date).toBe(Date.UTC(2026, 6, 15) / 1000)
      expect(modalDay(card.due_date)).toBe('2026-07-15')
      expect(modalDay(card.start_date)).toBe('2026-07-01')

      updateKanbanCard('c1', { due_date: '2026-08-02' as never })
      card = getKanbanCard('c1')!
      expect(modalDay(card.due_date)).toBe('2026-08-02')
    })
  }

  it('POSITIVE CONTROL: epochs (number or numeric text) and full ISO timestamps pass through unchanged', () => {
    process.env.TZ = 'Europe/London'
    createKanbanCard({ id: 'n', title: 'num', due_date: 1784073600 })
    createKanbanCard({ id: 't', title: 'text', due_date: '1784073600' as never })
    createKanbanCard({ id: 'i', title: 'iso', due_date: '2026-07-15T09:30:00Z' as never })
    expect(getKanbanCard('n')!.due_date).toBe(1784073600)
    expect(getKanbanCard('t')!.due_date).toBe(1784073600)
    expect(getKanbanCard('i')!.due_date).toBe(Date.UTC(2026, 6, 15, 9, 30) / 1000)
  })

  it('an unparseable string is stored as null, not NaN', () => {
    createKanbanCard({ id: 'x', title: 'bad', due_date: 'next tuesday' as never })
    expect(getKanbanCard('x')!.due_date).toBeNull()
  })
})

describe('POST /api/kanban with start_date', () => {
  const unknownFieldWarns = () => warn.mock.calls.filter((c) => /ismeretlen|unknown/i.test(String(c[1])))

  it('stores start_date and logs no unknown-field warning', async () => {
    const out = await call('POST', '/api/kanban', { title: 'Planned', start_date: Date.UTC(2026, 9, 12) / 1000 })
    expect(out.status).toBeLessThan(300)
    expect(getKanbanCard(out.body.id)!.start_date).toBe(Date.UTC(2026, 9, 12) / 1000)
    expect(unknownFieldWarns()).toEqual([])
  })

  it('POSITIVE CONTROL: a genuinely unknown key is still warned about, by name', async () => {
    await call('POST', '/api/kanban', { title: 'Typo', strat_date: 1 })
    const calls = unknownFieldWarns()
    expect(calls).toHaveLength(1)
    expect(calls[0][0].keys).toEqual(['strat_date'])
  })
})

describe('static web fixes', () => {
  const web = (f: string) => readFileSync(join(__dirname, '..', '..', 'web', f), 'utf8')

  it('the aging rule colours the left edge, after the priority block', () => {
    const css = web('style.css')
    const aging = css.search(/^\.kanban-card\[data-aging\]\s*\{[^}]*border-left-color:\s*var\(--card-aging-color\)/m)
    const lastPriority = Math.max(...[...css.matchAll(/^(?:\[data-theme="dark"\] )?\.kanban-card\[data-priority="[a-z]+"\]/gm)].map((m) => m.index!))
    expect(aging).toBeGreaterThan(-1)
    expect(aging).toBeGreaterThan(lastPriority)
  })

  it('the Board / Timeline / Team / Archived switcher row wraps', () => {
    const html = web('index.html')
    const row = html.match(/<div style="([^"]*)">\s*<button class="view-btn active" id="kanbanViewBoard"/)
    expect(row?.[1]).toMatch(/flex-wrap:\s*wrap/)
  })

  it('the card modal has at most two fields per row, and Project is not beside Due', () => {
    const html = web('index.html')
    const modal = html.slice(html.indexOf('id="cardModalOverlay"'))
    const rows = modal.split('<div class="form-row">').slice(1, 6)
    for (const r of rows) {
      const head = r.split('<div class="form-row">')[0]
      expect((head.match(/class="form-group form-half"/g) || []).length).toBeLessThanOrEqual(2)
    }
    const dueRow = rows.find((r) => r.includes('id="cardDue"'))!
    expect(dueRow.slice(0, dueRow.indexOf('id="cardDue"') + 400)).not.toContain('id="cardProject"')
  })
})
