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
// The #748 review (Marveen, 2026-11-02) added the shapes the first round still let through:
//
//   bash /dev/stdin <<EOF, bash /dev/fd/0 <<EOF   the "script file" IS the stdin
//   bash 3<<EOF <&3                 the dup moves the fd-3 body onto stdin
//   (bash) <<EOF, tee >(bash) <<EOF a subshell / process substitution around the shell
//   bash <<<"$(cat <<EOF ... )"     the here-string makes the substitution the stdin
//
// and ONE over-block it introduced: a wrapper-less unknown command whose ARGUMENT
// happens to be an interpreter name (`grep -c python <<EOF`, `git commit -m "node
// fix" -F - <<EOF`) was read as a program. The interpreter must stand in command
// position: directly, or behind a wrapper whose argument shape is known (below).
//
// The #748 round-2 review (Marveen, 2026-11-02) left three pre-existing shapes (#773):
//
//   { bash; } 2>&1 <<EOF, done 2>&1 <<EOF, fi >/dev/null <<EOF   a redirect between the
//                                   compound's closer and the heredoc hid the closer
//   dash <<EOF, ksh <<EOF           shells the SHELLS set knew but INTERPRETERS did not
//   while read l; do bash -c "$l"; done <<EOF   the -c program is a word READ from the body:
//                                   where a body text reads stdin (read/mapfile/readarray),
//                                   an interpreter whose -c program is dynamic runs it
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

import { find as findHeredocs, findAll, hereWord } from './heredoc-scan.mjs'

/** Interpreters that run their stdin as a program when no script is named. */
export const INTERPRETERS = new Set(['python', 'python3', 'bash', 'sh', 'zsh', 'dash', 'ksh', 'rbash', 'node', 'perl', 'ruby', 'php'])
/** Flags after which the PROGRAM follows (stdin is data), per interpreter: bash -e is
 *  errexit, not a script; python3 -m runs a module whose input the body is. */
export const STDIN_SCRIPT_FLAGS = {
  bash: ['-c', '--command'], sh: ['-c'], zsh: ['-c', '--command'], dash: ['-c'], ksh: ['-c'], rbash: ['-c', '--command'],
  python: ['-c', '-m'], python3: ['-c', '-m'],
  node: ['-e', '-p', '--eval', '--print'], perl: ['-e', '-E'], ruby: ['-e'], php: ['-r'],
}
export const SHELLS = new Set(['sh', 'bash', 'zsh', 'dash', 'ksh', 'rbash'])

const KEYWORDS = new Set(['if', 'then', 'elif', 'else', 'fi', 'while', 'until', 'do', 'done',
  'case', 'esac', 'in', 'select', 'function', 'coproc', '!', '{', '}', '(', ')', '[[', ']]'])
const TRANSPARENT = new Set(['exec', 'command', 'time', 'nohup', 'env', 'builtin'])
// Options of a transparent prefix that take their value in the NEXT word (`env -u VAR`,
// `exec -a NAME`, `time -o FILE`); every other `-x` word behind the prefix is one option.
const PREFIX_VALUES = { env: ['-u', '--unset', '-C', '--chdir'], exec: ['-a'], time: ['-f', '-o', '--format', '--output'] }
const MAX_NEST = 8
// Longer opener lines are not split into words: 4000 heredocs on one line took 31 s
// in the Python twin (measured 2026-11-01). The body then stays code: over-block, not a gap.
const LINE_CAP = 4096

// Wrappers that hand their stdin to the command they run. `values` are the options
// that take their value in a SEPARATE word (`sudo -u x`, `timeout -k 1 5`); `skip` is
// the number of positional operands before the command (timeout's DURATION, chroot's
// NEWROOT, flock's FILE). The command behind the wrapper is read like any other.
const WRAPPERS = {
  sudo: { values: ['-u', '-g', '-C', '-D', '-h', '-p', '-r', '-t', '-T', '-U', '--user', '--group', '--chdir', '--host', '--prompt', '--role', '--type', '--other-user'], skip: 0 },
  doas: { values: ['-u', '-C'], skip: 0 },
  timeout: { values: ['-k', '-s', '--kill-after', '--signal'], skip: 1 },
  nice: { values: ['-n', '--adjustment'], skip: 0 },
  ionice: { values: ['-c', '-n', '-p', '-P', '-u'], skip: 0 },
  stdbuf: { values: ['-i', '-o', '-e'], skip: 0 },
  strace: { values: ['-e', '-o', '-p', '-s', '-a', '-b', '-E', '-P', '-S', '-X', '-I', '-O', '-u'], skip: 0 },
  ltrace: { values: ['-e', '-o', '-p', '-s', '-a', '-n', '-u', '-l', '-x'], skip: 0 },
  chroot: { values: ['--userspec', '--groups'], skip: 1 },
  flock: { values: ['-w', '-E', '--timeout', '--conflict-exit-code'], skip: 1 },
  setsid: { values: [], skip: 0 },
  unbuffer: { values: [], skip: 0 },
  watch: { values: ['-n', '-d', '--interval', '--differences'], skip: 0 },
  caffeinate: { values: ['-t', '-w'], skip: 0 },
  unshare: { values: ['-S', '-G', '--setuid', '--setgid', '-w', '--wd', '-R', '--root', '--map-user', '--map-group', '--map-users', '--map-groups'], skip: 0 },
  nsenter: { values: ['-t', '--target', '-S', '-G', '--setuid', '--setgid', '-w', '--wd', '-r', '--root'], skip: 0 },
  'systemd-run': { values: ['-p', '--property', '-u', '--unit', '-E', '--setenv', '--description', '-M', '--machine', '--slice', '--on-active', '--on-calendar', '--uid', '--gid', '--nice', '--working-directory'], skip: 0 },
}
// `sudo -s <<EOF` / `sudo -i <<EOF` with no command: the user's shell reads the body.
const SHELL_FLAGS_OF = { sudo: ['-s', '-i', '--shell', '--login'], doas: ['-s'] }
// These turn the body into ARGUMENTS of the command they run (`xargs -I{} sh -c {}`):
// the body is a program whenever an interpreter stands anywhere behind them.
const ARGV_FEEDERS = new Set(['xargs', 'parallel'])
// Remote and container runners (#777 review 7): the program is the first non-option word after
// the host/container, and the values of their value-taking options are skipped (`ssh -l python3
// host bash`: the value is not the host, the host is not the program; `ssh host python3 - bash`:
// python3 is the program, bash only its argument). docker/podman/nerdctl and kubectl carry a
// subcommand (exec/run/create/debug, also behind `compose`/`container` and global options)
// before the options; an unknown subcommand falls back to the first interpreter word (the
// pre-review-6 shape). sshpass has no host: the word after its options is the program
// (usually an ssh, read the same way).
const REMOTE_RUNNERS = new Set(['ssh', 'mosh', 'sshpass', 'plink', 'docker', 'podman', 'nerdctl', 'kubectl'])
const DOCKER_VALUE_FLAGS = ['-u', '--user', '-w', '--workdir', '-e', '--env', '--env-file', '--detach-keys', '-v', '--volume',
     '-p', '--publish', '--name', '--network', '--net', '--entrypoint', '-l', '--label', '--label-file',
     '--mount', '--cpus', '-m', '--memory', '--memory-swap', '-h', '--hostname', '--add-host', '--cap-add',
     '--cap-drop', '--device', '--dns', '--dns-search', '--dns-option', '--domainname', '--expose', '--gpus',
     '--ipc', '--log-driver', '--log-opt', '--pid', '--platform', '--pull', '--restart', '--security-opt',
     '--shm-size', '--stop-signal', '--stop-timeout', '--sysctl', '--tmpfs', '--ulimit', '--userns', '--uts',
     '--volumes-from', '--cidfile', '--cgroup-parent', '--ip', '--ip6', '--mac-address', '--index', '--link',
     '--runtime', '--storage-opt', '--group-add', '--isolation', '--pids-limit', '--cpu-shares',
     '--cpuset-cpus', '--cpuset-mems', '--oom-score-adj', '--health-cmd', '--health-interval', '--profile',
     '-f', '--file', '--project-directory', '--project-name', '-H', '--host', '--context', '-c',
     '--config', '-L', '--log-level', '--connection', '--url', '--namespace', '--address']
const RUNNER_VALUE_FLAGS = {
  ssh: ['-l', '-p', '-i', '-o', '-F', '-J', '-b', '-c', '-D', '-E', '-e', '-I', '-L', '-m', '-O', '-Q', '-R',
    '-S', '-W', '-w', '-B', '-P'],
  mosh: ['-p', '--port', '--ssh', '--server', '--predict', '--family', '--bind-server', '--experimental-remote-ip'],
  plink: ['-l', '-P', '-i', '-pw', '-pwfile', '-m', '-L', '-R', '-D', '-proxycmd', '-hostkey', '-nc', '-sercfg',
    '-sshlog', '-sshrawlog', '-loghost'],
  sshpass: ['-p', '-f', '-d', '-P'],
  docker: DOCKER_VALUE_FLAGS, podman: DOCKER_VALUE_FLAGS, nerdctl: DOCKER_VALUE_FLAGS,
  kubectl: ['-n', '--namespace', '-c', '--container', '--context', '--kubeconfig', '--cluster', '--user', '--as',
     '--as-group', '--request-timeout', '-f', '--filename', '--pod-running-timeout', '-s', '--server',
     '--token', '--image', '--env', '--port', '--overrides', '--restart', '--labels', '-l',
     '--image-pull-policy', '--serviceaccount', '--target', '--profile', '--copy-to', '--set-image',
     '--cache-dir', '--certificate-authority', '--client-certificate', '--client-key', '-v'],
}
const SUBCOMMAND_RUNNERS = new Set(['docker', 'podman', 'nerdctl', 'kubectl'])
const RUNNER_SUBCOMMANDS = new Set(['exec', 'run', 'create', 'debug'])
const RUNNER_SUBCOMMAND_PREFIX = new Set(['compose', 'container'])
// xargs/parallel options that take their value in a SEPARATE word: the value is not the
// command (#777 review 6: `xargs -a python3 sh <<A`, `parallel -a python3 sh <<A` run sh).
// xargs' deprecated `-i` carries its value glued only, so it is not here.
const FEEDER_VALUE_FLAGS = new Set([
  '-I', '-n', '-P', '-L', '-s', '-d', '-E', '-a', '-j', '-S', '-N',
  '--max-args', '--max-procs', '--max-lines', '--max-chars', '--delimiter', '--replace',
  '--arg-file', '--eof', '--process-slot-var', '--jobs', '--sshlogin', '--sshloginfile',
  '--slf', '--colsep', '--joblog', '--results', '--tmpdir', '--wd', '--workdir',
  '--block', '--retries', '--timeout', '--halt'])

/** The index of the first word after an xargs/parallel's options (words.length when none). */
function afterFeederOptions(words, k) {
  let j = k + 1
  while (j < words.length) {
    const x = words[j][0]
    if (x === '--') return j + 1
    if (!x.startsWith('-')) return j
    j += FEEDER_VALUE_FLAGS.has(x) ? 2 : 1
  }
  return j
}

/** The first interpreter word from `j` on, -1 when none: the fallback for a runner of unknown
 *  shape (#777 review 7). */
function firstInterpreter(words, j) {
  for (let i = j; i < words.length; i++) if (INTERPRETERS.has(baseName(words[i][0]))) return i
  return -1
}

/** The PROGRAM behind an xargs/parallel's options, -1 when none: the first word past the
 *  prefixes (assignments, transparent prefixes, known wrappers with their options). The body
 *  becomes this word's ARGUMENTS, so its name decides, not a later word's (#777 review 7:
 *  `xargs python3 tool.py sh` runs python3, sh is only an argument; review 6's "shell word
 *  wins" over-blocked here). */
function argvProgram(words, j) {
  for (;;) {
    j = skipPrefix(words, j)
    if (j >= words.length) return -1
    const base = baseName(words[j][0])
    if (!Object.hasOwn(WRAPPERS, base)) return j
    const nj = afterWrapper(words, j, WRAPPERS[base])
    if (nj < 0) return j
    j = nj
  }
}

/** The PROGRAM of the remote/container runner at word `k`, -1 when none: the subcommand
 *  (docker/podman/nerdctl/kubectl), the options (value-taking ones with their value), the
 *  host/container/pod, then the next word; `--` ends the options. An unknown subcommand falls
 *  back to the first interpreter word (#777 review 7). */
function runnerProgram(words, k) {
  const base = baseName(words[k][0])
  const flags = RUNNER_VALUE_FLAGS[base] ?? []
  let j = k + 1
  let skip = base === 'sshpass' ? 0 : 1
  if (SUBCOMMAND_RUNNERS.has(base)) {
    while (j < words.length && (words[j][0].startsWith('-') || RUNNER_SUBCOMMAND_PREFIX.has(words[j][0]))) j += flags.includes(words[j][0]) ? 2 : 1
    if (j >= words.length || !RUNNER_SUBCOMMANDS.has(words[j][0])) return firstInterpreter(words, k + 1)
    j++
  }
  while (j < words.length) {
    const x = words[j][0]
    if (x === '--') {
      j++
      if (skip) { skip--; j++; continue }
      return j < words.length ? j : -1
    }
    if (x.startsWith('-')) { j += flags.includes(x) ? 2 : 1; continue }
    if (skip) { skip--; j++; continue }
    return j
  }
  return -1
}

// A path that IS the standard input: `bash /dev/stdin <<EOF` runs the body.
const STDIN_PATH_RE = /^\/(?:dev\/stdin|(?:dev|proc\/self)\/fd\/0+)$/
const REDIR_OP_RE = /^([0-9]*[<>]+&?|[0-9]*>\||&>>?)$/   // a bare operator: the next word is its target (`>|` too, #775)
const REDIR_GLUED_RE = /^([0-9]*[<>]|&>)/             // `2>&1`, `>log`: no operand
// A shell option cluster (`-xo`, `+O`, `-xoo`): EVERY o/O in it takes one value word
// (`bash -xo pipefail`, `bash -c -xoo pipefail errexit "$l"`, measured with real bash, #775 review).
const OPT_CLUSTER_RE = /^[-+][A-Za-z]+$/

/** How many value words a shell option cluster takes (its o/O count), 0 when it is not one. */
function optValues(word) {
  return OPT_CLUSTER_RE.test(word) ? (word.match(/[oO]/g) ?? []).length : 0
}

// bash's GNU long options, with one OR two dashes (`bash -norc`, `bash --rcfile F`): bash reads
// them only at the START of its argument list, not after the first short option (`bash -x
// --norc`: invalid option; `bash -x -norc`: the n,o,r,c cluster). None is a -c cluster and no
// o in them takes a value; rcfile/init-file take one value word (`--rcfile=F` is invalid).
// Some never run the body (help, version, dump-*, pretty-print, wordexp, protected,
// rpm-requires). Measured with real bash, 61 forms (#775 round-2 review).
const BASH_LONG_OPT_RE = /^--?(debug|debugger|dump-po-strings|dump-strings|help|init-file|login|noediting|noprofile|norc|posix|pretty-print|protected|rcfile|restricted|verbose|version|wordexp|rpm-requires)$/
const BASH_LONG_OPT_VALUE = new Set(['rcfile', 'init-file'])
const BASH_LONG_OPT_NO_RUN = new Set(['help', 'version', 'dump-po-strings', 'dump-strings', 'pretty-print', 'protected', 'wordexp', 'rpm-requires'])

// The `&` of `>&1`, `<&0`, `&>log` is a redirect, not a command boundary (`bash 2>&1 <<SH`);
// neither is the `|` of `>|` (`bash >| log <<SH`, #775).
const SIMPLE_SPLIT = /\|\||&&|(?<![<>])&(?!>)|[;\n(){}`]|(?<!>)\||\$\(/
// the same, keeping its separators: [command, separator, command, ...] (#777 review 7: a `&`
// AFTER the command backgrounds it, and then it does not read the inherited stdin)
const SIMPLE_SPLIT_KEEP = /(\|\||&&|(?<![<>])&(?!>)|[;\n(){}`]|(?<!>)\||\$\()/
// an own input redirect at the start of a word (`<f`, `0<f`, `<<<x`, `<<T`, `< <(..)`); a `<&n`
// dup is NOT one, it may be the inherited stdin itself
const OWN_STDIN_RE = /^0?(<<<|<<|<>|<)(?!&)/
const HEAD_SPLIT = /(\|\||&&|(?<![<>])&(?!>)|[;(`]|(?<!>)\||\$\()/
const TAIL_SPLIT = /\|\||&&|;|(?<![<>])&(?!>)/
const PIPE_SPLIT = /(?<!>)\|/
// One shell word: unquoted chars, backslash-escaped chars, "..." and '...' pieces. An
// unpaired quote is dropped (the segment may be cut at a substitution boundary).
const SHELL_WORD_RE = /(?:[^\s"'\\]|\\.|"(?:[^"\\]|\\.)*"|'[^']*')+/g
const FUNC_DEF_RE = /(?:\bfunction\s+)?([A-Za-z_][A-Za-z0-9_:.-]*)\s*\(\s*\)\s*([{(])|\bfunction\s+([A-Za-z_][A-Za-z0-9_:.-]*)\s*([{(])/g
const CLOSERS = { done: ['while', 'until', 'for', 'select'], fi: ['if'], esac: ['case'], '}': ['{'] }
const COMPOUND_TOKEN_RE = /(?<![\w$-])(while|until|for|select|done|if|fi|case|esac)(?![\w-])|[{}]/g
const HERESTRING_TAIL_RE = /<<<\s*["']?\s*$/
// A body text that READS its stdin (`while read l; do ...`, `f(){ read l; ...}`): a
// dynamic -c program in it (`bash -c "$l"`) is a line of the body (#773).
const READS_STDIN_RE = /(?<![\w$-])(read|mapfile|readarray)(?![\w-])/

const baseName = (w) => w.split('/').pop()

/** [word without quotes and backslashes, dynamic?]: `$P`, `{a,b}`, `pytho?3` expand. */
function word(tok) {
  // Shell-rule unquoting (#777): a quote or backslash inside single quotes stays, so the
  // program of `bash -c 'bash -c "source /dev/stdin"'` keeps its inner quotes (the old
  // strip-everything form gave `bash -c source /dev/stdin`, and /dev/stdin read as $0).
  let out = ''
  let dyn = false                  // a `$` in single quotes or behind a backslash does not expand
  for (let i = 0; i < tok.length;) {
    const c = tok[i]
    if (c === "'") {
      let j = tok.indexOf("'", i + 1)
      if (j < 0) j = tok.length
      out += tok.slice(i + 1, j)
      i = j + 1
    } else if (c === '"') {
      i++
      while (i < tok.length && tok[i] !== '"') {
        if (tok[i] === '\\' && i + 1 < tok.length && '"\\$`\n'.includes(tok[i + 1])) { out += tok[i + 1]; i += 2 }
        else { if (tok[i] === '$' || tok[i] === '`') dyn = true; out += tok[i]; i++ }
      }
      i++
    } else if (c === '\\' && i + 1 < tok.length) {
      out += tok[i + 1]
      i += 2
    } else {
      if ('$*?[{`'.includes(c)) dyn = true
      out += c
      i++
    }
  }
  return [out, dyn]
}

/** The text without its trailing `# ...` comment (#777: after `bash <<< X #c` and
 *  `bash <<EOF #c` the `#c` read as a script file). Quote-aware; `#` opens a comment
 *  only at the start of a word (`x#y` is one word). */
function stripComment(text) {
  let q = null
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (q) {
      if (c === '\\' && q === '"') i++
      else if (c === q) q = null
    } else if (c === '\\') i++
    else if (c === "'" || c === '"') q = c
    else if (c === '#' && (i === 0 || text[i - 1] === ' ' || text[i - 1] === '\t')) return text.slice(0, i)
  }
  return text
}

/** The index of the command word from `k` on: assignments, keywords, transparent
 *  prefixes and redirects are stepped over (words.length when none is left). */
function skipPrefix(words, k) {
  while (k < words.length) {
    const w = words[k][0]
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(w) || KEYWORDS.has(w)) k++
    else if (TRANSPARENT.has(baseName(w))) k = afterTransparent(words, k, baseName(w))
    else if (REDIR_GLUED_RE.test(w)) k += REDIR_OP_RE.test(w) ? 2 : 1   // a bare operator takes the next word
    else break
  }
  return k
}

/** The index after a transparent prefix and its options (`env -i bash`, `command -p bash`,
 *  `exec -a name bash`). An `env -S...` word stays in place: runsWords reads it as the
 *  command standing in one word. */
function afterTransparent(words, k, base) {
  const values = PREFIX_VALUES[base] ?? []
  let j = k + 1
  while (j < words.length) {
    const x = words[j][0]
    if (x === '--') return j + 1
    if (!x.startsWith('-') || x.startsWith('-S') || x.startsWith('--split-string')) return j
    j += values.includes(x) ? 2 : 1
  }
  return j
}

/** A transparent prefix before the command word (command, builtin, exec, env, nohup) bypasses
 *  a shell function and runs the PATH command (measured with bash, #777 review 6); the `time`
 *  keyword runs the function. */
function functionBypassed(words, k) {
  return words.slice(0, k).some(([x]) => TRANSPARENT.has(baseName(x)) && baseName(x) !== 'time')
}

/** The last word that is not a redirect: `} 2>&1` -> `}`, `done > log` -> `done` (#773). */
function lastOperand(words) {
  let j = words.length - 1
  while (j >= 0) {
    if (REDIR_GLUED_RE.test(words[j][0]) && !REDIR_OP_RE.test(words[j][0])) j--
    else if (j >= 1 && REDIR_OP_RE.test(words[j - 1][0])) j -= 2
    else break
  }
  return j >= 0 ? words[j][0] : ''
}

/** [words, k]: the segment's words and the index of its command word (k == length: none).
 *  A `)` closing a subshell or process substitution ends the simple command
 *  (`(bash) <<EOF`, `tee >(bash) <<EOF`): the words after it are not its operands. */
function cmdWord(seg) {
  // quote-aware: `bash > "o 775" <<EOF` has one target word, not two (#775)
  let words = (seg.replaceAll(')', ' ) ').match(SHELL_WORD_RE) ?? []).map(word)
  const k = skipPrefix(words, 0)
  const close = words.findIndex(([w], i) => i > k && w === ')')
  if (close >= 0) words = words.slice(0, close)
  return [words, k]
}

/** The index of the command a wrapper runs, past its options and operands; -1 when none. */
function afterWrapper(words, k, spec) {
  let j = k + 1
  let skip = spec.skip
  while (j < words.length) {
    const x = words[j][0]
    if (x === '--') { j++; break }
    if (x.startsWith('-')) { j += spec.values.includes(x) ? 2 : 1; continue }
    if (skip > 0) { skip--; j++; continue }
    break
  }
  return j < words.length ? j : -1
}

/** The consumer among `words` from command position `k` on, or ''. `loose`: the words
 *  stand in a body text that reads stdin, so a dynamic -c program counts as the body. */
function runsWords(words, k, depth, loose) {
  k = skipPrefix(words, k)
  if (k >= words.length || depth > MAX_NEST) return ''
  const [w, dyn] = words[k]
  const base = baseName(w)
  if (dyn) return w
  if (base === 'eval') {
    // `eval <<< X` runs nothing (measured, #777): eval reads its OPERANDS. A dynamic
    // operand (`eval "$(cat)"`, `eval "$l"`) may be the stdin; literal operands are a
    // command line of their own (`eval 'bash'` reads stdin, `eval 'echo hi'` does not).
    const rest = words.slice(k + 1)
    if (rest.some(([, d]) => d)) return w
    return rest.length && anyRunsStdin(rest.map(([x]) => x).join(' '), depth + 1) ? w : ''
  }
  if (base === 'source' || base === '.') {
    // `source /dev/stdin <<< X` runs it, `source file <<< X` runs the FILE; a dynamic
    // operand or a process substitution (`source <(cat)`) may be the stdin.
    return words.slice(k + 1).some(([x, d]) => d || STDIN_PATH_RE.test(x) || x.startsWith('<')) ? w : ''
  }
  if (base.startsWith('-S') || base.startsWith('--split-string')) return base   // env -S'python3 -'
  if (!INTERPRETERS.has(base)) {
    if (Object.hasOwn(WRAPPERS, base)) {
      const j = afterWrapper(words, k, WRAPPERS[base])
      if (j >= 0) return runsWords(words, j, depth + 1, loose)
      const shellFlags = SHELL_FLAGS_OF[base] ?? []
      return words.slice(k + 1).some(([x]) => shellFlags.includes(x)) ? base : ''
    }
    if (ARGV_FEEDERS.has(base)) {
      const j = argvProgram(words, afterFeederOptions(words, k))
      const pb = j >= 0 ? baseName(words[j][0]) : ''
      return INTERPRETERS.has(pb) ? pb : ''
    }
    if (REMOTE_RUNNERS.has(base)) {
      const j = runnerProgram(words, k)
      // `ssh host <<A` with no command: the remote login shell reads the body (#777 review 7);
      // a container runner without a command does not start.
      if (j < 0) return ['ssh', 'mosh', 'plink'].includes(base) ? base : ''
      return runsWords(words, j, depth + 1, loose)
    }
    return ''                         // an unknown command: its ARGUMENTS are not a program
  }
  const shell = SHELLS.has(base)
  const flags = STDIN_SCRIPT_FLAGS[base] ?? []
  let longOpts = base === 'bash' || base === 'rbash'   // GNU long options only at the start of the list (BASH_LONG_OPT_RE)
  for (let j = k + 1; j < words.length; j++) {
    const [x, xdyn] = words[j]
    if (xdyn || x === '-' || x === '-s' || STDIN_PATH_RE.test(x)) return base
    if (REDIR_OP_RE.test(x)) { j++; continue }       // a bare redirect operator takes the next word
    if (REDIR_GLUED_RE.test(x)) continue             // a glued redirect (2>&1, >log) is no operand
    if (longOpts) {
      const lm = BASH_LONG_OPT_RE.exec(x)
      if (lm) {
        if (BASH_LONG_OPT_NO_RUN.has(lm[1])) return ''   // `bash --version <<EOF`: the body is never read
        if (BASH_LONG_OPT_VALUE.has(lm[1])) j++          // `--rcfile F`, `-init-file F`
        continue
      }
      longOpts = false
    }
    if ((base === 'bash' || base === 'rbash') && x.startsWith('--') && x.length > 2) return ''   // `bash --rcfile=F`, `bash -x --norc`: invalid option, nothing runs
    if (shell && x === '--') {
      // `bash -login -- <<EOF` runs the body; a word after `--` is a script FILE (even
      // `-x`), only redirections may follow (#777 review 2). The file may be the stdin
      // itself: `bash -- /dev/stdin <<< X` runs it (measured, #777).
      let m = j + 1
      if (m < words.length && !words[m][1] && STDIN_PATH_RE.test(words[m][0])) return base
      while (m < words.length && !words[m][1]) {
        const y = words[m][0]
        if (REDIR_OP_RE.test(y)) m += 2
        else if (REDIR_GLUED_RE.test(y)) m += 1
        else return ''
      }
      return base
    }
    // `bash -c'rm ..'`, `bash -c-x`: a dash word that is not a letter cluster is an invalid option, nothing runs (#777)
    if (shell && (x.startsWith('-') || x.startsWith('+')) && x.length > 1 && !flags.includes(x) && !OPT_CLUSTER_RE.test(x)) return ''
    if (flags.includes(x) || (shell && /^-[A-Za-z]*c/.test(x))) {
      // the program follows the flag. A shell still reads options after -c (`bash -c -x
      // "$l"`, `-o pipefail`, `+x`), a `-` or `--` ends them (`bash -c - "$l"`), and a
      // redirect may stand in between (`bash -c 2>/dev/null "$l"`): the program is the
      // word after these (#773, #775). A `--long` option after -c bash rejects
      // (`bash -c --norc "$l"`: nothing runs): data. python/perl/node run the FIRST word
      // after -c (`python3 -c -- "$l"`: the program is `--`), only a redirect may intervene.
      // Dynamic in a reading body: the body.
      // an o/O inside the -c cluster takes a value too (`bash -co pipefail "$l"`, `-oc`, `-cO`):
      // the program follows the value word(s).
      let m = j + 1 + (shell ? optValues(x) : 0)
      let ended = !shell          // options over: only a redirect may still intervene
      while (m < words.length && !words[m][1]) {
        const y = words[m][0]
        if (REDIR_OP_RE.test(y)) m += 2
        else if (REDIR_GLUED_RE.test(y)) m += 1
        else if (ended) break
        else if (y === '-' || y === '--') { m += 1; ended = true }   // `bash -c -- 2>/dev/null "$l"`
        else if (optValues(y)) m += 1 + optValues(y)   // `-o pipefail`, `-xo pipefail`, `-xoo pipefail errexit`
        else if (y.startsWith('--')) return ''
        else if (y.startsWith('-') || y.startsWith('+')) {
          if (shell && !OPT_CLUSTER_RE.test(y)) return ''   // `bash -c -c'x'`: invalid option, nothing runs
          m += 1
        } else break
      }
      if (m >= words.length) return ''
      // dynamic program: in a reading body it is a body line; INSIDE a literal -c program a
      // `$(cat)` program is the fed body itself (#777)
      if (words[m][1]) return (loose || (depth >= 1 && words[m][0] === '$STDIN')) ? base : ''
      // #777: the LITERAL -c program may itself read stdin: `bash -c 'bash' <<EOF`,
      // `sh -c 'cat | sh' <<< X`, `bash -c 'eval "$(cat)"' <<< X` run the body (measured
      // with bash), `bash -c 'echo hi' <<EOF` does not. The program text is a command line.
      return shell && anyRunsStdin(words[m][0], depth + 1) ? base : ''
    }
    if (shell && optValues(x)) { j += optValues(x); continue }  // `-o pipefail`, `-xo pipefail`: value word(s) follow
    if (x.startsWith('-') || (shell && x.startsWith('+'))) continue
    return ''                         // a program FILE: the body is its input
  }
  return base
}

/** Does one simple command run its STDIN as a program? Returns the consumer word or ''. */
export function runsStdinAsProgram(seg, loose = false, depth = 0) {
  const [words, k] = cmdWord(seg)
  return runsWords(words, k, depth, loose)
}

/** The text with every CLOSED `$(...)` and `...` substitution collapsed into one dynamic
 *  word, `$SUB`. An unclosed one (the heredoc stands INSIDE the substitution) is left as
 *  is, for enclosingRuns. So `bash -c "$(echo "$l")"` reads as `bash -c "$SUB"` (a dynamic
 *  program), and a closed substitution in the head is not read in place of the outer
 *  command (#775). */
const STDIN_SUB_RE = /^\s*(?:<\s*\/(?:dev\/stdin|(?:dev|proc\/self)\/fd\/0+)|cat(?:\s+(?:-|\/(?:dev\/stdin|(?:dev|proc\/self)\/fd\/0+)))?)\s*$/

const BACKTICK_CLOSER = /\\`|`/

/** The head stands in an UNCLOSED backtick substitution (an odd number of unescaped backticks). */
function inBackticks(head) {
  let n = 0
  for (let i = 0; i < head.length; i++) if (head[i] === '`' && !escaped(head, i)) n++
  return n % 2 === 1
}

/** text[i] has an odd number of backslashes before it (`\``, `\\\``: neither opener nor closer). */
function escaped(text, i) {
  let k = i
  while (k > 0 && text[k - 1] === '\\') k--
  return (i - k) % 2 === 1
}

function subBlanked(text) {
  let out = ''
  let i = 0
  const n = text.length
  while (i < n) {
    const ch = text[i]
    if (ch === '$' && text.startsWith('$(', i)) {
      let depth = 1
      let j = i + 2
      while (j < n && depth) {
        if (text[j] === '(') depth++
        else if (text[j] === ')') depth--
        j++
      }
      if (depth) { out += text.slice(i); break }
      // `$(cat)`, `$(cat -)`, `$(</dev/stdin)`: the substitution IS the stdin; inside a literal
      // -c program that stdin is the fed body (`bash -c 'bash -c "$(cat)"' <<< X` runs it,
      // measured, #777). runsWords reads the name.
      out += STDIN_SUB_RE.test(text.slice(i + 2, j - 1)) ? '$STDIN' : '$SUB'
      i = j
    } else if (ch === '`' && !escaped(text, i)) {
      // a `\`` is not a closer (inside backticks it is a NESTED substitution): the head
      // `echo `echo \`bash ` stays unclosed, and HEAD_SPLIT cuts it at the nested opener
      // (#777 review 4: "echo `echo \`bash <<< 'x'\``" runs the word)
      let j = i + 1
      while (j < n && (text[j] !== '`' || escaped(text, j))) j++
      if (j >= n) { out += text.slice(i); break }
      out += STDIN_SUB_RE.test(text.slice(i + 1, j)) ? '$STDIN' : '$SUB'
      i = j + 1
    } else {
      out += ch
      i++
    }
  }
  return out
}

/** Does any simple command of a compound text (loop or function body) run its stdin as a
 *  program? Read twice: as is (`x=$(bash -c "$l")` runs INSIDE the substitution), and with
 *  the closed substitutions collapsed (`bash -c "$(echo "$l")"`: the program is the
 *  substitution, #775). */
/** The simple command has its OWN stdin and does not read the inherited one: an own input
 *  redirect (OWN_STDIN_RE) or backgrounded (`&`: non-interactive bash gives it /dev/null).
 *  Measured with bash (probe-sandbox, #777 review 7): `f(){ cat & wait; }; f <<<B` prints
 *  nothing; `x=$(cat)`, `(cat)`, `cat <&0`, the second `cat` after an own-redirect command and
 *  the second `cat` after a pipeline print B. */
function ownStdin(piece, nextSep) {
  if (nextSep === '&') return true
  const toks = piece.match(SHELL_WORD_RE) ?? []
  for (let i = 0; i < toks.length; i++) {
    const m = OWN_STDIN_RE.exec(toks[i])
    if (!m) continue
    // the target: the glued rest of the token, else the next token (unquoted)
    const target = word(toks[i].slice(m[0].length) || toks[i + 1] || '')[0]
    if (m[1] === '<' && STDIN_PATH_RE.test(target)) continue      // `bash </dev/stdin`: the inherited stdin itself
    if (m[1] === '<<<' && target === '$STDIN') continue           // `bash <<< "$(cat)"` (collapsed): its content
    return true
  }
  return false
}

/** A code interpreter (python/node/ruby/perl/php), not a shell: the same rule as
 *  nestedPrograms' and the Python twin's _code_consumer. */
const codeConsumer = (c) => INTERPRETERS.has(baseName(c)) && !SHELLS.has(baseName(c))

/** Which word of a compound text (function or loop body) runs the INHERITED stdin as a
 *  program: '' when none, 'nested' at the depth cap (what cannot be followed is not waved
 *  through). EVERY simple command of the body counts (#777 review 7, the first consumer was
 *  not enough): own-stdin and backgrounded commands are skipped; a function word resolves
 *  through its own body, recursively (the depth cap closes it); when ANY consumer is a shell,
 *  eval, source, a dynamic word, a closer or the cap, that word is it (the body is shell-read);
 *  else the first interpreter word, when every consumer is a code interpreter. The right side
 *  of a pipe counts as inherited (`cat | bash` runs the body): the conservative side. Read
 *  twice: as is (an `x=$(bash -c "$l")` INSIDE a substitution runs too, and `$(cat)` reads the
 *  inherited stdin), and with the closed substitutions collapsed into dynamic words (#775). */
function anyRunsStdin(text, depth = 0, funcs = {}) {
  if (depth > MAX_NEST) return 'nested'
  const loose = READS_STDIN_RE.test(text)
  const blanked = subBlanked(text)
  let interp = ''
  for (const t of blanked !== text ? [text, blanked] : [text]) {
    const parts = t.split(SIMPLE_SPLIT_KEEP)
    for (let i = 0; i < parts.length; i += 2) {
      const p = parts[i]
      if (!p.trim()) continue
      const [words, k] = cmdWord(p)
      let c = ''
      if (k < words.length && !words[k][1] && Object.hasOwn(funcs, words[k][0]) && !functionBypassed(words, k)) {
        c = anyRunsStdin(funcs[words[k][0]], depth + 1, funcs)
      }
      c = c || runsWords(words, k, depth, loose)
      if (!c || ownStdin(p, parts[i + 1] ?? '')) continue
      if (!codeConsumer(c)) return c
      if (!interp) interp = c
    }
  }
  return interp
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
  const [words, k] = cmdWord(seg)
  if (k < words.length && Object.hasOwn(funcs, words[k][0]) && !functionBypassed(words, k)) {
    // The function shadows PATH (#777 review 6: `python3(){ bash; }; python3 <<A` is read by
    // bash), so the function comes FIRST and its BODY's consumer is the word: the name
    // (python3, f) does not say whether code or a shell reads the body.
    const c = anyRunsStdin(funcs[words[k][0]], 0, funcs)
    if (c) return c
  }
  const direct = runsStdinAsProgram(seg)
  if (direct) return direct
  const last = lastOperand(words)
  if (cmd !== undefined && Object.hasOwn(CLOSERS, last)) return anyRunsStdin(compoundText(cmd, end, last), 0, funcs) ? last : ''
  return ''
}

/** The head's innermost part is a substitution: the OUTER command decides.
 *    eval "$(cat <<EOF)" -> program    . <(cat <<SH) / bash <(cat <<SH) -> program
 *    bash -c "$(cat <<EOF)" -> program  x=$(cat <<EOF) / echo "$(cat <<EOF)" -> data
 *    bash <<<"$(cat <<EOF)" -> program  the here-string IS the outer command's stdin
 *  `parts` is the head split with its separators kept: [text, sep, text, ...]. */
function enclosingRuns(parts, funcs) {
  let i = parts.length - 1
  let depth = 0
  while (i >= 2 && depth < MAX_NEST) {
    const sep = parts[i - 1]
    let outer = parts[i - 2]
    if (sep !== '$(' && sep !== '`' && sep !== '(') return ''
    const hs = sep !== '(' && outer.match(HERESTRING_TAIL_RE)
    if (hs) {
      const c = simpleRuns(outer.slice(0, hs.index), funcs)
      if (c) return c
      i -= 2
      depth++
      continue
    }
    const procsub = sep === '(' && /[<>]$/.test(outer.trimEnd())
    if (sep === '(' && !procsub) return ''          // a subshell: `( cat <<EOF )` is the cat's
    if (procsub) outer = outer.trimEnd().slice(0, -1)
    const [words, k] = cmdWord(outer)
    if (k < words.length) {
      const [w, dyn] = words[k]
      const base = baseName(w)
      if (dyn || base === 'source' || base === '.' || base === 'eval') return w
      if (Object.hasOwn(funcs, w)) {
        const c = anyRunsStdin(funcs[w], 0, funcs)
        if (c) return c
      }
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
      // descriptor as a file (`bash /dev/fd/3 3<<SH`: the path stands for `-`) or dups
      // it onto stdin (`bash 3<<SH <&3`): then the command is read as if it had stdin.
      const path = '/(?:dev|proc/self)/fd/0*' + n + '(?![0-9])'
      const dup = '(?:^|\\s)0?<&\\s*0*' + n + '-?(?=\\s|$)'
      let line = head + ' ' + stripComment(cmd.slice(h.tailStart, h.lineEnd)).split(TAIL_SPLIT)[0]
      if (!new RegExp(path).test(line) && !new RegExp(dup).test(line)) return ''
      line = line.replace(new RegExp(path, 'g'), '-').replace(new RegExp(dup, 'g'), ' ')
      // the last simple command, with its closer (`{ bash; } 2>&1 3<<EOF <&3`, #775):
      // the same way as the stdin-heredoc head below
      return simpleRuns(subBlanked(line).split(HEAD_SPLIT).at(-1), funcs, cmd, h.start)
    }
  }
  const parts = subBlanked(head).split(HEAD_SPLIT)
  let tail = stripComment(cmd.slice(h.tailStart, h.lineEnd)).split(TAIL_SPLIT)[0]
  // inside a backtick substitution the next closer (`\`` of a nested one, or the bare backtick)
  // ends the simple command: `echo `echo \`bash <<< 'x'\` tail`` runs `bash`, the ` tail` after
  // the closer is the OUTER echo's (#777 review 4)
  if (inBackticks(head)) tail = tail.split(BACKTICK_CLOSER)[0]
  const pipe = tail.split(PIPE_SPLIT)
  const first = simpleRuns(parts[parts.length - 1] + ' ' + pipe[0], funcs, cmd, h.start)
  if (first) return first
  for (const p of pipe.slice(1)) {
    const c = simpleRuns(p, funcs)
    if (c) return c
  }
  return enclosingRuns(parts, funcs) || closingPipeRuns(cmd, h, parts, funcs)
}

/** The heredoc stands inside an UNCLOSED `$( )` or backtick and the line closing the
 *  substitution pipes it on: `echo $(cat <<EOF ... EOF\n) | bash` runs the body (measured
 *  with bash, #777). The pipe members after the closing `)` are read like the opener line's. */
function closingPipeRuns(cmd, h, parts, funcs) {
  if ((h.kind ?? 'heredoc') !== 'heredoc') return ''
  if (!parts.some((sep, i) => i % 2 === 1 && (sep === '$(' || sep === '`'))) return ''
  const rest = cmd.slice(h.end + 1)
  const nl = rest.indexOf('\n')
  const line = nl < 0 ? rest : rest.slice(0, nl)
  const close = /[)`]/.exec(line)
  if (!close) return ''
  const tail = stripComment(line.slice(close.index + 1)).split(TAIL_SPLIT)[0]
  for (const p of tail.split(PIPE_SPLIT).slice(1)) {
    const c = simpleRuns(p, funcs)
    if (c) return c
  }
  return ''
}

/**
 * Every stdin FEED of `cmd` that is RUN: the heredoc bodies of programHeredocs() plus the
 * here-string words whose consumer runs its stdin (`bash <<< 'curl ..'`, `sh -c 'cat | sh'
 * <<< X`), as [{ consumer, body }] with the DECODED word (#777). A dynamic word
 * (`bash <<< "$(echo ..)"`) keeps its unexpanded text: the gates cannot read what the shell
 * only produces at run time (a stated gap).
 */
export function programFeeds(cmd) {
  const s = String(cmd ?? '')
  const out = programHeredocs(s)
  if (!s.includes('<<<')) return out
  const { records } = findAll(s)
  const funcs = functionBodies(s)
  for (const h of records) {
    if (h.kind !== 'herestring' || !h.text.trim()) continue
    const consumer = heredocRunsBody(s, h, funcs)
    if (consumer) out.push({ consumer: baseName(consumer), body: h.text })
  }
  return out
}

/** The index of the word a shell's `-c` runs, from command position `k` on, or -1: the
 *  twin of the gate's _c_programs walk (clusters with c, `-o val`, long options at the
 *  start, `-c -- prog`, `-c -x prog`, redirects in between; a dash word that is not a
 *  letter cluster is an invalid option). -1 when the program is a FILE or the stdin. */
function cProgramIndex(words, k, depth = 0) {
  k = skipPrefix(words, k)
  if (k >= words.length) return -1
  const [w, dyn] = words[k]
  const base = baseName(w)
  if (dyn) return -1
  if (Object.hasOwn(WRAPPERS, base)) {           // `nice bash -c x`, `timeout 5 sh -c x`
    const j = afterWrapper(words, k, WRAPPERS[base])
    return j >= 0 && depth < MAX_NEST ? cProgramIndex(words, j, depth + 1) : -1
  }
  if (!SHELLS.has(base)) return -1
  const flags = STDIN_SCRIPT_FLAGS[base] ?? []
  let longOpts = base === 'bash' || base === 'rbash'
  for (let j = k + 1; j < words.length; j++) {
    const [x, xdyn] = words[j]
    if (REDIR_OP_RE.test(x)) { j++; continue }
    if (REDIR_GLUED_RE.test(x)) continue
    if (xdyn) return -1
    if (longOpts) {
      const lm = BASH_LONG_OPT_RE.exec(x)
      if (lm) {
        if (BASH_LONG_OPT_NO_RUN.has(lm[1])) return -1
        if (BASH_LONG_OPT_VALUE.has(lm[1])) j++
        continue
      }
      longOpts = false
    }
    if ((base === 'bash' || base === 'rbash') && x.startsWith('--') && x.length > 2) return -1
    if (flags.includes(x) || (OPT_CLUSTER_RE.test(x) && /^-[A-Za-z]*c/.test(x))) {
      let m = j + 1 + optValues(x)
      let ended = false
      while (m < words.length) {
        const [y, ydyn] = words[m]
        if (REDIR_OP_RE.test(y)) m += 2
        else if (REDIR_GLUED_RE.test(y)) m += 1
        else if (ydyn || ended) break
        else if (y === '-' || y === '--') { m += 1; ended = true }
        else if (optValues(y)) m += 1 + optValues(y)
        else if (y.startsWith('--')) return -1
        else if (y.startsWith('-') || y.startsWith('+')) {
          if (!OPT_CLUSTER_RE.test(y)) return -1
          m += 1
        } else break
      }
      return m < words.length ? m : -1
    }
    if (optValues(x)) { j += optValues(x); continue }
    if ((x.startsWith('-') || x.startsWith('+')) && x.length > 1) {
      if (!OPT_CLUSTER_RE.test(x)) return -1
      continue
    }
    return -1                      // `-` (stdin) or a program FILE
  }
  return -1
}

/** The simple commands of a shell text, quote-aware (the twin of the gate's segment split):
 *  cut at `&&`, `||`, `;`, `&`, `|`, newline, `(`, `)`, `{`, `}`, backtick and `$(`
 *  outside quotes; a `$( )` or backtick inside double quotes is command context again. */
function simpleSegments(text) {
  const out = []
  const n = text.length
  const stack = []               // [closer, outer paren depth] per open substitution in double quotes
  let start = 0
  let i = 0
  let q = null
  let depth = 0
  const cut = (end, width) => { out.push(text.slice(start, end)); start = end + width; i = start }
  while (i < n) {
    const c = text[i]
    if (q === "'") { if (c === "'") q = null; i++; continue }
    if (q === "$'") { if (c === '\\') i += 2; else { if (c === "'") q = null; i++ } continue }
    if (q === '"') {
      if (c === '\\') { i += 2; continue }
      if (c === '"') { q = null; i++; continue }
      if (c === '`' || text.startsWith('$(', i)) {
        stack.push([c === '`' ? '`' : ')', depth])
        depth = 0
        q = null
        cut(i, c === '`' ? 1 : 2)
        continue
      }
      i++
      continue
    }
    if (stack.length) {
      const closer = stack[stack.length - 1][0]
      if (closer === ')' && c === '(') depth++
      else if (c === closer && (closer === '`' || depth === 0)) { depth = stack.pop()[1]; q = '"'; cut(i, 1); continue }
      else if (closer === ')' && c === ')') depth--
    }
    if (c === '\\') { i += 2; continue }
    if (c === "'") { q = "'"; i++; continue }
    if (c === '"') { q = '"'; i++; continue }
    if (text.startsWith("$'", i)) { q = "$'"; i += 2; continue }
    if (text.startsWith('||', i) || text.startsWith('&&', i) || text.startsWith('$(', i)) { cut(i, 2); continue }
    if (c === '&' && text[i + 1] !== '>' && text[i - 1] !== '>' && text[i - 1] !== '<') { cut(i, 1); continue }   // `<&0` is a dup, not a separator (#777 review 3)
    if (c === '|' && text[i - 1] !== '>') { cut(i, 1); continue }
    if (';\n(){}`'.includes(c)) { cut(i, 1); continue }
    i++
  }
  out.push(text.slice(start))
  return out.map((s) => s.trim()).filter(Boolean)
}

/** `cmd` with the DATA heredoc bodies and the comment lines blanked (offsets kept): the
 *  text the shell actually runs, the twin of the gate's light text. */
function lightText(cmd) {
  let s = cmd
  if (s.includes('<<')) {
    const { heredocs, certain } = findHeredocs(s)
    const funcs = heredocs.length ? functionBodies(s) : {}
    for (const h of heredocs) {
      const body = s.slice(h.bodyStart, h.bodyEnd)
      if (heredocRunsBody(s, h, funcs) || !certain || (!h.quoted && /\$\(|`/.test(body))) continue
      s = s.slice(0, h.bodyStart) + body.replace(/[^\n]/g, ' ') + s.slice(h.bodyEnd)
    }
  }
  return s.split('\n').map((l) => (/^\s*#/.test(l) ? ' '.repeat(l.length) : l)).join('\n')
}

/**
 * Every program a shell runs through `-c` in `cmd`, as the DECODED text (#777):
 * `bash -c "rm x"`, `sh -xc 'x'`, `bash -o pipefail -c x`, `bash -c -- x`, `bash -c $'r\x6d x'`,
 * `bash -c rm\ x`, `nice bash -c x`. A dynamic word keeps its unexpanded text (stated gap).
 * The child guard and the egress gate judge each text as a command line of its own.
 */
export function shellCPrograms(cmd) {
  const s = String(cmd ?? '')
  if (!/(?:^|[^A-Za-z0-9_-])(?:sh|bash|zsh|dash|ksh|rbash)(?![A-Za-z0-9_-])/.test(s)) return []
  const out = []
  for (const seg of simpleSegments(lightText(s))) {
    const fixed = seg.replaceAll(')', ' ) ')
    const matches = [...fixed.matchAll(SHELL_WORD_RE)]
    let words = matches.map((m) => word(m[0]))
    const k = skipPrefix(words, 0)
    const close = words.findIndex(([w], i) => i > k && w === ')')
    if (close >= 0) words = words.slice(0, close)
    const m = cProgramIndex(words, k)
    if (m < 0) continue
    const w = hereWord(fixed, matches[m].index, [])
    if (w && w[0].trim()) out.push(w[0])
  }
  return out
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
