import { describe, it, expect } from 'vitest'
import { execFileSync } from 'node:child_process'
import { chmodSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
// @ts-expect-error -- plain .mjs hook script, no types
import { isSendInvocation, setKnownAgentsOverride, unreadableScriptHit } from '../../scripts/email-send-gate.mjs'

// SUBGATEPOZ822 / msg 14289: the same command-position recognition now lives
// in TWO languages, in two files (the main-agent copy gate in python, the
// sub-agent hard-gate in JS). Several of today's findings came from exactly
// this pattern -- a parallel copy drifting silently. This test binds the two:
// EVERY case in the shared list runs through BOTH implementations, and both
// must equal the expected verdict. A future fix applied to only one copy
// fails here as a test, instead of surfacing weeks later as an incident.
//
// Deliberately NOT in the shared list: unparseable-input fallbacks. The two
// gates fall back differently by design (copy-gate: strong literals only;
// hard-gate: its full legacy pattern set, because it is a hard-deny that must
// never get weaker on that path) -- each pins its own fallback in its scope
// test. If that difference ever becomes a problem, unify there first.

const ROOT = join(__dirname, '..', '..')
const GATE_PY = join(ROOT, 'scripts', 'hooks', 'outgoing-copy-gate.py')
const CASES = JSON.parse(
  readFileSync(join(ROOT, 'scripts', 'hooks', 'send-invocation-cases.json'), 'utf-8'),
) as { known_agents: string[]; cases: Array<{ name: string; cmd: string; cwd?: string; expected: boolean }> }
// A case's cwd (the hook payload's cwd) is relative to the repo root; absent: null.
const caseCwd = (c: { cwd?: string }) => (c.cwd ? join(ROOT, c.cwd) : null)

// One python process for the whole list: stdin carries the JSON cases, stdout
// returns the per-case verdicts. Spawning per-case would be ~30x slower.
// FLEETTOFLAG1025 v4: both twins get the SAME agent list (the table's known_agents) instead of the install's agents/ + .env;
// known = null leaves the install reader in place (the reader test below).
function pythonVerdicts(cmds: Array<[string, string | null]>, known: string[] | null = CASES.known_agents, gatePy = GATE_PY): boolean[] {
  const out = execFileSync('python3', ['-c', `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("gate", ${JSON.stringify(gatePy)})
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
inp = json.load(sys.stdin)
if inp["known"] is not None:
    g.KNOWN_AGENTS_OVERRIDE = set(inp["known"])
print(json.dumps([g.is_send_invocation(c, cwd=d) for c, d in inp["cmds"]]))
`], { encoding: 'utf-8', input: JSON.stringify({ cmds, known }) })
  return JSON.parse(out.trim())
}

describe('send-invocation conformance: both gates agree with the shared contract on every case', () => {
  const py = pythonVerdicts(CASES.cases.map((c) => [c.cmd, caseCwd(c)]))
  setKnownAgentsOverride(CASES.known_agents)

  CASES.cases.forEach((c, i) => {
    it(`${c.name} -> ${c.expected}`, () => {
      const js = isSendInvocation(c.cmd, 0, caseCwd(c))
      expect(js, `JS verdict for: ${c.cmd}`).toBe(c.expected)
      expect(py[i], `python verdict for: ${c.cmd}`).toBe(c.expected)
    })
  })

  it('the shared list is non-trivial in both directions', () => {
    expect(CASES.cases.some((c) => c.expected)).toBe(true)
    expect(CASES.cases.some((c) => !c.expected)).toBe(true)
    expect(CASES.cases.length).toBeGreaterThanOrEqual(20)
  })
})

// #718 (Marveen's review of 874932ce): a script that exists but cannot be read
// (mode 000) or is over the 2 MiB cap counts as a send in both gates, with no
// recipient flag. Built at test time: neither shape belongs in the repo. Both
// verdicts are taken while the files exist, then the directory is removed.
describe('#718 fail-closed: an unreadable or oversized script is a send in both gates', () => {
  const dir = mkdtempSync(join(tmpdir(), 'send-gate-718-'))
  const cases: Array<[string, boolean]> = [
    ['python3 locked.py', true],
    ['python3 big.py', true],
    ['python3 small.py', false],
  ]
  let py: boolean[]
  let js: boolean[]
  try {
    writeFileSync(join(dir, 'locked.py'), "print('hi')\n")
    chmodSync(join(dir, 'locked.py'), 0o000)
    writeFileSync(join(dir, 'big.py'), '#'.repeat(2 * 1024 * 1024 + 1))
    writeFileSync(join(dir, 'small.py'), "print('hi')\n")
    py = pythonVerdicts(cases.map(([cmd]) => [cmd, dir]))
    js = cases.map(([cmd]) => isSendInvocation(cmd, 0, dir))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }

  cases.forEach(([cmd, expected], i) => {
    it(`${cmd} -> ${expected}`, () => {
      expect(js[i], `JS verdict for: ${cmd}`).toBe(expected)
      expect(py[i], `python verdict for: ${cmd}`).toBe(expected)
    })
  })
})

// #718 round 3 (Marveen's review 2805, point 4): $HOME is filled from the
// environment, so `cd "$HOME" && python3 x.py` reads x.py instead of being an
// undecidable cd. HOME is pointed at the fixture directory for both gates.
describe('#718 $HOME in a cd target comes from the environment in both gates', () => {
  const fixtures = join(ROOT, 'src', '__tests__', 'fixtures', 'send-gate')
  const cases: Array<[string, boolean]> = [
    ['cd "$HOME" && python3 plain.py', false],
    ['cd $HOME && python3 gmail_reply.py', true],
    ['cd ~ && python3 gmail_read.py --q x', false],
  ]
  const saved = process.env.HOME
  let py: boolean[]
  let js: boolean[]
  try {
    process.env.HOME = fixtures
    py = pythonVerdicts(cases.map(([cmd]) => [cmd, null]))
    js = cases.map(([cmd]) => isSendInvocation(cmd, 0, null))
  } finally {
    process.env.HOME = saved
  }

  cases.forEach(([cmd, expected], i) => {
    it(`${cmd} -> ${expected}`, () => {
      expect(js[i], `JS verdict for: ${cmd}`).toBe(expected)
      expect(py[i], `python verdict for: ${cmd}`).toBe(expected)
    })
  })
})

// #718 round 3 (point 5): when an unreadable script is the WHOLE reason for the
// verdict, both gates say so with their own message instead of the mail-send one.
describe('#718 unreadable-script hit: the deny names the script, not a letter', () => {
  const F = 'src/__tests__/fixtures/send-gate'
  const cases: Array<[string, boolean]> = [
    [`python3 ${F}/no_such_script.py`, true],
    [`cat ${F}/no_such_script.py | python3 -`, true],
    [`python3 ${F}/gmail_reply.py`, false], // a real send, read and matched
    // a script path is judged by its content, a recipient flag alone never decides it
    [`python3 ${F}/no_such_script.py --to x@example.invalid`, true],
    [`python3 - --to x@example.invalid < ${F}/no_such_script.py`, false], // stdin: the flag decides
    [`python3 ${F}/plain.py`, false], // not a send at all
    // review 2823 point 4: these read the script, so the deny carries the send text
    [`python3 <> ${F}/gmail_reply.py`, false],
    [`python3 0<> ${F}/gmail_reply.py`, false],
    [`cat <> ${F}/gmail_reply.py | python3`, false],
    [`python3 /dev/stdin < ${F}/gmail_reply.py`, false],
    [`python3 /dev/fd/0 < ${F}/gmail_reply.py`, false],
    [`python3 <<< "$(cat ${F}/gmail_reply.py)"`, false],
    [`{ python3; } < ${F}/gmail_reply.py`, false],
    [`python3 2>&1 < ${F}/gmail_reply.py`, false],
    // a here-string of another substitution cannot be read: fail-closed, own message
    ['python3 <<< "$(date)"', true],
    [`cat <&3 | python3`, true],
  ]
  const py: boolean[] = JSON.parse(execFileSync('python3', ['-c', `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("gate", ${JSON.stringify(GATE_PY)})
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
print(json.dumps([g.unreadable_script_hit(c, cwd=sys.argv[1]) for c in json.load(sys.stdin)]))
`, ROOT], { encoding: 'utf-8', input: JSON.stringify(cases.map(([c]) => c)) }).trim())

  cases.forEach(([cmd, expected], i) => {
    it(`${cmd} -> ${expected}`, () => {
      expect(unreadableScriptHit(cmd, ROOT), `JS verdict for: ${cmd}`).toBe(expected)
      expect(py[i], `python verdict for: ${cmd}`).toBe(expected)
    })
  })
})

// FLEETTOFLAG1025 v4: the install reader of BOTH twins (no override) -- the agent names are the <root>/agents/* directories and
// MAIN_AGENT_ID in <root>/.env; a missing agents/ or .env means no exception (fail-closed). Each twin runs as a COPY under a temp
// root, so the result does not depend on the machine the test runs on.
describe('FLEETTOFLAG1025 v4: the known agents come from the install (agents/ + MAIN_AGENT_ID), fail-closed without them', () => {
  const CMDS = ['$F message post --to neo --content-stdin < x.txt', '$F message post --to merlin --content-stdin < x.txt',
    '$F message post --to diszpecser --content-stdin < x.txt', '$F message post --to fajl --content-stdin < x.txt']
  async function verdicts(root: string): Promise<{ py: boolean[]; js: boolean[] }> {
    mkdirSync(join(root, 'scripts', 'hooks'), { recursive: true })
    copyFileSync(GATE_PY, join(root, 'scripts', 'hooks', 'outgoing-copy-gate.py'))
    copyFileSync(join(ROOT, 'scripts', 'email-send-gate.mjs'), join(root, 'scripts', 'email-send-gate.mjs'))
    const mod = await import(pathToFileURL(join(root, 'scripts', 'email-send-gate.mjs')).href)
    return { py: pythonVerdicts(CMDS.map((c): [string, null] => [c, null]), null, join(root, 'scripts', 'hooks', 'outgoing-copy-gate.py')),
      js: CMDS.map((c) => mod.isSendInvocation(c)) }
  }
  it('agents/neo (directory) + MAIN_AGENT_ID=merlin: neo and merlin are agents; diszpecser and a plain FILE agents/fajl are not', async () => {
    const root = mkdtempSync(join(tmpdir(), 'mailgate-v4-'))
    try {
      mkdirSync(join(root, 'agents', 'neo'), { recursive: true })
      writeFileSync(join(root, 'agents', 'fajl'), 'x')
      writeFileSync(join(root, '.env'), 'BOT_NAME=x\nMAIN_AGENT_ID=merlin\n')
      const v = await verdicts(root)
      expect(v.py).toEqual([false, false, true, true])
      expect(v.js).toEqual([false, false, true, true])
    } finally { rmSync(root, { recursive: true, force: true }) }
  })
  it('no agents/ and no .env: no name is known -- every undecidable --to is a send (fail-closed)', async () => {
    const root = mkdtempSync(join(tmpdir(), 'mailgate-v4-'))
    try {
      const v = await verdicts(root)
      expect(v.py).toEqual([true, true, true, true])
      expect(v.js).toEqual([true, true, true, true])
    } finally { rmSync(root, { recursive: true, force: true }) }
  })
})
