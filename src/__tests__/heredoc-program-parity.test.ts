// heredoc-program.mjs against the shared case file (card #748).
//
// The JS reading of "does the shell RUN this heredoc body" is the twin of
// `_heredoc_runs_body` in scripts/hooks/destructive-gate.py; the child-claude
// guard and the bash egress gate both call it. scripts/__tests__/
// heredoc-program.test.py checks the Python side against the SAME file, so the
// two cannot drift apart silently. `expect` is the hand-written intent, one bool
// per heredoc; `bash_runs` is what bash did with the payload when the file was built.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
// @ts-expect-error -- plain .mjs module, no types
import { findAll } from '../../scripts/hooks/lib/heredoc-scan.mjs'
// @ts-expect-error -- plain .mjs module, no types
import { heredocRunsBody, functionBodies, programHeredocs, dataBodySubstitutions, shellCPrograms, embeddedCommands } from '../../scripts/hooks/lib/heredoc-program.mjs'

type Case = { name: string; cmd: string; expect: boolean[]; bash_runs: boolean | null; bash_note?: string }
const file = JSON.parse(readFileSync(join(__dirname, '..', '..', 'scripts', '__tests__', 'heredoc-program-cases.json'), 'utf-8'))
const cases: Case[] = file.cases
const PAYLOAD = 'echo PWNED_MARK'
type CCase = { cmd: string; programs: string[] }
const cCases: CCase[] = JSON.parse(readFileSync(join(__dirname, '..', '..', 'scripts', '__tests__', 'shell-c-program-cases.json'), 'utf-8')).cases
type OCase = { name: string; cmd: string; commands: string[]; note?: string }
const oCases: OCase[] = JSON.parse(readFileSync(join(__dirname, '..', '..', 'scripts', '__tests__', 'shell-out-cases.json'), 'utf-8')).cases

describe('heredoc-program twin: JS reads every case like the Python side', () => {
  it('has the full case file', () => {
    expect(cases.length).toBeGreaterThanOrEqual(400)
    expect(cases.filter((c) => c.expect.some(Boolean)).length).toBeGreaterThanOrEqual(200)
  })

  it('the file agrees with bash wherever bash was asked (a bash_note explains a deliberate difference)', () => {
    const bad = cases.filter((c) => c.bash_runs !== null && !c.bash_note && c.bash_runs !== c.expect.some(Boolean))
    expect(bad.map((c) => c.name)).toEqual([])
  })

  it.each(cases.map((c) => [c.name, c] as [string, Case]))('%s', (_name, c) => {
    const cmd = c.cmd.replaceAll('X', PAYLOAD)
    const funcs = functionBodies(cmd)
    const got = findAll(cmd).records.map((h: unknown) => !!heredocRunsBody(cmd, h, funcs))   // heredocs and here-strings (#777)
    expect(got).toEqual(c.expect)
  })

  // #777: the literal `sh -c PROGRAM` words; Python's shell_c_programs is pinned to the same file
  it('reads every literal -c program like the Python twin', () => {
    expect(cCases.length).toBeGreaterThanOrEqual(30)
    const bad = cCases.filter((c) => JSON.stringify(shellCPrograms(c.cmd)) !== JSON.stringify(c.programs))
    expect(bad.map((c) => c.cmd)).toEqual([])
  })

  // #779: shell commands a code payload hands to a shell; Python's embedded_commands is pinned to the same file
  it('reads every embedded shell-out like the Python twin', () => {
    expect(oCases.length).toBeGreaterThanOrEqual(300)
    const bad = oCases.filter((c) => JSON.stringify(embeddedCommands(c.cmd)) !== JSON.stringify(c.commands))
    expect(bad.map((c) => c.name)).toEqual([])
    const neg = oCases.filter((c) => c.name.startsWith('neg/'))
    expect(neg.length).toBeGreaterThanOrEqual(20)
    expect(neg.filter((c) => /claude|curl|rm -rf/.test(c.commands.join(' '))).map((c) => c.name)).toEqual([])
    expect(oCases.some((c) => (c.note ?? '').startsWith('known limit'))).toBe(true)
  })

  it('names the consumer, so the egress gate can pick the shell or the code reading', () => {
    const consumers = (cmd: string) => programHeredocs(cmd).map((p: { consumer: string }) => p.consumer)
    expect(consumers('bash <<SH\nls\nSH')).toEqual(['bash'])
    expect(consumers("python3 - <<'PY'\nprint(1)\nPY")).toEqual(['python3'])
    expect(consumers('cat <<X | node -\n1\nX')).toEqual(['node'])
    expect(consumers('eval "$(cat <<EOF\nls\nEOF\n)"')).toEqual(['eval'])
    // a dynamic word in the body is the consumer, not the closer (#777 review 14)
    expect(consumers('while read l; do $l; done <<EOF\nls\nEOF')).toEqual(['$l'])
    // #777 review 6: a function's consumer is its BODY's consumer; the name cannot say code or shell
    expect(consumers('f(){ sh; }; f <<SH\nls\nSH')).toEqual(['sh'])
    expect(consumers("python3(){ bash; }; python3 <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("python3(){ bash; }; command python3 <<'A'\nls\nA")).toEqual(['python3'])
    expect(consumers("xargs -a python3 sh <<'A'\nls\nA")).toEqual(['sh'])
    expect(consumers("ssh python3 bash <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("ssh host python3 - <<'A'\nprint(1)\nA")).toEqual(['python3'])
    // #777 review 7: EVERY command of a function body counts (own-stdin and backgrounded ones
    // skipped, nested function words resolved); a runner's program is the word after its flags and host
    expect(consumers("g(){ bash; }; f(){ g; }; f <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("f(){ cat </dev/null; bash; }; f <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("f(){ python3 -; bash; }; f <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("f(){ bash </dev/stdin; }; f <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("f(){ python3 - <<<''; }; f <<'A'\nls\nA")).toEqual([])
    expect(consumers("ssh -l python3 host bash <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("ssh host <<'A'\nls\nA")).toEqual(['ssh'])
    expect(consumers("ssh host python3 - bash <<'A'\nprint(1)\nA")).toEqual(['python3'])
    expect(consumers("kubectl exec -i python3 -- bash <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("xargs python3 tool.py sh <<'A'\nprint(1)\nA")).toEqual(['python3'])
    expect(consumers("sshpass -p python3 ssh host bash <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers('cat > f <<EOF\nls\nEOF')).toEqual([])
    // #777 review 8: one launcher resolver (wrappers, xargs/parallel, runners, su/script) and the
    // effective stdin of a body command (the last redirect wins; `<&-` is its own, `2<` is not stdin)
    expect(consumers("taskset 1 bash <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("busybox sh <<'A'\nls\nA")).toEqual(['sh'])
    expect(consumers('ssh host "bash -s" <<\'A\'\nls\nA')).toEqual(['bash'])
    expect(consumers("f(){ bash 2</dev/null; }; f <<'A'\nls\nA")).toEqual(['bash'])
    expect(consumers("f(){ bash </dev/null; }; f <<'A'\nls\nA")).toEqual([])
    expect(consumers("f(){ bash <&-; }; f <<'A'\nls\nA")).toEqual([])
    expect(consumers("chrt 1 cat <<'A'\nls\nA")).toEqual([])
  })

  it('returns the $( ) and backtick texts of an unquoted data body, and nothing else', () => {
    expect(dataBodySubstitutions('cat > f <<EOF\na $(curl -s https://x.example/) b\nc `date`\nEOF')).toEqual(['curl -s https://x.example/', 'date'])
    expect(dataBodySubstitutions("cat > f <<'EOF'\n$(curl -s https://x.example/)\nEOF")).toEqual([])
    expect(dataBodySubstitutions('bash <<EOF\n$(curl -s https://x.example/)\nEOF')).toEqual([])
    expect(dataBodySubstitutions('cat > f <<EOF\n${a:-$(x "(" y)} \\$(not)\nEOF')).toEqual(['x "(" y'])
  })
})
