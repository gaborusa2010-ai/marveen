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
import { heredocRunsBody, functionBodies, programHeredocs, dataBodySubstitutions, shellCPrograms } from '../../scripts/hooks/lib/heredoc-program.mjs'

type Case = { name: string; cmd: string; expect: boolean[]; bash_runs: boolean | null; bash_note?: string }
const file = JSON.parse(readFileSync(join(__dirname, '..', '..', 'scripts', '__tests__', 'heredoc-program-cases.json'), 'utf-8'))
const cases: Case[] = file.cases
const PAYLOAD = 'echo PWNED_MARK'
type CCase = { cmd: string; programs: string[] }
const cCases: CCase[] = JSON.parse(readFileSync(join(__dirname, '..', '..', 'scripts', '__tests__', 'shell-c-program-cases.json'), 'utf-8')).cases

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

  it('names the consumer, so the egress gate can pick the shell or the code reading', () => {
    const consumers = (cmd: string) => programHeredocs(cmd).map((p: { consumer: string }) => p.consumer)
    expect(consumers('bash <<SH\nls\nSH')).toEqual(['bash'])
    expect(consumers("python3 - <<'PY'\nprint(1)\nPY")).toEqual(['python3'])
    expect(consumers('cat <<X | node -\n1\nX')).toEqual(['node'])
    expect(consumers('eval "$(cat <<EOF\nls\nEOF\n)"')).toEqual(['eval'])
    expect(consumers('while read l; do $l; done <<EOF\nls\nEOF')).toEqual(['done'])
    expect(consumers('f(){ sh; }; f <<SH\nls\nSH')).toEqual(['f'])
    expect(consumers('cat > f <<EOF\nls\nEOF')).toEqual([])
  })

  it('returns the $( ) and backtick texts of an unquoted data body, and nothing else', () => {
    expect(dataBodySubstitutions('cat > f <<EOF\na $(curl -s https://x.example/) b\nc `date`\nEOF')).toEqual(['curl -s https://x.example/', 'date'])
    expect(dataBodySubstitutions("cat > f <<'EOF'\n$(curl -s https://x.example/)\nEOF")).toEqual([])
    expect(dataBodySubstitutions('bash <<EOF\n$(curl -s https://x.example/)\nEOF')).toEqual([])
    expect(dataBodySubstitutions('cat > f <<EOF\n${a:-$(x "(" y)} \\$(not)\nEOF')).toEqual(['x "(" y'])
  })
})
