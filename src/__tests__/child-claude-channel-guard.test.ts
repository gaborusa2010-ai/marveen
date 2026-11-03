// CHILDCLAUDEPOLLER1010 -- the guard that stops a session from starting a child
// `claude` that would kill its own Telegram poller.
//
// The failure it prevents was measured 2026-10-10: three `claude -p` calls from
// a live sub-agent's Bash each loaded the Telegram plugin, whose bot.pid logic
// SIGTERMed the parent's live poller ("replacing stale poller pid=<N>"). The
// parent's channel stayed dead until the session restarted, and nothing in the
// command's own output said so. See docs/mcp-list-channel-plugin.md.
//
// Driven two ways, like the sibling egress tests: the exported decision
// function, and the real hook process fed a PreToolUse payload on stdin. The
// state dirs are real directories, because the guard reads the token file.
import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
// @ts-expect-error -- plain .mjs hook script, no types
import { decide, inspectSegment, sessionOwnsChannel, stripInertText } from '../../scripts/hooks/child-claude-channel-guard.mjs'

const __dirname = dirname(fileURLToPath(import.meta.url))
const HOOK = join(__dirname, '..', '..', 'scripts', 'hooks', 'child-claude-channel-guard.mjs')
const SCAFFOLD = readFileSync(join(__dirname, '..', 'web', 'agent-scaffold.ts'), 'utf-8')
const WEB = readFileSync(join(__dirname, '..', 'web.ts'), 'utf-8')

let root: string
let tg: string // this session's state dir, holds a token
let bare: string // a sub-agent state dir without a token (the launcher's fence)
let empty: string // an empty scratch dir
let home: string // a HOME whose ~/.claude/channels/telegram holds the main bot's token
let cfg: string // this session's config dir
let pluginCfg: string // another config dir that enables the telegram plugin
let CHANNEL_ENV: Record<string, string>

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'child-claude-guard-'))
  const mk = (...p: string[]) => { const d = join(root, ...p); mkdirSync(d, { recursive: true }); return d }
  tg = mk('agent', 'channels', 'telegram')
  writeFileSync(join(tg, '.env'), 'TELEGRAM_BOT_TOKEN=123:abc\n')
  bare = mk('bare', 'channels', 'telegram')
  empty = mk('empty')
  home = mk('home')
  writeFileSync(join(mk('home', '.claude', 'channels', 'telegram'), '.env'), 'TELEGRAM_BOT_TOKEN=999:main\n')
  cfg = mk('agent', 'cfg')
  pluginCfg = mk('other-cfg')
  writeFileSync(join(pluginCfg, 'settings.json'), JSON.stringify({ enabledPlugins: { 'telegram@claude-plugins-official': true } }))
  // The sub-agent launcher exports all three state variables at once.
  CHANNEL_ENV = {
    HOME: home,
    CLAUDE_CONFIG_DIR: cfg,
    TELEGRAM_STATE_DIR: tg,
    DISCORD_STATE_DIR: join(root, 'agent', 'channels', 'discord'),
    SLACK_STATE_DIR: join(root, 'agent', 'channels', 'slack'),
  }
})

afterAll(() => { rmSync(root, { recursive: true, force: true }) })

describe('child-claude guard: when the session is at risk at all', () => {
  it('acts when the session state dir holds a bot token', () => {
    expect(sessionOwnsChannel(CHANNEL_ENV)).toBe(true)
  })

  // Review point 1 (#1852): the launcher exports TELEGRAM_STATE_DIR to every
  // sub-agent; without a token the plugin exits before the bot.pid kill.
  it('stays out of a sub-agent whose state dir has no token', () => {
    const env = { ...CHANNEL_ENV, TELEGRAM_STATE_DIR: bare }
    expect(sessionOwnsChannel(env)).toBe(false)
    expect(decide('claude -p x', env).allow).toBe(true)
  })

  it('acts on a token in the real environment even with a bare state dir', () => {
    expect(sessionOwnsChannel({ ...CHANNEL_ENV, TELEGRAM_STATE_DIR: bare, TELEGRAM_BOT_TOKEN: '1:x' })).toBe(true)
  })

  it('without TELEGRAM_STATE_DIR, checks the fallback dirs the plugin would use', () => {
    expect(sessionOwnsChannel({ HOME: home })).toBe(true) // ~/.claude/channels/telegram (0.0.6)
    expect(sessionOwnsChannel({ HOME: empty, CLAUDE_CONFIG_DIR: join(root, 'agent') })).toBe(true) // $CLAUDE_CONFIG_DIR/channels/telegram (0.0.7)
    expect(sessionOwnsChannel({ HOME: empty })).toBe(false)
  })

  // Review point 5: discord 0.0.4 and slack-channel 0.1.0 have no bot.pid kill.
  it('does not act on DISCORD_ or SLACK_STATE_DIR alone', () => {
    expect(sessionOwnsChannel({ HOME: empty, DISCORD_STATE_DIR: tg, SLACK_STATE_DIR: tg })).toBe(false)
  })
})

describe('child-claude guard: what it blocks', () => {
  it.each([
    ['claude -p "hi"'],
    ['claude mcp list'],
    ['cd /work && claude -p "ok"'],
    ['/Users/someone/.local/bin/claude -p x'],
    ['nohup claude -p x &'],
    ['echo start; claude --model haiku -p x'],
  ])('denies %s with the inherited environment', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(false)
  })

  // Review point 3: unsetting the state variable points the child at the main
  // agent's state dir (~/.claude/channels/telegram), it does not isolate.
  it('denies unsetting TELEGRAM_STATE_DIR with the inherited config dir', () => {
    expect(decide('env -u TELEGRAM_STATE_DIR claude -p x', CHANNEL_ENV).allow).toBe(false)
  })

  // Review point 4: an empty CLAUDE_CONFIG_DIR is not a config dir of its own.
  it.each([
    ['CLAUDE_CONFIG_DIR= claude -p x'],
    ['CLAUDE_CONFIG_DIR="" claude -p x'],
    ['env -u TELEGRAM_STATE_DIR CLAUDE_CONFIG_DIR= claude -p x'],
    ['env -u TELEGRAM_STATE_DIR CLAUDE_CONFIG_DIR="$UNSET_VAR_X" claude -p x'],
  ])('denies an empty config dir: %s', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(false)
  })

  it.each([
    ['config dir that resolves to this session\'s', 'CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" claude -p x'],
    ['~/.claude', 'CLAUDE_CONFIG_DIR=~/.claude claude -p x'],
    ['config dir that enables the telegram plugin', () => `CLAUDE_CONFIG_DIR=${pluginCfg} claude -p x`],
    ['state dir set to the session\'s own', () => `TELEGRAM_STATE_DIR=${tg} claude -p x`],
    ['token-less state dir but a token in the environment', () => `TELEGRAM_STATE_DIR=${empty} TELEGRAM_BOT_TOKEN=1:x claude -p x`],
    ['env -i with HOME only (falls back to the main bot dir)', 'env -i PATH=/usr/bin HOME=$HOME claude -p x'],
  ])('denies a %s', (_name, command) => {
    const c = typeof command === 'function' ? command() : command
    expect(decide(c, CHANNEL_ENV).allow).toBe(false)
  })

  // Optional point of the second review: unknown variables alone may expand to
  // nothing, like a bare `$X`.
  it.each([
    ['CLAUDE_CONFIG_DIR=$X$Y claude -p x'],
    ['CLAUDE_CONFIG_DIR="${X}${Y}" claude -p x'],
  ])('denies a config dir made only of unknown variables: %s', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(false)
  })
})

// Second review of #1852: the hazard is the child's, so a session without a
// token is not safe by itself. Its child can be pointed at a state dir that
// holds one -- on a default install the main bot's ~/.claude/channels/telegram.
describe('child-claude guard: a token-less session redirecting its child', () => {
  let TOKENLESS: Record<string, string>
  beforeAll(() => { TOKENLESS = { ...CHANNEL_ENV, TELEGRAM_STATE_DIR: bare } })

  it.each([
    ['unsets the state dir (falls back to the main bot dir)', 'env -u TELEGRAM_STATE_DIR claude -p x'],
    ['unsets it with -u<NAME>', 'env -uTELEGRAM_STATE_DIR claude -p x'],
    ['empties the environment (falls back to the main bot dir)', () => `env -i PATH=/usr/bin HOME=${home} claude -p x`],
    ['points the state dir at one with a token', () => `TELEGRAM_STATE_DIR=${tg} claude -p x`],
    ['points the state dir at an unresolvable value', 'TELEGRAM_STATE_DIR=$UNSET_VAR_X claude -p x'],
  ])('denies a child that %s', (_name, command) => {
    const c = typeof command === 'function' ? command() : command
    expect(sessionOwnsChannel(TOKENLESS)).toBe(false)
    expect(decide(c, TOKENLESS).allow).toBe(false)
  })

  it('allows the same redirect when the fallback holds no token', () => {
    expect(decide('env -u TELEGRAM_STATE_DIR claude -p x', { ...TOKENLESS, HOME: empty }).allow).toBe(true)
  })

  it('allows a redirected child that has a config dir of its own', () => {
    expect(decide(`env -u TELEGRAM_STATE_DIR CLAUDE_CONFIG_DIR=${empty} claude -p x`, TOKENLESS).allow).toBe(true)
  })
})

describe('child-claude guard: what it leaves alone', () => {
  // Review point 2: an own, non-empty config dir enables no channel plugin.
  // Measured 2026-10-10: a fresh config dir started no telegram server, even
  // from a cwd whose project settings enable the plugin.
  it.each([
    ['fresh literal config dir', () => `CLAUDE_CONFIG_DIR=${empty} claude -p x`],
    ['config dir from a shell variable', 'CLAUDE_CONFIG_DIR=$S/cfg claude -p x'],
    ['quoted config dir from a shell variable', 'CLAUDE_CONFIG_DIR="$S/cfg" claude --model haiku -p "say ok"'],
    ['single-quoted literal config dir', () => `CLAUDE_CONFIG_DIR='${empty}' claude -p x`],
    ['own config dir via env', () => `env CLAUDE_CONFIG_DIR=${empty} claude plugin validate .`],
  ])('allows a %s', (_name, command) => {
    const c = typeof command === 'function' ? command() : command
    expect(decide(c, CHANNEL_ENV).allow).toBe(true)
  })

  // The plugin exits on the missing token before it reaches bot.pid.
  // Measured 2026-10-10 with a plugin-enabled config dir: "TELEGRAM_BOT_TOKEN
  // required", exit, and the parent's poller kept running.
  it('allows a child pointed at a token-less state dir', () => {
    expect(decide(`TELEGRAM_STATE_DIR=${empty} claude -p x`, CHANNEL_ENV).allow).toBe(true)
    expect(decide(`env -i PATH=/usr/bin HOME=${home} TELEGRAM_STATE_DIR=${empty} claude -p x`, CHANNEL_ENV).allow).toBe(true)
  })

  it.each([
    ['claude --version'],
    ['claude --help'],
    ['claude install latest'],
    ['claude plugin install --help'], // review point 2: --help anywhere
    ['claude mcp add myserver -- npx srv'], // writes config, starts no server
    ['claude mcp remove myserver'],
    ['grep claude -p notes.txt'], // `claude` is an argument, not the command
    ['echo "claude -p"'],
    ['ls ~/.claude/skills'],
    ['cat claude-code-install-watch.md'],
  ])('allows %s', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(true)
  })

  // Measured 2026-10-10 on the first live version: a python heredoc that only
  // DOCUMENTED the guard was blocked, because a backtick inside the heredoc text
  // started a new segment. Inert text must not count as a command.
  it.each([
    ["python3 - <<'PY'\nprint('a plain child `claude` does')\nPY\n"],
    ['cat > note.md <<EOF\nclaude -p hi\nEOF'],
    ['git commit -m "docs: never run claude -p from a channel session"'],
    ["echo 'x; claude -p'"],
  ])('allows inert text that only mentions claude: %s', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(true)
  })

  it('still sees a real invocation after a heredoc ends', () => {
    expect(decide('cat > f <<EOF\nhi\nEOF\nclaude -p x', CHANNEL_ENV).allow).toBe(false)
  })

  it('keeps quoted text one token that splits nothing', () => {
    expect(stripInertText('echo "a; b"').split(/[;]/)).toHaveLength(1)
  })

  it('reads the environment changes of the segment, in order', () => {
    const hit = inspectSegment(' env -u TELEGRAM_STATE_DIR CLAUDE_CONFIG_DIR=/e claude -p x')
    expect(hit.ops).toEqual([['unset', 'TELEGRAM_STATE_DIR'], ['set', 'CLAUDE_CONFIG_DIR', '/e']])
    expect(inspectSegment('git status')).toBeNull()
  })
})

// #729: heredocs are read by lib/heredoc-scan.mjs. Every must-block row was
// checked against bash itself (a fake `claude` on PATH ran); every allow row
// did not run it. The one deliberate exception is the unclosed heredoc: bash
// treats the rest as body, the guard cannot be certain and keeps it visible.
describe('child-claude guard: heredoc reading (#729)', () => {
  it.each([
    ['echo hi # <<EOF\nclaude -p x\nEOF'],
    ['echo "<<EOF"\nclaude -p x'],
    ["echo '<<EOF'\nclaude -p x"],
    ['cat > f <<E\\OF\njunk\nEOF\nclaude -p x'],
    ['cat > f <<E"O"F\njunk\nEOF\nclaude -p x'],
    ['cat > f <<\\EOF\njunk\nEOF\nclaude -p x'],
    ["cat > f <<'END-X'\njunk\nEND-X\nclaude -p x"],
    ['cat > f <<-EOF\n\tjunk\n\tEOF\nclaude -p x'],
    ['cat > f <<EOF; claude -p x\njunk\nEOF'],
    ['cat > a <<A; cat > b <<B\none\nA\ntwo\nB\nclaude -p x'],
    ['bash <<EOF\nclaude -p x\nEOF'],
    ["bash -s <<'SH'\nclaude -p x\nSH"],
    ['sh - <<SH\nclaude -p x\nSH'],
    ['bash -o pipefail <<SH\nclaude -p x\nSH'],
    ['timeout 30 bash <<SH\nclaude -p x\nSH'],
    ['X=1 bash -e <<SH\necho a\nclaude -p x\nSH'],
    ['bash > log <<SH\nclaude -p x\nSH'],
    ['cat <<SH | bash\nclaude -p x\nSH'],
    ["cat <<'SH' | sh -s\nclaude -p x\nSH"],
    ['cat > f <<EOF\n$(claude -p x)\nEOF'],
    ['cat > f <<EOF\nresult: `claude -p x`\nEOF'],
    ["cat > f <<EOF\ndon't $(claude -p x)\nEOF\necho 'x'"],
    ['cat > f <<EOF\nunclosed body\nclaude -p x'],
  ])('blocks %j', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(false)
  })

  it.each([
    ["cat > f <<'EOF'\n$(claude -p x)\nEOF"],
    ['cat > f <<\\EOF\n`claude -p x`\nEOF'],
    ["cat > f <<'END-X'\nclaude -p x\nEND-X"],
    ['cat > f <<E\\OF\nclaude -p x\nEOF'],
    ["bash scripts/agent-msg.sh cody marveen - <<'EOF'\nnever run claude -p here\nEOF"],
    ["bash -c 'echo hi' <<EOF\nclaude -p x\nEOF"],
    ['python3 - <<PY\n# claude -p x\nPY'],
    ["cat > f <<EOF\ndon't run claude -p\nEOF"],
    ["cat <<'EOF' | tee f\nclaude -p x\nEOF"],
  ])('allows %j', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(true)
  })
})

// #748 (the #729 review's remainder, Marveen comment 3284): the guard's own
// bodyIsProgram missed the shapes below, and the base let every one of them
// through while bash ran the body. The reading now comes from the shared twin
// lib/heredoc-program.mjs (case file scripts/__tests__/heredoc-program-cases.json,
// every must-run row checked against bash). The allow rows are the same shapes
// with a data body: the base let them through too, and so must this.
describe('child-claude guard: a heredoc body the shell RUNS (#748)', () => {
  it.each([
    ['bash 0<<SH\nclaude -p x\nSH'],
    ['bash 2>&1 <<SH\nclaude -p x\nSH'],
    ['bash &>/dev/null <<SH\nclaude -p x\nSH'],
    ['bash /dev/fd/3 3<<SH\nclaude -p x\nSH'],
    ['eval "$(cat <<EOF\nclaude -p x\nEOF\n)"'],
    ['builtin eval "$(cat <<EOF\nclaude -p x\nEOF\n)"'],
    ['eval "`cat <<EOF\nclaude -p x\nEOF\n`"'],
    ['. <(cat <<SH\nclaude -p x\nSH\n)'],
    ['source <(cat <<SH\nclaude -p x\nSH\n)'],
    ['bash <(cat <<SH\nclaude -p x\nSH\n)'],
    ['while read l; do $l; done <<EOF\nclaude -p x\nEOF'],
    ['while read l; do eval "$l"; done <<EOF\nclaude -p x\nEOF'],
    ['while read l\ndo\n  $l\ndone <<EOF\nclaude -p x\nEOF'],
    ['f(){ bash; }; f <<SH\nclaude -p x\nSH'],
    ['function g { sh; }; g <<SH\nclaude -p x\nSH'],
    ['h() ( bash ); h <<SH\nclaude -p x\nSH'],
    ['f(){ bash; }; cat <<SH | f\nclaude -p x\nSH'],
    ['bash \\\n<<SH\nclaude -p x\nSH'],
    ['`bash <<SH\nclaude -p x\nSH\n`'],
    ['sudo -u x bash <<SH\nclaude -p x\nSH'],
    ['if true; then bash <<SH\nclaude -p x\nSH\nfi'],
    ['if true; then bash; fi <<SH\nclaude -p x\nSH'],
    ['{ bash; } <<SH\nclaude -p x\nSH'],
    ['bash -s -- a <<SH\nclaude -p x\nSH'],
    ['! bash <<SH\nclaude -p x\nSH'],
    ['bash -c "$(cat <<EOF\nclaude -p x\nEOF\n)"'],
    ['x=$(bash <<SH\nclaude -p x\nSH\n)'],
    // review round 2 (Marveen, card comment 3543): stdin by path, subshell, here-string,
    // process substitution, wrapper options
    ['bash /dev/stdin <<SH\nclaude -p x\nSH'],
    ['bash /dev/fd/0 <<SH\nclaude -p x\nSH'],
    ['bash /proc/self/fd/0 <<SH\nclaude -p x\nSH'],
    ['bash 3<<SH <&3\nclaude -p x\nSH'],
    ['(bash) <<SH\nclaude -p x\nSH'],
    ['( cd /tmp && bash ) <<SH\nclaude -p x\nSH'],
    ['(cat <<SH | bash)\nclaude -p x\nSH'],
    ['bash <<<"$(cat <<SH\nclaude -p x\nSH\n)"'],
    ['tee >(bash) <<SH >/dev/null\nclaude -p x\nSH'],
    ['timeout -k 1 5s bash <<SH\nclaude -p x\nSH'],
    ['sudo -u x -- bash <<SH\nclaude -p x\nSH'],
    ['sudo -s <<SH\nclaude -p x\nSH'],
    ['env -i bash <<SH\nclaude -p x\nSH'],
    ['env -u HOME bash <<SH\nclaude -p x\nSH'],
    ["env -S'bash -' <<SH\nclaude -p x\nSH"],
    ['command -p bash <<SH\nclaude -p x\nSH'],
    ['exec -a name bash <<SH\nclaude -p x\nSH'],
    ['ssh host bash <<SH\nclaude -p x\nSH'],
    ['docker exec -i c bash <<SH\nclaude -p x\nSH'],
  ])('blocks %j', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(false)
  })

  it.each([
    ['bash 0<<A 3<<B\necho a\nA\nclaude -p x\nB'],
    ['cat 3<<B | bash\nclaude -p x\nB'],
    ['y=$(cat <<EOF\nclaude -p x\nEOF\n)'],
    ['echo "$(cat <<EOF\nclaude -p x\nEOF\n)"'],
    ['bash "$(cat <<EOF\nclaude -p x\nEOF\n)"'],
    ['diff <(cat <<A\nclaude -p x\nA\n) <(cat <<B\nclaude -p x\nB\n)'],
    ['( cat > f <<EOF\nclaude -p x\nEOF\n)'],
    ['while read l; do echo "$l"; done <<EOF\nclaude -p x\nEOF'],
    ['f(){ cat > f; }; f <<EOF\nclaude -p x\nEOF'],
    ["bash -ec 'echo hi' <<EOF\nclaude -p x\nEOF"],
    ['python3 -m json.tool <<EOF\n{"a": "claude -p x"}\nEOF'],
    ['sudo -u x cat > f <<EOF\nclaude -p x\nEOF'],
    // review round 2, point 5: an interpreter name as an ARGUMENT is not a program.
    // Round 1 blocked these two (regression against main); the body is data.
    ["grep -c python <<'EOF'\nclaude -p hi\nEOF"],
    ['git commit -m "node fix" -F - <<\'EOF\'\nclaude -p hi\nEOF'],
    ['cat 3<<EOF <&3\nclaude -p x\nEOF'],
    ['cat /dev/fd/3 3<<SH\nclaude -p x\nSH'],
    ['(cat) <<EOF\nclaude -p x\nEOF'],
    ['echo <<<"$(cat <<EOF\nclaude -p x\nEOF\n)"'],
    ['tee >(cat >/dev/null) <<EOF >/dev/null\nclaude -p x\nEOF'],
    ["docker exec -i c bash -c 'echo hi' <<EOF\nclaude -p x\nEOF"],
    ["awk '{print}' <<EOF\nclaude -p x\nEOF"],
    ['sort -S 1M <<EOF\nclaude -p x\nEOF'],
    ['env -u HOME cat <<EOF\nclaude -p x\nEOF'],
    ['env -i <<EOF\nclaude -p x\nEOF'],
  ])('allows %j', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(true)
  })
})

describe('child-claude guard: the real hook process', () => {
  const run = (command: string, env: Record<string, string>) => spawnSync(process.execPath, [HOOK], {
    input: JSON.stringify({ tool_name: 'Bash', tool_input: { command } }),
    env: { PATH: process.env.PATH ?? '', ...env },
    encoding: 'utf8',
  })

  it('emits a deny decision with the reason and the safe pattern', () => {
    const r = run('claude -p "hi"', CHANNEL_ENV)
    expect(r.status).toBe(0)
    const reason = JSON.parse(r.stdout).hookSpecificOutput
    expect(reason.permissionDecision).toBe('deny')
    expect(reason.permissionDecisionReason).toMatch(/replacing stale poller/)
    expect(reason.permissionDecisionReason).toMatch(/TELEGRAM_STATE_DIR=<empty-dir> CLAUDE_CONFIG_DIR=<empty-dir>/)
    // Review point 3: the deny text must not recommend unsetting the variable.
    expect(reason.permissionDecisionReason).not.toMatch(/env -u TELEGRAM_STATE_DIR/)
    expect(reason.permissionDecisionReason).toMatch(/Do NOT unset TELEGRAM_STATE_DIR/)
  })

  it('stays silent (allow) for an isolated child', () => {
    const r = run(`TELEGRAM_STATE_DIR=${empty} CLAUDE_CONFIG_DIR=${empty} claude -p x`, CHANNEL_ENV)
    expect(r.status).toBe(0)
    expect(r.stdout).toBe('')
  })

  it('stays silent in a token-less sub-agent', () => {
    const r = run('claude -p x', { ...CHANNEL_ENV, TELEGRAM_STATE_DIR: bare })
    expect(r.status).toBe(0)
    expect(r.stdout).toBe('')
  })

  it('denies a token-less sub-agent unsetting its state dir', () => {
    const r = run('env -u TELEGRAM_STATE_DIR claude -p x', { ...CHANNEL_ENV, TELEGRAM_STATE_DIR: bare })
    expect(r.status).toBe(0)
    expect(JSON.parse(r.stdout).hookSpecificOutput.permissionDecision).toBe('deny')
  })

  it('fails open on a payload it cannot parse', () => {
    const r = spawnSync(process.execPath, [HOOK], { input: 'not json', env: CHANNEL_ENV, encoding: 'utf8' })
    expect(r.status).toBe(0)
    expect(r.stdout).toBe('')
  })
})

describe('child-claude guard: where it lands', () => {
  it('is injected on spawn for sub-agents', () => {
    expect(SCAFFOLD).toContain('if (agentGetsChildClaudeGuard(name)) injectChildClaudeGuard(existing)')
  })

  it('is backfilled into the existing fleet at startup', () => {
    expect(WEB).toContain('if (ensureChildClaudeGuard(agentName)) childGuardPatched.push(agentName)')
  })

  it('exempts the main agent, like the outgoing-copy gate', () => {
    const fn = SCAFFOLD.slice(SCAFFOLD.indexOf('export function agentGetsChildClaudeGuard('))
    expect(fn.slice(0, 200)).toMatch(/return name !== MAIN_AGENT_ID/)
  })
})

describe('#777: a here-string program and a literal `bash -c` program reach the child claude', () => {
  // bash runs every shape below (oracle: agents/cody/work/777/oracle-out.txt); on the base
  // (f2fba696) each one passed the guard.
  it.each([
    ["bash <<< 'claude -p x'"],
    ["bash <<< 'claude -p x' #comment"],
    ["bash 3<<< 'claude -p x' <&3"],
    ["bash -- /dev/stdin <<< 'claude -p x'"],
    ["sh -c 'cat | sh' <<< 'claude -p x'"],
    ["bash -c 'bash' <<EOF\nclaude -p x\nEOF"],
    ["bash -c 'bash -c \"source /dev/stdin\"' <<< 'claude -p x'"],
    ["bash -c 'bash -c \"$(cat)\"' <<< 'claude -p x'"],
    ["bash -xc 'claude -p x'"],
    ["bash -o pipefail -c 'claude -p x'"],
    ["nice bash -c 'claude -p x'"],
    ["bash -c $'cl\\141ude -p x'"],
    ["bash -c 'cl''aude -p x'"],
    ["bash -c 'bash -c \"bash -c \\\"claude -p x\\\"\"'"],
    // review 3: a here-string inside an executed heredoc body, inside backticks, `<&0` before -c
    ["bash <<'A'\nsh <<< $'claude -p x'\nA"],
    ["bash <<A\nbash <<< 'claude -p x'\nA"],
    ["echo `bash <<< 'claude -p x'`"],
    ["x=`bash <<< 'claude -p x'`"],
    ["bash <&0 -c $'cl\\x61ude -p x'"],
    ["bash <<< 'claude -p x"],
    // review 4: ANY non-code-interpreter consumer runs the body (eval, source, $SHELL, a
    // function, a brace group, the closing-line pipe); nested escaped backticks
    ["source /dev/stdin <<'A'\nbash <<< 'claude -p x'\nA"],
    ["eval \"$(cat <<'A'\nbash <<< 'claude -p x'\nA\n)\""],
    ["echo \"$(cat <<'A'\nbash <<< 'claude -p x'\nA\n)\" | bash"],
    ["$SHELL <<'A'\nbash <<< 'claude -p x'\nA"],
    ["bash \\\n<<'A'\nbash <<< 'claude -p x'\nA"],
    ["while read l; do eval \"$l\"; done <<'A'\nbash <<< 'claude -p x'\nA"],
    ["f(){ bash; }; f <<'A'\nbash <<< 'claude -p x'\nA"],
    ["{ bash; } <<'A'\nbash <<< 'claude -p x'\nA"],
    ["cat <<'A' | eval \"$(cat)\"\nbash <<< 'claude -p x'\nA"],
    ["echo `echo \\`bash <<< 'claude -p x'\\``"],
    ["echo `echo \"\\`bash <<< 'claude -p x'\\`\"`"],
    ["echo `echo \\`bash <<< 'claude -p x'\\` tail`"],
  ])('denies %s', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(false)
  })

  it.each([
    ["wc -l <<< 'claude -p x'"],
    ["grep -c claude <<< 'claude -p x'"],
    ["echo \"bash -c 'claude -p x'\""],
    ["bash -c'claude -p x'"],
    ["cat <<< 'claude -p x' > notes.txt"],
    ["cat <<'A'\nbash <<< 'claude -p x'\nA"],
    ["echo `grep -c claude <<< 'claude -p x'`"],
    ["python3 - <<'PY'\nx = \"bash <<< 'claude -p x'\"\nPY"],
    ["git commit -m \"$(cat <<'A'\nbash <<< 'claude -p x'\nA\n)\""],
    ["echo `echo hi` 'bash <<< claude -p x'"],
  ])('allows the data shape %s', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(true)
  })
})

describe('#777 review 12: gdb reading a heredoc/here-string is denied whole; quoted separators in the opener line', () => {
  // gdb's command language runs the stdin many ways (abbreviations, eval, python, interpreter-exec):
  // the guard denies any gdb that reads one, whatever the body. The destructive gate's twin:
  // scripts/__tests__/destructive-gate.test.py section 23. Quoted shapes: probe-sandbox bash runs each.
  it.each([
    ["gdb -batch -ex 'python import sys; exec(sys.stdin.read())' <<'A'\nimport os\nA"],
    ["gdb -batch -ex 'she bash' <<'A'\nls\nA"],
    ["gdb -batch -ex 'eval \"shell %s\", \"bash\"' <<'A'\nls\nA"],
    ["gdb -batch -ex 'interpreter-exec console \"shell bash\"' <<'A'\nls\nA"],
    ["gdb -batch -ex 'pi' <<'A'\nimport os\nA"],
    ["gdb -batch prog <<'A'\nbt\nA"],
    ["gdb-multiarch -batch <<'A'\nbt\nA"],
    ["gdb -batch -ex 'py exec(input())' <<< 'import os'"],
    ["bash <<'A'\ngdb -batch <<'B'\nbt\nB\nA"],
    ["env X='a;b' bash <<'A'\nclaude -p x\nA"],
    ["env X=a\\;b bash <<'A'\nclaude -p x\nA"],
    ["bash -s 'x(y' <<'A'\nclaude -p x\nA"],
    ["cat <<'A' - 'x;y' | bash\nclaude -p x\nA"],
    ["bash -c 'IFS= read -r l; bash -c \"$l\"' <<< 'claude -p x'"],
  ])('denies %s', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(false)
  })

  it.each([
    ['gdb -batch -ex bt ./prog ./core'],
    ["cat <<'A'\ngdb -batch <<'B'\nbt\nB\nA"],
    ["echo 'a;b' <<'A'\nclaude -p x\nA"],
    ["env X='a;b' cat <<'A'\nclaude -p x\nA"],
  ])('allows %s', (command) => {
    expect(decide(command, CHANNEL_ENV).allow).toBe(true)
  })
})
