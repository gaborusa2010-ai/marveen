// Does a here-document body become a PROGRAM, or the INPUT of a program?
//
// The JS twin of `_heredoc_runs_body` in scripts/hooks/destructive-gate.py (#748).
// Two JS gates need the same answer -- the child-claude guard (which must keep a
// body the shell runs visible) and the bash egress gate (which must read a body
// the shell runs as a command line) -- and before this file each had its own,
// narrower reading. The #729 review (Marveen, comment 3284) listed the shapes
// bash runs and the gates let through:
//
//   bash 0<<SH                     the 0 is a file descriptor, not a script name
//   eval "$(cat <<EOF ... )"       the OUTER command runs the substitution's output
//   builtin eval "$(cat <<EOF)"    `builtin` is transparent
//   . <(cat <<SH ... )             process substitution sourced
//   bash <(cat <<SH ... )          process substitution run as a script file
//   while read l; do $l; done <<EOF  the loop body runs a dynamic command word
//   f(){ bash; }; f <<SH           the function body holds a shell reading stdin
//   bash \<newline><<SH, `bash <<SH`, sudo -u x bash <<SH, if ..; then bash <<SH,
//   bash -s -- a <<SH, ! bash <<SH   (the child guard's own misses)
//
// Both twins are pinned to ONE case file, scripts/__tests__/heredoc-program-cases.json:
// scripts/__tests__/heredoc-program.test.py checks Python, src/__tests__/
// heredoc-program-parity.test.ts checks this file. A change to one side fails the
// other until both agree again. Every must-run row in that file was checked against
// bash itself (the body's payload really ran).
//
// The answer is the CONSUMER: the command word that reads the body as a program
// ('' when none). Callers that only need yes/no test truthiness; the egress gate
// uses the word to choose between the shell reading and the code reading.

import { find as findHeredocs } from './heredoc-scan.mjs'

/** Interpreters that run their stdin as a program when no script is named. */
export const INTERPRETERS = new Set(['python', 'python3', 'bash', 'sh', 'zsh', 'node', 'perl', 'ruby', 'php'])
/** Flags after which the PROGRAM follows (stdin is data), per interpreter: bash -e is
 *  errexit, not a script; python3 -m runs a module whose input the body is. */
export const STDIN_SCRIPT_FLAGS = {
  bash: ['-c', '--command'], sh: ['-c'], zsh: ['-c', '--command'],
  python: ['-c', '-m'], python3: ['-c', '-m'],
  node: ['-e', '-p', '--eval', '--print'], perl: ['-e', '-E'], ruby: ['-e'], php: ['-r'],
}
const SHELLS = new Set(['sh', 'bash', 'zsh', 'dash', 'ksh'])

const KEYWORDS = new Set(['if', 'then', 'elif', 'else', 'fi', 'while', 'until', 'do', 'done',
  'case', 'esac', 'in', 'select', 'function', 'coproc', '!', '{', '}', '(', ')', '[[', ']]'])
const TRANSPARENT = new Set(['exec', 'command', 'time', 'nohup', 'env', 'builtin'])
const MAX_NEST = 8
// Longer opener lines are not split into words: 4000 heredocs on one line took 31 s
// in the Python twin (measured 2026-11-01). The body then stays code: over-block, not a gap.
const LINE_CAP = 4096

// The `&` of `>&1`, `<&0`, `&>log` is a redirect, not a command boundary (`bash 2>&1 <<SH`).
const SIMPLE_SPLIT = /\|\||&&|(?<![<>])&(?!>)|[;|\n(){}`]|\$\(/
const HEAD_SPLIT = /(\|\||&&|(?<![<>])&(?!>)|[;|(`]|\$\()/
const TAIL_SPLIT = /\|\||&&|;|(?<![<>])&(?!>)/
const FUNC_DEF_RE = /(?:\bfunction\s+)?([A-Za-z_][A-Za-z0-9_:.-]*)\s*\(\s*\)\s*([{(])|\bfunction\s+([A-Za-z_][A-Za-z0-9_:.-]*)\s*([{(])/g
const CLOSERS = { done: ['while', 'until', 'for', 'select'], fi: ['if'], esac: ['case'], '}': ['{'] }
const COMPOUND_TOKEN_RE = /(?<![\w$-])(while|until|for|select|done|if|fi|case|esac)(?![\w-])|[{}]/g

const baseName = (w) => w.split('/').pop()

/** [word without quotes and backslashes, dynamic?]: `$P`, `{a,b}`, `pytho?3` expand. */
function word(tok) {
  return [tok.replace(/["'\\]/g, ''), /[$*?[{`]/.test(tok)]
}

/** [words, k]: the segment's words and the index of its command word (k == length: none). */
function cmdWord(seg) {
  const words = seg.trim().split(/\s+/).filter(Boolean).map(word)
  let k = 0
  while (k < words.length) {
    const w = words[k][0]
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(w) || KEYWORDS.has(w) || TRANSPARENT.has(baseName(w))) k++
    else if (/^([0-9]*[<>]|&>)/.test(w)) k += /^([0-9]*[<>]+&?|&>>?)$/.test(w) ? 2 : 1   // a bare operator takes the next word
    else break
  }
  return [words, k]
}

/** Does one simple command run its STDIN as a program? Returns the consumer word or ''. */
export function runsStdinAsProgram(seg) {
  const [words, k] = cmdWord(seg)
  if (k >= words.length) return ''
  const [w, dyn] = words[k]
  const base = baseName(w)
  if (dyn || base === 'source' || base === '.' || base === 'eval') return w
  if (words.slice(k).some(([x]) => x.startsWith('-S') || x.startsWith('--split-string'))) return base
  if (!INTERPRETERS.has(base)) {
    // An interpreter behind an unknown wrapper (timeout, strace, sudo, ...) runs the
    // body all the same; the wrapper's argument shape is not guessed.
    const inner = words.slice(k).find(([x]) => INTERPRETERS.has(baseName(x)))
    return inner ? baseName(inner[0]) : ''
  }
  const shell = SHELLS.has(base)
  const flags = STDIN_SCRIPT_FLAGS[base] ?? []
  for (let j = k + 1; j < words.length; j++) {
    const [x, xdyn] = words[j]
    if (xdyn || x === '-' || x === '-s') return base
    if (/^([0-9]*[<>]+&?|&>>?)$/.test(x)) { j++; continue }       // a bare redirect operator takes the next word
    if (/^([0-9]*[<>]|&>)/.test(x)) continue                    // a glued redirect (2>&1, >log) is no operand
    if (flags.includes(x) || (shell && /^-[A-Za-z]*c/.test(x))) return ''   // the program follows the flag
    if (shell && /^[-+][oO]$/.test(x)) { j++; continue }  // `-o pipefail` takes a value
    if (x.startsWith('-') || (shell && x.startsWith('+'))) continue
    return ''                         // a program FILE: the body is its input
  }
  return base
}

function anyRunsStdin(text) {
  for (const p of text.split(SIMPLE_SPLIT)) {
    if (!p.trim()) continue
    const c = runsStdinAsProgram(p)
    if (c) return c
  }
  return ''
}

/** {name: body text} for every `name() { ... }` / `name() ( ... )` / `function name { ... }`. */
export function functionBodies(cmd) {
  const out = {}
  for (const m of cmd.matchAll(FUNC_DEF_RE)) {
    const name = m[1] || m[3]
    const opener = m[2] || m[4]
    const closer = opener === '{' ? '}' : ')'
    let depth = 1
    let i = m.index + m[0].length
    while (i < cmd.length && depth) {
      if (cmd[i] === opener) depth++
      else if (cmd[i] === closer) depth--
      i++
    }
    out[name] = cmd.slice(m.index + m[0].length, depth === 0 ? i - 1 : i)
  }
  return out
}

/** The compound command closed by `closer` (done/fi/esac/}) from its opener to `end`. */
function compoundText(cmd, end, closer) {
  const openers = CLOSERS[closer]
  const stack = []
  let start = 0
  for (const m of cmd.slice(0, end).matchAll(COMPOUND_TOKEN_RE)) {
    const tok = m[0]
    if (openers.includes(tok)) stack.push(m.index)
    else if (tok === closer) start = stack.length ? stack.pop() : 0   // unmatched: everything before (over-block)
  }
  return cmd.slice(start, end)
}

/** The opener's head or one pipe member: the interpreter itself, a function whose body
 *  runs stdin, or (head only) the closer of a compound whose body runs stdin. */
function simpleRuns(seg, funcs, cmd, end) {
  const direct = runsStdinAsProgram(seg)
  if (direct) return direct
  const [words, k] = cmdWord(seg)
  if (k < words.length && Object.hasOwn(funcs, words[k][0]) && anyRunsStdin(funcs[words[k][0]])) return words[k][0]
  const last = words.length ? words[words.length - 1][0] : ''
  if (cmd !== undefined && Object.hasOwn(CLOSERS, last)) return anyRunsStdin(compoundText(cmd, end, last)) ? last : ''
  return ''
}

/** The head's innermost part is a substitution: the OUTER command decides.
 *    eval "$(cat <<EOF)" -> program    . <(cat <<SH) / bash <(cat <<SH) -> program
 *    bash -c "$(cat <<EOF)" -> program  x=$(cat <<EOF) / echo "$(cat <<EOF)" -> data
 *  `parts` is the head split with its separators kept: [text, sep, text, ...]. */
function enclosingRuns(parts, funcs) {
  let i = parts.length - 1
  let depth = 0
  while (i >= 2 && depth < MAX_NEST) {
    const sep = parts[i - 1]
    let outer = parts[i - 2]
    if (sep !== '$(' && sep !== '`' && sep !== '(') return ''
    const procsub = sep === '(' && /[<>]$/.test(outer.trimEnd())
    if (sep === '(' && !procsub) return ''          // a subshell: `( cat <<EOF )` is the cat's
    if (procsub) outer = outer.trimEnd().slice(0, -1)
    const [words, k] = cmdWord(outer)
    if (k < words.length) {
      const [w, dyn] = words[k]
      const base = baseName(w)
      if (dyn || base === 'source' || base === '.' || base === 'eval') return w
      if (Object.hasOwn(funcs, base) && anyRunsStdin(funcs[base])) return base
      const flags = STDIN_SCRIPT_FLAGS[base] ?? []
      if (INTERPRETERS.has(base) && (procsub || words.slice(k + 1).some(([x]) => flags.includes(x)))) return base
      if (INTERPRETERS.has(base)) return ''       // `bash "$(cat <<EOF)"`: the output names a script FILE
    }
    i -= 2
    depth++
  }
  return ''
}

/**
 * The consumer that runs heredoc `h` (a record from lib/heredoc-scan.mjs) of `cmd`
 * as a program, or '' when the body is data. `funcs` is functionBodies(cmd),
 * passed in by callers that judge several heredocs of one command.
 */
export function heredocRunsBody(cmd, h, funcs) {
  let ls = cmd.lastIndexOf('\n', h.start - 1)
  while (ls > 0 && cmd[ls - 1] === '\\') ls = cmd.lastIndexOf('\n', ls - 2)
  if (h.start - ls > LINE_CAP || h.lineEnd - h.tailStart > LINE_CAP) return 'unread'
  if (funcs === undefined) funcs = functionBodies(cmd)
  let head = cmd.slice(ls + 1, h.start).replaceAll('\\\n', '')
  const fd = head.match(/(^|\s)([0-9]+)$/)         // `bash 0<<SH`: a file descriptor, not a script
  if (fd) {
    head = head.slice(0, fd.index + fd[1].length)
    const n = fd[2].replace(/^0+/, '')
    if (n) {
      // `bash 0<<A 3<<B`: on fd 3 the body is not stdin, DATA, unless the line reads the
      // descriptor as a file (`bash /dev/fd/3 3<<SH`): then it is the program.
      const line = head + cmd.slice(h.tailStart, h.lineEnd)
      if (!new RegExp('/(?:dev|proc/self)/fd/0*' + n + '(?![0-9])').test(line)) return ''
      const [words, k] = cmdWord(head)
      return k < words.length ? words[k][0] : ''
    }
  }
  const parts = head.split(HEAD_SPLIT)
  const tail = cmd.slice(h.tailStart, h.lineEnd).split(TAIL_SPLIT)[0]
  const pipe = tail.split('|')
  const first = simpleRuns(parts[parts.length - 1] + ' ' + pipe[0], funcs, cmd, h.start)
  if (first) return first
  for (const p of pipe.slice(1)) {
    const c = simpleRuns(p, funcs)
    if (c) return c
  }
  return enclosingRuns(parts, funcs)
}

/**
 * Every heredoc body of `cmd` that is RUN, with its consumer's base name:
 * [{ consumer, body }]. The mirror of extractHeredocBodies() for program bodies.
 * An unterminated heredoc still has text, and "truncated" must not read as
 * "nothing to inspect": the scanner returns it with the rest of the command.
 */
export function programHeredocs(cmd) {
  const s = String(cmd ?? '')
  if (!s.includes('<<')) return []
  const { heredocs } = findHeredocs(s)
  const funcs = functionBodies(s)
  const out = []
  for (const h of heredocs) {
    const consumer = heredocRunsBody(s, h, funcs)
    if (consumer) out.push({ consumer: baseName(consumer), body: s.slice(h.bodyStart, h.bodyEnd).replace(/\n$/, '') })
  }
  return out
}

/**
 * The `$( )` and backtick texts inside the UNQUOTED-tag heredoc bodies that are
 * data: the body itself is not run, but the shell expands those before the
 * consumer ever sees it (`cat > f <<EOF ... $(curl ...) ... EOF`). The
 * destructive gate keeps such bodies visible as "plain" ranges for the same
 * reason; the egress gate judges each text as a command line of its own.
 */
/** Index of the `)` closing a `$(` whose text starts at `i`; body.length when unclosed.
 *  Quoted text and backslash-escaped characters do not count (`$(x "(" y)`). */
function substitutionEnd(body, i) {
  let depth = 1
  let quote = ''
  for (; i < body.length; i++) {
    const c = body[i]
    if (quote) {
      if (c === '\\' && quote === '"') i++
      else if (c === quote) quote = ''
      continue
    }
    if (c === '\\') i++
    else if (c === '"' || c === "'") quote = c
    else if (c === '(') depth++
    else if (c === ')' && --depth === 0) return i
  }
  return body.length
}

export function dataBodySubstitutions(cmd) {
  const s = String(cmd ?? '')
  if (!s.includes('<<')) return []
  const { heredocs } = findHeredocs(s)
  const funcs = functionBodies(s)
  const out = []
  for (const h of heredocs) {
    if (h.quoted || heredocRunsBody(s, h, funcs)) continue
    const body = s.slice(h.bodyStart, h.bodyEnd)
    for (let i = 0; i < body.length; i++) {
      if (body[i] === '\\') { i++; continue }
      if (body.startsWith('$(', i)) {
        const j = substitutionEnd(body, i + 2)
        out.push(body.slice(i + 2, j < body.length ? j : body.length))
        i = j
      } else if (body[i] === '`') {
        const j = body.indexOf('`', i + 1)
        out.push(body.slice(i + 1, j < 0 ? body.length : j))
        i = j < 0 ? body.length : j
      }
    }
  }
  return out
}
