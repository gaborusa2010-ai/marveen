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

import { find as findHeredocs, findAll, hereWord, ansiC } from './heredoc-scan.mjs'

/** Interpreters that run their stdin as a program when no script is named. */
export const INTERPRETERS = new Set(['python', 'python3', 'bash', 'sh', 'zsh', 'dash', 'ksh', 'rbash', 'ash', 'hush', 'mksh', 'yash', 'node', 'perl', 'ruby', 'php'])
/** Flags after which the PROGRAM follows (stdin is data), per interpreter: bash -e is
 *  errexit, not a script; python3 -m runs a module whose input the body is. */
export const STDIN_SCRIPT_FLAGS = {
  bash: ['-c', '--command'], sh: ['-c'], zsh: ['-c', '--command'], dash: ['-c'], ksh: ['-c'], rbash: ['-c', '--command'], ash: ['-c'], hush: ['-c'], mksh: ['-c'], yash: ['-c'],
  python: ['-c', '-m'], python3: ['-c', '-m'],
  node: ['-e', '-p', '--eval', '--print'], perl: ['-e', '-E'], ruby: ['-e'], php: ['-r'],
}
export const SHELLS = new Set(['sh', 'bash', 'zsh', 'dash', 'ksh', 'rbash', 'ash', 'hush', 'mksh', 'yash'])

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
  // #777 review 8: further launchers that exec the command after their options
  chrt: { values: ['-T', '-P', '-D', '--sched-runtime', '--sched-period', '--sched-deadline'], skip: 1 },
  taskset: { values: [], skip: 1 },
  setpriv: { values: ['--reuid', '--regid', '--ruid', '--euid', '--rgid', '--egid', '--groups', '--inh-caps', '--ambient-caps', '--bounding-set', '--securebits', '--pdeathsig', '--selinux-label', '--apparmor-profile', '--landlock-access', '--landlock-rule'], skip: 0 },
  firejail: { values: [], skip: 0 },
  'xvfb-run': { values: ['-n', '--server-num', '-s', '--server-args', '-f', '--auth-file', '-e', '--error-file', '-p', '--xauth-protocol', '-w', '--wait'], skip: 0 },
  busybox: { values: [], skip: 0 },
  pkexec: { values: ['--user'], skip: 0 },
  // #777 review 9
  fakeroot: { values: ['-l', '--lib', '--faked', '-s', '-i', '-b'], skip: 0 },
  proot: { values: ['-r', '--rootfs', '-b', '--bind', '-m', '--mount', '-w', '--pwd', '--cwd', '-q', '--qemu', '-k', '--kernel-release'], skip: 0 },
  valgrind: { values: [], skip: 0 },
  torsocks: { values: ['-u', '--user', '-p', '--pass', '-a', '--address', '-P', '--port'], skip: 0 },
  catchsegv: { values: [], skip: 0 },
  'flatpak-spawn': { values: [], skip: 0 },
  machinectl: { values: ['-E', '--setenv', '--uid', '-M', '--machine', '-H', '--host'], skip: 2 },
  prlimit: { values: ['-p', '--pid', '-o', '--output'], skip: 0 },
  numactl: { values: ['-C', '--physcpubind', '-N', '--cpunodebind', '-m', '--membind', '-p', '--preferred', '-i', '--interleave', '-w', '--weighted-interleave'], skip: 0 },
  cgexec: { values: ['-g'], skip: 0 },
  setarch: { values: [], skip: 1 },
  linux32: { values: [], skip: 0 },
  linux64: { values: [], skip: 0 },
  chpst: { values: ['-u', '-U', '-b', '-e', '-/', '-C', '-n', '-l', '-L', '-m', '-d', '-o', '-p', '-f', '-c'], skip: 0 },
  faketime: { values: ['-p'], skip: 1 },
  newgrp: { values: [], skip: 1 },
}
// Launchers that start a shell reading the stdin when no command follows (#777 review 9):
// `pkexec <<A`, `firejail <<A`, `fakeroot <<A`, `proot <<A`, `machinectl shell host <<A`, `setarch x86_64 <<A`, `newgrp grp <<A`.
const SHELL_WITHOUT_COMMAND = new Set(['pkexec', 'firejail', 'fakeroot', 'proot', 'machinectl', 'setarch', 'linux32', 'linux64', 'newgrp'])
// ...but not when they only print something (#777 review 10: `setarch --list`, `machinectl list`).
const INFO_FLAGS = ['--help', '-h', '--version', '-V', '--list']
// The program follows a marker word (#777 review 9): `distrobox enter box -- prog`, `toolbox run [-c box] prog`. No marker: distrobox enter /
// toolbox enter start a shell, their other subcommands (create, list, rm) run nothing (#777 review 10).
const MARKER_LAUNCHERS = { distrobox: ['--', true], 'distrobox-enter': ['--', true], toolbox: ['run', true] }
const SHELL_SUBCOMMAND = { distrobox: 'enter', toolbox: 'enter' }
// A gdb that reads a heredoc/here-string is BLOCKED by every gate whatever its flags (#777 review 12):
// its command language runs shell lines and code many ways (`she`, `!`, `pipe`, `python exec(stdin)`,
// `eval "shell %s"`, `interpreter-exec`, guile, `-x /dev/stdin`, an inferior inheriting the stdin).
// The Python twin's _GDB_NAMES.
const GDB_NAMES = new Set(['gdb', 'gdb-multiarch'])
// gdb under any build name: `rust-gdb`, `aarch64-linux-gnu-gdb`, `cuda-gdb` (#777 review 15). The Python twin's _is_gdb.
const isGdb = (n) => GDB_NAMES.has(n) || n.endsWith('-gdb') || n.startsWith('gdb-')
const TOOLBOX_VALUE_FLAGS = ['-c', '--container', '-d', '--distro', '-r', '--release']
// su/runuser/script: `-c CMD` runs CMD through a shell; without -c (runuser -u: the command after
// the options) they start a shell that reads the stdin (#777 review 8).
const SU_LIKE = new Set(['su', 'runuser', 'script', 'sg'])
const SU_VALUE_FLAGS = ['-u', '--user', '-g', '--group', '-G', '--supp-group', '-s', '--shell', '-w', '--whitelist-environment', '-T', '-O', '-I', '-B', '--log-timing', '--log-out', '--log-in', '--log-io', '-E', '--echo', '-m', '--logging-format']
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
const REMOTE_RUNNERS = new Set(['ssh', 'mosh', 'sshpass', 'plink', 'rsh', 'dbclient', 'autossh', 'docker', 'podman', 'nerdctl', 'kubectl'])
// A remote login shell reads the stdin; the remote command line is re-parsed by a shell.
const SSH_LIKE = ['ssh', 'mosh', 'plink', 'rsh', 'dbclient', 'autossh']
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
  rsh: ['-l'],
  dbclient: ['-p', '-l', '-i', '-L', '-R', '-o', '-W', '-K', '-I', '-J', '-c', '-m', '-b', '-B'],
  autossh: ['-M', '-l', '-p', '-i', '-o', '-F', '-J', '-b', '-c', '-D', '-E', '-e', '-I', '-L', '-m', '-O', '-Q',
    '-R', '-S', '-W', '-w', '-B', '-P'],
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

/** Does the option word carry the NEXT word as its value: a value option itself, or a short
 *  cluster (`-tl`, `-iu`, `-ic`) whose LAST letter is a value option and no earlier letter is
 *  (after an earlier value letter the rest of the cluster is the value: `-lroot`) (#777 review 8). */
function takesValue(x, flags) {
  const has = (f) => (flags instanceof Set ? flags.has(f) : flags.includes(f))
  if (has(x)) return true
  if (x.length < 3 || x[0] !== '-' || x[1] === '-') return false
  for (let i = 1; i < x.length; i++) if (has('-' + x[i])) return i === x.length - 1
  return false
}

/** The index of the first word after an xargs/parallel's options (words.length when none). */
function afterFeederOptions(words, k) {
  let j = k + 1
  while (j < words.length) {
    const x = words[j][0]
    if (x === '--') return j + 1
    if (!x.startsWith('-')) return j
    j += takesValue(x, FEEDER_VALUE_FLAGS) ? 2 : 1
  }
  return j
}

/** The first interpreter word from `j` on, -1 when none: the fallback for a runner of unknown
 *  shape (#777 review 7). */
function firstInterpreter(words, j) {
  for (let i = j; i < words.length; i++) if (INTERPRETERS.has(baseName(words[i][0]))) return i
  return -1
}

const LAUNCHERS = new Set([...Object.keys(WRAPPERS), ...ARGV_FEEDERS, ...REMOTE_RUNNERS, ...SU_LIKE, ...Object.keys(MARKER_LAUNCHERS), ...GDB_NAMES])

/** The first shell word after index j ('' if none): the conservative answer when a launcher
 *  runs a program we do not model (review-6 rule, #777 review 8: `chrt 1 bash`, `firejail bash`). */
function shellWordAfter(words, j) {
  for (let i = j + 1; i < words.length; i++) if (SHELLS.has(baseName(words[i][0]))) return baseName(words[i][0])
  return ''
}

/** Who consumes the body when a launcher runs the command at index j. `argv`: the body becomes
 *  that command's ARGUMENTS (behind xargs/parallel), not its stdin. A program we do not model
 *  answers with any shell word behind it (#777 review 8). */
function viaLauncher(words, j, depth, loose, argv) {
  const j0 = j
  j = skipPrefix(words, j)
  if (j >= words.length) {
    // behind xargs a bare `env`/`nohup`/`time` execs the body's words (#777 review 9)
    const prefix = argv ? words.slice(j0).find(([x]) => TRANSPARENT.has(baseName(x))) : undefined
    return prefix ? baseName(prefix[0]) : ''
  }
  const [x, dyn] = words[j]
  if (dyn) return x
  const base = baseName(x)
  if (LAUNCHERS.has(base)) return launcherConsumer(words, j, base, depth + 1, loose, argv)
  // behind xargs the body is this program's argument list: an interpreter runs it (`xargs bash -c`)
  if (argv) return INTERPRETERS.has(base) ? base : shellWordAfter(words, j)
  const c = runsWords(words, j, depth + 1, loose)
  if (c || INTERPRETERS.has(base) || base === 'eval' || base === 'source' || base === '.') return c
  return shellWordAfter(words, j)
}

/** su/runuser/script (#777 review 8). `-c CMD` (also in a cluster: `-qc`) runs CMD through a
 *  shell; behind xargs the body may be that CMD. `runuser -u U -- cmd` execs cmd. Without a
 *  command they start a shell that reads the stdin. `sg [-] group [-c] command` hands the
 *  command to /bin/sh -c (#777 review 10: `sg grp cat` is data). */
function suConsumer(words, k, base, depth, loose, argv) {
  if (base === 'sg') {
    let rest = words.slice(k + 1)
    if (rest.length && rest[0][0] === '-') rest = rest.slice(1)
    let cmd = rest.slice(1)
    if (cmd.length && cmd[0][0] === '-c') cmd = cmd.slice(1)
    if (argv || !cmd.length || cmd.some(([, d]) => d)) return base
    return programConsumer(cmd.map(([x]) => x).join(' '), base, depth)
  }
  const hasUser = (end) => words.slice(k + 1, end).some(([w]) => w === '-u' || w === '--user')
  let j = k + 1
  while (j < words.length) {
    const x = words[j][0]
    if (x === '--') { j++; break }
    if (x === '-c' || x === '--command' || x === '--session-command' || (/^-[A-Za-z]*c$/.test(x) && !x.startsWith('--'))) {
      if (argv || j + 1 >= words.length) return base
      const [v, vdyn] = words[j + 1]
      return vdyn ? base : programConsumer(v, base, depth)
    }
    if (x.startsWith('--command=') || x.startsWith('--session-command=')) return argv ? base : programConsumer(x.slice(x.indexOf('=') + 1), base, depth)
    if (x === '-') { j++; continue }
    if (!x.startsWith('-')) {
      if (base === 'runuser' && hasUser(j)) break   // `runuser -u U cmd`: the command
      j++                                            // su USER / script FILE: an operand, keep looking for -c
      continue
    }
    j += takesValue(x, SU_VALUE_FLAGS) ? 2 : 1
  }
  if (base === 'runuser' && j < words.length && hasUser(j)) return viaLauncher(words, j, depth, loose, argv)
  return argv ? '' : base
}

/** ONE resolver for every non-interpreter launcher at index k: stdin wrappers (sudo, nice,
 *  chrt...), argv feeders (xargs, parallel), remote/container runners (ssh, docker, kubectl) and
 *  su-like commands. Returns the word that consumes the body, or ''. The three separate paths it
 *  replaces each had their own gaps (#777 review 8). `argv`: the body is the launcher's argument
 *  list (we are behind an xargs/parallel), not its stdin. The Python twin's _launcher_consumer. */
function launcherConsumer(words, k, base, depth, loose, argv = false) {
  if (depth > MAX_NEST) return 'nested'
  if (Object.hasOwn(WRAPPERS, base)) {
    const j = afterWrapper(words, k, WRAPPERS[base])
    if (j < 0) {
      // `xargs nice` runs the body line as a command; `sudo -s <<A` a shell
      if (argv) return base
      const rest = words.slice(k + 1).map(([x]) => x)
      if (SHELL_WITHOUT_COMMAND.has(base) && !rest.some((x) => INFO_FLAGS.includes(x))) {
        // `machinectl shell box <<A` reads the body; `machinectl list`/`status box` do not
        if (base !== 'machinectl' || ['shell', 'login'].includes(rest.find((x) => !x.startsWith('-')) ?? '')) return base
      }
      const shellFlags = SHELL_FLAGS_OF[base] ?? []
      return rest.some((x) => shellFlags.includes(x)) ? base : ''
    }
    return viaLauncher(words, j, depth, loose, argv)
  }
  if (ARGV_FEEDERS.has(base)) {
    const j = afterFeederOptions(words, k)
    // parallel without a command runs its input lines (stdin or arguments) as commands;
    // xargs without one runs echo
    if (j >= words.length) return base === 'parallel' ? base : ''
    return viaLauncher(words, j, depth, loose, true)
  }
  if (REMOTE_RUNNERS.has(base)) {
    // behind xargs the body's words join the remote command line, whatever program it names (#777 review 9)
    if (argv && SSH_LIKE.includes(base)) return base
    const j = runnerProgram(words, k)
    // `ssh host <<A`: the remote login shell reads the body; behind xargs the body is the remote
    // command (#777 review 7, 8). A container runner without a command runs nothing.
    if (j < 0) return SSH_LIKE.includes(base) ? base : ''
    let c = viaLauncher(words, j, depth, loose, argv)
    if (!c && SSH_LIKE.includes(base) && !argv) {
      // the remote shell re-parses the joined words: `ssh host "bash -s"`, `ssh host 'cd /; bash'`
      c = anyRunsStdin(words.slice(j).map(([x]) => x).join(' '), depth + 1)
    }
    return c
  }
  if (SU_LIKE.has(base)) return suConsumer(words, k, base, depth, loose, argv)
  if (isGdb(base)) return 'gdb'
  if (Object.hasOwn(MARKER_LAUNCHERS, base)) {
    let [marker, shell] = MARKER_LAUNCHERS[base]
    const rest = words.slice(k + 1).map(([x]) => x)
    if (Object.hasOwn(SHELL_SUBCOMMAND, base)) shell = (rest.find((x) => !x.startsWith('-')) ?? '') === SHELL_SUBCOMMAND[base]
    let j = words.findIndex(([x], i) => i > k && x === marker)
    if (j >= 0 && base === 'toolbox') {
      j++
      while (j < words.length && words[j][0].startsWith('-')) j += takesValue(words[j][0], TOOLBOX_VALUE_FLAGS) ? 2 : 1
      j--
    }
    if (j < 0 || j + 1 >= words.length) return argv || shell ? base : ''
    return viaLauncher(words, j + 1, depth, loose, argv)
  }
  return ''
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
    while (j < words.length && (words[j][0].startsWith('-') || RUNNER_SUBCOMMAND_PREFIX.has(words[j][0]))) j += takesValue(words[j][0], flags) ? 2 : 1
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
    if (x.startsWith('-')) { j += takesValue(x, flags) ? 2 : 1; continue }
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
// neither is the `|` of `>|` (`bash >| log <<SH`, #775). `{` and `}` are reserved WORDS: a
// mid-word brace (`/dev/std{i..i}n`, `${x}`, `a}b`) is part of the word, and splitting there hid
// a dynamic redirect target (#777 review 10).
const SIMPLE_SPLIT = /\|\||&&|(?<![<>|])&(?!>)|[;\n()`]|(?<![^\s;&|()])[{}](?![^\s;&|()<>])|(?<!>)\|&?|\$\(/
// the same, keeping its separators: [command, separator, command, ...] (#777 review 7: a `&`
// AFTER the command backgrounds it, and then it does not read the inherited stdin)
const SIMPLE_SPLIT_KEEP = /(\|\||&&|(?<![<>|])&(?!>)|[;\n()`]|(?<![^\s;&|()])[{}](?![^\s;&|()<>])|(?<!>)\|&?|\$\()/
const HEAD_SPLIT = /(\|\||&&|(?<![<>|])&(?!>)|[;(`]|(?<!>)\|&?|\$\()/
const TAIL_SPLIT = /\|\||&&|;|(?<![<>|])&(?!>)/
const PIPE_SPLIT = /(?<!>)\|&?/
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

/** A versioned interpreter name is the same interpreter (#779 round 4): python3.14, python3.14t,
 *  python2.7, node22, nodejs, perl5.36, ruby3.2, php8.2. The twin of _canon_interp. */
const VERSIONED_INTERP_RE = /^(python|perl|ruby|php|node)(\d+(?:\.\d+)*)[a-z]?$/
export function canonInterp(name) {
  if (name === 'nodejs') return 'node'
  const m = VERSIONED_INTERP_RE.exec(name)
  if (!m) return name
  if (m[1] === 'python') return m[2].split('.')[0] === '3' ? 'python3' : 'python'
  return m[1]
}
/** Interpreter flags whose VALUE is the next word: that word is not a program file (#779 r4). */
const INTERP_VALUE_FLAGS = {
  python: ['-W', '-X'], python3: ['-W', '-X'],
  node: ['-r', '--require', '--import', '--loader', '--experimental-loader'],
  ruby: ['-r', '-I'],
}
const rawBase = (w) => w.split('/').pop()
/** basename + canonical interpreter name; any other word is its plain basename. */
const baseName = (w) => canonInterp(rawBase(w))

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
    } else if (c === '$' && tok[i + 1] === "'") {
      // $'..' is ANSI-C quoting, not an expansion: `g$'d'b`, `$'\x67db'` name gdb (#777 review 15)
      let j = i + 2
      while (j < tok.length && tok[j] !== "'") j += tok[j] === '\\' ? 2 : 1
      out += ansiC(tok.slice(i + 2, j))
      i = j + 1
    } else if (c === '$' && tok[i + 1] === '"') {
      i++   // $".." is a (translated) double-quoted string
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
/** Each unquoted, balanced `${...}` holding whitespace replaced by `${_}`: still dynamic, one word. */
function collapseParams(text) {
  if (!text.includes('${')) return text
  const state = quoteMap(text)
  let out = ''
  for (let i = 0; i < text.length; i++) {
    if (!state[i] && text.startsWith('${', i)) {
      const end = paramEnd(text, state, i)
      if (end && /\s/.test(text.slice(i, end))) { out += '${_}'; i = end - 1; continue }
    }
    out += text[i]
  }
  return out
}

function cmdWord(seg) {
  // quote-aware: `bash > "o 775" <<EOF` has one target word, not two (#775)
  // a `${...}` is one word whatever it holds (#777 review 14: `X=${Y:-a b} bash <<A` ran bash)
  let toks = collapseParams(seg).replaceAll(')', ' ) ').match(SHELL_WORD_RE) ?? []
  // an unquoted word starting with `#` opens a comment: the rest is not the command
  // (#777 review 8: `f(){ bash #<x` read `#<x` as a script file)
  const hash = toks.findIndex((t) => t.startsWith('#'))
  if (hash >= 0) toks = toks.slice(0, hash)
  let words = toks.map(word)
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
    if (x.startsWith('-')) { j += takesValue(x, spec.values) ? 2 : 1; continue }
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
    // the dynamic operand is what runs (`G=gdb; eval "$G" <<A`, `bash -c 'eval "$0"' gdb`) (#777 review 15)
    if (rest.some(([, d]) => d)) return rest.find(([, d]) => d)[0]
    return rest.length ? programConsumer(rest.map(([x]) => x).join(' '), w, depth) : ''
  }
  if (base === 'source' || base === '.') {
    // `source /dev/stdin <<< X` runs it, `source file <<< X` runs the FILE; a dynamic
    // operand or a process substitution (`source <(cat)`) may be the stdin.
    return words.slice(k + 1).some(([x, d]) => d || STDIN_PATH_RE.test(x) || x.startsWith('<')) ? w : ''
  }
  if (base.startsWith('-S') || base.startsWith('--split-string')) return base   // env -S'python3 -'
  // an unknown command: its ARGUMENTS are not a program
  if (!INTERPRETERS.has(base)) return LAUNCHERS.has(base) || isGdb(base) ? launcherConsumer(words, k, base, depth, loose) : ''
  const shell = SHELLS.has(base)
  const flags = STDIN_SCRIPT_FLAGS[base] ?? []
  let longOpts = base === 'bash' || base === 'rbash'   // GNU long options only at the start of the list (BASH_LONG_OPT_RE)
  for (let j = k + 1; j < words.length; j++) {
    const [x, xdyn] = words[j]
    if (xdyn || (x === '-' && !shell) || x === '-s' || STDIN_PATH_RE.test(x) || x.startsWith('<(')) return base   // `bash <(cat)`: the script is a pipe, maybe the stdin (#777 review 8)
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
    if (shell && (x === '--' || x === '-')) {
      // A shell treats a lone `-` like `--` (measured in sh, dash, bash: `bash - f.sh` runs
      // the FILE, a lone `-` reads the stdin; #779 round 2).
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
      // A gdb the program feeds the stdin to is the consumer (#777 review 13: `bash -c 'gdb -q' <<A`).
      return shell ? programConsumer(words[m][0], base, depth) : ''
    }
    if (shell && optValues(x)) { j += optValues(x); continue }  // `-o pipefail`, `-xo pipefail`: value word(s) follow
    if ((INTERP_VALUE_FLAGS[base] ?? []).includes(x)) { j++; continue }   // `python3 -W ignore`, `node -r mod` (#779 r4)
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
// A stdin (fd 0) redirection at the start of a word: `<f`, `0<f`, `<<<x`, `<<T`, `<<-T`, `<>f`, `<&n`.
// `<(..)` is a process substitution (an ARGUMENT), not a redirection.
const STDIN_REDIR_RE = /^0?(<<<|<<-?|<>|<&|<)(?!\()/
// A redirection target that is (or may be) the inherited stdin: /dev/stdin, //dev/./stdin,
// /dev/fd/N, /proc/<anything>/fd/N (self, $$, thread-self, task/..).
const INHERITED_TARGET_RE = /stdin|(?:^|\/)fd(?:\/|$)/
// `set -m` / `set -o monitor`: job control on, a background job keeps the stdin.
const JOB_CONTROL_RE = /(?<![\w-])set\s+(?:-[A-Za-z]*m|-o\s+monitor)/

/** A redirection target that is a fixed file or text the shell does not expand, and that is
 *  not the inherited stdin under another name. */
function literalTarget(raw) {
  if (!raw || '<>'.includes(raw[0]) || raw.includes('$') || raw.includes('`')) return false
  const [target, dyn] = word(raw)
  // a glob or brace target may expand to /dev/stdin (`</dev/std[i]n`, #777 review 9)
  return !dyn && !INHERITED_TARGET_RE.test(target)
}

/** The simple command has its OWN stdin and does not read the inherited one (#777 review 8,
 *  the effective_stdin rule; the Python twin's _own_stdin). Stdin redirections are followed in
 *  bash order and the LAST one wins (`bash </dev/null <&0` reads the inherited stdin). Own only
 *  when that last target is literal: a file (not /dev/stdin, /dev/fd/N, /proc/PID/fd/N under any
 *  spelling), a here-string or a quoted-delimiter heredoc without expansion; `<&-` closes it.
 *  `<&n`, a process substitution and anything with `$` or a backtick may be the inherited stdin.
 *  Without a stdin redirection a background job (`&`) gets /dev/null, unless job control is on
 *  (`set -m`). Inside a read/mapfile loop body nothing is exempt: the lines come from the stdin. */
function ownStdin(piece, nextSep, loose = false, jobControl = false) {
  if (loose) return false
  let toks = piece.match(SHELL_WORD_RE) ?? []
  // the same comment cut as cmdWord: `bash # <x` has no redirection (#777 review 9)
  const hash = toks.findIndex((t) => t.startsWith('#'))
  if (hash >= 0) toks = toks.slice(0, hash)
  let own = null
  for (let i = 0; i < toks.length; i++) {
    const m = STDIN_REDIR_RE.exec(toks[i])
    if (!m) continue
    let raw = toks[i].slice(m[0].length)
    if (!raw && i + 1 < toks.length) raw = toks[++i]
    const op = m[1]
    if (op === '<&') own = raw === '-'
    // any quote or backslash in the delimiter quotes it: `<<\\B`, `<<'B'`, `<<B"C"` (#777 review 9)
    else if (op === '<<' || op === '<<-') own = [...`\\'"`].some((q) => raw.includes(q)) && !raw.includes('$') && !raw.includes('`')
    else own = literalTarget(raw)
  }
  if (own !== null) return own
  return nextSep === '&' && !jobControl
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
  const jobControl = JOB_CONTROL_RE.test(text)
  const blanked = subBlanked(text)
  let interp = ''
  let shell = ''
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
      if (!c || ownStdin(p, parts[i + 1] ?? '', loose, jobControl)) continue
      if (strongConsumer(c)) return c   // a gdb or a dynamic word anywhere in the body wins (#777 review 13/14)
      if (!codeConsumer(c)) shell = shell || c
      else if (!interp) interp = c
    }
  }
  return shell || interp
}

/** {name: body text} for every `name() { ... }` / `name() ( ... )` / `function name { ... }`. */
export function functionBodies(cmd) {
  const out = {}
  for (const m of cmd.matchAll(FUNC_DEF_RE)) {
    const name = m[1] || m[3]
    const opener = m[2] || m[4]
    const start = m.index + m[0].length
    out[name] = cmd.slice(start, bodyEnd(cmd, start, opener))
  }
  return out
}

/** Where bash ends a function body opened by `opener` at `start` (cmd.length when it does not):
 *  the LATER of the parsed end and the old plain bracket count, so a parser error over-blocks
 *  (#777 review 11: `f(){ echo ${x:- ; } ; bash; }` ended at the `}` inside ${...}).
 *  The Python twin's _body_end. */
function bodyEnd(cmd, start, opener) {
  return Math.max(parsedBodyEnd(cmd, start, opener), countedBodyEnd(cmd, start, opener))
}

/** The pre-review-10 end: the bracket that balances `opener`, quotes not seen. */
function countedBodyEnd(cmd, start, opener) {
  const closer = opener === '{' ? '}' : ')'
  let depth = 1
  for (let i = start; i < cmd.length; i++) {
    if (cmd[i] === opener) depth++
    else if (cmd[i] === closer && !--depth) return i
  }
  return cmd.length
}

/** The index after the escape, quote or expansion starting at `i` (`\x`, '..', "..", `..`,
 *  $'..', ${..}, $(..)), or -1 when none starts there. */
function skipQuoted(cmd, i) {
  const n = cmd.length
  const c = cmd[i]
  if (c === '\\') return i + 2
  if (c === "'") {
    const j = cmd.indexOf("'", i + 1)
    return j < 0 ? n : j + 1
  }
  if (c === '"' || c === '`') {
    i++
    while (i < n && cmd[i] !== c) {
      const j = c === '"' && '\\$`'.includes(cmd[i]) ? skipQuoted(cmd, i) : -1
      i = j >= 0 ? j : i + (cmd[i] === '\\' ? 2 : 1)
    }
    return i + 1
  }
  if (c === '$' && i + 1 < n) {
    const d = cmd[i + 1]
    if (d === "'") {
      i += 2
      while (i < n && cmd[i] !== "'") i += cmd[i] === '\\' ? 2 : 1
      return i + 1
    }
    if (d === '(') return parsedBodyEnd(cmd, i + 2, '(') + 1
    if (d === '{') {
      let depth = 1
      i += 2
      while (i < n) {
        const j = skipQuoted(cmd, i)
        if (j >= 0) { i = j; continue }
        if (cmd[i] === '{') depth++
        else if (cmd[i] === '}' && !--depth) return i + 1
        i++
      }
      return n
    }
  }
  return -1
}

/** [delimiter, end index] of the here-document word at `i`, quotes and escapes removed. */
function heredocDelimiter(cmd, i) {
  const n = cmd.length
  let out = ''
  while (i < n && !' \t\n;&|()<>'.includes(cmd[i])) {
    const c = cmd[i]
    if (c === "'" || c === '"') {
      let j = cmd.indexOf(c, i + 1)
      if (j < 0) j = n
      out += cmd.slice(i + 1, j)
      i = j + 1
    } else if (c === '\\') {
      out += cmd.slice(i + 1, i + 2)
      i += 2
    } else {
      out += c
      i++
    }
  }
  return [out, i]
}

/** Where bash ends the body, parsed: quotes, escapes, expansions, comments and here-document
 *  bodies are skipped. `)` closes a `( )` body. A `}` closes only as a reserved word in command
 *  position, after `;`, `&`, a newline or a closed `)`/`}`, with a word end after it (measured in
 *  probe-sandbox, #777 review 10: `echo a}b`, `echo '}'`, `echo }` do not close). A `{` word
 *  opens a nested group. The Python twin's _parsed_body_end. */
function parsedBodyEnd(cmd, start, opener) {
  const n = cmd.length
  let depth = 1
  let i = start
  let closed = -1                                 // index of the last closing `)`/`}`
  let pending = []                                // here-documents whose body starts at the next newline
  while (i < n) {
    const c = cmd[i]
    const j = skipQuoted(cmd, i)
    if (j >= 0) { i = j; continue }
    if (c === '#' && (i === start || ' \t\n;&|(){}'.includes(cmd[i - 1]))) {
      const e = cmd.indexOf('\n', i)
      i = e < 0 ? n : e
      continue
    }
    if (cmd.startsWith('<<<', i)) { i += 3; continue }
    if (cmd.startsWith('<<', i)) {
      const m = /<<(-?)[ \t]*/y
      m.lastIndex = i
      const mm = m.exec(cmd)
      const [word, e] = heredocDelimiter(cmd, m.lastIndex)
      pending.push([word, mm[1] === '-'])
      i = e
      continue
    }
    if (c === '\n' && pending.length) {
      for (const [word, strip] of pending) {
        while (i < n) {
          let e = cmd.indexOf('\n', i + 1)
          if (e < 0) e = n
          const line = cmd.slice(i + 1, e)
          i = e
          if ((strip ? line.replace(/^\t+/, '') : line) === word) break
        }
      }
      pending = []
      continue
    }
    if (opener === '(') {
      if (c === '(') depth++
      else if (c === ')' && !--depth) return i
    } else if ((c === '{' || c === '}') && (i === start || ' \t\n;&|()'.includes(cmd[i - 1])) &&
               (i + 1 >= n || ' \t\n;&|()<>'.includes(cmd[i + 1]))) {
      if (c === '{') depth++
      else {
        let p = i - 1
        while (p >= start && ' \t'.includes(cmd[p])) p--
        if (p < start || ';&\n'.includes(cmd[p]) || (')}'.includes(cmd[p]) && p === closed)) {
          depth--
          closed = i
          if (!depth) return i
        }
      }
    } else if (c === ')') closed = i
    i++
  }
  return n
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
  if (cmd !== undefined && Object.hasOwn(CLOSERS, last)) {
    // a gdb inside the compound is the consumer, not the closer (#777 review 13: `{ gdb -q; } <<A`)
    const c = anyRunsStdin(compoundText(cmd, end, last), 0, funcs)
    return strongConsumer(c) ? c : (c ? last : '')
  }
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
 * A quoted or escaped separator before the consumer (`env X='a;b' bash <<A`, `bash -s 'x(y' <<A`)
 * cut the opener line in the wrong place and the body passed as data (#777 review 12). The
 * quote-aware reading comes second and only adds a consumer: the opener line alone does not
 * show a quote opened on an earlier line. The Python twin's _heredoc_runs_body.
 */
export function heredocRunsBody(cmd, h, funcs) {
  if (funcs === undefined) funcs = functionBodies(cmd)
  const plain = heredocRunsSplit(cmd, h, funcs, plainSplit)
  if (plain && !codeConsumer(plain)) {
    // ...unless the quoted cut finds a gdb or a dynamic word (#777 review 14)
    const q = heredocRunsSplit(cmd, h, funcs, quotedSplit)
    return strongConsumer(q) ? q : plain
  }
  // a code consumer from the plain cut may be a word of a quoted value (#777 review 13:
  // `timeout 5 bash -s 'a;python3' <<A` is read by bash): a shell from the quoted cut wins
  const quoted = heredocRunsSplit(cmd, h, funcs, quotedSplit)
  if (plain && !SHELLS.has(baseName(quoted)) && !isGdb(baseName(quoted))) return plain   // the quoted cut misreads a line that starts inside an earlier quote
  return quoted || plain
}

const plainSplit = (rx, text) => text.split(rx)

/** Per character: inside quotes (0 no, 1 single, 2 double)? `$(` and backticks inside double
 *  quotes go back to command context, `$'..'` escapes its closer. The Python twin's _quote_map. */
function quoteMap(text) {
  const state = new Array(text.length).fill(0)
  const stack = []
  let q = 0
  let depth = 0
  let i = 0
  const n = text.length
  while (i < n) {
    const ch = text[i]
    if (ch === '\\' && q !== 1 && i + 1 < n) {
      state[i] = q
      state[i + 1] = q
      i += 2
      continue
    }
    if (q === 2 && (ch === '`' || text.startsWith('$(', i))) {
      const step = ch === '`' ? 1 : 2
      stack.push([ch === '`' ? '`' : ')', depth])
      depth = 0
      for (let k = 0; k < step; k++) state[i + k] = 0
      i += step
      q = 0
      continue
    }
    if (q === 0 && stack.length) {
      const closer = stack[stack.length - 1][0]
      if (closer === ')' && ch === '(') depth++
      else if (ch === closer && (closer === '`' || depth === 0)) {
        depth = stack.pop()[1]
        state[i] = 0
        i++
        q = 2
        continue
      } else if (closer === ')' && ch === ')') depth--
    }
    if (q === 0 && text.startsWith("$'", i)) {
      i += 2
      while (i < n && text[i] !== "'") {
        const step = text[i] === '\\' && i + 1 < n ? 2 : 1
        for (let k = 0; k < step; k++) state[i + k] = 1
        i += step
      }
      if (i < n) i++
      continue
    }
    if (q === 0 && (ch === "'" || ch === '"')) {
      q = ch === "'" ? 1 : 2
      i++
      continue
    }
    if ((q === 1 && ch === "'") || (q === 2 && ch === '"')) {
      q = 0
      i++
      continue
    }
    state[i] = q
    i++
  }
  return state
}

/** text.split(rx) at the matches outside quotes, backslash escapes and balanced `${...}` only
 *  (#777 review 12, 13: `X=${Y:-a;b} bash <<A`). The Python twin's _quoted_split; rx keeps its
 *  separators when it has a capture group. */
function quotedSplit(rx, text) {
  const state = quoteMap(text)
  const mask = text.split('')
  for (let i = 0; i < text.length; i++) {
    if (state[i]) mask[i] = '_'
    else if (text[i] === '\\' && i + 1 < text.length) {
      mask[i] = mask[i + 1] = '_'
      i++
    } else if (text.startsWith('${', i)) {
      const end = paramEnd(text, state, i)
      if (end) {
        for (let k = i; k < end; k++) mask[k] = '_'
        i = end - 1
      }
    }
  }
  const keep = new RegExp(rx.source + '|').exec('').length > 1
  const out = []
  let pos = 0
  for (const m of mask.join('').matchAll(new RegExp(rx.source, 'g'))) {
    out.push(text.slice(pos, m.index))
    if (keep) out.push(text.slice(m.index, m.index + m[0].length))
    pos = m.index + m[0].length
  }
  out.push(text.slice(pos))
  return out
}

/** The index after the `}` closing the `${` at i (nested `${`/`{` counted, quoted text skipped),
 *  or 0 when it is not closed on this text. The Python twin's _param_end. */
function paramEnd(text, state, i) {
  let depth = 0
  for (let j = i + 1; j < text.length; j++) {
    if (state[j]) continue
    const ch = text[j]
    if (ch === '\\') { j++; continue }
    if (ch === '{') depth++
    else if (ch === '}' && --depth === 0) return j + 1
  }
  return 0
}

const PIPE_COMPOUND_RE = /^\s*(?:(?:if|while|until|for|select|case)(?![\w-])|[({])/

/** More compound openers or `(` than closers: the compound goes on after this line. */
function unbalanced(text) {
  const toks = [...text.matchAll(COMPOUND_TOKEN_RE)].map((m) => m[0])
  const opened = toks.filter((t) => ['while', 'until', 'for', 'select', 'if', 'case', '{'].includes(t)).length
  const closed = toks.filter((t) => ['done', 'fi', 'esac', '}'].includes(t)).length
  return opened > closed || (text.match(/\(/g) ?? []).length > (text.match(/\)/g) ?? []).length
}

/** The index of the last newline before pos that ends a command line: not inside a quote, an
 *  escape, a `$(`, `${` or backtick, and not in an earlier heredoc body; -1 when none. */
function logicalLineStart(cmd, pos) {
  const chars = [...cmd.slice(0, pos)]
  for (const r of findAll(cmd).records) {
    if (r.kind !== 'herestring' && r.end <= pos) {   // the scanner's heredoc records carry no kind
      for (let i = r.bodyStart; i < r.end; i++) if (chars[i] !== '\n') chars[i] = '_'
    }
  }
  const text = chars.join('')
  const state = quoteMap(text)
  const stack = []
  let last = -1
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (state[i]) continue
    if (ch === '\\') { i++; continue }
    if (text.startsWith('$(', i) || text.startsWith('${', i)) { stack.push(text[i + 1] === '(' ? ')' : '}'); i++; continue }
    if (ch === '`') { if (stack.at(-1) === '`') stack.pop(); else stack.push('`') }
    else if (stack.length && (ch === '(' || ch === '{')) stack.push(ch === '(' ? ')' : '}')
    else if (stack.length && ch === stack.at(-1)) stack.pop()
    else if (ch === '\n' && !stack.length) last = i
  }
  return last
}

function heredocRunsSplit(cmd, h, funcs, split) {
  // the quoted cut starts at the logical line: a newline inside a quote or a `$(`/`${` before
  // the opener does not end it (#777 review 14: `X="a<newline>b" bash <<A`)
  let ls = split === quotedSplit ? logicalLineStart(cmd, h.start) : cmd.lastIndexOf('\n', h.start - 1)
  while (ls > 0 && cmd[ls - 1] === '\\') ls = cmd.lastIndexOf('\n', ls - 2)
  if (h.start - ls > LINE_CAP || h.lineEnd - h.tailStart > LINE_CAP) return 'unread'
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
      let line = head + ' ' + split(TAIL_SPLIT, stripComment(cmd.slice(h.tailStart, h.lineEnd)))[0]
      if (!new RegExp(path).test(line) && !new RegExp(dup).test(line)) return ''
      line = line.replace(new RegExp(path, 'g'), '-').replace(new RegExp(dup, 'g'), ' ')
      // the last simple command, with its closer (`{ bash; } 2>&1 3<<EOF <&3`, #775):
      // the same way as the stdin-heredoc head below
      return simpleRuns(split(HEAD_SPLIT, subBlanked(line)).at(-1), funcs, cmd, h.start)
    }
  }
  const parts = split(HEAD_SPLIT, subBlanked(head))
  let tail = split(TAIL_SPLIT, stripComment(cmd.slice(h.tailStart, h.lineEnd)))[0]
  // inside a backtick substitution the next closer (`\`` of a nested one, or the bare backtick)
  // ends the simple command: `echo `echo \`bash <<< 'x'\` tail`` runs `bash`, the ` tail` after
  // the closer is the OUTER echo's (#777 review 4)
  if (inBackticks(head)) tail = tail.split(BACKTICK_CLOSER)[0]
  const pipe = split(PIPE_SPLIT, tail)
  const first = simpleRuns(parts[parts.length - 1] + ' ' + pipe[0], funcs, cmd, h.start)
  if (first) return first
  const fullTail = stripComment(cmd.slice(h.tailStart, h.lineEnd))
  let off = pipe[0].length + 1
  for (const p of pipe.slice(1)) {
    let c
    if (PIPE_COMPOUND_RE.test(p)) {
      // a compound member is read whole, to its closer on a later line too (#777 review 13, 14:
      // `cat <<A | (gdb -q)`, `cat <<A | if true; then bash; fi`)
      let text = fullTail.slice(off)
      if (unbalanced(text)) text += '\n' + cmd.slice(h.end)
      c = anyRunsStdin(text, 0, funcs)
    } else c = simpleRuns(p, funcs)
    if (c) return c
    off += p.length + 1
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
function simpleSegments(text, withSeps = false) {
  const out = []
  const seps = []                // the separator text before each segment (#779 pipes)
  let sep = ''
  const n = text.length
  const stack = []               // [closer, outer paren depth] per open substitution in double quotes
  let start = 0
  let i = 0
  let q = null
  let depth = 0
  const cut = (end, width) => { out.push(text.slice(start, end)); seps.push(sep); sep = text.slice(end, end + width); start = end + width; i = start }
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
  seps.push(sep)
  if (!withSeps) return out.map((s) => s.trim()).filter(Boolean)
  // an empty segment hands its separator on: `a |& b` is `|` then `&` around nothing
  const kept = []
  let carry = ''
  out.forEach((seg, k) => {
    if (seg.trim()) { kept.push({ seg: seg.trim(), sep: carry + seps[k] }); carry = '' }
    else carry += seps[k]
  })
  return kept
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
    // #779 round 7: a continuation glued on both sides joins the words (`bash -c\<NL>'X'` is the
    // glued `-c'X'`, which bash rejects as an invalid option); SHELL_WORD_RE alone would split
    // there and read X as the program. The Python twin already reads it as one word.
    const fixed = seg.replace(/(?<=[^\s\\])\\\n(?=\S)/g, '').replaceAll(')', ' ) ')
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
  return out.concat(pipedPrograms(lightText(s)))
}

const segWords = (seg) => (seg.match(SHELL_WORD_RE) ?? []).map(word)

/** #779 (Marveen comment 3627d): `echo 'X' | bash`, `printf '%s\\n' 'X' | sh -s` -- a literal
 *  echo/printf piped into a shell that reads its program from stdin; the program is the echoed
 *  text. A producer that is not echo/printf (`cat f`, `$X`) stays out of reach. The Python twin
 *  is _piped_programs in destructive-gate.py. */
function pipedPrograms(light) {
  const parts = simpleSegments(light, true)
  const out = []
  for (let k = 1; k < parts.length; k++) {
    if (parts[k].sep !== '|' && parts[k].sep !== '|&') continue
    // the heredoc consumer resolver decides, so wrappers (`| sudo bash`), `bash -`, `sh -s` read
    // the same way as for a body (#779 review 3833: `| bash -` and `| sudo bash` passed)
    if (!SHELLS.has(baseName(runsStdinAsProgram(parts[k].seg)))) continue
    const prog = echoedText(segWords(parts[k - 1].seg))
    if (prog && prog.trim()) out.push(prog)
  }
  return out
}

/** The text a literal echo/printf writes, or null. */
function echoedText(words) {
  const k = skipPrefix(words, 0)
  if (k >= words.length) return null
  const base = baseName(words[k][0])
  let args = words.slice(k + 1).map(([w]) => w)
  if (base === 'echo') {
    while (args.length && /^-[neE]+$/.test(args[0])) args = args.slice(1)
    return args.join(' ')
  }
  if (base === 'printf') {
    if (args[0] === '--') args = args.slice(1)
    if (!args.length || args[0] === '-v') return null
    return args.length > 1 ? args.slice(1).join('\n') : args[0].replaceAll('\\n', '\n')
  }
  return null
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
    if (!consumer) continue
    const body = s.slice(h.bodyStart, h.bodyEnd).replace(/\n$/, '')
    out.push({ consumer: baseName(consumer), body })
  }
  return out
}

/** Does a gdb read one of the command's heredocs or here-strings (this level only; the gates
 *  recurse into the bodies a shell runs)? Every gate blocks that whole (#777 review 12). A
 *  consumer the shell expands at run time may be gdb and blocks the same way (#777 review 13),
 *  unless it is a `$NAME` assigned only literal non-gdb words before. The Python twin's gdb
 *  checks in _stdin_programs. */
export function gdbReadsStdin(cmd) {
  const s = String(cmd ?? '')
  if (!s.includes('<<')) return false
  const { records } = findAll(s)
  const funcs = functionBodies(s)
  return records.some((h) => {
    const c = heredocRunsBody(s, h, funcs)
    return baseName(c) === 'gdb' || (dynamicGdb(c) && !assignedNonGdb(s, c, h.start))
  })
}

// A consumer the shell expands at run time (`$G -q <<A`, `$(echo gdb)`, a backtick, `"$@"` or `$1`
// in a function, a glob or brace name). The Python twin's _DYNAMIC_CONSUMER_RE.
const DYNAMIC_CONSUMER_RE = /[$`*?[{]/

/** fnmatchcase(name, pattern): `*`, `?`, `[..]` (`[!..]` negated), the rest literal. */
function globMatch(name, pattern) {
  let rx = ''
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i]
    if (c === '*') rx += '.*'
    else if (c === '?') rx += '.'
    else if (c === '[' && pattern.indexOf(']', i + 2) > 0) {
      const j = pattern.indexOf(']', i + 2)
      let body = pattern.slice(i + 1, j).replaceAll('\\', '\\\\')
      if (body.startsWith('!')) body = '^' + body.slice(1)
      rx += '[' + body + ']'
      i = j
    } else rx += c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  }
  return new RegExp('^' + rx + '$', 's').test(name)
}

/** A consumer that BLOCKs whole: gdb, or a word that may expand to gdb. */
function strongConsumer(c) {
  return Boolean(c) && (baseName(c) === 'gdb' || dynamicGdb(c))
}

/** The consumer of a command line run as a program (`bash -c P`, `su -c P`, `sg G P`, `eval P`):
 *  a gdb or a dynamic word inside it, else the runner itself (#777 review 14). */
function programConsumer(text, base, depth) {
  const c = anyRunsStdin(text, depth + 1)
  return strongConsumer(c) ? c : (c ? base : '')
}

/** An expanded word (`$..`, a backtick) or a glob/brace pattern that may name gdb. */
function dynamicGdb(consumer) {
  if (consumer.startsWith('$') || consumer.startsWith('`')) return true
  if (!DYNAMIC_CONSUMER_RE.test(consumer)) return false
  const pattern = rawBase(consumer).replace(/\{[^{}]*\}/g, '*')
  return [...GDB_NAMES, 'x-gdb', 'gdb-x'].some((n) => globMatch(n, pattern))
}

// ...unless it is a plain `$NAME` whose every assignment in the command before it is a literal
// non-gdb word: `P=/x/venv/bin/python; $P - <<EOF` (30 real fleet commands in the replay).
const VAR_CONSUMER_RE = /^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$/
const LITERAL_VALUE = `(?:'([^']*)'|"([^"$\`\\\\]*)"|([^\\s;&|()<>$\`'"\\\\]*))(?=[\\s;&|)]|$)`

// What may stand before a word that starts a simple command (the Python twin's _STATEMENT_START_RE).
const STATEMENT_START_RE = /(?:^|[;&|(){}\n]|(?<![\w-])(?:then|do|else|elif))[ \t]*$/

/** Every occurrence of NAME before the consumer is a read (`$NAME`, `${NAME}`), a literal
 *  `NAME=word` or a `for NAME in word..` at a statement start, all non-gdb. Any other write
 *  (`read`, `+=`, `eval`, `printf -v`, `declare -n`, `NAME[0]=`, `mapfile`, `${NAME:=..}`,
 *  `select`, `export`) is no exception (#777 review 14). The Python twin's _assigned_non_gdb. */
function assignedNonGdb(text, consumer, end) {
  const m = VAR_CONSUMER_RE.exec(consumer)
  if (!m) return false
  const name = m[1]
  const head = text.slice(0, end)
  let writes = 0
  for (const occ of head.matchAll(new RegExp('(?<![A-Za-z0-9_])' + name + '(?![A-Za-z0-9_])', 'g'))) {
    const i = occ.index
    const j = i + name.length
    if (head.slice(i - 1, i) === '$' || (head.slice(i - 2, i) === '${' && head[j] === '}')) continue
    if (!STATEMENT_START_RE.test(head.slice(0, i))) {
      const pre = /^([\s\S]*?)for[ \t]+$/.exec(head.slice(0, i))
      if (!pre || !STATEMENT_START_RE.test(pre[1])) return false
      const words = /^[ \t]+in((?:[ \t]+[A-Za-z0-9_./-]+)+)[ \t]*(?:;|\n)/.exec(head.slice(j))
      if (!words || words[1].trim().split(/\s+/).some((w) => isGdb(baseName(w)))) return false
      writes++
      continue
    }
    if (head[j] !== '=') return false
    const value = new RegExp('^' + LITERAL_VALUE).exec(head.slice(j + 1))
    if (!value) return false
    const w = value[1] ?? value[2] ?? value[3] ?? ''
    if (!w || DYNAMIC_CONSUMER_RE.test(w) || isGdb(baseName(w.split(/\s/)[0]))) return false
    writes++
  }
  return writes > 0
}

// Without a heredoc too (#777 review 13): an -ex/-iex/--eval-command/--init-eval-command value
// whose gdb command runs a shell line or code (`-ex 'shell X'`, `-ex '!X'`, `-ex 'pipe p 1 | X'`,
// `-ex 'python ..'`, `py`, `pi`, guile, compile, eval, interpreter-exec; gdb takes any unique
// prefix of 2+ letters) or is dynamic BLOCKs; `-ex bt`, `-ex 'info functions'` stay allowed.
// The Python twin's _GDB_EX_FLAG_RE and friends.
const GDB_EX_FLAG_RE = /^--?(ex|iex|ev[a-z-]*|init-e[a-z-]*)(=[\s\S]*)?$/
const GDB_EX_LONG = ['eval-command', 'init-eval-command']
const GDB_RUNNING_CMDS = ['shell', 'pipe', 'python', 'python-interactive', 'guile', 'compile', 'eval', 'interpreter-exec']

// A call: a `(` after a name, `)`, `]` or a quote; sizeof/alignof are operators (#777 review 15).
const GDB_CALL_RE = /(?<![A-Za-z0-9_$])(?!(?:sizeof|alignof|_Alignof|__alignof__)\s*\()[A-Za-z0-9_$]+\s*\(|[)\]'"]\s*\(/
const GDB_SAFE_ALIASES = ['bt', 'where', 'i', 'p', 'x', 'l', 'f', 'pt', 'c', 'q']
const GDB_SAFE_PREFIXES = [['backtrace', 4], ['info', 3], ['print', 1], ['disassemble', 5], ['list', 2],
  ['ptype', 3], ['whatis', 5], ['frame', 2], ['show', 3], ['continue', 4], ['gcore', 5],
  ['generate-core-file', 18], ['set', 3], ['quit', 2], ['thread', 3], ['faas', 4], ['taas', 4], ['tfaas', 5]]

/** The command `frame apply` / `thread apply` / faas / taas / tfaas runs (#777 review 15), ''
 *  when it cannot be read, null when the line applies nothing. The Python twin's _gdb_applied. */
function gdbApplied(name, rest) {
  let words = rest.split(/\s+/).filter(Boolean)
  const num = (w) => /^[0-9][0-9.*-]*$/.test(w)
  if (!['faas', 'taas', 'tfaas'].includes(name)) {
    if (!(['f', 'fr', 'fra', 'fram', 'frame'].includes(name) || 'thread'.startsWith(name))) return null
    if (!words.length || !'apply'.startsWith(words[0].toLowerCase())) return null
    words = words.slice(1)
    if (words[0] === 'all') words = words.slice(1)
    else if (words[0] === 'level') { words = words.slice(1); while (words.length && num(words[0])) words = words.slice(1) }
    else {
      if (!words.length || !num(words[0])) return ''
      while (words.length && num(words[0])) words = words.slice(1)
    }
  }
  while (words.length && words[0].startsWith('-')) {
    const w = words.shift()
    if (w === '--') break
  }
  return words.join(' ')
}

/** A gdb -ex value is allowed only when every line is a read-only inspection command (an
 *  ALLOWLIST, #777 review 14); anything else, a call included, runs. The Python twin's _gdb_value_runs. */
function gdbValueRuns(value) {
  for (const line of value.split('\n')) {
    const v = line.trim()
    if (!v) continue
    const m = /^([A-Za-z][A-Za-z-]*)(?=$|[ \t/])/.exec(v)
    if (!m) return true
    const name = m[1].toLowerCase()
    if (GDB_CALL_RE.test(v.slice(m[0].length).replace(/^\/\w*/, ''))) return true   // `p/x (char)c` is a cast
    if (['r', 'run', 'start'].includes(name)) {
      if (v.slice(m[0].length).trim()) return true   // `run $(X)`: gdb hands the arguments to a shell
      continue   // bare `run`: the program on the command line, judged there
    }
    if (!GDB_SAFE_ALIASES.includes(name) && !GDB_SAFE_PREFIXES.some(([c, n]) => name.length >= n && c.startsWith(name))) return true
    const applied = gdbApplied(name, v.slice(m[0].length))
    if (applied !== null) {
      if (!applied || gdbValueRuns(applied)) return true   // `thread apply all CMD`, `frame apply`, faas/taas/tfaas run CMD
      continue
    }
    if ('set'.startsWith(name) && name.startsWith('se') &&
        !/^[A-Za-z-]+[ \t]+(?:pagination|confirm|print|width|height|style|listsize)(?![\w-])/.test(v)) return true
  }
  return false
}

const gdbWord = (w) => isGdb(baseName(w.replace(/^[$(`{]+/, '')))

/** A gdb in this simple command runs a shell line or code through an -ex style value; the gdb
 *  word may stand after a wrapper (`sudo gdb -ex ..`), so every word is looked at. Returns the
 *  offending value, or ''. The Python twin's _gdb_ex_runs. */
function gdbExSegment(seg) {
  let toks = seg.match(SHELL_WORD_RE) ?? []
  const hash = toks.findIndex((t) => t.startsWith('#'))
  if (hash >= 0) toks = toks.slice(0, hash)
  const words = toks.map(word)
  for (let g = 0; g < words.length; g++) {
    if (!gdbWord(words[g][0])) continue   // `(gdb -ex ..)`: the paren is not the name (#777 review 14)
    for (let j = g + 1; j < words.length; j++) {
      const m = GDB_EX_FLAG_RE.exec(words[j][0])
      if (!m || !(m[1] === 'ex' || m[1] === 'iex' || GDB_EX_LONG.some((f) => f.startsWith(m[1])))) continue
      let value, dyn
      if (m[2] !== undefined) [value, dyn] = [m[2].slice(1), words[j][1]]
      else if (j + 1 < words.length) [value, dyn] = words[++j]
      else break
      if (dyn || gdbValueRuns(value)) return value || '(dynamic)'
    }
  }
  return ''
}

/** The first gdb -ex/-iex value in `cmd` (this level: the shell text, not the heredoc bodies and
 *  -c programs, which the gates recurse into) that runs a shell line or code, or ''. */
export function gdbExRuns(cmd) {
  const s = String(cmd ?? '')
  if (!/g\S*d\S*b/.test(s) && !s.includes("$'")) return ''   // `g''db`, `g\db`, `g$'\x64'b` too (#777 review 14, 15)
  for (const seg of simpleSegments(lightText(s))) {
    const v = gdbExSegment(seg)
    if (v) return v
  }
  return ''
}

// `--args`, `-args`, `--arg`, `-ar`, quoted too: gdb takes any unique prefix (#777 review 15)
const GDB_ARGS_RE = /^--?ar(?:gs?)?$/

/** The programs after `gdb .. --args` in `cmd` (gdb runs them on `run`), as raw text; the gates
 *  judge each as a command line (#777 review 14). The Python twin's _gdb_args_programs. */
export function gdbArgsPrograms(cmd) {
  // No raw-text prefilter: quotes split the flag (-'a'rgs, "--"args, -$'a'rgs), only the
  // unquoted word is matched.
  const s = String(cmd ?? '')
  const out = []
  for (const seg of simpleSegments(lightText(s))) {
    let toks = seg.match(SHELL_WORD_RE) ?? []
    const hash = toks.findIndex((t) => t.startsWith('#'))
    if (hash >= 0) toks = toks.slice(0, hash)
    toks.forEach((t, g) => {
      if (!gdbWord(word(t)[0])) return
      const rest = toks.slice(g + 1)
      const a = rest.findIndex((t) => GDB_ARGS_RE.test(word(t)[0]))
      if (a >= 0) out.push(rest.slice(a + 1).join(' '))
    })
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

// #779: the shell commands a code payload (python/node/perl/ruby) hands to a shell. The twin of
// destructive-gate.py _SHELLOUT_RE / shell_out_commands / embedded_commands; the shared case
// file scripts/__tests__/shell-out-cases.json pins both sides.
const SHELLOUT_RE = new RegExp(
  String.raw`(?:(?<![\w$])(?:execSync|spawnSync|execFileSync)|` +
  String.raw`require\(\s*(?:\(\s*)*['"](?:node:)?child_process['"]\s*(?:\)\s*)+\.\s*(?:exec|execFile|spawn|execSync|execFileSync|spawnSync)|` +
  String.raw`(?<![\w.])(?:os\.system|os\.popen|os\.exec[lv]?[pe]*|os\.spawn[lv]p?e?|pty\.spawn|` +
  String.raw`subprocess\.(?:run|call|check_call|check_output|Popen|getoutput|getstatusoutput)|` +
  String.raw`child_process\.(?:exec|execSync|execFile|execFileSync|spawn|spawnSync)|IO\.popen|` +
  String.raw`Open3\.(?:capture2|capture3|popen3)|asyncio\.create_subprocess_(?:shell|exec)|` +
  // #779 review 3833: the module object from __import__/importlib (`__import__("os").system(`)
  // #779 round 5: spaces before the parenthesis, and a bare import_module (`from importlib import import_module`)
  String.raw`(?:__import__|(?:importlib\s*\.\s*)?import_module)\s*\(\s*['"](?:os|subprocess|pty)['"]\s*\)\s*\.\s*` +
  String.raw`(?:system|popen|exec[lv]?[pe]*|spawn(?:[lv]p?e?)?|run|call|check_call|check_output|Popen|getoutput|` +
  String.raw`getstatusoutput)|` +
  // #779 review 3835: ruby's Kernel methods with a receiver; a bare spawn stays out (a JS spawn from another module, r2 negative)
  String.raw`Kernel\.(?:system|exec|spawn)|Process\.spawn|system|exec|qx))\s*\(`, 'g')
// #779 review 3835: perl/ruby call without parentheses (`system "X"`, `exec 'X', 'a'`)
const SHELLOUT_BARE_RE = /(?<![\w.$>:])(?:Kernel\.)?(?:system|exec)[ \t]+(?=['"])/g
const SHELLOUT_MEMBERS = {
  subprocess: 'run|call|check_call|check_output|Popen|getoutput|getstatusoutput',
  os: 'system|popen|exec[lv]?[pe]*|spawn[lv]p?e?',
  pty: 'spawn',
  child_process: 'exec|execFile|spawn|execSync|execFileSync|spawnSync',
}
// #779 review 3833: `import os, subprocess as sp`, a node child_process alias or destructuring,
// an ES import from "child_process"; the twin of destructive-gate.py _imported_shellout_re
const IMPORT_LIST_RE = /(?<![\w.])import[ \t]+(\w+(?:[ \t]+as[ \t]+\w+)?(?:[ \t]*,[ \t]*\w+(?:[ \t]+as[ \t]+\w+)?)*)/g
const CP_MODULE = String.raw`['"](?:node:)?child_process['"]`
// #779 review 3835: any assignment, not only right after const (`const a=1, cp=require(..)`)
const JS_CP_ALIAS_RE = new RegExp(
  // #779 round 6: nested parentheses around the module (`require( ( "node:child_process" ) )`)
  String.raw`(?<![\w$.])([\w$]+)\s*=\s*require\(\s*(?:\(\s*)*` + CP_MODULE + String.raw`\s*(?:\)\s*)*\)(?![\s)]*\.)`, 'g')
// an ES import clause: default, `* as ns`, `{named}`, or default plus one of the other two
const JS_CP_IMPORT_RE = new RegExp(
  String.raw`(?<![\w$.])import\s*((?:[\w$]+\s*,\s*)?(?:\*\s*as\s+[\w$]+|\{[^}]*\})|[\w$]+)\s*from\s*` + CP_MODULE, 'g')
const JS_CP_NAMED_RE = new RegExp(
  String.raw`(?<![\w$.])\{([^}]*)\}\s*=\s*require\(\s*(?:\(\s*)*` + CP_MODULE + String.raw`\s*(?:\)\s*)*\)`, 'g')
// #779 review 3835: a parenthesised import may span lines, a bare one may continue with a backslash
const FROM_IMPORT_RE = /(?<![\w.])from\s+(subprocess|os|pty)\s+import\s+(?:\(([^)]*)\)|((?:[\w \t,]|\\\n)+))/g
const EXEC_FAMILY_RE = /(?:^|\.)(?:exec[lv]?[pe]*|spawn[lv]p?e?)\s*\($/
const STR_LIT_RE = /(['"])((?:\\.|(?!\1).)*)\1/g
const reEscape = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const shQuote = (w) => `'${w.replaceAll("'", `'"'"'`)}'`

function importedShellOutRe(payload) {
  const names = []
  for (const [, items] of payload.matchAll(IMPORT_LIST_RE)) {
    for (const item of items.split(',')) {
      const parts = item.trim().split(/\s+/).filter(Boolean)
      if (parts.length === 3 && ['subprocess', 'os', 'pty'].includes(parts[0])) {
        names.push(`${reEscape(parts[2])}\\.(?:${SHELLOUT_MEMBERS[parts[0]]})`)
      }
    }
  }
  const named = [...payload.matchAll(JS_CP_NAMED_RE)].map((m) => m[1])
  const aliases = [...payload.matchAll(JS_CP_ALIAS_RE)].map((m) => m[1])
  for (const [, clause] of payload.matchAll(JS_CP_IMPORT_RE)) {
    const brace = /\{([^}]*)\}/.exec(clause)
    if (brace) named.push(brace[1])
    for (const m of clause.trim().matchAll(/(?:^|\bas\s+)([\w$]+)\s*(?:,|$)/g)) aliases.push(m[1])
  }
  for (const alias of aliases) names.push(`${reEscape(alias)}\\.(?:${SHELLOUT_MEMBERS.child_process})`)
  for (const items of named) {
    for (const item of items.split(',')) {
      const m = /^\s*([\w$]+)(?:(?:\s*:\s*|\s+as\s+)([\w$]+))?\s*$/.exec(item)
      if (m && new RegExp(`^(?:${SHELLOUT_MEMBERS.child_process})$`).test(m[1])) names.push(reEscape(m[2] || m[1]))
    }
  }
  for (const [, mod, paren, bare] of payload.matchAll(FROM_IMPORT_RE)) {
    for (const item of (paren ?? bare).replaceAll('\\', ' ').split(',')) {
      const parts = item.trim().split(/\s+/).filter(Boolean)
      if (parts.length && new RegExp(`^(?:${SHELLOUT_MEMBERS[mod]})$`).test(parts[0])) {
        names.push(reEscape(parts.length === 3 && parts[1] === 'as' ? parts[2] : parts[0]))
      }
    }
  }
  return names.length ? new RegExp(`(?<![\\w.$])(?:${names.join('|')})\\s*\\(`, 'g') : null
}

/** The closed string literals of a code payload as [start, end] spans: triple quotes, '...',
 *  "..." (not across a newline) and `...`; a quote after a backslash opens nothing, an unclosed
 *  quote is not a literal. The twin of destructive-gate.py _literal_spans. */
function literalSpans(text) {
  const spans = []
  const n = text.length
  let i = 0
  while (i < n) {
    const ch = text[i]
    if (!'\'"`'.includes(ch) || (i && text[i - 1] === '\\')) { i++; continue }
    if (ch !== '`' && text.startsWith(ch.repeat(3), i)) {
      const j = text.indexOf(ch.repeat(3), i + 3)
      if (j < 0) break
      spans.push([i, j + 3])
      i = j + 3
      continue
    }
    let j = i + 1
    while (j < n && text[j] !== ch && (ch === '`' || text[j] !== '\n')) j += text[j] === '\\' ? 2 : 1
    if (j < n && text[j] === ch) { spans.push([i, j + 1]); i = j + 1 } else i++
  }
  return spans
}

/** The text from an opening paren to its partner (the rest of the text when unbalanced). */
function balancedArg(text, open) {
  let depth = 0
  for (let i = open; i < text.length; i++) {
    if (text[i] === '(') depth++
    else if (text[i] === ')' && --depth === 0) return text.slice(open + 1, i)
  }
  return text.slice(open + 1)
}

/**
 * The shell commands a code payload hands to a shell or exec: the string literals in the
 * arguments of a known shell-out call. One literal is a shell string; several are an argv,
 * joined with the space-holding words quoted (`["bash", "-c", "rm x"]` keeps its -c program);
 * an exec/spawn call's file literal is dropped when argv[0] repeats it. A string built at run
 * time (concatenation, an f-string with a variable, base64) is NOT read: a known limit.
 * A call written INSIDE a string literal is data, not a call.
 */
/** #779 round 5: perl's `qx(X)` reads as `system("X")`, `qq(X)` / `q(X)` as the literal "X";
 *  the twin of destructive-gate.py _perl_quotes (no nesting, a space before the parenthesis is
 *  read too, #779 round 6; qx{..}, qx/../, backticks: limit, pinned in shell-out-cases.json). */
function perlQuotes(payload) {
  return payload.replace(/(?<![\w$@%])q([qx]?)\s*\(([^()\n]*)\)/g, (all, kind, body) => {
    const q = !body.includes('"') ? '"' : !body.includes("'") ? "'" : null
    if (!q) return all
    return kind === 'x' ? `system(${q}${body}${q})` : q + body + q
  })
}

/** #779 round 6: `from importlib import import_module as im` and `import importlib as il` rename
 *  the module loader; the alias reads as the loader. The twin of _importlib_aliases. */
// #779 round 7: a Python identifier, as Python's Unicode `\w` reads it (`ím` is a name too).
const PY_ID = String.raw`[\p{L}\p{N}_]`

function importlibAliases(payload) {
  // #779 round 7: the name may stand anywhere in an import list, also in parentheses
  // (`from importlib import reload, import_module as im`, `import sys, importlib as il`).
  // #779 round 8 (Marveen comment 3865 C, D): a parenthesised list may span lines, a bare list
  // may continue with `\<NL>`; an assignment renames too (`im = importlib.import_module`,
  // `f = __import__`, `il = importlib`).
  const loaders = []
  const modules = []
  const imports = []
  const NB = String.raw`(?<![\p{L}\p{N}_.])`
  const NOT_USED = String.raw`(?![ \t]*[\p{L}\p{N}_(.])`
  for (const m of payload.matchAll(new RegExp(NB + String.raw`from\s+importlib\s+import\s+(?:\(([^()]*)\)|((?:[^()\n;\\]|\\\n?)*))`, 'gu'))) {
    const list = (m[1] ?? m[2]).replaceAll('\\\n', ' ')
    for (const a of list.matchAll(new RegExp(String.raw`(?:^|,)\s*import_module\s+as\s+(${PY_ID}+)`, 'gu'))) loaders.push(a[1])
  }
  for (const m of payload.matchAll(new RegExp(NB + String.raw`import\s+((?:[^()\n;\\]|\\\n?)*)`, 'gu'))) {
    for (const a of m[1].replaceAll('\\\n', ' ').matchAll(new RegExp(String.raw`(?:^|,)\s*importlib\s+as\s+(${PY_ID}+)`, 'gu'))) modules.push(a[1])
  }
  for (const a of payload.matchAll(new RegExp(NB + `(${PY_ID}+)` + String.raw`\s*=\s*(?:importlib\s*\.\s*)?import_module` + NOT_USED, 'gu'))) loaders.push(a[1])
  for (const a of payload.matchAll(new RegExp(NB + `(${PY_ID}+)` + String.raw`\s*=\s*__import__` + NOT_USED, 'gu'))) imports.push(a[1])
  for (const a of payload.matchAll(new RegExp(NB + `(${PY_ID}+)` + String.raw`\s*=\s*importlib` + NOT_USED, 'gu'))) modules.push(a[1])
  for (const alias of loaders) {
    payload = payload.replace(new RegExp(String.raw`(?<![\p{L}\p{N}_.])` + alias + String.raw`\s*\(`, 'gu'), 'import_module(')
  }
  for (const alias of imports) {
    payload = payload.replace(new RegExp(String.raw`(?<![\p{L}\p{N}_.])` + alias + String.raw`\s*\(`, 'gu'), '__import__(')
  }
  for (const alias of modules) {
    payload = payload.replace(new RegExp(String.raw`(?<![\p{L}\p{N}_.])` + alias + String.raw`\s*\.\s*import_module(?![\p{L}\p{N}_])`, 'gu'), 'importlib.import_module')
  }
  return payload
}

export function shellOutCommands(payload) {
  const text = importlibAliases(perlQuotes(String(payload ?? '')))
  const hits = new Map()
  for (const rx of [SHELLOUT_RE, importedShellOutRe(text)].filter(Boolean)) {
    for (const m of text.matchAll(rx)) hits.set(`${m.index},${m.index + m[0].length}`, m)
  }
  const out = []
  const order = [...hits.values()].sort((a, b) => a.index - b.index || a[0].length - b[0].length)
  const spans = order.length ? literalSpans(text) : []
  for (const m of order) {
    if (spans.some(([a, b]) => a < m.index && m.index < b)) continue
    const arg = balancedArg(text, text.indexOf('(', m.index + m[0].length - 1))
    let words = [...arg.matchAll(STR_LIT_RE)].map((x) => x[2])
    if (words.length >= 2 && EXEC_FAMILY_RE.test(m[0]) && baseName(words[0]) === baseName(words[1])) words = words.slice(1)
    const inner = argvText(words)
    if (inner && !out.includes(inner)) out.push(inner)
  }
  const bareSpans = literalSpans(text)
  for (const m of text.matchAll(SHELLOUT_BARE_RE)) {
    if (bareSpans.some(([a, b]) => a < m.index && m.index < b)) continue
    const inner = argvText(bareArgs(text, m.index + m[0].length))
    if (inner && !out.includes(inner)) out.push(inner)
  }
  return out
}

/** One literal is a shell string; several are an argv, the space-holding words quoted. */
function argvText(words) {
  return (words.length === 1 ? words[0] : words.map((w) => (/\s/.test(w) ? shQuote(w) : w)).join(' ')).trim()
}

/** The string literals of a call without parentheses (`system "a", 'b'`), up to the first
 *  thing that is not a literal or a comma; the twin of destructive-gate.py _bare_args. */
function bareArgs(text, i) {
  const words = []
  for (;;) {
    const lit = /(['"])((?:\\.|(?!\1).)*)\1/y
    lit.lastIndex = i
    const m = lit.exec(text)
    if (!m) return words
    words.push(m[2])
    const sep = /[ \t]*,[ \t]*/y
    sep.lastIndex = lit.lastIndex
    if (!sep.exec(text)) return words
    i = sep.lastIndex
  }
}

/** The quoted script after an interpreter's -c/-e. #779 round 5 (Marveen msg 17489): EVERY
 *  -c/-e/-E/-p/--eval/--command (also clustered: `-le`, `-Ic`, and `--eval='...'`) up to the
 *  command separator is read, wherever it stands; an unknown option or an option value
 *  (`--input-type=module`, `-r json`, `-MPOSIX=floor`) never stops the search. Over-reading a
 *  script argument that happens to be -c is accepted. The twin of _script_arg_ranges. */
function scriptArgPrograms(text) {
  return scriptArgGroups(text).flatMap((g) => g.map((r) => r.text))
}

/** The scriptArgPrograms programs per group: a group is a run of quoted pieces glued together
 *  (`-c "a"'b'` is one program), each {text, ansi} where ansi is a `$'..'` piece. #779 round 6
 *  (Marveen comment 3846): the `&` of a redirect (`2>&1`, `&>f`, `>&2`, `<&0`) and `>|` do not
 *  separate, a backslash-newline continues the line; the interpreter name may be quoted
 *  (`'python3' -c`), so may the flag (`"-c"`); the program may stick to the flag (`-c'..'`,
 *  `-Ic'..'`, `perl -e'..'`) and may be `$'..'`; `--print` is the long `-p`. #779 round 7
 *  (Marveen comment 3861): a line continuation (`\\<NL>`) may stand ANYWHERE (between flag and
 *  program, glued to a word: `-I\\<NL> -c`), bash removes it and the word joins; the
 *  interpreter's path may be quoted (`"/usr/bin/python3" -c`), the flag partly (`-"c"`), the whole
 *  flag=program word too (`"--eval=..."`, `"-c..."`), and `$"..."` is a program. The twin of
 *  _script_arg_groups. */
function scriptArgGroups(text) {
  const state = quoteMap(text)
  const out = []
  const n = text.length
  const sep = (i) => {
    const c = text[i]
    if (state[i] || !';&|\n'.includes(c)) return false
    if (c === '&' && ((i && '<>'.includes(text[i - 1])) || text[i + 1] === '>')) return false
    if (c === '|' && i && text[i - 1] === '>') return false
    return true
  }
  const cont = (i) => !state[i] && text[i] === '\\' && text[i + 1] === '\n'
  const skip = (p) => {
    while (p < n && (text[p] === ' ' || text[p] === '\t' || cont(p))) p += cont(p) ? 2 : 1
    return p
  }
  const startsProgram = (p) => p < n && (text[p] === '"' || text[p] === "'" || (text[p] === '$' && (text[p + 1] === "'" || text[p + 1] === '"')))
  const program = (p, first = []) => {
    const group = [...first]
    while (p < n) {
      const ansi = text[p] === '$' && text[p + 1] === "'"
      if (text[p] === '$' && (text[p + 1] === "'" || text[p + 1] === '"')) p++
      if (text[p] !== '"' && text[p] !== "'") break
      let j = p + 1
      while (j < n && state[j]) j++
      group.push({ text: text.slice(p + 1, j), ansi })
      p = j + 1
    }
    if (group.length) out.push(group)
  }
  // #779 round 8 (Marveen comment 3865 A, B): a path piece is Unicode like the Python twin's \w
  // and may hold `$`, `{}`, `@`, `+`, `~` (`"$HOME/venv/bin/python3"`, `"/home/gábor/.."`).
  for (const m of text.matchAll(/(?<![\p{L}\p{N}_-])(['"]?)(?:[\p{L}\p{N}_.@$\{\}+~-]*\/)*(python(?:\d+(?:\.\d+)*[a-z]?)?|bash|sh|zsh|nodejs|node\d*|perl(?:\d+(?:\.\d+)*)?|ruby(?:\d+(?:\.\d+)*)?|php(?:\d+(?:\.\d+)*)?)\1(?=[ \t]|\\\n)/gu)) {
    let k = m.index + m[0].length
    for (;;) {
      k = skip(k)
      if (k >= n || sep(k)) break
      let j = k
      while (j < n) {
        if (cont(j)) j += 2
        else if (state[j] || (!' \t\n'.includes(text[j]) && !sep(j))) j++
        else break
      }
      const w = text.slice(k, j)
      const flat = w.replaceAll('\\\n', '')
      const unq = flat.replace(/['"]/g, '')
      const bare = /^[\w'"-]+$/.test(flat) && /^-[\w-]+$/.test(unq) ? unq : flat
      if (SCRIPT_FLAGS.has(bare) || /^-[A-Za-z]*[ceE]$/.test(bare)) {
        const p = skip(j)
        if (startsProgram(p)) program(p)
      } else {
        // round 8: the flag name alone quoted (`"--eval"="..."`), the quote after the dashes
        // (`--"eval=..."`, `-"c..."`)
        const eq = /^(?:(['"])(?:--(?:eval|command|print)=|-[A-Za-z]*[ceE])\1|(['"])--(?:eval|command|print)\2=|--(?:eval|command|print)=|-[A-Za-z]*[ceE])(?:\\\n)*(?=['"]|\$['"])/.exec(w)
        const whole = /^(?:(['"])(?:--(?:eval|command|print)=|-[A-Za-z]*[ceE])|--(['"])(?:eval|command|print)=|-(['"])[A-Za-z]*[ceE])(?=[^'"])/.exec(w)
        if (eq) program(k + eq[0].length)
        else if (whole) {
          const a = k + whole[0].length
          let b = a
          while (b < n && state[b]) b++
          program(b + 1, [{ text: text.slice(a, b), ansi: false }])
        }
      }
      k = j
    }
  }
  return out
}

/** #779 round 6: the escapes printf, echo -e and $'..' turn into characters: \n \t \r, \xHH,
 *  and octal \NNN / \0NNN. The twin of destructive-gate.py _c_unescape. */
function cUnescape(s) {
  return s.replace(/\\(x[0-9A-Fa-f]{1,2}|0?[0-7]{1,3}|[ntr])/g, (all, e) => {
    if (e === 'n') return '\n'
    if (e === 't') return '\t'
    if (e === 'r') return '\r'
    return String.fromCharCode(e[0] === 'x' ? parseInt(e.slice(1), 16) : parseInt(e, 8))
  })
}
const SCRIPT_FLAGS = new Set(['-c', '-e', '-E', '-p', '--command', '--eval', '--print'])

/** `echo "<code>" | python3 -`: the quoted runs of the stage before an interpreter that runs its
 *  stdin as the program; the twin of _stdin_pipe_ranges. #779 round 4: no bare `-` needed
 *  (`| python3`, `| python3 -I`, `| node`); the decision is runsStdinAsProgram. */
function stdinPipePrograms(text) {
  const state = quoteMap(text)
  const bars = []
  for (let i = 0; i < text.length; i++) {
    if (!state[i] && text[i] === '|' && !text.startsWith('||', i) && !(i && text[i - 1] === '|')) bars.push(i)
  }
  if (!bars.length) return []
  const bounds = [0, ...bars.map((b) => b + 1), text.length]
  const parts = []
  for (let k = 0; k < bounds.length - 1; k++) parts.push([bounds[k], bounds[k + 1] - (k + 1 <= bars.length ? 1 : 0)])
  const out = []
  parts.forEach(([a, b], k) => {
    const toks = segWords(text.slice(a, b))
    if (!toks.length || k === 0 || !INTERPRETERS.has(baseName(runsStdinAsProgram(text.slice(a, b))))) return
    const [pa, pb] = parts[k - 1]
    const prev = quoteMap(text.slice(pa, pb))
    for (let i = 0; i < pb - pa;) {
      if (prev[i]) {
        let j = i
        while (j < pb - pa && prev[j] === prev[i]) j++
        out.push(text.slice(pa + i, pa + j))
        i = j
      } else i++
    }
  })
  return out
}

/** Every code payload of `cmd`: the run heredoc bodies (and, on an uncertain read, the others
 *  left in the light text), the -c/-e scripts, `| python3 -` input and the run here-strings. */
function interpreterPayloads(cmd) {
  const s = String(cmd ?? '')
  const light = lightText(s)
  const out = []
  if (s.includes('<<')) {
    const { heredocs, certain } = findHeredocs(s)
    const funcs = heredocs.length ? functionBodies(s) : {}
    for (const h of heredocs) {
      const body = s.slice(h.bodyStart, h.bodyEnd)
      const plain = !certain || (!h.quoted && /\$\(|`/.test(body))
      if (heredocRunsBody(s, h, funcs) || (!certain && plain)) out.push(light.slice(h.bodyStart, h.bodyEnd))
    }
    if (s.includes('<<<')) {
      for (const h of findAll(s).records) if (h.kind === 'herestring' && h.text.trim() && heredocRunsBody(s, h, funcs)) out.push(h.text)
    }
  }
  for (const group of scriptArgGroups(light)) {
    out.push(...group.map((r) => r.text))
    // #779 round 6: `-c "a"'b'` is one program, `$'..'` decodes its escapes
    if (group.length > 1 || group.some((r) => r.ansi)) out.push(group.map((r) => (r.ansi ? cUnescape(r.text) : r.text)).join(''))
  }
  for (const p of stdinPipePrograms(light)) {
    out.push(p)
    // #779 round 5/6: `printf 'import os\nos.system(..)' | python3` (also \x0a, \012)
    const dec = cUnescape(p)
    if (dec !== p) out.push(dec)
  }
  return out.filter((p) => p.trim())
}

/** Every shell command a code payload of `cmd` hands to a shell (#779). The gates judge each
 *  as a command line of its own, like a -c program. */
export function embeddedCommands(cmd) {
  const out = []
  for (const payload of interpreterPayloads(cmd)) {
    for (const inner of shellOutCommands(payload)) if (!out.includes(inner)) out.push(inner)
  }
  return out
}
