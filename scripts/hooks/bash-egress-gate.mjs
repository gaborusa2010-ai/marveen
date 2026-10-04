#!/usr/bin/env node
// PreToolUse hook: egress allowlist enforcement for the Bash tool.
//
// BASHEGRESS901. The security review of 2026-08-31 measured the hole: the
// egress allowlist sat on exactly one tool, WebFetch. A `curl` or `wget` run
// through Bash never touched it, so for every agent with Bash the allowlist was
// advice, not a boundary. On the day it was found the protection held only
// because Roxy knew she could go around it and chose not to.
//
// This gate closes the obvious path. It is deliberately NOT a claim to have
// closed all of them -- see LIMITS below, and say so out loud rather than
// letting the list look like a wall.
//
// ONE allowlist, not two. The decision reuses egress-gate.mjs's built-in
// prefixes and store/egress-allowlist.json through a shared import. A second
// copy would drift, and a drifted allowlist is worse than none: it looks
// current.
//
// DECISION SHAPE (host-level, unlike the WebFetch gate's prefix-level):
//   - No network fetcher token in the command  -> allow. A URL inside an echo,
//     a comment or a grep pattern is not egress.
//   - Fetcher present, every URL literal resolves to an allowed host -> allow.
//   - Fetcher present, a host is not on the list -> BLOCK.
//   - Fetcher present, the host cannot be determined (no URL literal at all, or
//     a shell variable inside the host) -> BLOCK. This is the fail-closed case:
//     `curl "$URL"` is exactly how a bypass would be written, and a gate that
//     waves through what it cannot read is not a gate.
//
// Host-level for the BUILT-IN list and for `domains`, for one practical reason:
// the fleet's own calls are written `http://localhost:$PORT/api/...`, where the
// port is a shell variable. The host is what egress is about there; insisting on
// a full literal prefix would block the fleet's own dashboard traffic and the
// gate would be switched off within the day.
//
// A runtime `prefixes` entry is different and matches as a PREFIX (PREFIXNARROW901):
// when the operator approves a path, the path is the grant. Approving
// https://host/api/v1/me must not hand over the rest of the host.
//
// LIMITS, stated plainly:
//   - A command that runs a SCRIPT which fetches (`bash scripts/foo.sh`) shows
//     no fetcher token here and is allowed. Scripts in the repo are reviewed
//     code; this gate is about ad-hoc commands.
//   - `npm install`, `git clone`, `gh`, `pip` and friends carry no URL and are
//     not matched. Blocking package managers would stop ordinary development.
//   - A determined author can still obfuscate a URL past this. That is a real
//     limit of a text-matching gate, not an oversight.
//   The point is that a plain `curl https://evil.example/` now hits something.

import { readFileSync, appendFileSync, mkdirSync, realpathSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { loadRuntimeAllowlist, ALLOWED_HOSTS } from './egress-gate.mjs'
import { tokenize, dropFlagValues, readCommand, tokenizeDetailed, splitSegments, extractHeredocBodies, verbIndex } from './lib/shell-command.mjs'
import { prefixAllows } from './lib/url-prefix.mjs'
import { redactSecrets } from './lib/redact.mjs'
import { localStamp } from './lib/local-time.mjs'
import { sessionAgentName } from './lib/agent-id.mjs'
export { tokenize } from './lib/shell-command.mjs'

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const EGRESS_BLOCK_LOG = join(REPO_ROOT, 'store', 'egress-blocked.log')

// VERBSCOPE901: a fetcher is a COMMAND, not a word that appears somewhere.
// The fourth false positive of the morning was an inter-agent message whose
// TEXT explained this very gate -- it quoted `curl "$URL"` and the word wget,
// and the gate blocked the message. The command's verb was `bash`, running a
// script; nothing in it was going to fetch anything. So the decision now starts
// from the verb of each segment and only then looks at that verb's arguments.
// YTDLP901 (Roxy, 2026-09-01, measured): `yt-dlp` was missing, and it is the one
// downloader actually INSTALLED on this host beyond curl/wget. The same URL was
// refused with curl and allowed with yt-dlp -- to any host. Worse, a skill was
// built on that gap and explained it with a reason that had already stopped being
// true. A fetcher list is only as good as the binaries that are really present.
const FETCHER_CMDS = new Set(['curl', 'wget', 'lynx', 'w3m', 'aria2c', 'httpie', 'http',
  'yt-dlp', 'youtube-dl'])

// Interpreters that fetch when handed a program inline. `bash script.sh` is NOT
// one of these -- a script's contents are reviewed code and a stated gap.
// FFMPEG901 (Roxy, 2026-09-01, measured -- and she found it in her OWN daily tool).
// ffmpeg speaks http/https/ftp/rtmp/tcp on BOTH sides: it downloads, and it can
// send a LOCAL FILE to a remote URL. yt-dlp only pulled in; this pushes out.
//
// It cannot join FETCHER_CMDS, though: that set means "this verb IS a fetch", so
// a fetch with no readable URL fails closed -- and that would refuse every local
// `ffmpeg -i in.mov out.mp4`, which is Roxy's ordinary work. These verbs count as
// a fetch only when a URL is actually visible among the arguments.
// STATED COST: a URL built into a shell variable is therefore NOT caught here,
// where `curl "$U"` still fails closed. Refusing all local video work would be
// the bigger harm, and a gate that blocks the day job gets switched off.
// Cody named the shape exactly (2026-09-01), and his point was NOT that failing
// open here is wrong -- failing closed would stop Roxy's daily work, the worse
// trade. His point is that this is the SAME decision as the
// no-url-literal branch below, taken the OPPOSITE way. There, a fetcher with no
// readable URL is refused; here it is allowed. If this ever proves too loose, that
// one asymmetry is the whole of it -- revisit it deliberately, not by accident.
const URL_ONLY_FETCHERS = new Set(['ffmpeg', 'ffplay', 'ffprobe'])

// ffmpeg does not only speak http(s). Its protocol list covers ftp, rtmp, rtsp,
// srt, tcp and udp, and the MUXER side speaks them too -- which is how a local
// file leaves the machine. Matching only http/https here would have caught the
// download and waved the upload through; measured, `-f data rtmp://host/live`
// did exactly that.
const NET_URL_RE = /^(https?|ftps?|rtmps?|rtsp|srt|tcp|udp|ws|wss):\/\/[^\s'"`]+$/i

const INTERPRETERS = new Set(['python', 'python3', 'node', 'ruby', 'perl', 'sh', 'bash', 'zsh'])
// The subset whose inline program IS a shell command line.
const SHELLS = new Set(['sh', 'bash', 'zsh'])
const INLINE_CODE_FLAGS = new Set(['-c', '-e'])

// Wrappers to step over when looking for the real verb.
const WRAPPERS = new Set(['sudo', 'command', 'nohup', 'nice', 'stdbuf', 'env', 'time',
  // WRAPFLAG901 (found by reviewing my own change, 2026-09-01): `xargs` was not
  // here at all, so `echo x | xargs -I{} curl <url>` read its verb as `xargs`.
  'xargs', 'setsid', 'ionice', 'taskset'])

// URL literals. Deliberately greedy about what counts as a URL character so a
// variable inside the host is CAPTURED (and then rejected) rather than silently
// truncated into a clean-looking hostname.
// QUERYAMP901 (Roxy, 2026-09-01): this used to exclude `&`, `;`, `|` and `>`.
// On the RAW command line that is right -- there they end the URL. But this
// matches a TOKEN that has already been through the quote-aware reader, where
// those characters can only have come from inside quotes, i.e. they are part of
// the URL. Excluding them meant every two-parameter GET failed to look like a
// URL at all, so `targets` came out empty and the fail-closed branch refused
// the call -- INCLUDING the fleet's own documented memory search against
// localhost. A gate that blocks the command written in our own CLAUDE.md is a
// gate that gets switched off. The strict form still guards raw program text.
const URL_WHOLE_RE = /^https?:\/\/[^\s'"`]+$/i
const URL_ANYWHERE_RE = /\bhttps?:\/\/[^\s'"`)\;|&>]+/gi

// Fetchers that live inside PROGRAM TEXT rather than as a command argument.
//
// NODEHTTP901 (Cody, 2026-09-01, measured AFTER the heredoc fix): reading the
// program was only half the job -- the gate then has to RECOGNISE the fetch.
// `node -e "require('https').get('<url>', ...)"` still went out with a 200 and no
// log line, because the list knew `fetch(` and `node-fetch` but not node's own
// http/https modules. The lesson is the shape of the whole day: fixing the path
// to the evidence does not help if the evidence is not on the list.
// The list stays anchored -- a bare `get(` would match every Express route.
const CODE_FETCHER_RE = /urlopen|urlretrieve|requests\.(get|post|put|patch|delete|head|Session)|http\.client|node-fetch|\bfetch\s*\(|require\(\s*['"](?:node:)?https?['"]\s*\)|\bhttps?\.(get|request)\s*\(|\baxios\b|\bhttpx\.|\baiohttp\b|\burllib3\b/

// Flags whose NEXT token is a value, not a destination. Their values are
// dropped before anything is scanned.
//
// BASHEGRESS901-FP: the very first live block this gate produced was a FALSE
// POSITIVE, on the fleet's own dashboard POST -- a legitimate call to localhost
// whose JSON BODY quoted an example URL and the words "curl" and "urlopen".
// Scanning raw command text cannot tell a fetch DESTINATION from data being
// SENT, and a gate that blocks writing about egress is a gate that gets
// switched off the same day. So the command is tokenised and these values are
// removed before fetcher detection or URL extraction runs.
/**
 * The command's tokens with every value-flag argument removed: what remains is
 * what can actually name a destination.
 *
 * The reading itself lives in lib/shell-command.mjs, shared with the skill gate,
 * which made the identical "bag of words" mistake an hour after this one. That
 * module also strips here-document bodies -- the third false positive of the
 * day was a patch script whose heredoc quoted a URL in a comment.
 */
export function targetTokens(cmd) {
  return dropFlagValues(tokenize(cmd))
}

/** Hosts that are always fine: the machine talking to itself. */
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '0.0.0.0'])

/**
 * Extract the host portion of a URL literal WITHOUT URL parsing, because the
 * literal may still contain shell syntax that `new URL` would either reject or,
 * worse, quietly accept.
 * Returns { host, unresolved } -- unresolved when shell expansion could change
 * the host.
 */
export function hostOf(urlLiteral) {
  const afterScheme = String(urlLiteral).replace(/^https?:\/\//i, '')
  // Strip userinfo: everything before the LAST '@' in the authority belongs to
  // credentials, and `http://allowed.example@evil.example/` is the classic way
  // to make a denied host read as an allowed one.
  const authority = afterScheme.split(/[/?#]/)[0]
  const at = authority.lastIndexOf('@')
  const hostPort = at >= 0 ? authority.slice(at + 1) : authority
  const host = hostPort.replace(/:\d*$/, '').replace(/:.*$/, '').toLowerCase()
  // A shell expansion in the HOST means the real destination is unknown. In the
  // port or in credentials it does not: `http://localhost:$PORT/` is how the
  // fleet's own dashboard calls are written, and the host there is fully known.
  const unresolved = /[$`*?]/.test(host) || host === ''
  return { host, unresolved }
}

/** Does `host` match an allowed entry, exactly or as a subdomain? */
function hostAllowed(host, allowedHosts) {
  if (LOCAL_HOSTS.has(host)) return true
  return allowedHosts.some((d) => host === d || host.endsWith('.' + d))
}

/**
 * Pure decision, no I/O, so it is testable without a filesystem or a network.
 * `runtimeList` is the decoded store/egress-allowlist.json.
 */
export function bashEgressDecision(command, runtimeList = { domains: [], prefixes: [], quarantineDomains: [] }, depth = 0) {
  const cmd = String(command ?? '')
  if (!cmd) return { blocked: false, tier: 'empty' }

  const allowedHosts = [...ALLOWED_HOSTS, ...(runtimeList.domains ?? [])]
  // PREFIXNARROW901: a runtime PREFIX used to be widened into its HOST here, so
  // approving https://host/api/v1/me quietly opened the whole host to Bash. The
  // egress procedure says the opposite -- narrow the grant to what is actually
  // needed -- and the operator who writes a path plainly meant the path. A
  // prefix now matches as a prefix; only a `domains` entry opens a host.
  const allowedPrefixes = (runtimeList.prefixes ?? []).filter((p) => typeof p === 'string' && p)

  let sawFetcher = false
  const targets = []

  // HEREDOCFETCH901 / FLAGEAT901 (Forgacs, 2026-09-01): iterate the RAW segments
  // and drop flag VALUES only for the branch that wants them. `-e` lives in the
  // shared VALUE_FLAGS table (it is curl's referer flag), so `node -e "<program>"`
  // used to arrive here as a bare `node` -- program eaten, fetch invisible. The
  // skill gate already read raw segments for exactly this reason; this one did not.
  const heredocBodies = extractHeredocBodies(cmd)
  for (const rawSeg of splitSegments(tokenizeDetailed(cmd))) {
    const seg = dropFlagValues(rawSeg)
    if (seg.length === 0) continue
    const texts = seg.map((t) => t.text)
    // WRAPFLAG901: one shared wrapper table for BOTH gates (see lib).
    const vi = verbIndex(texts)
    const verb = (texts[vi] || '').split('/').pop()
    const args = seg.slice(vi + 1)
    if (!verb) continue

    if (FETCHER_CMDS.has(verb)) {
      sawFetcher = true
      // A destination is an argument that IS a url, quoted or not.
      for (const a of args) if (URL_WHOLE_RE.test(a.text)) targets.push(a.text)
      continue
    }

    if (URL_ONLY_FETCHERS.has(verb)) {
      const urls = args.filter((a) => NET_URL_RE.test(a.text) || URL_WHOLE_RE.test(a.text)).map((a) => a.text)
      if (urls.length === 0) continue          // local-only work: not a fetch
      sawFetcher = true
      targets.push(...urls)
      continue
    }

    if (INTERPRETERS.has(verb)) {
      // Only an INLINE program can fetch here; `bash script.sh` cannot be read
      // and is a stated gap, not a silent one.
      // Read the program off the RAW segment: dropFlagValues would have eaten a
      // `-e` program before we ever saw it.
      const rawTexts = rawSeg.map((t) => t.text)
      const rawVi = rawTexts.indexOf(texts[vi])
      const rawArgs = rawVi === -1 ? rawTexts.slice(1) : rawTexts.slice(rawVi + 1)
      const codeIdx = rawArgs.findIndex((t) => INLINE_CODE_FLAGS.has(t))
      // A heredoc feeds the program on STDIN: no inline flag exists, and the body
      // was stripped before tokenising. Without this the whole segment was skipped.
      const inlineProgram = codeIdx === -1 ? '' : rawArgs.slice(codeIdx + 1).join(' ')
      // Only the heredocs THIS segment owns. Attaching every body in the command
      // to every interpreter was too wide, and it showed up immediately: a
      // `cat > file.py <<'X' ... X` followed by `python3 file.py` had the cat's
      // body judged as the python's program. The marker token (`<<X`) stays in
      // the owning segment after the body is stripped, so ownership is readable.
      const ownMarkers = new Set(
        rawTexts.filter((t) => t.startsWith('<<')).map((t) => t.replace(/^<<-?/, '').replace(/^['"]|['"]$/g, '')),
      )
      // STDINPROG901: the heredoc is the interpreter's PROGRAM only when the
      // interpreter actually reads its program from stdin -- `bash <<EOF`,
      // `python3 - <<PY`. With a SCRIPT FILE argument (`bash send.sh <<EOF`) the
      // body is the script's INPUT, not code the interpreter runs. Missing that
      // distinction blocked the fleet's own messaging idiom within a minute:
      // `bash scripts/agent-msg.sh <to> - <<EOF ...` refused because the MESSAGE
      // TEXT discussed a fetch. Prose about a fetch is not a fetch.
      // A redirect token (`<<EOF`, `>out`) is not an operand -- skipping that is
      // what tells `bash <<EOF` (stdin program) from `bash send.sh <<EOF` (input).
      const firstOperand = rawArgs.find(
        (t) => !t.startsWith('<') && !t.startsWith('>') && (!t.startsWith('-') || t === '-'),
      )
      const readsStdin = firstOperand === undefined || firstOperand === '-'
      const ownBodies = readsStdin
        ? heredocBodies.filter((h) => ownMarkers.has(h.marker)).map((h) => h.body)
        : []
      const program = [inlineProgram, ...ownBodies].filter(Boolean).join('\n')
      if (!program) continue

      // `sh -c "curl ..."` is the obvious way around a verb-scoped gate, so the
      // inline program is read as a command in its own right. Depth-limited:
      // one nested shell is a real pattern, ten is someone playing.
      // The inner call is a COMPLETE judgement of that program: if it refuses,
      // that is the answer; if it allows, this segment is settled and must not
      // be re-judged here (an allowed inner fetch has no target token at this
      // level, and demanding one turned `sh -c "curl <allowed>"` into a block).
      // QUOTEDNOTE901 (Cody, 2026-09-01): re-reading the program as a SHELL
      // command line is right for `sh -c "curl ..."` and a category error for
      // everything else. A python program that merely QUOTES a shell command --
      // a note, a memory entry, a skill's Pitfalls section recording what was
      // tested -- was read as if it ran it. That blocked exactly the work this
      // whole audit consists of: writing down what we found.
      // Shells get the shell reading; python/node/ruby/perl are judged by
      // CODE_FETCHER_RE on their own text instead.
      // HONEST REMAINDER: quoting a CODE-level fetcher (urlopen, https.get) in a
      // python or node program still trips the gate, because there the quote and
      // the call are the same text. Paraphrase the call name in notes.
      if (SHELLS.has(verb)) {
        // DEPTHOPEN901 (Cody, 2026-09-01, measured): past the recursion limit the
        // branch used to just fall through, and a shell program carries none of
        // the CODE_FETCHER_RE markers -- so a four-deep `sh -c` nest came out as
        // "no fetcher" and was ALLOWED. Everywhere else this gate fails closed;
        // here the deepest, least readable case was the one it waved through.
        if (depth >= 3) return { blocked: true, tier: 'nesting-too-deep' }
        const inner = bashEgressDecision(program, runtimeList, depth + 1)
        if (inner.blocked) return inner
        if (inner.tier === 'allowed') continue
      }

      // A program can also fetch without spawning anything: urlopen, requests,
      // fetch(). Those URLs live in the program text, not in an argument.
      if (!CODE_FETCHER_RE.test(program)) continue
      sawFetcher = true
      for (const m of program.match(URL_ANYWHERE_RE) ?? []) targets.push(m)
    }
  }

  if (!sawFetcher) return { blocked: false, tier: 'no-fetcher' }

  if (targets.length === 0) {
    // A fetcher with no readable destination: `curl "$TARGET_URL"`, or a URL
    // assembled at runtime. It cannot be judged, so it is not allowed.
    return { blocked: true, tier: 'no-url-literal' }
  }

  for (const u of targets) {
    const { host, unresolved } = hostOf(u)
    if (unresolved) return { blocked: true, tier: 'unresolved-host', url: u }
    // DOTSEG901 (Roxy, measured): a plain startsWith let `/me/../generations`
    // pass as `/me`, so the one narrow form of grant was widened by six
    // characters. prefixAllows normalises the path first and matches on a
    // segment boundary.
    if (allowedPrefixes.some((p) => prefixAllows(u, p))) continue
    if (!hostAllowed(host, allowedHosts)) return { blocked: true, tier: 'not-allowed', url: u, host }
  }
  return { blocked: false, tier: 'allowed' }
}

const BLOCK_MESSAGE =
  'Egress TILTOTT (bash-egress-gate hook). Ez a Bash parancs halozati hivast inditana ' +
  'olyan celra, ami nincs az engedelylistan -- vagy a cel a parancsbol nem allapithato meg ' +
  '(pl. valtozoba rejtett URL), es akkor a kapu ZAR. ' +
  'Kulso web-tartalom (RSS, dokumentacio, cikkek, ismeretlen API-k) KIZAROLAG a ' +
  'quarantine-reader sub-agensen keresztul kerheto le: ' +
  'Agent({ subagent_type: "quarantine-reader", prompt: `FETCH {"url":"...","nonce":"..."}` }). ' +
  'A letiltott hivas rogzitve lett a store/egress-blocked.log fajlban. ' +
  'Ha a hivas jogos, az operator jovahagyhatja: adja hozza a domaint a ' +
  'store/egress-allowlist.json fajlhoz ({ "domains": ["example.com"] }), majd futtassa ujra. ' +
  'Ha az URL valtozoban van, irja ki literalisan -- a kapu csak azt tudja megitelni, amit lat.'

const SELFTEST = process.env.EGRESS_GATE_SELFTEST === '1'

function logLine(kind, detail) {
  try {
    mkdirSync(join(REPO_ROOT, 'store'), { recursive: true })
    const ts = localStamp()
    if (SELFTEST) kind = `SELFTEST_${kind}`
    appendFileSync(EGRESS_BLOCK_LOG, `${ts} ${kind} ${detail}\n`, 'utf-8')
  } catch {
    // A failed log must never turn into a failed agent.
  }
}

function allow() { process.exit(0) }

function deny(reason) {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  }))
  process.exit(0)
}

function isInvokedDirectly() {
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === (process.argv[1] ? realpathSync(process.argv[1]) : '')
  } catch {
    return false
  }
}

if (isInvokedDirectly()) {
  let payload
  try {
    payload = JSON.parse(readFileSync(0, 'utf-8'))
  } catch {
    allow()  // malformed or empty input must never block the agent
  }
  if (payload?.tool_name !== 'Bash') allow()
  const command = String(payload?.tool_input?.command ?? '')
  const decision = bashEgressDecision(command, loadRuntimeAllowlist())
  if (decision.blocked) {
    // The URL is logged, the rest of the command is NOT: a command line can
    // carry a token or a password, and this log is read casually.
    // A blocked URL can carry a key in its query string; the log is read
    // casually, so it goes through the same redaction as the skill gate.
    const urlPart = decision.url ? ` url="${redactSecrets(decision.url)}"` : ''
    logLine('BLOCKED_BASH', `reason="${decision.tier}"${urlPart} agent="${sessionAgentName(payload, REPO_ROOT)}"`)
    deny(BLOCK_MESSAGE)
  }
  allow()
}
