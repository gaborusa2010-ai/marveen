// Owner, Telegram msg 5948 (2026-10-05): "Rád vár egyben". The owner strip on
// the kanban page used to show only the cards ASSIGNED to him (3 that day),
// while 12 open cards carried the "Rád vár" label, so the strip showed a quarter
// of what was actually waiting on him. Now a card reaches the strip through
// either door, and fleet.py kanban-rad-var lets an agent open the second door
// with one call.
//
// Two halves, two techniques:
// - the dashboard predicate is pulled out of web/app.js and evaluated in a vm,
//   because the strip is browser code with no module boundary to import;
// - the fleet.py command runs against a patched urlopen, the same way
//   fleet-helper-search-carries-the-label.test.ts does it, so no live server
//   and no real board is touched.
import { describe, it, expect } from 'vitest'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const APP_JS = readFileSync(join(ROOT, 'web', 'app.js'), 'utf-8')
const SCRIPT = join(ROOT, 'seed-skills', 'fleet-helper', 'scripts', 'fleet.py')

function slice(src: string, startMarker: string, endMarker: string): string {
  const a = src.indexOf(startMarker)
  const b = src.indexOf(endMarker, a)
  if (a < 0 || b < 0) throw new Error(`marker not found: ${startMarker}`)
  return src.slice(a, b)
}

// The constant, the normaliser and the predicate sit together right above
// renderKanbanOwnerStrip; kanbanHideFromLanes is evaluated next to them so the
// "label does not hide from the lane" half is checked on the real function.
const ctx: Record<string, unknown> = { owner: 'Gabor' }
vm.createContext(ctx)
vm.runInContext(
  slice(APP_JS, "const KANBAN_OWNER_QUEUE_LABEL", 'function renderKanbanOwnerStrip()') +
    slice(APP_JS, 'function kanbanHideFromLanes(card)', 'function renderSwimlaneBoard(') +
    "function ownerAssigneeName() { return owner }\n" +
    "globalThis.onStrip = kanbanIsOnOwnerStrip; globalThis.hide = kanbanHideFromLanes",
  ctx,
)
const onStrip = ctx.onStrip as (c: unknown, owner: string) => boolean
const hide = ctx.hide as (c: unknown) => boolean

const RAD_VAR = { id: 'e6c42279', name: 'Rád vár' }

describe('owner strip: assignee OR the "Rád vár" label', () => {
  it('takes his own open card, as before', () => {
    expect(onStrip({ status: 'planned', assignee: 'gabor', labels: [] }, 'Gabor')).toBe(true)
  })

  it("takes an agent's card that carries the label", () => {
    expect(onStrip({ status: 'waiting', assignee: 'forgacs', labels: [RAD_VAR] }, 'Gabor')).toBe(true)
  })

  it('folds accents and case in the label name', () => {
    const typed = { id: 'x', name: '  rad VAR ' }
    expect(onStrip({ status: 'planned', assignee: 'cody', labels: [typed] }, 'Gabor')).toBe(true)
  })

  it('leaves an unlabelled agent card and every done card out', () => {
    expect(onStrip({ status: 'planned', assignee: 'cody', labels: [{ id: 'y', name: 'Bug' }] }, 'Gabor')).toBe(false)
    expect(onStrip({ status: 'done', assignee: 'forgacs', labels: [RAD_VAR] }, 'Gabor')).toBe(false)
    expect(onStrip({ status: 'done', assignee: 'gabor', labels: [] }, 'Gabor')).toBe(false)
  })

  it('survives a card with no labels field at all', () => {
    expect(onStrip({ status: 'planned', assignee: 'cody' }, 'Gabor')).toBe(false)
  })

  it("does NOT hide a labelled agent card from its agent's lane; only his cards are hidden", () => {
    expect(hide({ status: 'waiting', assignee: 'forgacs', labels: [RAD_VAR] })).toBe(false)
    expect(hide({ status: 'planned', assignee: 'Gabor', labels: [] })).toBe(true)
  })
})

const HARNESS = `
import json, sys, importlib.util
spec = importlib.util.spec_from_file_location("fleet", sys.argv[1])
fleet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)

state = json.loads(sys.argv[2])
calls = []

class FakeResp:
    def __init__(self, obj): self._b = json.dumps(obj).encode(); self.headers = {}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False

def fake_urlopen(req, timeout=20):
    path = "/" + req.full_url.split("/", 3)[3]
    m = req.get_method()
    calls.append([m, path])
    if m == "GET" and path == "/api/kanban":
        return FakeResp(state["cards"])
    if m == "GET" and path == "/api/kanban/labels":
        return FakeResp(state["labels"])
    card = next((c for c in state["cards"] if path.startswith("/api/kanban/" + c["id"] + "/labels")), None)
    if m == "POST" and card:
        lid = json.loads(req.data)["labelId"]
        card["labels"].append(next(l for l in state["labels"] if l["id"] == lid))
        return FakeResp({"ok": True})
    if m == "DELETE" and card:
        lid = path.rsplit("/", 1)[1]
        card["labels"] = [l for l in card["labels"] if l["id"] != lid]
        return FakeResp({"ok": True})
    if m == "GET" and card:
        return FakeResp(card["labels"])
    raise AssertionError("unexpected call " + m + " " + path)

fleet.urllib.request.urlopen = fake_urlopen
fleet.token = lambda: "test-token"
try:
    out = fleet.kanban_owner_queue(sys.argv[3], remove=sys.argv[4] == "1")
except RuntimeError as e:
    out = {"error": str(e)}
print(json.dumps({"out": out, "calls": calls}, ensure_ascii=False))
`

type Run = { out: Record<string, unknown>; calls: [string, string][] }

function run(ref: string, remove: boolean, cardLabels: unknown[], labelName = 'Rád vár'): Run {
  const dir = mkdtempSync(join(tmpdir(), 'fleet-radvar-test-'))
  mkdirSync(join(dir, 'store'), { recursive: true })
  writeFileSync(join(dir, 'store', '.dashboard-token'), 'test-token\n', 'utf-8')
  const state = {
    cards: [{ seq: 427, id: '67cacea0', title: 't', assignee: 'cody', labels: cardLabels }],
    labels: [{ id: 'e6c42279', name: labelName }, { id: 'aa', name: 'Bug' }],
  }
  const stdout = execFileSync('python3', ['-c', HARNESS, SCRIPT, JSON.stringify(state), ref, remove ? '1' : '0'], {
    // PYTHONDONTWRITEBYTECODE: see fleet-helper-search-carries-the-label.test.ts --
    // a .pyc under seed-skills/ trips template-identity-hygiene.test.ts.
    env: { ...process.env, CLAW_DIR: dir, CLAW_BASE: 'http://127.0.0.1:1', PYTHONDONTWRITEBYTECODE: '1' },
    encoding: 'utf-8',
  })
  return JSON.parse(stdout) as Run
}

const writes = (r: Run) => r.calls.filter(([m]) => m !== 'GET')

describe('fleet.py kanban-rad-var', () => {
  it('puts the label on by card number, through the API, with the label id', () => {
    const r = run('427', false, [])
    expect(writes(r)).toEqual([['POST', '/api/kanban/67cacea0/labels']])
    expect(r.out).toMatchObject({ seq: 427, id: '67cacea0', on_owner_strip: true, changed: true })
  })

  it('accepts #427 and the hex id as well', () => {
    expect(run('#427', false, []).out.changed).toBe(true)
    expect(run('67cacea0', false, []).out.changed).toBe(true)
  })

  it('is idempotent: no second POST when the label is already on', () => {
    const r = run('427', false, [{ id: 'e6c42279', name: 'Rád vár' }])
    expect(writes(r)).toEqual([])
    expect(r.out).toMatchObject({ on_owner_strip: true, changed: false })
  })

  it('--le takes it off with a DELETE, and is idempotent too', () => {
    const r = run('427', true, [{ id: 'e6c42279', name: 'Rád vár' }])
    expect(writes(r)).toEqual([['DELETE', '/api/kanban/67cacea0/labels/e6c42279']])
    expect(r.out).toMatchObject({ on_owner_strip: false, changed: true })
    expect(writes(run('427', true, []))).toEqual([])
  })

  it('finds the label by folded name, the same way the dashboard does', () => {
    expect(run('427', false, [], 'rad var').out.changed).toBe(true)
  })

  it('refuses an unknown card loudly instead of guessing', () => {
    const r = run('99999', false, [])
    expect(String(r.out.error)).toContain("no card '99999'")
    expect(writes(r)).toEqual([])
  })
})
