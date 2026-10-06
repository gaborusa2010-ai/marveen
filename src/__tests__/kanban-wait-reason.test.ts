import { describe, it, expect, beforeEach } from 'vitest'
import { spawnSync } from 'node:child_process'
import { Readable } from 'node:stream'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import {
  initDatabase, getDb, createKanbanCard, getKanbanCard, moveKanbanCard,
  createLabel, getLabelsForCard, getBlockersForCard,
  setKanbanWaitReason, validateKanbanWaitReason,
} from '../db.js'
import { tryHandleKanban } from '../web/routes/kanban.js'
import type { RouteContext } from '../web/routes/types.js'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

function route(method: string, path: string, body?: unknown) {
  const out: { status: number; body: any } = { status: 200, body: null }
  const res: any = {
    writeHead(status: number) { out.status = status; return res },
    setHeader() { return res },
    end(chunk?: string) { if (chunk) out.body = JSON.parse(chunk) },
  }
  const payload = body === undefined ? '' : JSON.stringify(body)
  const req: any = Readable.from(payload ? [Buffer.from(payload)] : [])
  req.headers = {}
  const url = new URL(`http://localhost:3420${path}`)
  return {
    ctx: { req, res, path: url.pathname, method, url } as RouteContext,
    out,
  }
}

describe('wait-reason database contract', () => {
  beforeEach(() => initDatabase(':memory:'))

  it('creates all three columns and persists then clears a reason', () => {
    const columns = getDb().prepare('PRAGMA table_info(kanban_cards)').all() as Array<{ name: string }>
    expect(columns.map((c) => c.name)).toEqual(expect.arrayContaining(['wait_kind', 'wait_note', 'wait_until']))
    createKanbanCard({ id: 'a', title: 'A', status: 'waiting' })
    setKanbanWaitReason('a', { kind: 'date', note: 'review', until: 1_900_000_000 })
    expect(getKanbanCard('a')).toMatchObject({ wait_kind: 'date', wait_note: 'review', wait_until: 1_900_000_000 })
    setKanbanWaitReason('a', null)
    expect(getKanbanCard('a')).toMatchObject({ wait_kind: null, wait_note: null, wait_until: null })
  })

  it('validates every kind and rejects incomplete or unknown shapes', () => {
    expect(validateKanbanWaitReason({ kind: 'owner' })).toMatchObject({ ok: true, reason: { kind: 'owner' } })
    expect(validateKanbanWaitReason({ kind: 'external', note: 'Vendor' })).toMatchObject({ ok: true })
    expect(validateKanbanWaitReason({ kind: 'date', until: 123 })).toMatchObject({ ok: true })
    expect(validateKanbanWaitReason({ kind: 'card', blockerId: 'b' })).toMatchObject({ ok: true })
    for (const raw of [
      { kind: 'external' }, { kind: 'date' }, { kind: 'card' }, { kind: 'mystery' },
      { kind: 'owner', surprise: true }, { kind: 'owner', note: 'x'.repeat(201) },
    ]) expect(validateKanbanWaitReason(raw).ok).toBe(false)
  })

  it.each(['Rád vár', 'rad var'])('owner attaches the folded %s label and clearing removes it', (name) => {
    createKanbanCard({ id: 'a', title: 'A', status: 'waiting' })
    createLabel({ id: 'owner-label', name, color: '#fff' })
    setKanbanWaitReason('a', { kind: 'owner' })
    expect(getKanbanCard('a')!.wait_kind).toBe('owner')
    expect(getLabelsForCard('a').map((l) => l.id)).toEqual(['owner-label'])
    setKanbanWaitReason('a', null)
    expect(getLabelsForCard('a')).toHaveLength(0)
  })

  it('refuses owner when the label does not exist without changing stored state', () => {
    createKanbanCard({ id: 'a', title: 'A', status: 'waiting' })
    expect(() => setKanbanWaitReason('a', { kind: 'owner' })).toThrow('Rád vár')
    expect(getKanbanCard('a')!.wait_kind).toBeNull()
  })

  it('card reason persists and creates the blocker relationship', () => {
    createKanbanCard({ id: 'a', title: 'A', status: 'waiting' })
    createKanbanCard({ id: 'b', title: 'B' })
    setKanbanWaitReason('a', { kind: 'card', blockerId: 'b', note: 'dependency' })
    expect(getKanbanCard('a')).toMatchObject({ wait_kind: 'card', wait_note: 'dependency' })
    expect(getBlockersForCard('a').map((c) => c.id)).toEqual(['b'])
  })

  it('leaving waiting clears the reason and owner label, while reordering inside waiting keeps both', () => {
    createKanbanCard({ id: 'a', title: 'A', status: 'waiting' })
    createLabel({ id: 'owner-label', name: 'Rád vár', color: '#fff' })
    setKanbanWaitReason('a', { kind: 'owner', note: 'decision' })
    moveKanbanCard('a', 'waiting', 9)
    expect(getKanbanCard('a')).toMatchObject({ status: 'waiting', sort_order: 9, wait_kind: 'owner', wait_note: 'decision' })
    expect(getLabelsForCard('a')).toHaveLength(1)
    moveKanbanCard('a', 'in_progress', 0)
    expect(getKanbanCard('a')).toMatchObject({ status: 'in_progress', wait_kind: null, wait_note: null, wait_until: null })
    expect(getLabelsForCard('a')).toHaveLength(0)
  })

  it('moving to waiting without a reason remains supported and stores NULL', () => {
    createKanbanCard({ id: 'a', title: 'A' })
    expect(moveKanbanCard('a', 'waiting', 0)).toBe(true)
    expect(getKanbanCard('a')).toMatchObject({ status: 'waiting', wait_kind: null })
  })
})

describe('wait-reason routes', () => {
  beforeEach(() => initDatabase(':memory:'))

  it('invalid /move wait returns 400 and does not move the card', async () => {
    createKanbanCard({ id: 'a', title: 'A', status: 'planned' })
    const { ctx, out } = route('POST', '/api/kanban/a/move', { status: 'waiting', wait: { kind: 'external' } })
    await tryHandleKanban(ctx)
    expect(out.status).toBe(400)
    expect(getKanbanCard('a')).toMatchObject({ status: 'planned', wait_kind: null })
  })

  it('/move rejects wait on a non-waiting target and preserves the card', async () => {
    createKanbanCard({ id: 'a', title: 'A', status: 'planned' })
    const { ctx, out } = route('POST', '/api/kanban/a/move', { status: 'done', wait: { kind: 'date', until: 123 } })
    await tryHandleKanban(ctx)
    expect(out.status).toBe(400)
    expect(getKanbanCard('a')!.status).toBe('planned')
  })

  it('valid /move wait moves the card and stores the reason with its blocker', async () => {
    createKanbanCard({ id: 'a', title: 'A', status: 'planned' })
    createKanbanCard({ id: 'b', title: 'B' })
    const { ctx, out } = route('POST', '/api/kanban/a/move', { status: 'waiting', wait: { kind: 'card', blockerId: 'b' } })
    await tryHandleKanban(ctx)
    expect(out.status).toBe(200)
    expect(getKanbanCard('a')).toMatchObject({ status: 'waiting', wait_kind: 'card' })
    expect(getBlockersForCard('a').map((c) => c.id)).toEqual(['b'])
  })

  it('/move with a card wait that would close a cycle is 409 and does not move', async () => {
    createKanbanCard({ id: 'a', title: 'A', status: 'planned' })
    createKanbanCard({ id: 'b', title: 'B', status: 'waiting' })
    setKanbanWaitReason('b', { kind: 'card', blockerId: 'a' })
    const { ctx, out } = route('POST', '/api/kanban/a/move', { status: 'waiting', wait: { kind: 'card', blockerId: 'b' } })
    await tryHandleKanban(ctx)
    expect(out.status).toBe(409)
    expect(getKanbanCard('a')).toMatchObject({ status: 'planned', wait_kind: null })
  })

  it('PUT /wait on a non-waiting card is 409 and stores nothing', async () => {
    createKanbanCard({ id: 'a', title: 'A', status: 'planned' })
    const { ctx, out } = route('PUT', '/api/kanban/a/wait', { kind: 'date', until: 123 })
    await tryHandleKanban(ctx)
    expect(out.status).toBe(409)
    expect(getKanbanCard('a')!.wait_kind).toBeNull()
  })

  it('PUT of a whole card accepts wait_* read-only fields and still applies writable changes', async () => {
    createKanbanCard({ id: 'a', title: 'A', status: 'waiting' })
    setKanbanWaitReason('a', { kind: 'external', note: 'Vendor' })
    const card = getKanbanCard('a')!
    const { ctx, out } = route('PUT', '/api/kanban/a', { ...card, title: 'Changed' })
    await tryHandleKanban(ctx)
    expect(out.status).toBe(200)
    expect(getKanbanCard('a')).toMatchObject({ title: 'Changed', wait_kind: 'external', wait_note: 'Vendor' })
  })
})

describe('kanbanWaitReason browser inference', () => {
  const source = readFileSync(join(ROOT, 'web', 'app.js'), 'utf8')
  const start = source.indexOf("const KANBAN_OWNER_QUEUE_LABEL")
  const end = source.indexOf('function renderKanbanOwnerStrip()', start)
  const ctx: Record<string, any> = {}
  vm.createContext(ctx)
  vm.runInContext(source.slice(start, end) + '\nglobalThis.waitReason = kanbanWaitReason', ctx)
  const reason = ctx.waitReason as (card: any) => any

  it('prefers an explicit reason over label and blocker inference', () => {
    expect(reason({ wait_kind: 'external', wait_note: 'Vendor', labels: [{ name: 'Rád vár' }], blockers: [{ status: 'planned' }] }))
      .toMatchObject({ kind: 'external', note: 'Vendor', source: 'explicit' })
  })

  it('infers owner from a folded label, then card only from open blockers', () => {
    expect(reason({ labels: [{ name: 'rad VAR' }], blockers: [{ status: 'planned' }] })).toMatchObject({ kind: 'owner', source: 'label' })
    expect(reason({ labels: [], blockers: [{ status: 'planned' }] })).toMatchObject({ kind: 'card', source: 'blockers' })
    expect(reason({ labels: [], blockers: [{ status: 'done' }] })).toBeNull()
  })

  it('marks an expired explicit date', () => {
    expect(reason({ wait_kind: 'date', wait_until: 1 })).toMatchObject({ kind: 'date', until: 1, expired: true })
  })
})

const FLEET_HARNESS = String.raw`
import json, sys, importlib.util
spec = importlib.util.spec_from_file_location("fleet", sys.argv[1])
fleet = importlib.util.module_from_spec(spec); spec.loader.exec_module(fleet)
state = {"cards": [{"seq": 7, "id": "card7", "title": "Seven", "status": sys.argv[2], "wait_kind": None}]}
calls = []
class Resp:
  headers = {}
  def __init__(self, obj): self.data = json.dumps(obj).encode()
  def read(self): return self.data
  def __enter__(self): return self
  def __exit__(self, *args): return False
def urlopen(req, timeout=20):
  path = "/" + req.full_url.split("/", 3)[3]; method = req.get_method()
  payload = json.loads(req.data) if req.data else None
  calls.append([method, path, payload])
  if method == "GET": return Resp(state["cards"])
  card = state["cards"][0]
  if method == "POST": card.update({"status": "waiting", "wait_kind": payload["wait"]["kind"]})
  elif method == "PUT": card.update({"wait_kind": payload["kind"]})
  elif method == "DELETE": card.update({"wait_kind": None})
  return Resp({"ok": True})
fleet.urllib.request.urlopen = urlopen; fleet.token = lambda: "token"
code = fleet.main(["kanban-var"] + json.loads(sys.argv[3]))
print(json.dumps({"code": code, "calls": calls, "card": state["cards"][0]}))
sys.exit(code)
`

function fleet(status: string, args: string[]) {
  return spawnSync('python3', ['-c', FLEET_HARNESS, join(ROOT, 'seed-skills/fleet-helper/scripts/fleet.py'), status, JSON.stringify(args)], {
    encoding: 'utf8', env: { ...process.env, CLAW_BASE: 'http://test', PYTHONDONTWRITEBYTECODE: '1' },
  })
}

describe('fleet.py kanban-var', () => {
  it('moves a non-waiting card with the wait body', () => {
    const r = fleet('planned', ['7', 'kulso', 'Vendor'])
    const parsed = JSON.parse(r.stdout.trim().split('\n').at(-1)!)
    expect(parsed.calls).toContainEqual(['POST', '/api/kanban/card7/move', { status: 'waiting', sort_order: 0, wait: { kind: 'external', note: 'Vendor' } }])
    expect(parsed.card).toMatchObject({ status: 'waiting', wait_kind: 'external' })
  })

  it('updates an already-waiting card via PUT /wait', () => {
    const parsed = JSON.parse(fleet('waiting', ['7', 'datum', '2030-01-02']).stdout.trim().split('\n').at(-1)!)
    expect(parsed.calls.some((c: any[]) => c[0] === 'PUT' && c[1] === '/api/kanban/card7/wait' && c[2].kind === 'date')).toBe(true)
    expect(parsed.card.wait_kind).toBe('date')
  })

  it('--torol uses DELETE and the refreshed state is clear', () => {
    const parsed = JSON.parse(fleet('waiting', ['7', '--torol']).stdout.trim().split('\n').at(-1)!)
    expect(parsed.calls).toContainEqual(['DELETE', '/api/kanban/card7/wait', null])
    expect(parsed.card.wait_kind).toBeNull()
  })

  it.each([
    { args: ['7'] }, { args: ['7', 'kulso'] }, { args: ['7', 'datum', 'not-a-date'] },
    { args: ['7', 'kulso', 'Vendor', '--megj', 'extra'] },
  ])('bad args exit 2, print usage, and make no write: $args', ({ args }) => {
    const r = fleet('planned', args)
    expect(r.status).toBe(2)
    expect(r.stderr).toContain('usage: fleet.py kanban-var')
    const parsed = JSON.parse(r.stdout.trim().split('\n').at(-1)!)
    expect(parsed.calls.filter((c: any[]) => c[0] !== 'GET')).toEqual([])
    expect(parsed.card).toMatchObject({ status: 'planned', wait_kind: null })
  })
})
