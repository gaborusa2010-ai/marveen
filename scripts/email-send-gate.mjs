#!/usr/bin/env node
// PreToolUse hard-gate: blocks outbound email-send for sub-agents.
//
// Governance control (Szabi 2026-06-25, after the Boni incident: a sub-agent
// autonomously emailed a fabricated address asking for money in Szabi's name).
// Sub-agents may NOT send outbound email; any email must be routed through the
// main agent (Marveen) for approval -- only Marveen retains email-send.
//
// STATED LIMIT (msg 14298): this gate catches the ACCIDENTAL send, not a
// determined evader. Static analysis of arbitrary interpreter code is
// undecidable, so a sub-agent CAN send through interpreter code the patterns
// do not model -- the premise "a sub-agent cannot send outbound" does NOT
// hold against intent, only against accident. The exec-heuristic below covers
// the naive shapes (process-spawn plus a known mailer name in one code
// string) and claims no more. Our sub-agents are not adversaries; if that
// assumption ever changes, this gate is the wrong tool.
//
// Why a hook and not a permissions deny-list: the hook is version- and
// mode-independent, and it can analyze command CONTENT (the Bash send-shape
// heuristics below), which a name/prefix deny rule cannot express.
// CORRECTION (SKIPDENY910, measured 2026-09-10): this comment used to claim
// that --dangerously-skip-permissions BYPASSES the settings.json deny list.
// That is false on every CLI version we measured (2.1.63, 2.1.110, 2.1.267;
// marker-file ground truth, deny arm vs no-deny control, -p AND interactive
// TUI): the deny list IS enforced under the flag, and a tool-name deny is
// enforced by removing the tool from the session entirely. The hook remains
// the primary gate anyway -- future CLI behavior is not a contract, and the
// deny list stays a second, independent layer, not the load-bearing one.
//
// This file is wired into every sub-agent's .claude/settings.json by
// writeAgentSettingsFromProfile() (agent-scaffold.ts), guarded by
// name !== MAIN_AGENT_ID, and re-applied on every spawn (respawn-safe).

import { closeSync, lstatSync, openSync, readdirSync, readFileSync, readSync, realpathSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, isAbsolute, join, normalize, resolve } from 'node:path'
import { homedir } from 'node:os'

// HOOKDEPLOAD1008: the ledger module is a DYNAMIC import. As a static import, a
// broken recipient-ledger.mjs (a syntax error, a conflicted merge, a missing
// export) failed the module link before any line of this file ran: node exited
// 1, which PreToolUse treats as NON-blocking, so EVERY call went through
// unchecked -- the send_email deny included, which never needs the ledger.
// Now the failure is kept, only a decision that needs the ledger hits it, and
// that one fails closed at the entrypoint (exit 2, the reason on stderr).
let loadLedger, isVerifiedIn, splitAddresses, parseAddresses, SOURCE_HELP
let LEDGER_LOAD_ERROR = null
try {
  ({ loadLedger, isVerifiedIn, splitAddresses, parseAddresses, SOURCE_HELP } = await import('./recipient-ledger.mjs'))
  const missing = Object.entries({ loadLedger, isVerifiedIn, splitAddresses, parseAddresses })
    .filter(([, fn]) => typeof fn !== 'function').map(([name]) => name)
  if (missing.length) throw new Error(`missing export: ${missing.join(', ')}`)
} catch (err) {
  LEDGER_LOAD_ERROR = err
  const unavailable = () => { throw new Error(`recipient-ledger.mjs did not load: ${err?.message ?? err}`) }
  loadLedger = isVerifiedIn = splitAddresses = parseAddresses = unavailable
  SOURCE_HELP = '(a ledger modul nem toltheto be)'
}

// Bash command patterns that send mail. SUBGATEPOZ822 (2026-08-22): these are
// no longer the primary trigger -- they matched CONTENT anywhere in the
// command string, and the header's old premise ("a sub-agent has no
// legitimate need to invoke these") broke measurably: the developer of the
// mail tooling IS a sub-agent, and the gate blocked the delivery of the
// mail-gate fix three times in one afternoon (commit message, PR body,
// card-comment sqlite write), plus five more content hits across the fleet
// the same day. The primary trigger is now isSendInvocation() below
// (command-position analysis); this list remains ONLY as the conservative
// fallback when a command cannot be tokenized (unbalanced quote) -- on
// unparseable input the gate behaves exactly as before, never weaker.
const SEND_PATTERNS = [
  /support-mail\/send\.py/i,
  /\bsend\.py\b/i,
  /api\.resend\.com/i,
  /\bresend\b[^\n]*\b(email|send|message)\b/i,
  /\bsendmail\b/i,
  /\bmsmtp\b/i,
  /\bswaks\b/i,
  /\bsmtplib\b|SMTP\s*\(/i,
  /\bmail\.send\b|\bsendEmail\b/i,
  // graph-mail.ts (PR #668, M365/Exchange Online client-credentials mailbox):
  // its CLI is `tsx scripts/graph-mail.ts send ...`, which none of the above
  // patterns catch (no "sendmail"/"mail.send" substring). Also gate any direct
  // call to the exported sendMail() (e.g. a one-off `node -e`/`tsx -e` that
  // imports the module without going through the CLI).
  /\bgraph-mail\b[^\n]*\bsend\b/i,
  /\bsendMail\s*\(/i,
]

// --- command-position analysis (SUBGATEPOZ822) ------------------------------
// Ported from scripts/hooks/outgoing-copy-gate.py (KAPUHATOKOR822, PR #1042,
// two adversarial rounds): quote-aware tokenization, then the INVOKED program
// of each pipeline/sequence segment decides. A quoted token keeps its
// POSITION, so a quoted provider URL in curl argument position still fires
// (the normal way curl is written), while the same domain inside a quoted -d
// payload stays content (the target pattern is anchored to the token start).
// Wrapper-shell -c strings recurse; interpreter code-string arguments
// (python -c / node -e) are scanned for code-level send calls -- code handed
// to an interpreter IS operation, not content.
// Order-independent heredoc stripping (round 3, msg 14286): the rest of
// the intro line (e.g. a redirect after the marker) is command text and
// is KEPT -- only the body is cut. Without this, marker-first spellings
// leaked the body into command position (FP), and dropping the intro
// would have lost a heredoc-fed real sender (FN).
// Round 7 (review 2872), as _HEREDOC in the python twin: any quoting of the tag,
// non-word tags, a tab-indented closing tag after <<-, not a here-string (<<<).
// JS has no conditional group, so <<- and << are two alternatives; heredoc(m)
// gives the opener line and the body of either.
const HEREDOC_RE = /((?<!<)<<-\s*(['"]?)([^\s'"<>;&|()]+)\2[^\n]*)\n([\s\S]*?\n)\t*\3(?=\s|$)|((?<!<)<<\s*(['"]?)([^\s'"<>;&|()]+)\6[^\n]*)\n([\s\S]*?\n)\7(?=\s|$)/g
const heredoc = (m) => ({ open: m[1] ?? m[5], body: m[4] ?? m[8] })
const ENV_ASSIGN = /^[A-Za-z_][A-Za-z_0-9]*=/
// A word stepped over before the command, also `a[i]=v` and `a+=v`; see _ASSIGN_PREFIX
// in hooks/outgoing-copy-gate.py (review 3034).
const ASSIGN_PREFIX = /^[A-Za-z_][A-Za-z_0-9]*(?:\[[^\]]*\])?\+?=/
// How many tokens the leading assignment word spans (0: none): bash keeps `a[1 2]=x`
// one word up to the matching ]; see _assign_prefix_len in the python twin.
function assignPrefixLen(toks) {
  if (ASSIGN_PREFIX.test(toks[0])) return 1
  if (!/^[A-Za-z_][A-Za-z_0-9]*\[/.test(toks[0])) return 0
  let depth = 0
  for (let n = 0; n < toks.length; n++) {
    const tok = toks[n]
    for (let i = 0; i < tok.length; i++) {
      if (tok[i] === '[') depth++
      else if (tok[i] === ']' && --depth === 0) return /^\+?=/.test(tok.slice(i + 1)) ? n + 1 : subscriptAssignEnd(toks)
    }
  }
  return subscriptAssignEnd(toks)
}
// A `]` the tokenizer no longer shows as quoted or expanded (`a["]"]=x`) still ends
// in one assignment word for bash; see _subscript_assign_end in the python twin (#729).
function subscriptAssignEnd(toks) {
  const n = toks.findIndex((tok) => /\]\+?=/.test(tok))
  return n + 1
}
// KWSPLIT924: shell reserved words that put the NEXT word in command position.
// Stripped only at the head of a segment, never used as separators -- see the
// full rationale at _CMD_POSITION_KEYWORDS in outgoing-copy-gate.py (the python
// twin); the shared send-invocation-cases.json binds the two copies.
// `}` too (#729): `a+=()} python3 x.py` runs x.py.
const CMD_POSITION_KEYWORDS = new Set(['if', 'then', 'else', 'elif', 'do', 'while', 'until', '{', '}', '!'])
// #729/#748: ssmtp and mutt send the way sendmail does (Marveen: the gate is accident
// protection, the extra block is cheap). mail / mailx / s-nail are judged by upstream's
// MAIL_PROG rule below (MAILGATEAPIB1027). Mirrors _SENDER_PROG in the python twin.
const SENDER_PROG = /^(sendmail|msmtp|swaks|ssmtp|mutt)$/i
// MAILGATEAPIB1027: mail / mailx / s-nail send when given an address, a subject, a cc/bcc/from/attach option or
// any operand (a recipient); with none of these (`mail`, `mail -f mbox`, `mail -u user`) they read mail.
// Mirrors _MAIL_PROG / _mail_prog_sends in hooks/outgoing-copy-gate.py.
const MAIL_PROG = /^(mail|mailx|s-nail)$/i
const MAIL_SEND_OPT = /^-[A-Za-z]*[sbcrtaAq]/
const CURL_SMTP = /^(?:smtps?:\/\/|--mail-(?:rcpt|from)(?:=|$))/i
function mailProgSends(rest) {
  let skip = false
  for (const t of rest) {
    if (skip) { skip = false; continue }
    if (t.includes('@')) return true
    if (t === '-f' || t === '-u') { skip = true; continue }
    if (t.startsWith('-')) {
      if (MAIL_SEND_OPT.test(t)) return true
      continue
    }
    return true
  }
  return false
}
const SENDPY = /^send\.py$/i
// SUBAGENTMAILGATE1016: a version-suffixed interpreter (python3.12, python3.13, and
// the free-threaded python3.13t) is the same python; the old /^python3?$/ let it
// through as "not an interpreter".
const PYTHON = /^python(3(\.\d+t?)?)?$/i
const NODEISH = /^(node|tsx|ts-node|deno|bun|npx)$/i
const GRAPHMAIL = /^graph-mail(\.ts|\.js)?$/i
const WRAPPER_SHELL = /^(sh|bash|zsh|dash)$/i
const CURLISH = /^(curl|wget|http)$/i
const RESEND_TARGET = /^(https?:\/\/)?([^/@\s]*\.)?api\.resend\.com(\/|$)/i
// RESENDGETTWIN1010: port of the python twin's method verdict (RESENDGATE826,
// outgoing-copy-gate.py :: _curl_resend_verdict). Until now this copy was
// method-blind and denied a read-only GET /domains the python copy passed.
// Strict direction kept: the method must be RECOGNIZED (explicit -X/--request/
// --method, or an implicit POST from a body flag); an undecidable method
// (variable, truncated flag, config file) stays a send. httpie (`http`) takes
// its method positionally and its body as `key=value` items, none of which this
// flag scan reads, so it is undecidable too and stays a send in both twins.
const HTTPIE = /^http$/i
const CURL_BODY_OPTS = new Set([
  '-d', '--data', '--data-raw', '--data-binary', '--data-urlencode',
  '--data-ascii', '-F', '--form', '--form-string', '--json',
  '-T', '--upload-file',
  // wget body flags
  '--post-data', '--post-file', '--body-data', '--body-file',
])
const SAFE_METHODS = new Set(['GET', 'HEAD'])
const ALPHA = /^\p{L}+$/u // python str.isalpha()

// 'read' | 'send' | 'unknown' -- the caller treats unknown as a send.
function curlResendVerdict(prog, rest) {
  if (HTTPIE.test(prog)) return 'unknown'
  let method = null
  let hasBody = false
  let getForced = false
  const n = rest.length
  for (let i = 0; i < n; i++) {
    const t = rest[i]
    if (t === '-X' || t === '--request' || t === '--method') {
      if (i + 1 >= n || !ALPHA.test(rest[i + 1])) return 'unknown'
      method = rest[i + 1].toUpperCase()
      i++
      continue
    }
    if (t.startsWith('--request=') || t.startsWith('--method=')) {
      const m = t.split('=').slice(1).join('=')
      if (!ALPHA.test(m)) return 'unknown'
      method = m.toUpperCase()
      continue
    }
    if (t === '-G' || t === '--get') { getForced = true; continue }
    if (t === '-K' || t === '--config') return 'unknown' // a config file can carry a hidden method or body
    if (CURL_BODY_OPTS.has(t) ||
        [...CURL_BODY_OPTS].some((o) => o.startsWith('--') && t.startsWith(o + '='))) {
      hasBody = true
      continue
    }
    if (t.startsWith('-') && !t.startsWith('--') && t.length > 1) {
      // single-dash cluster (-sS, -sX POST, -sd '{}'): the letters are bundled
      const letters = t.slice(1)
      if (letters.includes('X')) {
        const after = letters.slice(letters.indexOf('X') + 1)
        if (after) {
          if (!ALPHA.test(after)) return 'unknown'
          method = after.toUpperCase()
        } else {
          if (i + 1 >= n || !ALPHA.test(rest[i + 1])) return 'unknown'
          method = rest[i + 1].toUpperCase()
          i++
        }
      } else if (letters.includes('d') || letters.includes('F') || letters.includes('T')) {
        hasBody = true
      } else if (letters.includes('G')) {
        getForced = true
      } else if (letters.includes('K')) {
        return 'unknown'
      }
    }
  }
  if (method !== null && !SAFE_METHODS.has(method)) return 'send'
  // implicit POST (the default of curl -d/-F/--json/-T), or a suspicious
  // "GET with a body" -- both are treated as a send
  if (hasBody && !getForced) return 'send'
  return 'read'
}
const CODE_SEND = /\bsmtplib\b|SMTP\s*\(|\bsendMail\s*\(|\bsendEmail\b|\bmail\.send\b/i
// Naive-shape exec heuristic (msg 14298): process-spawn AND a known mailer
// name together in one interpreter code string. Covers the accidental shapes;
// see the STATED LIMIT in the header for what it deliberately does not claim.
const CODE_EXECISH = /\bsubprocess\b|os\.system|\bpopen\b|child_process|\bexec[A-Za-z]*\s*\(|\bspawn[A-Za-z]*\s*\(/i
const CODE_SENDER_LIT = /sendmail|msmtp|swaks|send\.py/i
// Issue #1853 (stylnet): the support-mail sender LOADED AS A MODULE (`import send`,
// `from send import main`, runpy, __import__/import_module, a spec built from send.py).
// Case-sensitive on purpose; `send` must be the whole module name. Mirrors
// _CODE_SEND_MODULE in hooks/outgoing-copy-gate.py; send-invocation-cases.json binds them.
const CODE_SEND_MODULE = new RegExp(
  String.raw`\bimport\s+(?:[\w.]+\s*,\s*)*send\b(?![\w.])` +
  String.raw`|\bfrom\s+send\s+import\b` +
  String.raw`|\brunpy\b[^\n]*\bsend\b` +
  String.raw`|\b(?:__import__|import_module)\s*\(\s*['"]send['"]` +
  String.raw`|\bspec_from_file_location\b[^\n]*\bsend\.py\b` +
  // The same class on the Graph sender (export sendMail): renamed on import, or a string key.
  String.raw`|\bsendMail\s+as\b|\[\s*['"]sendMail['"]\s*\]`,
)
// #718 (aaf838d6): a send straight through a mail provider's HTTP API (Gmail
// messages/send or drafts/send by REST URL on the gmail or www googleapis host, or the
// Google client's messages() / drafts() chain ending in send(), also split over two
// statements; Graph sendMail or send/reply/replyAll/forward on one message). Host and
// send path are matched apart, as a pair, because the URL is often assembled. Mirrors
// _code_api_sends in hooks/outgoing-copy-gate.py.
const CODE_API_CLIENT_SEND = /\.(?:messages|drafts)\(\)\s*\.send\s*\(/
const CODE_API_PAIRS = [
  // v1.46 merge: the action word ends as in upstream's ACTION_END (send-status is a read).
  [/(?:gmail|www)\.googleapis\.com/i, /\b(?:messages|drafts)\/send(?![A-Za-z0-9_-]|[^\x00-\x7f])/],
  [/\.users\(\)\s*\.(?:messages|drafts)\(\)/, /\.send\s*\(/],
  [/graph\.microsoft\.com/i, /\/sendMail(?![A-Za-z0-9_-]|[^\x00-\x7f])|\/messages\/[^/\s'"]+\/(?:send|reply|replyAll|forward)(?![A-Za-z0-9_-]|[^\x00-\x7f])/],
]
const codeApiSends = (code) =>
  CODE_API_CLIENT_SEND.test(code) || CODE_API_PAIRS.some(([host, path]) => host.test(code) && path.test(code))
// #725: a script FILE that imports the stdlib mail client and both connects and
// sends with it is a sender. Mirrors _script_sends in hooks/outgoing-copy-gate.py;
// the split literals keep this file's own text from matching when it is read.
const SCRIPT_SMTP_IMPORT = new RegExp('^[ \\t]*(?:import[ \\t][^\\n#]*\\bsmtp' + 'lib\\b|from\\s+smtp' + 'lib\\s+import\\b)', 'm')
const SCRIPT_SMTP_CLIENT = /\b(?:SMTP(?:_SSL)?|LMTP)\s*\(/ // LMTP is an SMTP subclass (review 3034)
const SCRIPT_SMTP_SEND = new RegExp('\\.(?:send' + 'mail|send_message)\\s*\\(')
const scriptSends = (code, path) =>
  codeApiSends(code) ||
  (!SENDPY.test(basename(path)) && SCRIPT_SMTP_IMPORT.test(code) && SCRIPT_SMTP_CLIENT.test(code) && SCRIPT_SMTP_SEND.test(code))
// MAILGATESCRIPT1027: a Microsoft Graph send endpoint (sendMail, or send/reply/replyAll/forward of a message)
// MAILGATESCRIPT1027 R1: a send needs the endpoint shape AND Graph evidence in the same text (an application
// route of the same shape is no send); `\/` is normalised to `/` first (JSON-escaped URLs).
const GRAPH_SEND = /\/(?:me|users\/[^\n]{1,120}?)\/(?:sendMail(?![A-Za-z0-9_-]|[^\x00-\x7f])|messages\/[^\n]{1,160}?\/(?:send|reply|replyAll|forward)(?![A-Za-z0-9_-]|[^\x00-\x7f]))/i
const GRAPH_EVIDENCE = /graph\.microsoft\.com|microsoft-graph-client|msgraph|\.api\s*\(/i
const graphSends = (text) => {
  const t = text.replaceAll('\\/', '/')
  return GRAPH_SEND.test(t) && GRAPH_EVIDENCE.test(t)
}
// MAILGATEHTTPAPI1027: an HTTP mail API reached from a script (a Gmail REST send from urllib went past
// the main agent's approval gate). Each provider needs its own evidence AND a send shape in the same
// text, so a Gmail read or a text that only names the provider is no send. The evidence is a literal API
// host or a client call or package name, never the bare provider word: after the `\/` normalisation a
// gate's own regex source carries the shapes, and a gate file must not count as a sender. Mirrors
// _MAIL_API_RULES in hooks/outgoing-copy-gate.py; hooks/mail-api-send-cases.json binds the two twins.
const ACTION_END = String.raw`(?![A-Za-z0-9_-]|[^\x00-\x7f])`
const MAIL_API_RULES = [
  // Gmail: the REST host (also an assembled URL) or the client; the messages or drafts send call.
  [/gmail\.googleapis\.com|[/]gmail[/]v1[/]|\bbuild\s*\(\s*['"]gmail['"]|\bgoogle\.gmail\s*\(|['"]@googleapis\/gmail['"]/i,
    new RegExp(String.raw`\/(?:messages|drafts)\/send` + ACTION_END + String.raw`|\.(?:messages|drafts)\s*(?:\(\s*\))?\s*\.send\s*\(`, 'i')],
  // Resend: the emails endpoint on the API host (and its batch), or the SDK's emails.send( on a client
  // of any name (Samu 50950 A1: `const client = new Resend(k)`), the package being the evidence.
  [/api\.resend\.com|['"]resend['"]|\bimport\s+resend\b/i,
    new RegExp(String.raw`api\.resend\.com\/emails(?:\/batch)?` + ACTION_END + String.raw`|\.emails\.send\s*\(`, 'i')],
  // SendGrid: v3 mail send on the API host, or the mail client.
  [/api\.sendgrid\.com|['"]@sendgrid\/mail['"]|\bSendGridAPIClient\s*\(/i,
    new RegExp(String.raw`api\.sendgrid\.com\/v3\/mail\/send` + ACTION_END + String.raw`|['"]@sendgrid\/mail['"]|\bSendGridAPIClient\s*\(`, 'i')],
  // Mailgun: the API host with a v3 domain messages path (an assembled URL too), or the client's
  // messages.create( next to the package name. A bare "/messages" is no evidence: the dashboard's own
  // /api/messages is one.
  [/mailgun\.net|['"]mailgun(?:\.js)?['"]/i,
    new RegExp(String.raw`\/v3\/[^\n]{1,200}?\/messages(?:\.mime)?` + ACTION_END + String.raw`|\.messages\.create\s*\(`, 'i')],
  // Postmark: the email endpoints on the API host (single, batch, with a template), or the SDKs (MAILGATEAPIB1027):
  // the node client's sendEmail family, python postmarker's emails.send family.
  [/api\.postmarkapp\.com|['"]postmark['"]|\bpostmarker\b/i,
    new RegExp(String.raw`api\.postmarkapp\.com\/email(?:\/batch|\/withTemplate)?` + ACTION_END +
      String.raw`|\.sendEmail(?:Batch|WithTemplate|BatchWithTemplates)?\s*\(|\.emails\.send(?:_batch|_with_template)?\s*\(`, 'i')],
]
const mailApiSends = (text) => {
  const t = text.replaceAll('\\/', '/')
  return MAIL_API_RULES.some(([evidence, send]) => evidence.test(t) && send.test(t))
}
// A Microsoft Graph send or an HTTP mail API send in a code string or a script file.
const webMailSends = (text) => graphSends(text) || mailApiSends(text)
// MAILGATEHEREDOCFP1027: a send marker that only sits in DATA is no send. The interpreter's code is read with its
// prose blanked: a string literal whose text holds whitespace and every comment become spaces (length and line
// breaks kept); a literal without whitespace (a URL, a package name, a 'sendmail' argv item) stays, and so do the
// interpolations of a template literal or an f-string. An unterminated literal is left raw to the end of where the
// scan stopped. Mirrors _mask_prose_literals in hooks/outgoing-copy-gate.py (see the rationale there);
// hooks/prose-literal-cases.json binds the two.
const PY_STR_PREFIX = /^(?:rb|br|fr|rf|r|b|u|f)$/i
const isAlpha = (ch) => /^\p{L}$/u.test(ch)
const isWordish = (ch) => /^[\p{L}\p{N}_]$/u.test(ch)
const blankText = (s) => Array.from(s, (ch) => (ch === '\n' ? '\n' : ' ')).join('')
export function maskProseLiterals(text, lang) {
  if (lang !== 'py' && lang !== 'js') return text
  const chars = Array.from(text)
  const n = chars.length
  const at = (k, s) => { for (let q = 0; q < s.length; q++) if (chars[k + q] !== s[q]) return false; return true }
  const slice = (a, b) => chars.slice(a, b).join('')
  // ASCII whitespace only, the same set in both twins (str.isspace and \s differ outside ASCII).
  const isSpace = (ch) => ' \t\n\r\f\v'.includes(ch)
  const out = []
  let i = 0
  while (i < n) {
    const c = chars[i]
    if ((lang === 'py' && c === '#') || (lang === 'js' && at(i, '//'))) {
      let j = chars.indexOf('\n', i)
      if (j === -1) j = n
      out.push(blankText(slice(i, j)))
      i = j
      continue
    }
    if (lang === 'js' && at(i, '/*')) {
      let j = -1
      for (let k = i + 2; k < n - 1; k++) if (chars[k] === '*' && chars[k + 1] === '/') { j = k; break }
      if (j === -1) { out.push(slice(i, n)); break }
      out.push(blankText(slice(i, j + 2)))
      i = j + 2
      continue
    }
    if (c === "'" || c === '"' || (lang === 'js' && c === '`')) {
      let prefix = ''
      if (lang === 'py') {
        let k = i
        while (k > 0 && isAlpha(chars[k - 1]) && i - k < 2) k--
        if (k < i && PY_STR_PREFIX.test(slice(k, i)) && (k === 0 || !isWordish(chars[k - 1]))) prefix = slice(k, i).toLowerCase()
      }
      const triple = lang === 'py' && at(i, c + c + c)
      const quote = triple ? c + c + c : c
      const interp = (lang === 'js' && c === '`') || prefix.includes('f')
      const multiline = triple || (lang === 'js' && c === '`')
      let j = i + quote.length
      const parts = []
      let buf = ''
      let closed = false
      while (j < n) {
        const ch = chars[j]
        if (ch === '\\' && j + 1 < n) { buf += ch + chars[j + 1]; j += 2; continue }
        if (at(j, quote)) { closed = true; break }
        if (ch === '\n' && !multiline) break
        if (interp && ((lang === 'js' && at(j, '${')) || (lang === 'py' && ch === '{' && !at(j, '{{')))) {
          const start = j + (lang === 'js' ? 2 : 1)
          let depth = 1
          let k = start
          while (k < n && depth) {
            if (chars[k] === '{') depth++
            else if (chars[k] === '}') depth--
            k++
          }
          if (depth) break
          parts.push([false, buf])
          buf = ''
          parts.push([true, slice(j, k)])
          j = k
          continue
        }
        if (interp && lang === 'py' && at(j, '{{')) { buf += '{{'; j += 2; continue }
        buf += ch
        j++
      }
      if (!closed) {
        // Raw up to where the scan stopped: its text is not read again as code (a `//` in it is no comment).
        out.push(slice(i, j))
        i = j
        continue
      }
      parts.push([false, buf])
      const prose = parts.some(([code, s]) => !code && Array.from(s).some(isSpace))
      out.push(quote)
      for (const [code, s] of parts) out.push(code || !prose ? s : blankText(s))
      out.push(quote)
      i = j + quote.length
      continue
    }
    out.push(c)
    i++
  }
  return out.join('')
}
// The masking language of an interpreter: python, a JS runtime, else none.
const langOf = (prog) => (PYTHON.test(basename(prog)) ? 'py' : NODEISH.test(basename(prog)) ? 'js' : null)
const codeStringSends = (code, lang = null) => {
  const masked = maskProseLiterals(code, lang)
  // #718 provider-API patterns (ours) next to upstream's web-mail rules, both on the masked text.
  return CODE_SEND.test(masked) || CODE_SEND_MODULE.test(masked) || webMailSends(masked) || codeApiSends(masked) ||
    (CODE_EXECISH.test(masked) && CODE_SENDER_LIT.test(code))
}

// Unquoted newline / backtick / `$(` become segment separators; quoted text is
// untouched (it is content). Tracks quote state by hand -- no shell involved.
export function maskSubshellMarkers(cmd) {
  let out = ''
  let q = null
  for (let i = 0; i < cmd.length; i++) {
    const ch = cmd[i]
    if (q) {
      if (ch === '\\' && q === '"' && i + 1 < cmd.length) { out += cmd[i] + cmd[i + 1]; i++; continue }
      if (ch === q) q = null
      out += ch
      continue
    }
    if (ch === "'" || ch === '"') { q = ch; out += ch; continue }
    if (ch === '\\' && i + 1 < cmd.length) { out += cmd[i] + cmd[i + 1]; i++; continue }
    if (ch === '\n' || ch === '`') { out += ';'; continue }
    if (ch === '$' && cmd[i + 1] === '(') { out += ';'; i++; continue }
    out += ch
  }
  return out
}

// Quote-aware tokenizer -> [[token, ...], ...] per segment. Throws on an
// unbalanced quote (the caller falls back to the legacy content patterns).
// seg.op: the operator text before the segment. The stdin redirections of a
// compound command and of `exec` are spread to the commands they feed
// (spreadShellStdin, see _spread_shell_stdin in the python twin).
export function segmentsTokens(cmd) {
  const s = maskSubshellMarkers(cmd.replace(HEREDOC_RE, (_, a, _b, _c, _d, e) => a ?? e))
  const segments = []
  let cur = []
  let tok = ''
  let hasTok = false
  let q = null
  let piped = false // the next segment's stdin is this one's output (`a | b`): seg.piped
  let ops = ''
  let escapedEnd = -1 // the index right after the last backslash pair
  const pushTok = () => {
    if (hasTok) {
      if (!cur.length) { cur.op = ops; ops = '' }
      cur.push(tok); tok = ''; hasTok = false
    }
  }
  const pushSeg = () => {
    pushTok()
    if (cur.length) { if (piped) cur.piped = true; segments.push(cur); cur = [] }
  }
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]
    if (q) {
      if (ch === '\\' && q === '"' && i + 1 < s.length) { tok += s[++i]; continue }
      if (ch === q) { q = null; continue }
      tok += ch
      continue
    }
    if (ch === "'" || ch === '"') { q = ch; hasTok = true; continue }
    if (ch === '\\' && i + 1 < s.length) { tok += s[++i]; hasTok = true; escapedEnd = i + 1; continue }
    if (ch === ' ' || ch === '\t') { pushTok(); continue }
    // #718 (review 2823): the `&` of a redirection (`2>&1`, `>&2`, `<&3`, `&>f`)
    // is part of the word, not a separator; see _mask_subshell_markers.
    // An escaped `\>` is a plain character (#729): `\>&sendmail b` runs sendmail.
    const redirAmp = ch === '&' && ((i > 0 && '<>'.includes(s[i - 1]) && escapedEnd !== i) ||
      (s[i + 1] === '>' && !(i > 0 && '&|'.includes(s[i - 1]))))
    if ('|&;()'.includes(ch) && !redirAmp) {
      pushSeg()
      ops += ch
      if (ch === '|') piped = s[i + 1] !== '|' && s[i - 1] !== '|'
      else if (!(ch === '&' && s[i - 1] === '|')) piped = false
      continue
    }
    tok += ch
    hasTok = true
  }
  if (q) throw new Error('unbalanced quote')
  pushSeg()
  spreadShellStdin(segments)
  return segments
}

const GROUP_OPEN = new Set(['{', 'if', 'for', 'while', 'until', 'case', 'select'])
const GROUP_CLOSE = new Set(['}', 'fi', 'done', 'esac'])
// How many leading tokens are in words (see _lead_run in the python twin).
function leadRun(toks, words) {
  let n = 0
  while (n < toks.length && words.has(toks[n])) n++
  return n
}

// The stdin redirections among toks, each as [op, word]; see _stdin_redirects.
function stdinRedirects(toks) {
  const out = []
  for (let i = 0; i < toks.length; i++) {
    const m = REDIRECT.exec(toks[i])
    if (m === null) continue
    const word = m[2] ? m[2] : (toks[i + 1] ?? '')
    const fd = toks[i].slice(0, toks[i].length - m[1].length - m[2].length)
    if (['<', '<>', '<&', '<<<'].includes(m[1]) && (fd === '' || fd === '0')) out.push([m[1], word])
    if (!m[2]) i++
  }
  return out
}

// Append redirects to every segment that reads the shell's stdin: not one fed by
// a pipe, not an assignment-only one.
function feedStdin(segments, redirects) {
  for (const seg of segments) {
    if (!seg.piped && assignments(seg, new Map()) === null) {
      for (const [op, word] of redirects) seg.push(op, word)
    }
  }
}

// See _spread_shell_stdin in the python twin.
function spreadShellStdin(segments) {
  const stack = []
  segments.forEach((seg, j) => {
    let start = null
    for (const ch of seg.op ?? '') {
      if (ch === ')') start = stack.length ? stack.pop() : 0
      else if (ch === '(') stack.push(j)
    }
    const head = seg.length ? seg[0] : ''
    const closes = leadRun(seg, GROUP_CLOSE)
    for (let k = 0; k < closes; k++) start = stack.length ? stack.pop() : 0
    if (start !== null) feedStdin(segments.slice(start, j), stdinRedirects(seg.slice(closes)))
    for (let k = leadRun(seg, GROUP_OPEN); k > 0; k--) stack.push(j)
    if (head === 'exec' && seg.slice(1, 2).every((t) => REDIRECT.test(t))) {
      feedStdin(segments.slice(j + 1), stdinRedirects(seg.slice(1)))
    }
  })
}

const basename = (t) => t.split('/').pop()

// SENDWRAP924: a wrapper in front of the sender is stepped over WITH its own
// flags -- see the full rationale at _WRAPPERS in outgoing-copy-gate.py (the
// python twin). An unknown long flag without `=` is read both ways, so it errs
// toward auditing. send-invocation-cases.json binds the two copies.
const WRAPPERS = new Map([
  ['time', ['fo', ['format', 'output'], 0]],
  ['sudo', ['ughpCUrtDRT', ['user', 'group', 'host', 'prompt', 'close-from', 'other-user',
    'role', 'type', 'chdir', 'chroot', 'command-timeout'], 0]],
  ['env', ['uCS', ['unset', 'chdir', 'split-string'], 0]],
  ['nohup', ['', [], 0]],
  ['nice', ['n', ['adjustment'], 0]],
  ['exec', ['a', [], 0]],
  ['command', ['', [], 0]],
  ['xargs', ['ILnPsdEa', ['arg-file', 'delimiter', 'eof', 'replace', 'max-lines', 'max-args',
    'max-procs', 'max-chars', 'process-slot-var'], 0]],
  ['timeout', ['sk', ['signal', 'kill-after'], 1]],
  ['setsid', ['', [], 0]],
])
const HEAD_DEPTH = 8

// #718 (review 2814): a leading redirection (`< f python3`) is moved behind the
// command, see _REDIRECT in the python twin.
const REDIRECT = /^(?:\d*|&)(<<<|<<-|<<|<>|<&|>&|>>|>\||<|>)([\s\S]*)$/

const LEAD_EXPANSION = /^(?:\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z_0-9]*|[@*0-9]))+/

// MAILGATEUV1020: a project launcher's `run` subcommand in front of the sender --
// see the full rationale at _LAUNCHERS in outgoing-copy-gate.py (the python twin).
// name -> [subcommand, short flags taking a value, long flags taking a value,
// module flags, flag lists measured as complete].
const UV_RUN_LONG_VAL = ['extra', 'no-extra', 'group', 'no-group', 'only-group', 'env-file', 'with',
  'with-editable', 'with-requirements', 'package', 'python-platform', 'index', 'default-index',
  'index-url', 'extra-index-url', 'find-links', 'index-strategy', 'keyring-provider',
  'upgrade-package', 'resolution', 'prerelease', 'fork-strategy', 'exclude-newer',
  'exclude-newer-package', 'no-sources-package', 'reinstall-package', 'link-mode',
  'config-setting', 'config-settings-package', 'no-build-isolation-package', 'no-build-package',
  'no-binary-package', 'cache-dir', 'refresh-package', 'python', 'color', 'allow-insecure-host',
  'directory', 'project', 'config-file']
const LAUNCHERS = new Map([
  ['uv', ['run', 'wifPCp', UV_RUN_LONG_VAL, ['-m', '--module'], true]],
  ['pipx', ['run', '', ['index-url', 'pip-args', 'python', 'spec', 'with'], [], true]],
  ...['poetry', 'pdm', 'hatch', 'pipenv', 'rye', 'conda', 'mamba', 'micromamba']
    .map((n) => [n, ['run', '', [], [], false]]),
])

function launcherHeads(toks, spec, d) {
  const [sub, shortVal, longVal, moduleFlags, complete] = spec
  // The subcommand may follow the launcher's global options (`uv --directory d run`):
  // every word before it is an option or the value right after one.
  let j = -1
  for (let x = 1; x < toks.length; x++) {
    if (toks[x] === sub) { j = x; break }
    if (!toks[x].startsWith('-') && !(x > 1 && toks[x - 1].startsWith('-'))) break
  }
  if (j < 0) return [toks]
  const heads = []
  if (!complete) {
    // An unmeasured launcher's options are unknown, so every word in its option
    // run that could be the program is judged as the program: the word after an
    // option may be that option's value, or the command (Dani's #2086 review:
    // `conda run --no-capture-output -n base python send.py` mixes a flag and a
    // value option, which no single reading of all options fits).
    for (let x = j + 1; x < toks.length; x++) {
      if (toks[x] === '--') { heads.push(toks.slice(x + 1)); break }
      if (toks[x].startsWith('-') && toks[x].length > 1) continue
      heads.push(toks.slice(x))
      if (!(x > j + 1 && toks[x - 1].startsWith('-') && toks[x - 1].length > 1)) break
    }
    if (!heads.length) heads.push([])
    return heads.flatMap((h) => commandHeads(h, d + 1))
  }
  let i = j + 1
  let head = null
  while (i < toks.length) {
    const t = toks[i]
    if (t === '--') { i++; break }
    if (moduleFlags.includes(t)) { head = ['python3', '-m', ...toks.slice(i + 1)]; break }
    if (t.startsWith('--')) {
      const eq = t.indexOf('=')
      if (eq > 0 && moduleFlags.includes(t.slice(0, eq))) { head = ['python3', '-m', t.slice(eq + 1), ...toks.slice(i + 1)]; break }
      i += eq < 0 && longVal.includes(t.slice(2)) ? 2 : 1
      continue
    }
    if (t.startsWith('-') && t.length > 1) {
      let k = -1
      for (let x = 1; x < t.length; x++) if (shortVal.includes(t[x])) { k = x; break }
      i += k === t.length - 1 ? 2 : 1
      continue
    }
    break
  }
  heads.push(head ?? toks.slice(i))
  return heads.flatMap((h) => commandHeads(h, d + 1))
}

function commandHeads(toksIn, d = 0) {
  let toks = toksIn
  const moved = []
  while (toks.length && (assignPrefixLen(toks) || CMD_POSITION_KEYWORDS.has(toks[0]) || REDIRECT.test(toks[0]))) {
    const m = REDIRECT.exec(toks[0])
    const n = assignPrefixLen(toks) || (m === null || m[2] ? 1 : 2)
    if (m !== null) moved.push(...toks.slice(0, n))
    toks = toks.slice(n)
  }
  if (moved.length && toks.length) toks = [...toks, ...moved]
  if (!toks.length) return [toks]
  // An unset or empty expansion is no word at all (`$nothing sendmail b`); see
  // _LEAD_EXPANSION in the python twin (#729).
  const lead = LEAD_EXPANSION.exec(toks[0])
  if (lead && d < HEAD_DEPTH) {
    const rest = toks[0].slice(lead[0].length)
    return [toks, ...commandHeads([...(rest ? [rest] : []), ...toks.slice(1)], d + 1)]
  }
  const w = basename(toks[0])
  // Still a wrapper at the depth bound: counts as a send (null), see the python twin.
  if (d >= HEAD_DEPTH) return (WRAPPERS.has(w) || LAUNCHERS.has(w) || w === 'function' || w === 'coproc') ? [null] : [toks]
  if (w === 'function') return commandHeads(toks.slice(2), d + 1)
  if (w === 'coproc') return [...commandHeads(toks.slice(1), d + 1), ...commandHeads(toks.slice(2), d + 1)]
  const launcher = LAUNCHERS.get(w)
  if (launcher) {
    const heads = launcherHeads(toks, launcher, d)
    // #718: `uv run x.py` runs x.py with python: judged as `python3 x.py`, so the
    // script is read like any other python script (see _LAUNCHERS in the python twin).
    return w === 'uv' ? heads.map((h) => (h && h.length && h[0].endsWith('.py') ? ['python3', ...h] : h)) : heads
  }
  const spec = WRAPPERS.get(w)
  if (!spec) return [toks]
  const [shortVal, longVal, positionals] = spec
  let i = 1
  const starts = []
  while (i < toks.length) {
    const t = toks[i]
    if (t === '--') { i++; break }
    if (t.startsWith('--')) {
      if (!t.includes('=') && longVal.includes(t.slice(2))) { i += 2; continue }
      if (!t.includes('=')) starts.push(i + 2)
      i++
      continue
    }
    if (t.startsWith('-') && t.length > 1) {
      if (w === 'command' && (t.includes('v') || t.includes('V'))) return []
      let k = -1
      for (let j = 1; j < t.length; j++) if (shortVal.includes(t[j])) { k = j; break }
      i += k === t.length - 1 ? 2 : 1
      continue
    }
    break
  }
  starts.unshift(i)
  return starts.flatMap((s) => commandHeads(toks.slice(s + positionals), d + 1))
}

// SUBAGENTMAILGATE1016 (measured on the sub-agent gate, 2026-10-16): the
// support-mail sender was recognised only as `python3 <path>/send.py --to ...`,
// that is, with the script as the FIRST argument and the exact `--to` flag. Six
// spellings ran the same sender past this gate: an interpreter option first
// (`python3 -u`, `python3 -X utf8`), a version-suffixed interpreter, a glob or a
// variable as the script path (`s?nd.py`, `$P`), and the program read from
// stdin (`python3 - --to ... < send.py`). argparse also takes `--t` for `--to`.
// The boundary, fail-closed where the command cannot be read statically:
//   - the script operand is found past the interpreter's own options (a
//     separate argument for -X / -W / --check-hash-based-pycs);
//   - a recipient flag is `--to` or any argparse abbreviation of it (`--t`),
//     with or without `=value` (the sender's --to is required, so every
//     delivering call carries one of these);
//   - send.py by basename + a recipient flag: a send (as before, now from any
//     operand position, interpreter spelling and flag spelling);
//   - a script operand that cannot be resolved statically (glob or brace
//     characters, or a `$` expansion) + a recipient flag: a send, because what
//     runs is undecidable here (until MAILGATEVARARGS1020 also "or another `$`
//     token", see below);
//   - a program read from stdin (`-`, or no script operand and a `<` redirect)
//     + a recipient flag: a send (a heredoc-fed program is judged by its body,
//     heredocProgramSends, as before).
// Anything else stays allowed: `python3 -u other.py`, `python3 -c "print(1)"`,
// `for f in *.py; do python3 $f; done`, `python3 x.py --to-date ...`,
// `python3 "$DIR/gen.py" "$OUT"`.
// Mirrors _python_head_sends in hooks/outgoing-copy-gate.py; the shared
// send-invocation-cases.json binds the two.
//
// MAILGATEVARARGS1020 (measured on a production install, 2026-10-19): the
// "or another `$` token" half denied ordinary scripted work. In one day 26
// sub-agent Bash calls, across four agents, were denied as mail sends; none
// of them was one (no recipient flag, no sender, no mail program). Each was a
// script run from a variable path with a variable argument:
// `python3 "$SP/gen.py" "$SP/out.json"`, `python3 "$L" /dev/null "$SP/o.txt"`,
// `python3 $A/check.py $S/x.json && python3 $A/stamp.py --note-file "$A/n.json"`.
// The agents lost turns on rewrites and workarounds. The extra `$` rule also
// guarded nothing the rest of the gate guards: a literal sender path with a
// variable argument (`python3 scripts/support-mail/send.py $ARGS`) has always
// passed, because a recipient flag inside a variable is not visible statically
// on ANY branch. So this branch now asks what the send.py and the stdin
// branches ask: is there a recipient flag. A variable path WITH one
// (`python3 $P --to ...`) is still a send; `python3 $SENDER $ARGS` passes,
// pinned as a documented limit in send-invocation-cases.json.
const PY_OPT_WITH_ARG = new Set(['-X', '-W', '--check-hash-based-pycs'])
const RECIPIENT_FLAG = /^--to?(=|$)/
const UNDECIDABLE_PATH = /[*?[{$]/
const STDIN_PATHS = new Set(['/dev/stdin', '/dev/fd/0', '/proc/self/fd/0'])

// FLEETTOFLAG1025 v4 (Merlin 34827; the python twin: _FLEET_EXEMPT_BLOCK / _known_agents / _fleet_to_exempt): no tokenizer reading
// of the recipient value any more. On an UNDECIDABLE program or script the recipient flag is a send, EXCEPT when every --to / --to=
// value is a KNOWN agent name (the install's agents/ directories and its MAIN_AGENT_ID) AND the WHOLE command line has no `@`, no `~`
// word other than a `~/` path and no --cc / --bcc. Every other shape is a send (fail-closed).
const FLEET_EXEMPT_BLOCK = /@|(?:^|[\s='"(])~(?!\/)|--b?cc\b/
const FLEET_TOKEN_BLOCK = /^(?:--(?:cc?|bc{0,2})(?:=|$)|~(?!\/))/ // on the DEQUOTED words: `--c""c`, `--\cc` are `--cc` to the program;
// (v4.1, Merlin 34962) an argparse abbreviation (--c, --b, --bc) is --cc / --bcc to a default-argparse script: a send
const AGENT_NAME = /^[a-z0-9][a-z0-9_-]*$/
let knownAgentsOverride = null // the shared conformance list sets it (send-invocation-cases.json known_agents); null = the install
let lineExemptOk = false // set per top-level isSendInvocation call from the WHOLE command line
export function setKnownAgentsOverride(list) { knownAgentsOverride = list == null ? null : new Set(list) }
const tok = (t) => t.replace(/^\n+|\n+$/g, '')
const recipientFlag = (t) => RECIPIENT_FLAG.test(tok(t))
// the agent names of THIS install: <root>/agents/* directories and MAIN_AGENT_ID in <root>/.env (root = one level above this
// file). Any read error = an empty set, i.e. no exception (fail-closed).
function knownAgents() {
  if (knownAgentsOverride !== null) return knownAgentsOverride
  const root = join(dirname(fileURLToPath(import.meta.url)), '..')
  const names = new Set()
  try {
    const d = join(root, 'agents')
    for (const e of readdirSync(d, { withFileTypes: true })) if (e.isDirectory() && AGENT_NAME.test(e.name)) names.add(e.name)
  } catch { /* no agents/ directory: only the main agent can be known */ }
  try {
    const m = readFileSync(join(root, '.env'), 'utf-8').match(/^\s*MAIN_AGENT_ID\s*=\s*['"]?([^'"\s#]+)/m)
    if (m && AGENT_NAME.test(m[1])) names.add(m[1])
  } catch { /* no .env: the main agent is not known (fail-closed) */ }
  return names
}
// FLEETTOFLAG1025 v4 (Codex 0dd77c650df7): no address marker in the line -- neither in the raw text nor in any DEQUOTED word of any
// segment, the substitution-flattened form included. An unparseable line is not clean (fail-closed).
function lineClean(cmd) {
  if (FLEET_EXEMPT_BLOCK.test(cmd)) return false
  let words
  try {
    words = [cmd, flattenSubstitutions(cmd)].flatMap((c) => segmentsTokens(c).flat())
  } catch { return false }
  return !words.some((w) => w.includes('@') || FLEET_TOKEN_BLOCK.test(tok(w)))
}
// true (not a letter) only if the whole line passed the address check and every --to / --t value -- the very next token, or the
// part after `=` -- is a known agent name. A missing or empty value is not a name.
function fleetToExempt(rest) {
  if (!lineExemptOk) return false
  const vals = []
  for (let i = 0; i < rest.length; i++) {
    const t = tok(rest[i])
    if (t === '--to' || t === '--t') vals.push(i + 1 < rest.length ? tok(rest[i + 1]) : '')
    else if (t.startsWith('--to=') || t.startsWith('--t=')) vals.push(t.slice(t.indexOf('=') + 1))
  }
  if (!vals.length) return false
  const known = knownAgents()
  return vals.every((v) => known.has(v))
}
// an undecidable program / script with a recipient flag is a send unless the fleet-agent exception holds
const undecidableRecipientSends = (rest) => rest.some((t) => recipientFlag(t)) && !fleetToExempt(rest)
function pythonScriptOperand(rest) {
  let stdinRedirect = false
  for (let i = 0; i < rest.length; i++) {
    const t = rest[i]
    if (t === '--') return { script: rest[i + 1] ?? null, stdin: false, stdinRedirect }
    if (t === '-') return { script: null, stdin: true, stdinRedirect }
    if (/^-[cm]/.test(t)) return { script: null, stdin: false, stdinRedirect } // code string / module: judged elsewhere
    if (PY_OPT_WITH_ARG.has(t)) { i++; continue }
    const r = /^(\d*)(<<<|<<-|<<|<>|<&|<)([\s\S]*)$/.exec(t)
    if (r) { if (r[1] === '' || r[1] === '0') stdinRedirect = true; if (!r[3]) i++; continue } // another fd is not stdin; the word after a bare redirect
    if (/^(\d*|&)>/.test(t)) { if (/^(\d*|&)(>>?|>&|>\|)$/.test(t)) i++; continue }
    if (t.startsWith('-') && t.length > 1) continue
    if (STDIN_PATHS.has(t)) return { script: null, stdin: true, stdinRedirect } // the program comes from stdin, as with `-`
    return { script: t, stdin: false, stdinRedirect }
  }
  return { script: null, stdin: false, stdinRedirect }
}
// A command or process substitution as a word of the command (`$(...)`, a
// backtick pair, `<(...)`, `>(...)`), with the substitution replaced by one
// placeholder word that carries a `$`. maskSubshellMarkers splits these into
// their own segments, which is right for judging the INNER command, but it
// leaves the outer one without the word: `python3 $(echo .../send.py) --to x`
// became `python3` and `--to x`. Judged on this form as well, the word is an
// undecidable script path (or interpreter) like a variable. Quoted text is left
// alone: a quoted `"$(...)"` already reaches the tokenizer as a `$` word.
export function flattenSubstitutions(cmd) {
  let out = ''
  let q = null
  for (let i = 0; i < cmd.length; i++) {
    const ch = cmd[i]
    if (q) {
      if (ch === '\\' && q === '"' && i + 1 < cmd.length) { out += ch + cmd[++i]; continue }
      if (ch === q) q = null
      out += ch
      continue
    }
    if (ch === "'" || ch === '"') { q = ch; out += ch; continue }
    if (ch === '\\' && i + 1 < cmd.length) { out += ch + cmd[++i]; continue }
    const opens = (ch === '$' || ch === '<' || ch === '>') && cmd[i + 1] === '('
    if (opens || ch === '`') {
      let depth = 1
      let j = i + (opens ? 2 : 1)
      let iq = null
      for (; j < cmd.length && depth > 0; j++) {
        const c = cmd[j]
        if (iq) { if (c === iq) iq = null; continue }
        if (c === "'" || c === '"') { iq = c; continue }
        if (ch === '`') { if (c === '`') depth = 0; continue }
        if (c === '(') depth++
        else if (c === ')') depth--
      }
      out += ' $__SUBST__ '
      i = j - 1
      continue
    }
    out += ch
  }
  return out
}

// The command with every unquoted `name[...]` subscript as part of one word: the
// separators inside it become `_`. In command position bash reads `a[1;2]=x` up to
// the matching ] as one word and runs what follows (#725 review 3); elsewhere it
// splits, so this is an extra reading next to the plain one. See join_subscripts in
// the python twin (an unclosed `[` joins nothing; one pass pairs the brackets).
const SUBSCRIPT_START = /[A-Za-z_][A-Za-z_0-9]*\[/y
export function joinSubscripts(cmd) {
  const n = cmd.length
  const plain = new Array(n).fill(false) // unquoted and not escaped
  const close = new Map()
  const stack = []
  let q = null
  for (let i = 0; i < n; i++) {
    const ch = cmd[i]
    if (q) {
      if (ch === '\\' && q === '"') i++
      else if (ch === q) q = null
    } else if (ch === "'" || ch === '"') q = ch
    else if (ch === '\\') i++
    else {
      plain[i] = true
      if (ch === '[') stack.push(i)
      else if (ch === ']' && stack.length) close.set(stack.pop(), i)
    }
  }
  const out = cmd.split('')
  for (let i = 0; i < n; i++) {
    if (!plain[i] || (i > 0 && !(/\s/.test(cmd[i - 1]) || ';&|(`'.includes(cmd[i - 1])))) continue
    SUBSCRIPT_START.lastIndex = i
    const m = SUBSCRIPT_START.exec(cmd)
    if (m === null) continue
    const open = i + m[0].length - 1
    const end = close.get(open)
    if (end === undefined) { i = open; continue }
    for (let k = open + 1; k < end; k++) {
      if (plain[k] && ';|&()\n`'.includes(cmd[k])) out[k] = '_'
    }
    i = end
  }
  return out.join('')
}

// #718 (aaf838d6): the script file the interpreter runs is read and judged by the
// provider-API send patterns only (codeApiSends; why, where the file is looked up and
// the stated limits: _read_script in hooks/outgoing-copy-gate.py). A relative path
// resolves against the payload's cwd, moved by an earlier cd/pushd segment; a script
// written by a heredoc of the same command is judged by that body. FAIL-CLOSED: a
// script that cannot be read counts as a send. base null: the directory is unknown.
// Round 3 (Marveen's review 2805): a program fed on stdin from a file (`python3 - < x.py`,
// `cat x.py | python3`), a here-string, `python3 -m mod` from the base directory and a
// `.py` path run directly are read too; the stated limits are at _read_script.
// Round 4 (review 2814): a leading redirection (`< x.py python3`) counts, and a pipe
// other than a direct `cat` (head, tee) is a stated limit.
const PY_SCRIPT_READ_LIMIT = 2 * 1024 * 1024 // #718 reader; SCRIPT_READ_LIMIT below is SCRIPTSENDGATE1020's
// False only inside unreadableScriptHit: is an unreadable script the whole reason?
let unreadableSends = true
const expandHome = (p) => (p === '~' ? homedir() : p.startsWith('~/') ? join(homedir(), p.slice(2)) : p)
function readScript(script, base) {
  let path = expandHome(script)
  if (!isAbsolute(path)) {
    if (base === null) return null
    path = join(base, path)
  }
  try {
    const st = statSync(path)
    if (!st.isFile() || st.size > PY_SCRIPT_READ_LIMIT) return null
    return readFileSync(path, 'utf-8')
  } catch {
    return null
  }
}

// $NAME / ${NAME} set by an earlier assignment-only segment of the same command
// (`S=/x; cd $S`); see _expand_vars / _assignments in hooks/outgoing-copy-gate.py.
const VAR_REF = /\$(?:\{([A-Za-z_][A-Za-z_0-9]*)\}|([A-Za-z_][A-Za-z_0-9]*))/g
const expandVars = (tok, env) => tok.replace(VAR_REF, (m, a, b) => env.get(a || b) ?? m)
function assignments(toks, env) {
  const body = toks[0] === 'export' ? toks.slice(1) : toks
  if (!body.length || !body.every((t) => ENV_ASSIGN.test(t))) return null
  const out = new Map()
  for (const t of body) {
    const eq = t.indexOf('=')
    const value = expandHome(expandVars(t.slice(eq + 1), env))
    if (!UNDECIDABLE_PATH.test(value)) out.set(t.slice(0, eq), value)
  }
  return out
}

// [isCd, new base] for a cd/pushd/popd head; the base is null when it cannot be followed.
function cdTarget(head, base, env) {
  const w = head && head.length ? basename(head[0]) : ''
  if (w !== 'cd' && w !== 'pushd' && w !== 'popd') return [false, base]
  if (w === 'popd') return [true, null]
  const args = head.slice(1)
  while (args.length && args[0].startsWith('-') && args[0] !== '-') {
    if (args.shift() === '--') break
  }
  if (!args.length) return [true, homedir()]
  const arg = expandVars(args[0], env)
  if (arg === '-' || UNDECIDABLE_PATH.test(arg)) return [true, null]
  const target = expandHome(arg)
  if (isAbsolute(target)) return [true, normalize(target)]
  return [true, base === null ? null : join(base, target)]
}

// ['file', path] for a `<` redirect, ['code', text] for a here-string, else null.
function pythonStdin(rest) {
  for (let i = 0; i < rest.length; i++) {
    const t = rest[i].startsWith('0<') ? rest[i].slice(1) : rest[i] // fd 0 is stdin: `0< f` is `< f`
    if (t.startsWith('<<<')) return ['code', t.slice(3) || (rest[i + 1] ?? '')]
    if (t === '<<' || t === '<<-') { i++; continue }
    if (t.startsWith('<<')) continue
    if (t.startsWith('<&')) { // an fd copy: 0 is stdin itself, - closes it, others cannot be followed
      const word = t.slice(2) || (rest[i + 1] ?? '')
      if (word === '-') return ['file', '/dev/null']
      if (word !== '0') return ['file', '']
      if (!t.slice(2)) i++
      continue
    }
    const u = t.startsWith('<>') ? '<' + t.slice(2) : t // opened read-write, still stdin
    if (u === '<') return i + 1 < rest.length ? ['file', rest[i + 1]] : null
    if (u.startsWith('<')) return ['file', u.slice(1)]
  }
  return null
}
// A here-string that is one `$(cat F)` / `$(< F)` is F's text; any other
// substitution in it fails closed (see _HERESTRING_CAT in the python twin).
const HERESTRING_CAT = /^\s*\$\(\s*(?:cat\s+(?:--\s+)?|<\s*)([^\s()$`]+)\s*\)\s*$/
const HERESTRING_SUBST = /\$\(|`|\$__SUBST__/
// Program text given inline (a here-string fed to cat), not a file name.
const isCode = (f) => typeof f === 'object' && f !== null
const stdinBody = (path, base) => (isCode(path) ? path.code : path === '/dev/null' ? '' : readScript(path, base))

// null when no local module file runs (`python3 -m mod`), else whether it sends; pythonModule
// parses the flag cluster (`-um mod`, `-BImmod`, MAILGATEDIRZIP1027).
function pythonModuleSends(rest, base, env) {
  let mod = pythonModule(rest)
  if (!mod || base === null) return null
  mod = expandVars(mod, env)
  if (UNDECIDABLE_PATH.test(mod)) return null
  const rel = mod.replaceAll('.', '/')
  for (const cand of [`${rel}.py`, `${rel}/__main__.py`]) {
    const path = join(base, cand)
    let exists = true
    try { lstatSync(path) } catch { exists = false }
    if (exists) {
      const body = readScript(path, base)
      return body === null ? unreadableSends : scriptSends(body, path)
    }
  }
  return null
}

// MAILGATEDIRZIP1027: python runs DIR/__main__.py; a directory without one runs nothing ('').
function readPyScript(script, base) {
  let path = expandHome(script)
  if (!isAbsolute(path)) {
    if (base === null) return null
    path = join(base, path)
  }
  let dir = false
  try { dir = statSync(path).isDirectory() } catch { return null }
  if (!dir) return readScript(path, base)
  const main = join(path, '__main__.py')
  try { lstatSync(main) } catch { return '' }
  return readScript(main, base)
}

// True when the program comes from -c or -m: stdin is then data, not code.
function runsCodeArg(rest) {
  for (let i = 0; i < rest.length; i++) {
    const t = rest[i]
    if (/^-[cm]/.test(t)) return true
    if (t === '-' || t === '--' || !t.startsWith('-')) return false
    if (PY_OPT_WITH_ARG.has(t)) i++
  }
  return false
}

function pythonHeadSends(rest, base, written, env, stdinFiles = null) {
  const recipient = rest.some((t) => recipientFlag(t))
  const operand = pythonScriptOperand(rest)
  const { stdin, stdinRedirect } = operand
  let { script } = operand
  if (script !== null && !written.has(script)) script = expandVars(script, env)
  if (script !== null && SENDPY.test(basename(script))) return recipient
  if (script !== null && UNDECIDABLE_PATH.test(script)) return undecidableRecipientSends(rest) // MAILGATEVARARGS1020, FLEETTOFLAG1025 v4
  if (script === null) {
    const module = pythonModuleSends(rest, base, env)
    if (module !== null) return recipient || module
  }
  if (stdin || (script === null && !runsCodeArg(rest) && (stdinRedirect || stdinFiles))) {
    let src = pythonStdin(rest)
    if (src && src[0] === 'code') {
      const m = HERESTRING_CAT.exec(src[1])
      if (m) src = ['file', m[1]]
      else if (HERESTRING_SUBST.test(src[1])) return recipient || unreadableSends
      else return recipient || codeStringSends(src[1])
    }
    const files = src ? [expandVars(src[1], env)] : (stdinFiles ?? [])
    const bodies = files.map((f) => (!isCode(f) && written.has(f) ? written.get(f) : stdinBody(f, base)))
    if (bodies.some((b) => b === null)) return recipient || unreadableSends
    return recipient || bodies.some((b, k) => (isCode(files[k]) ? codeStringSends(b) : scriptSends(b, files[k])))
  }
  if (script !== null) {
    if (written.has(script)) return scriptSends(written.get(script), script)
    const body = readPyScript(script, base)
    return body === null ? unreadableSends : scriptSends(body, script)
  }
  return false
}

// Segments in order; a cd/pushd/popd head moves the base of the later ones, and an
// assignment-only segment is remembered for them. $HOME comes from the environment.
// The files `cat` prints: its operands and a `<` / `0<` stdin file; an output
// redirect is not one, a heredoc word stays (see _cat_operands in the python twin).
// A here-string is its text ({code}); `<&N` reads as unreadable; with file
// operands the stdin file is printed only for a `-` operand.
function catOperands(args) {
  const out = []
  let stdin = null
  for (let i = 0; i < args.length; i++) {
    const t = args[i]
    const m = /^(\d*|&)(<<<|<<-|<<|<>|<&|>&|>>|>\||<|>)([\s\S]*)$/.exec(t)
    if (m === null) {
      if (t === '-' || !t.startsWith('-')) out.push(t)
      continue
    }
    const word = m[3] || (args[i + 1] ?? '')
    if (!m[3]) i++
    if (m[1] !== '' && m[1] !== '0') continue
    if (m[2] === '<' || m[2] === '<>') stdin = [word]
    else if (m[2] === '<<<') stdin = [{ code: word }]
    else if (m[2] === '<&') stdin = word === '0' || word === '-' ? [] : ['']
    else if (!m[2].startsWith('>')) { out.push(t); i -= m[3] ? 0 : 1 } // a heredoc word stays (unreadable)
  }
  if (stdin === null) return out.filter((t) => t !== '-')
  return out.length ? out.flatMap((t) => (t === '-' ? stdin : [t])) : stdin
}

// The files a `cat` segment prints are the stdin of the segment piped after it.
function segmentsSend(segments, depth, base, written) {
  const env = new Map([['HOME', homedir()]])
  let catFiles = null
  // The files of each `cat` inside a compound command are the stdin of the segment
  // piped after its closer (`{ cat; } < f | python3`, review 2866). One list per open group.
  const groups = []
  const count = (s, ch) => s.split(ch).length - 1
  for (const toks of segments) {
    const op = toks.op || ''
    const closes = count(op, ')') + leadRun(toks, GROUP_CLOSE)
    let groupCats = []
    for (let k = 0; k < closes; k++) if (groups.length) groupCats = groupCats.concat(groups.pop())
    if (groups.length) groups[groups.length - 1].push(...groupCats)
    const opens = count(op, '(') + leadRun(toks, GROUP_OPEN)
    for (let k = 0; k < opens; k++) groups.push([])
    const assigned = assignments(toks, env)
    if (assigned) {
      for (const [k, v] of assigned) env.set(k, v)
      catFiles = null
      continue
    }
    const stdinFiles = toks.piped ? catFiles : null
    const heads = commandHeads(toks)
    if (heads.some((h) => headIsSend(h, depth, base, written, env, stdinFiles))) return true
    catFiles = groupCats.length ? groupCats : null
    for (const h of heads) {
      if (h && h.length && basename(h[0]) === 'cat') {
        const files = catOperands(h.slice(1)).map((t) => (isCode(t) ? t : expandVars(t, env)))
        catFiles = files.length ? files : null
        if (groups.length && catFiles) groups[groups.length - 1].push(...catFiles)
      }
    }
    for (const h of heads) {
      const [isCd, next] = cdTarget(h, base, env)
      if (isCd) { base = next; break }
    }
  }
  return false
}

function headIsSend(toks, depth, base, written, env, stdinFiles = null) {
  if (toks === null) return true // the depth bound was hit on a wrapper: audit it
  if (!toks.length) return false
  const prog = basename(toks[0])
  const rest = toks.slice(1)
  if (SENDER_PROG.test(prog)) return true
  if (MAIL_PROG.test(prog) && mailProgSends(rest)) return true
  // MAILGATEAPIB1027: curl speaks SMTP too (an smtp:// or smtps:// URL, --mail-rcpt, --mail-from)
  if (CURLISH.test(prog) && rest.some((t) => CURL_SMTP.test(t))) return true
  // The shell's own stdin redirect (`bash -c python3 < f`) feeds the string's
  // commands: judged as `{ STRING; } < f` (review 2823).
  if (WRAPPER_SHELL.test(prog) && depth < 3) {
    for (let i = 0; i < rest.length; i++) {
      if (rest[i] !== '-c' || !rest[i + 1]) continue
      const redirs = stdinRedirects([...rest.slice(0, i + 1), ...rest.slice(i + 2)])
      const code = redirs.length
        ? `{ ${rest[i + 1]}\n} ${redirs.map(([op, w]) => `${op} '${w.replaceAll("'", "'\\''")}'`).join(' ')}`
        : rest[i + 1]
      if (isSend(code, depth + 1, base)) return true
    }
  }
  if (PYTHON.test(prog) || NODEISH.test(prog)) {
    for (let i = 0; i < rest.length; i++) {
      if ((rest[i] === '-c' || rest[i] === '-e' || rest[i] === '--eval') &&
          rest[i + 1] && codeStringSends(rest[i + 1], langOf(prog))) return true
    }
  }
  const candidates = [prog]
  if ((PYTHON.test(prog) || NODEISH.test(prog)) && rest.length) candidates.push(basename(rest[0]))
  // Issue #1853: `python3 -m send` (or -m pkg.send) is the same sender by module name.
  if (PYTHON.test(prog)) {
    for (let i = 0; i < rest.length - 1; i++) {
      if (rest[i] === '-m' && rest[i + 1].split('.').pop() === 'send') candidates.push('send.py')
    }
  }
  if (candidates.some((c) => SENDPY.test(c)) && rest.some((t) => recipientFlag(t))) return true
  if (PYTHON.test(prog) && pythonHeadSends(rest, base, written, env, stdinFiles)) return true
  // a `.py` path run directly (`./x.py`; its shebang names the interpreter). Only a
  // file that can be read counts: one that cannot be read cannot be executed either,
  // and a bare `.py` word is also what a process substitution leaves behind
  // (`diff <(sed ...) x.py`).
  if (prog.endsWith('.py') && toks[0].includes('/')) {
    const path = written.has(toks[0]) ? toks[0] : expandVars(toks[0], env)
    const body = written.has(path) ? written.get(path) : readScript(path, base)
    if (body !== null && scriptSends(body, path)) return true
  }
  // An interpreter or program that cannot be resolved statically ($PY, a glob,
  // a substitution) with a recipient flag: what runs is undecidable here.
  // FLEETTOFLAG1025: the address rule -- but a send.py run by it (`$PY scripts/support-mail/send.py --to x`) keeps the bare flag rule
  const scriptTok = pythonScriptOperand(rest).script // the script position (interpreter options, redirections skipped), not a data argument
  if (UNDECIDABLE_PATH.test(toks[0]) && (undecidableRecipientSends(rest)
      || (scriptTok != null && SENDPY.test(basename(scriptTok)) && rest.some((t) => recipientFlag(t))))) return true
  if (toks.some((t) => GRAPHMAIL.test(basename(t))) && rest.includes('send')) return true
  // RESENDGETTWIN1010: only an actual send (POST/PUT/..., or a body) fires; a
  // read-only GET/HEAD passes; an undecidable method stays fail-closed.
  if (CURLISH.test(prog) && rest.some((t) => RESEND_TARGET.test(t))) {
    return curlResendVerdict(prog, rest) !== 'read'
  }
  return false
}

// True when some segment is still a wrapper at the depth bound (HEADDEPTH924),
// so the deny says WHY -- see wrapper_depth_hit in outgoing-copy-gate.py.
// #718: true when an unreadable script is the whole reason the command counts as a
// send; see unreadable_script_hit in hooks/outgoing-copy-gate.py.
export function unreadableScriptHit(cmd, cwd = null) {
  if (!isSendInvocation(cmd, 0, cwd)) return false
  unreadableSends = false
  try {
    return !isSendInvocation(cmd, 0, cwd)
  } finally {
    unreadableSends = true
  }
}

export function buildUnreadableScriptMsg() {
  return (
    'TILTVA (governance hard-gate): a futtatott python-szkriptet nem tudom elolvasni (nincs meg, ' +
    'nem olvashato, nem sima fajl, 2 MiB-nal nagyobb, vagy a mappaja nem kovetheto), igy nem tudom ' +
    'eldonteni, hogy levelet kuld-e. Ez szandekosan fail-closed. Ha elgepelted az utvonalat, javitsd. ' +
    'A relativ utvonal a munkamappadhoz (es a parancs korabbi cd-jehez) kepest ertendo: adj abszolut ' +
    'utvonalat, ha bizonytalan.'
  )
}

export function wrapperDepthHit(cmd) {
  let segments
  try {
    segments = segmentsTokens(cmd)
  } catch {
    return false
  }
  // Only when the bound is the WHOLE reason: a visible send in another segment wins.
  const heads = segments.flatMap((toks) => commandHeads(toks))
  const base = process.cwd()
  const written = heredocWritten(cmd)
  return heads.some((h) => h === null) && !heads.some((h) => h !== null && headIsSend(h, 0, base, written, new Map()))
}

export function buildWrapperDepthMsg() {
  return (
    'TILTVA (governance hard-gate): a parancs valodi fejet nem latom. ' +
    `A parancs a burkolo-korlatnal (${HEAD_DEPTH} egymasba agyazott burkolo: sudo, time, env, nohup, nice, timeout...) ` +
    'is meg burkolo, tehat nem tudom eldonteni, hogy levelkuldes-e, es a kapu ilyenkor fail-closed. ' +
    `Ha ez NEM levelkuldes: csokkentsd a burkolok szamat ${HEAD_DEPTH} vagy kevesebb ala. ` +
    'Ha levelkuldes: sub-agentkent Bash-bol amugy sem kuldhetsz, a kimeno emailt a fo-agens kuldi.'
  )
}

// Issue #1853, the heredoc form: the segmenter drops heredoc BODIES, but when the
// command that opens one is an interpreter (`python3 - <<'PY'`, `node <<EOF`) the body
// IS the program. Mirrors _heredoc_program_sends in hooks/outgoing-copy-gate.py.
// NARROWED (Geri's #1855 review): inside a heredoc body the exec + sender-literal half takes
// the literal only as a lowercase, QUOTED command word (an argv item or an os.system command),
// so a card id like SENDMAIL... in a report's prose no longer matches. Mirrors
// _HEREDOC_SENDER_ARGV / _heredoc_body_sends in hooks/outgoing-copy-gate.py.
// NOT COVERED, named: `python3 -c "exec(open('/tmp/x.py').read())"` and a CommonJS
// destructuring rename of the Graph sender (`const { sendMail: s } = require(...)`). (A script
// run by python, fed on stdin or piped from cat, and a here-string are read since #718.)
// A heredoc fed to a SHELL (`bash <<EOF`) is a shell program: its body is judged with
// isSendInvocation itself, one level deeper (Geri/Samu, #1855).
// WRITTEN, THEN RUN IN THE SAME COMMAND (#1855): `cat > P <<TAG` / `cat <<TAG > P`, then an
// interpreter or a shell on the same path token P: the body is judged as if fed directly.
// Exact token match; a file run by ANOTHER Bash call stays out of reach.
// PIPED (#1855): `cat <<'EOF' | bash` or `| python3 -` hands the body over the pipe on the
// heredoc's opening line; it is judged the same way.
const HEREDOC_SENDER_ARGV = /['"](?:[^'"\s]*\/)?(?:sendmail|msmtp|swaks|send\.py)(?=['"\s])/
// The provider-API patterns too, as for -c and a here-string (review 2866); prose
// literals masked first (MAILGATEHEREDOCFP1027).
const heredocBodySends = (body, lang = null) => {
  const masked = maskProseLiterals(body, lang)
  return CODE_SEND.test(masked) || CODE_SEND_MODULE.test(masked) || codeApiSends(masked) || webMailSends(masked) ||
    (CODE_EXECISH.test(masked) && HEREDOC_SENDER_ARGV.test(body))
}
const HEREDOC_SHELL = /(?:^|[\s;&|(])(?:\S*\/)?(?:bash|sh|zsh|dash)\b[^\n<]*$/i
const HEREDOC_INTERP = /(?:^|[\s;&|(])(?:\S*\/)?(python3?(?:\.\d+)?|node|tsx|ts-node|deno|bun)\b[^\n<]*$/i
const HEREDOC_PIPE = /\|\s*(?:\S*\/)?(python3?(?:\.\d+)?|node|tsx|ts-node|deno|bun|bash|sh|zsh|dash)\b/i
// A leading heredoc (`<<'X' python3`, `0<<X bash`): nothing but an fd number stands before
// the operator, and the program follows the tag on the opening line (review 2866 probe).
const HEREDOC_LEAD = /(?:^|[;&|(])\s*\d*\s*$/
const HEREDOC_OPENER_TAIL = /<<-?\s*(['"]?)[^\s'"<>;&|()]+\1(.*)/
const HEREDOC_REDIRECT = /(?:^|[\s;&|(])(?:cat|tee)\b[^\n]*?(?:>>?|\btee(?:\s+-a)?)\s*(['"]?)([^\s'"<>;&|]+)\1/
function writtenThenRun(cmd, written, depth, base) {
  if (!written.size) return false
  let segments
  try { segments = segmentsTokens(cmd) } catch { return false }
  for (const toks of segments) {
    for (const head of commandHeads(toks)) {
      if (!head || !head.length) continue
      const prog = basename(head[0])
      let args = head.slice(1).filter((t) => !t.startsWith('-'))
      if (prog.toLowerCase() === 'npx' && args.length) args = args.slice(1) // npx <runner> <file>
      if (!args.length || !written.has(args[0])) continue
      const body = written.get(args[0])
      if ((PYTHON.test(prog) || NODEISH.test(prog)) && heredocBodySends(body, langOf(prog))) return true
      if (WRAPPER_SHELL.test(prog) && depth < 3 && isSend(body, depth + 1, base)) return true
    }
  }
  return false
}
// path token -> body, for every heredoc the command writes to a file.
function heredocWritten(cmd) {
  const written = new Map()
  for (const m of cmd.matchAll(HEREDOC_RE)) {
    const { open, body } = heredoc(m)
    const opener = cmd.slice(cmd.lastIndexOf('\n', m.index - 1) + 1, m.index) + open
    const w = HEREDOC_REDIRECT.exec(opener)
    if (w) written.set(w[2], body)
  }
  return written
}
function heredocProgramSends(cmd, depth, base, written) {
  for (const m of cmd.matchAll(HEREDOC_RE)) {
    const { open, body } = heredoc(m)
    const piped = HEREDOC_PIPE.exec(open)
    if (piped) {
      const pbody = body
      if (WRAPPER_SHELL.test(piped[1])) {
        if (depth < 3 && isSend(pbody, depth + 1, base)) return true
      } else if (heredocBodySends(pbody, langOf(piped[1]))) return true
    }
    const lineStart = cmd.lastIndexOf('\n', m.index - 1) + 1
    let head = cmd.slice(lineStart, m.index)
    if (HEREDOC_LEAD.test(head)) head = HEREDOC_OPENER_TAIL.exec(open)[2]
    const interp = HEREDOC_INTERP.exec(head)
    if (interp) {
      if (heredocBodySends(body, langOf(interp[1]))) return true
    } else if (HEREDOC_SHELL.test(head) && depth < 3) {
      if (isSend(body, depth + 1, base)) return true
    }
  }
  return writtenThenRun(cmd, written, depth, base)
}

// #725 coarse rule (Marveen msg 14989, GO 14995): a send-capable token ANYWHERE in the
// text AND a construct this parser has repeatedly got wrong (heredoc of any kind,
// backslash-newline, an extglob paren, [[, case, a #, a $( ) or backtick substitution spanning
// a line) is not decided: it counts as a send. Thirteen review rounds found a hole every
// time in this family; on ambiguity the gate stops guessing. Deliberately a text match,
// not a parse. Mirrors ambiguous_send_text in hooks/outgoing-copy-gate.py, bound by
// send-invocation-cases.json. send.py only as a whole file name (review 3034): seeme-send.py,
// telegram_fallback_send.py and check-before-send.py are not the mailer.
const AMBIGUOUS_LITERALS = /(?<![\w.-])send\.py\b|api\.resend\.com|\bsendmail\b|\bmsmtp\b|\bswaks\b|\bsmtplib\b|\bsendMail\s*\(/i
const AMBIGUOUS_CONSTRUCTS = [
  /(?<!<)<<(?!<)/, // heredoc, not a here-string
  /\\\n/, // backslash-newline
  /[@?*+!]\(/, // extglob-like paren
  /\[\[/,
  /(?:^|[\s;&|(])case\s/,
  /#/, // comment rules (reviews 2942, 2945)
]

function multilineSubstitution(cmd) {
  let depth = 0
  let start = 0
  for (let i = 0; i < cmd.length; i++) {
    if (cmd.startsWith('$(', i) && !cmd.startsWith('$((', i)) {
      if (depth === 0) start = i
      depth++
    } else if (cmd[i] === ')' && depth) {
      depth--
      if (depth === 0 && cmd.slice(start, i).includes('\n')) return true
    }
  }
  const ticks = [...cmd.matchAll(/(?<!\\)`/g)].map((m) => m.index)
  for (let k = 0; k + 1 < ticks.length; k += 2) {
    if (cmd.slice(ticks[k], ticks[k + 1]).includes('\n')) return true
  }
  return false
}

export function ambiguousSendText(cmd) {
  if (!(AMBIGUOUS_LITERALS.test(cmd) || CODE_SEND.test(cmd) || CODE_SEND_MODULE.test(cmd) ||
        codeApiSends(cmd) || /\bgraph-mail\b/i.test(cmd))) return false
  return AMBIGUOUS_CONSTRUCTS.some((re) => re.test(cmd)) || multilineSubstitution(cmd)
}

// True when the coarse rule is the whole reason the command counts as a send.
export function ambiguousSendHit(cmd, cwd = null) {
  return ambiguousSendText(cmd) && !isSend(cmd, 0, cwd || process.cwd())
}

export function buildAmbiguousSendMsg() {
  return (
    'TILTVA (governance hard-gate): a parancs levelkuldesre utalo szot (sendmail, smtplib, send.py, ' +
    'levelezo API...) ES nehezen elemezheto szerkezetet (heredoc, sor vegi backslash, [[, case, extglob, #, ' +
    'tobbsoros $( ) vagy backtick) tartalmaz egyszerre. Ilyenkor a kapu nem probalja eldonteni, hogy ' +
    'kuldes-e: kuldeskent kezeli (#725, Marveen msg 14989). Ha ez NEM kuldes (kartya-, uzenet- vagy ' +
    'jegyzet-szoveg, teszt-fixture): a szoveget a Write toollal ird fajlba, es a parancs a fajlra ' +
    'hivatkozzon; a Write-ot ez a kapu nem nezi. Ha kuldes: sub-agentkent Bash-bol amugy sem kuldhetsz, ' +
    'a kimeno emailt a fo-agens kuldi.'
  )
}

// cwd: the directory the command runs in (the hook payload's cwd); a relative script
// path resolves against it. Null: this process's cwd.
export function isSendInvocation(cmd, depth = 0, cwd = null) {
  if (depth === 0 && ambiguousSendText(cmd)) return true
  return isSend(cmd, depth, cwd || process.cwd())
}

function isSend(cmd, depth, base) {
  // FLEETTOFLAG1025 v4: the fleet-agent exception is decided on the WHOLE line; a nested body (depth > 0) can only tighten it.
  // A module-level flag is enough: the hook is one process per tool call, and the evaluation is single-threaded.
  lineExemptOk = (depth === 0 || lineExemptOk) && lineClean(cmd)
  const written = heredocWritten(cmd)
  if (heredocProgramSends(cmd, depth, base, written)) return true
  let segments
  try {
    segments = segmentsTokens(cmd)
  } catch {
    // Unparseable command: fall back to the LEGACY content patterns, so on
    // this path the gate is exactly as strict as before -- never weaker.
    return SEND_PATTERNS.some((re) => re.test(cmd)) || linesSend(cmd, depth, base, written)
  }
  if (segmentsSend(segments, depth, base, written)) return true
  // The same command with every substitution as one undecidable word (see
  // flattenSubstitutions): the outer command keeps the word it would run; and with
  // every subscript kept as one word (joinSubscripts).
  for (const alt of [flattenSubstitutions(cmd), joinSubscripts(cmd)]) {
    if (alt === cmd) continue
    try {
      if (segmentsSend(segmentsTokens(alt), depth, base, written)) return true
    } catch {
      return SEND_PATTERNS.some((re) => re.test(cmd)) || linesSend(cmd, depth, base, written)
    }
  }
  return false
}

// `bash -c` reads a line at a time: a syntax error throws away its line and the
// next line still runs, so an unparseable command is also judged line by line;
// see _lines_send in the python twin (#729). Heredoc bodies are input, not lines.
function linesSend(cmd, depth, base, written) {
  for (const line of cmd.replace(HEREDOC_RE, (_, a, _b, _c, _d, e) => a ?? e).split('\n')) {
    try {
      if (line.trim() && segmentsSend(segmentsTokens(line), depth, base, written)) return true
    } catch {
      continue
    }
  }
  return false
}

// SCRIPTSENDGATE1020: a sender script under any name. isSendInvocation knows a
// Python sender only by the file name send.py, so `python3 helden_send.py --to
// x@y.hu < body.txt` -- a self-written SMTP script, the usual way a mail goes
// out on this fleet -- was no send for this gate (measured 2026-10-20). Cheap,
// local signals close it for the sub-agents. A run is a send when:
//   1. an interpreter (python, node, tsx, bun, deno) or a directly executed
//      script is given an e-mail address after --to / --cc / --bcc. This holds
//      with no script file too (`python3 -m pkg`, `python3 -c ...`);
//   2. the script file IMPORTS a mail library: an import statement of the
//      python SMTP modules (smtplib, aiosmtplib, yagmail), or a require /
//      import of nodemailer for a node script. A bare mention (a comment, a
//      mock, a regex like this file's own) is no import and passes;
//   3. one level of local helper modules imported by the script, resolved
//      next to it, imports one (`from mailhelper import send_mail`);
//   4. a shell `-c` string (sh, bash, zsh, dash) runs one of the above.
// A directly executed file (`./x`, `/abs/x`, `x.py`) is read as python or node
// by its #! line, else by its extension; an extensionless file without a
// python/node #! line is not judged.
// Deliberately NOT part of isSendInvocation: that function is the shared
// contract with the main agent's python gates (send-invocation-cases.json),
// and on the main agent the owner's single approval in the chat is the rule
// (owner decision, 2026-10-20). This narrowing is for the sub-agent hard gate
// only, where every recognised Bash send is denied anyway.
// Not seen, on purpose (docs/security-hardening.md lists them): mail sent
// without one of these libraries (Gmail API client, an HTTP mail API, raw
// socket SMTP, perl/ruby), a script read from stdin or named through a
// variable, a script reached after `cd dir &&` (resolved against the session
// cwd), helpers past the first level, an import after the first 1 MiB, and
// code assembled at runtime.
const PY_MAIL_MODULE = String.raw`(?:(?:aio)?smtplib|yagmail)`
const PY_MAIL_IMPORT = new RegExp(
  String.raw`^[ \t]*(?:import[ \t]+[^\n#]*\b${PY_MAIL_MODULE}\b|from[ \t]+${PY_MAIL_MODULE}\b[^\n]*\bimport\b)` +
  String.raw`|\bimport_module\(\s*['"]${PY_MAIL_MODULE}['"]`,
  'm',
)
const NODE_MAIL_MODULE = String.raw`['"]nodemailer['"]`
const NODE_MAIL_IMPORT = new RegExp(
  String.raw`\brequire\s*\(\s*${NODE_MAIL_MODULE}\s*\)|^[ \t]*import\b[^\n]*${NODE_MAIL_MODULE}|\bimport\s*\(\s*${NODE_MAIL_MODULE}\s*\)`,
  'm',
)
// MAILGATESCRIPT1027 R2: groups: 1 = X and 2 = NAMES of `from X import NAMES`, 3 = NAMES of `import NAMES`
const PY_LOCAL_IMPORT = /^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import\b(?:[ \t]*\(?[ \t]*([\w., \t]+))?|import[ \t]+([\w., \t]+))/gm
// MAILGATESCRIPT1027 R1: `export ... from './x'` (an ESM re-export) loads the helper too
// MAILGATESCRIPT1027 R2: a type-only import or export loads nothing
const NODE_LOCAL_IMPORT = /(?:^[ \t]*export(?![ \t]+type[ \t]*(?:\{|\*|[A-Za-z_$][\w$]*[ \t]+from\b))\b[^\n]*?\bfrom\s*|\brequire\s*\(\s*|^[ \t]*import(?![ \t]+type[ \t]*(?:\{|\*|[A-Za-z_$][\w$]*[ \t]+from\b))\b[^\n]*?\bfrom\s*|^[ \t]*import\s*|\bimport\s*\(\s*)['"](\.{1,2}\/[^'"\n]+)['"]/gm
const NODE_EXTS = ['', '.js', '.mjs', '.cjs', '.ts', '/index.js', '/index.mjs', '/index.ts']
const HELPER_LIMIT = 40
const ADDRESS_FLAG = /^--(?:to|cc|bcc)(?:=(.*))?$/
const SCRIPT_FILE = /\.(?:py|mjs|cjs|js|ts)$/i
const SHELL_C_FLAG = /^-[a-zA-Z]*c[a-zA-Z]*$/
const SCRIPT_READ_LIMIT = 1024 * 1024
const SHEBANG_READ_LIMIT = 512
const SHELL_C_DEPTH = 3

function readScriptHead(path, limit = SCRIPT_READ_LIMIT) {
  let fd = null
  try {
    fd = openSync(path, 'r')
    const buf = Buffer.alloc(limit)
    const n = readSync(fd, buf, 0, limit, 0)
    return buf.subarray(0, n).toString('utf-8')
  } catch {
    return null
  } finally {
    if (fd !== null) {
      try { closeSync(fd) } catch { /* already closed */ }
    }
  }
}

function resolveScriptPath(script, cwd) {
  if (!script || UNDECIDABLE_PATH.test(script)) return null
  let p = script
  if (p === '~' || p.startsWith('~/')) p = join(homedir(), p.slice(1))
  if (isAbsolute(p)) return p
  return cwd ? resolve(cwd, p) : null
}

// node/tsx/bun/deno: the first non-option word is the script; -e/--eval code
// strings are isSendInvocation's to judge.
// MAILGATESCRIPT1027: `bun run x` / `deno run x` skip the run word; `npx tsx x` / `npx ts-node x`
// / `npx node x` name the script after the runner, any other npx package is no script.
function nodeScriptOperand(prog, rest) {
  const words = []
  for (const t of rest) {
    if (t === '--') continue
    if (t.startsWith('-')) return null
    words.push(t)
    if (words.length === 2) break
  }
  if (prog === 'npx') return /^(?:tsx|ts-node|node)$/.test(words[0] ?? '') ? (words[1] ?? null) : null
  if ((prog === 'bun' || prog === 'deno') && words[0] === 'run') return words[1] ?? null
  return words[0] ?? null
}

function addressFlagGiven(rest) {
  for (let i = 0; i < rest.length; i++) {
    const m = ADDRESS_FLAG.exec(rest[i])
    if (!m) continue
    const val = m[1] ?? rest[i + 1] ?? ''
    if (val.includes('@')) return true
  }
  return false
}

// 'py' | 'node' | null from a #! line (`#!/usr/bin/python3`, `#!/usr/bin/env -S node`).
function shebangKind(text) {
  const m = /^#!\s*(\S+)([^\n]*)/.exec(text ?? '')
  if (!m) return null
  let prog = basename(m[1])
  if (prog === 'env') prog = basename(m[2].trim().split(/\s+/).find((w) => w && !w.startsWith('-') && !w.includes('=')) ?? '')
  if (PYTHON.test(prog)) return 'py'
  if (NODEISH.test(prog) && prog !== 'npx') return 'node'
  return null
}

// One level of local helpers: modules the script imports, resolved next to it.
function localHelperPaths(path, text, kind) {
  // MAILGATESCRIPT1027 R1: next to the script and, for a symlinked entry, next to its real path
  const dirs = [dirname(path)]
  try {
    const real = dirname(realpathSync(path))
    if (!dirs.includes(real)) dirs.push(real)
  } catch { /* lexical directory only */ }
  // MAILGATESCRIPT1027 R2: the limit applies per directory, so a symlinked entry's real-path helpers are looked at
  const lists = []
  for (const dir of dirs) {
    const out = []
    if (kind === 'py') {
      for (const m of text.matchAll(PY_LOCAL_IMPORT)) {
        if (m[1] && m[1].startsWith('.')) {
          const rest = m[1].replace(/^\.+/, '')
          let base = dir
          for (let i = 0; i < m[1].length - rest.length - 1; i++) base = dirname(base)
          const rels = rest ? [rest.split('.').join('/')] : (m[2] ?? '').split(',').map((n) => n.trim().split(/\s+/)[0])
          for (const rel of rels) {
            if (rel) out.push(join(base, `${rel}.py`), join(base, rel, '__init__.py'))
          }
          continue
        }
        const names = m[1] ? [m[1]] : (m[3] || '').split(',').map((n) => n.trim().split(/\s+/)[0])
        for (const name of names) {
          if (!name || name.startsWith('.')) continue
          const rel = name.split('.').join('/')
          out.push(join(dir, `${rel}.py`), join(dir, rel, '__init__.py'))
        }
      }
    } else {
      for (const m of text.matchAll(NODE_LOCAL_IMPORT)) {
        for (const ext of NODE_EXTS) out.push(resolve(dir, m[1] + ext))
      }
    }
    lists.push([...new Set(out)].filter((p) => p !== path).slice(0, HELPER_LIMIT))
  }
  return [...new Set(lists.flat())]
}

// MAILGATEAPIB1027: a script file's (or helper's) text: the mail imports, the web mail APIs, and a CALL that runs a
// mail program (a process-starting call whose first argument, a string or a list's first item, starts with
// sendmail, msmtp or swaks; read on the raw text). See _FILE_EXEC_SENDER in hooks/outgoing-copy-gate.py for why
// not the looser code-string rule. Mirrors _text_sends there.
// Samu 50995 N1: the call is found on the MASKED text (a call shape quoted inside a prose literal is blanked there),
// its first argument is read from the RAW text at the same position. The masker keeps the length in code points,
// not in UTF-16 units, so the position is carried over in code points.
const FILE_EXEC_CALL = new RegExp(
  String.raw`(?:\bsubprocess\.(?:run|call|check_call|check_output|Popen)|\bos\.(?:system|popen)|\bexec(?:File)?(?:Sync)?|\bspawn(?:Sync)?)\s*\(`, 'g')
const FILE_EXEC_ARG = new RegExp(
  String.raw`^\s*\[?\s*['"` + '`' + String.raw`](?:[^'"` + '`' + String.raw`\s]*\/)?(?:sendmail|msmtp|swaks)(?=['"` + '`' + String.raw`\s])`)
function fileExecSends(raw, masked) {
  const rawCp = Array.from(raw)
  // not expected; the raw text is then judged alone, which errs toward a send
  if (rawCp.length !== Array.from(masked).length) masked = raw
  for (const m of masked.matchAll(FILE_EXEC_CALL)) {
    const at = Array.from(masked.slice(0, m.index + m[0].length)).length
    if (FILE_EXEC_ARG.test(rawCp.slice(at).join(''))) return true
  }
  return false
}
const textSends = (raw, masked, imports) =>
  imports.test(masked) || webMailSends(masked) || fileExecSends(raw, masked)
function fileSends(path, text, kind, readScript) {
  const imports = kind === 'py' ? PY_MAIL_IMPORT : NODE_MAIL_IMPORT
  const lang = kind === 'py' ? 'py' : 'js'
  if (textSends(text, maskProseLiterals(text, lang), imports)) return true
  // The helpers are found on the raw text: a helper named only in a docstring is read too, which errs toward a send.
  return localHelperPaths(path, text, kind).some((h) => {
    const t = readScript(h)
    return t !== null && textSends(t, maskProseLiterals(t, lang), imports)
  })
}

// python runs a directory's __main__.py, and a zip app's __main__.py from inside the archive
// (MAILGATEDIRZIP1027). The archive is not unpacked here, so a zip run by python is a send.
const ZIP_MAGIC = 'PK\u0003\u0004'
function scriptFileSends(path, kind, readScript) {
  let p = path
  let text = readScript(p)
  if (text === null && kind === 'py') {
    p = join(path, '__main__.py')
    text = readScript(p)
  }
  // node runs DIR/package.json "main", else DIR/index.js; a main that cannot be read as named is a send
  if (text === null && kind === 'node') {
    const pkg = readScript(join(path, 'package.json'))
    let main = null
    if (pkg !== null) {
      try { main = JSON.parse(pkg)?.main ?? null } catch { return true }
    }
    const declared = typeof main === 'string' && main !== ''
    p = declared ? resolve(path, main) : join(path, 'index.js')
    text = readScript(p)
    if (text === null && declared) return true
  }
  if (text === null) return false
  if (kind === 'py' && text.includes(ZIP_MAGIC)) return true
  return fileSends(p, text, kind, readScript)
}

// MAILGATEDIRZIP1027: MOD of `-m MOD` / `-mMOD` among the interpreter options, else null (the Python twin's
// _python_module). `python -m MOD` runs MOD.py or MOD/__main__.py under cwd; an installed module cannot be
// read here, so with no readable candidate it is not a send.
function pythonModule(rest) {
  for (let i = 0; i < rest.length; i++) {
    const t = rest[i]
    if (t === '-m') return rest[i + 1] ?? null
    if (/^-m[^-]/.test(t)) return t.slice(2)
    // -m at the end of a cluster of flags without a value (`-um MOD`, `-Im MOD`, `-BmMOD`)
    const cluster = /^-[bBdEhiIOPqRsSuvVx]+m(.*)$/.exec(t)
    if (cluster) return cluster[1] || (rest[i + 1] ?? null)
    if (PY_OPT_WITH_ARG.has(t)) { i++; continue }
    if (t.startsWith('-') && t !== '--' && !t.startsWith('-c')) continue
    return null
  }
  return null
}
function scriptRunModuleSends(module, cwd, readScript) {
  if (!/^[\w.]+$/.test(module) || !cwd) return false
  const rel = module.split('.').join('/')
  return [rel + '.py', join(rel, '__main__.py')].some((c) => scriptFileSends(resolve(cwd, c), 'py', readScript))
}

function headRunsSenderScript(toks, cwd, readScript, depth) {
  if (!toks || !toks.length) return false
  const prog = basename(toks[0])
  const rest = toks.slice(1)
  if (WRAPPER_SHELL.test(prog)) {
    if (depth >= SHELL_C_DEPTH) return false
    for (let i = 0; i < rest.length - 1; i++) {
      if (SHELL_C_FLAG.test(rest[i]) && scriptRunSends(rest[i + 1], cwd, readScript, depth + 1)) return true
    }
    return false
  }
  let script
  let kind
  if (PYTHON.test(prog)) {
    if (addressFlagGiven(rest)) return true // also under -m / -c, where no file is named
    const module = pythonModule(rest)
    if (module) return scriptRunModuleSends(module, cwd, readScript)
    script = pythonScriptOperand(rest).script
    kind = 'py'
  } else if (NODEISH.test(prog)) {
    script = nodeScriptOperand(prog, rest)
    // MAILGATESCRIPT1027 R1: npx with another package than the tsx / ts-node / node runner form is no script run
    if (prog === 'npx' && script === null) return false
    if (addressFlagGiven(rest)) return true
    kind = 'node'
  } else if (SCRIPT_FILE.test(toks[0]) || toks[0].includes('/')) {
    // a directly executed file: its #! line picks the interpreter, else its extension
    const path = resolveScriptPath(toks[0], cwd)
    if (!path) return false
    kind = shebangKind(readScript(path, SHEBANG_READ_LIMIT)) ??
      (/\.py$/i.test(toks[0]) ? 'py' : SCRIPT_FILE.test(toks[0]) ? 'node' : null)
    if (!kind) return false
    if (addressFlagGiven(rest)) return true
    return scriptFileSends(path, kind, readScript)
  } else {
    return false
  }
  if (script === null) return false
  const path = resolveScriptPath(script, cwd)
  if (!path) return false
  return scriptFileSends(path, kind, readScript)
}

export function scriptRunSends(cmd, cwd = process.cwd(), readScript = readScriptHead, depth = 0) {
  let segments
  try {
    segments = segmentsTokens(cmd)
  } catch {
    return false // unparseable: isSendInvocation's legacy fallback already judged it
  }
  return segments.some((toks) => commandHeads(toks).some((h) => headRunsSenderScript(h, cwd, readScript, depth)))
}

// Outbound-shaped operations of the multiplexed manage_email tool. Each of
// these sends for real unless the call explicitly asks for a draft.
const MANAGE_EMAIL_SEND_OPS = new Set(['send', 'reply', 'replyall', 'forward'])

// --- tool-name classification ------------------------------------------------
// JS twin of email_extract.classify_mail_tool (scripts/hooks/email_extract.py,
// the main agent's gates). Both are bound by the shared case list
// scripts/hooks/mail-tool-name-cases.json, which
// src/__tests__/email-gate-matcher-drift.test.ts runs through BOTH copies; the
// full rationale is at the python twin.
//
// Classified by the base-name SUFFIX, never by what precedes it:
// vendor/gmail-mcp-fork prepends its tool prefix to every tool name verbatim
// (v2_, v2, work-, ...), so `(^|__)draft_email$`, `(^|__)manage_email$` and
// `gmail__reply_all$` missed every prefixed form: a prefixed draft skipped the
// recipient ledger, and a prefixed (or non-"gmail"-server) reply_all was no
// send at all. send_draft is a send this gate never knew. The classifier is a
// superset of the patterns this gate carried: send_email and the draft names
// keep a substring match, manage_email is the suffix (a look-alike such as
// manage_emails_bulk stays out); the generic connector words count only with
// "gmail" in the qualified name (the Telegram tool is `..._telegram__reply`).
// Kinds: 'send_email' | 'connector_send' | 'send_draft' | 'manage_email' |
// 'draft' | null (reads, search, labels, delete: not outbound).
const CONNECTOR_GENERIC_SUFFIXES = ['send_message', 'reply', 'forward']
const DRAFT_NAMES = ['draft_email', 'create_draft', 'update_draft', 'draft_gmail_message']
// The harder-mail MCP's sends (HARDERMAILGATES1019): connector_send, so the
// thread-reply narrowing (send_email only) can never release one.
const MAILBOX_SEND_SUFFIXES = ['send_mail', 'send_mail_auto']
// The Microsoft Graph MCP's names, hyphens normalized (MS365MAIL1024): the
// server names its tools with hyphens (send-mail, create-draft-email, ...),
// which no underscore-shaped suffix ever matched.
const MS365_SEND_SUFFIXES = ['send_mail', 'send_shared_mailbox_mail', 'reply_mail_message',
  'reply_all_mail_message', 'forward_mail_message', 'reply_shared_mailbox_mail',
  'reply_all_shared_mailbox_mail', 'forward_shared_mailbox_mail', 'forward_calendar_event',
  'reply_to_group_thread']
const MS365_DRAFT_SEND_SUFFIXES = ['send_draft_message', 'send_shared_mailbox_draft']
const MS365_DRAFT_SUFFIXES = ['create_draft_email', 'create_shared_mailbox_draft', 'create_reply_draft',
  'create_reply_all_draft', 'create_forward_draft', 'create_shared_mailbox_reply_draft',
  'create_shared_mailbox_reply_all_draft', 'create_shared_mailbox_forward_draft',
  'update_mail_message', 'update_shared_mailbox_message']

export function classifyMailTool(toolName) {
  const n = String(toolName ?? '').toLowerCase().replaceAll('-', '_')
  if (!n || n === 'bash') return null
  // send_gmail_message: the Google Workspace MCP's Gmail send tool
  if (n.includes('send_email') || n.includes('send_gmail_message')) return 'send_email'
  if (n.endsWith('reply_all')) return 'connector_send'
  if (n.includes('gmail') && CONNECTOR_GENERIC_SUFFIXES.some((b) => n.endsWith(b))) return 'connector_send'
  if (MAILBOX_SEND_SUFFIXES.some((b) => n.endsWith(b))) return 'connector_send'
  if (MS365_SEND_SUFFIXES.some((b) => n.endsWith(b))) return 'connector_send'
  if (MS365_DRAFT_SEND_SUFFIXES.some((b) => n.endsWith(b))) return 'send_draft'
  if (n.endsWith('send_draft')) return 'send_draft'
  if (n.endsWith('manage_email')) return 'manage_email'
  if (MS365_DRAFT_SUFFIXES.some((b) => n.endsWith(b))) return 'draft'
  if (DRAFT_NAMES.some((d) => n.includes(d))) return 'draft'
  return null
}

// --- Microsoft Graph letters (MS365MAIL1024) ----------------------------------
// The ms365 MCP server carries the letter Graph-shaped: body.Message.{subject,
// body:{contentType, content}, toRecipients/ccRecipients/bccRecipients:
// [{emailAddress:{address, name?}}]}, a reply's Comment, a forward's
// ToRecipients. The server reads keys in ANY case (so a key twice in different
// case is ambiguous -> reason), and moves a stray top-level field into body
// (so a stray is read wherever it sits). Reading is STRICT for the recipient
// checks (an unreadable list fails closed, like the Python gate's copy).
const isPlainObject = (v) => typeof v === 'object' && v !== null && !Array.isArray(v)

const MS365_TOP_GRAPH_KEYS = new Set(['torecipients', 'ccrecipients', 'bccrecipients'])

export function isMs365Shaped(toolInput) {
  if (!isPlainObject(toolInput)) return false
  for (const [k, v] of Object.entries(toolInput)) {
    const lk = String(k).toLowerCase()
    if (isPlainObject(v) && (lk === 'body' || lk === 'message' || lk === 'post')) return true
    if (Array.isArray(v) && MS365_TOP_GRAPH_KEYS.has(lk)) return true
  }
  return false
}

// A Microsoft Graph MCP mail tool, decided by NAME (MS365MAIL1024 round 2): the
// Graph server names its tools with hyphens, and the last segment of the
// qualified name must contain one -- so the harder-mail server's underscored
// send_mail is never Graph, however its payload looks. Same rule as
// ms365_tool_kind in email_extract.py; the suffix lists are classifyMailTool's.
export function ms365ToolKind(toolName) {
  const raw = String(toolName ?? '').toLowerCase()
  const last = raw.split('__').pop()
  if (!last.includes('-')) return null
  const n = raw.replaceAll('-', '_')
  if (MS365_SEND_SUFFIXES.some((s) => n.endsWith(s))) return 'send'
  if (MS365_DRAFT_SEND_SUFFIXES.some((s) => n.endsWith(s))) return 'draft_send'
  if (MS365_DRAFT_SUFFIXES.some((s) => n.endsWith(s)) || n.endsWith('create_draft_email')) return 'draft'
  return null
}

export function isMs365Tool(toolName) {
  return ms365ToolKind(toolName) !== null
}

// Does the call carry Graph MATERIAL at all: an object body/Message/Post, or a
// top-level Graph recipient key in ANY shape (a non-list toRecipients is
// Graph material -- it must be read strictly, and fails there)? A Graph-named
// draft with flat-only fields has none and keeps the flat checks, which is the
// pinned contract for the ledger-checked drafts; a non-Graph tool with an
// object body never enters the Graph block.
function ms365HasGraphFields(toolInput) {
  if (!isPlainObject(toolInput)) return false
  for (const [k, v] of Object.entries(toolInput)) {
    const lk = String(k).toLowerCase()
    if (isPlainObject(v) && (lk === 'body' || lk === 'message' || lk === 'post')) return true
    if (MS365_TOP_GRAPH_KEYS.has(lk)) return true
  }
  return false
}

// A Graph-shaped call that ALSO carries flat to/cc/bcc is ambiguous: one reader
// would hide the other's addresses (SECURITY review on MS365MAIL1024).
export function ms365FlatConflict(toolInput) {
  if (!isPlainObject(toolInput)) return null
  const flat = Object.entries(toolInput)
    .filter(([k, v]) => ['to', 'cc', 'bcc'].includes(String(k).toLowerCase())
      && !(v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0)))
    .map(([k]) => k)
  return flat.length
    ? `a hivas lapos (${flat.sort().join(', ')}) es Graph-alaku cimzettet is visz -- nem egyertelmu`
    : null
}

// (the object with lowercased keys, reason). A key twice in a different case is
// ambiguous.
export function ms365Lower(d, where) {
  const out = {}
  for (const [k, v] of Object.entries(d)) {
    const lk = String(k).toLowerCase()
    if (lk in out) {
      return [null, `${where}: a(z) '${lk}' mezo ketszer szerepel (kis/nagybetu) -- nem egyertelmu`]
    }
    out[lk] = v
  }
  return [out, null]
}

// Python-style sorted set repr, so the deny reasons read the same as the
// Python gate's: ['a', 'b'].
function ms365FieldRepr(keys) {
  const sorted = [...new Set(keys)].sort()
  return `['${sorted.join("', '")}']`
}

// Python-style repr of one short string (the quote Python's repr would pick),
// so an address named in a deny reason reads the same on both gates.
function ms365StrRepr(s) {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'"
  return q + s.replace(/\\/g, '\\\\').split(q).join(`\\${q}`) + q
}

// [{emailAddress: {address, name?}}] -> (addresses, reason). Strict: nothing
// else is accepted.
export function ms365RecipientList(value, where) {
  if (value === null || value === undefined) return [[], null]
  if (!Array.isArray(value)) return [[], `${where}: nem lista`]
  const out = []
  for (let i = 0; i < value.length; i++) {
    const here = `${where}[${i}]`
    const item = value[i]
    if (!isPlainObject(item)) return [[], `${here}: nem objektum`]
    const [it, reason] = ms365Lower(item, here)
    if (reason) return [[], reason]
    const extra = Object.keys(it).filter((k) => k !== 'emailaddress')
    if (extra.length) return [[], `${here}: ismeretlen mezo ${ms365FieldRepr(extra)}`]
    const ea = it.emailaddress
    if (!isPlainObject(ea)) return [[], `${here}.emailAddress: nem objektum`]
    const [e, reason2] = ms365Lower(ea, `${here}.emailAddress`)
    if (reason2) return [[], reason2]
    const extra2 = Object.keys(e).filter((k) => k !== 'address' && k !== 'name')
    if (extra2.length) return [[], `${here}.emailAddress: ismeretlen mezo ${ms365FieldRepr(extra2)}`]
    const addr = e.address
    if (typeof addr !== 'string' || !addr.trim()) {
      return [[], `${here}.emailAddress.address: hianyzik vagy nem szoveg`]
    }
    if (!MS365_ADDR_RE.test(addr.trim())) {
      return [[], `${here}.emailAddress.address: nem egyetlen puszta cim (${ms365StrRepr(addr.slice(0, 60))})`]
    }
    if ('name' in e && typeof e.name !== 'string') {
      return [[], `${here}.emailAddress.name: nem szoveg`]
    }
    out.push(addr.trim())
  }
  return [out, null]
}

const MS365_RECIPIENT_FIELDS = ['torecipients', 'ccrecipients', 'bccrecipients']

// One bare address, nothing around it: a '"owner@x" <stranger@y>' entry would
// let a recipient check find the owner while the letter goes to the stranger.
const MS365_ADDR_RE = /^[^\s<>,;:"'()\[\]@]+@[^\s<>,;:"'()\[\]@]+$/

// {to, cc, bcc, reason} of a Graph-shaped call, for the recipient checks: only
// the recipient fields are read, each strictly, wherever the server would take
// them from -- the top level and the top-level message object (strays the server
// moves into body), body itself (create-draft-email, update-mail-message),
// body.Message, and body.ToRecipients (a forward). A flat to/cc/bcc next to the
// Graph shape is a reason first: one reader would hide the other's addresses.
// A port of ms365_recipients in email_extract.py (same logic, same reason
// texts), so the two gates cannot drift.
export function ms365Recipients(toolInput) {
  if (!isPlainObject(toolInput)) return { to: [], cc: [], bcc: [], reason: 'a hivas: nem objektum' }
  const flat = ms365FlatConflict(toolInput)
  if (flat) return { to: [], cc: [], bcc: [], reason: flat }
  const [top, topReason] = ms365Lower(toolInput, 'a hivas')
  if (topReason) return { to: [], cc: [], bcc: [], reason: topReason }
  const places = [['a hivas', top]]
  if (top.message !== null && top.message !== undefined) {
    if (!isPlainObject(top.message)) return { to: [], cc: [], bcc: [], reason: 'a hivas.Message: nem objektum' }
    const [m, msgReason] = ms365Lower(top.message, 'a hivas.Message')
    if (msgReason) return { to: [], cc: [], bcc: [], reason: msgReason }
    places.push(['a hivas.Message', m])
  }
  const body = top.body
  if (isPlainObject(body)) {
    const [b, bodyReason] = ms365Lower(body, 'body')
    if (bodyReason) return { to: [], cc: [], bcc: [], reason: bodyReason }
    places.push(['body', b])
    if (b.message !== null && b.message !== undefined) {
      if (!isPlainObject(b.message)) return { to: [], cc: [], bcc: [], reason: 'body.Message: nem objektum' }
      const [m, msgReason] = ms365Lower(b.message, 'body.Message')
      if (msgReason) return { to: [], cc: [], bcc: [], reason: msgReason }
      places.push(['body.Message', m])
    }
  }
  const to = [], cc = [], bcc = []
  const buckets = [to, cc, bcc]
  for (const [where, d] of places) {
    for (let f = 0; f < MS365_RECIPIENT_FIELDS.length; f++) {
      const field = MS365_RECIPIENT_FIELDS[f]
      const [got, reason] = ms365RecipientList(d[field], `${where}.${field}`)
      if (reason) return { to: [], cc: [], bcc: [], reason }
      const bucket = buckets[f]
      for (const x of got) if (!bucket.includes(x)) bucket.push(x)
    }
  }
  return { to, cc, bcc, reason: null }
}

// RECOVERYPATH920: the recovery command in the deny message used to be the
// relative `node scripts/recipient-ledger.mjs`. Sub-agents run with cwd
// agents/<name>/, which has NO scripts/ directory, so from a gated agent the
// command died with "Cannot find module .../agents/<name>/scripts/
// recipient-ledger.mjs" -- the one path the gate offers was unreachable from
// the only place it is ever read. Resolve it from this file's own location:
// the gate script and the ledger CLI ship in the same directory, so this is
// correct from any cwd.
const LEDGER_CLI = join(dirname(fileURLToPath(import.meta.url)), 'recipient-ledger.mjs')

// Tool-input fields that carry recipient addresses across the mail tools we
// have. A reply that only names a messageId has none of these -- it is
// addressed by the thread, not by us, so there is nothing to invent.
const RECIPIENT_FIELDS = ['to', 'cc', 'bcc', 'recipients', 'recipient', 'recipient_email']

// Every address in this call that the ledger does not know. Pure: the lookup is
// injected so tests never touch the real ledger file.
export function unverifiedRecipients(toolInput, isVerified) {
  const out = []
  for (const field of RECIPIENT_FIELDS) {
    const value = toolInput?.[field]
    if (value === undefined || value === null || value === '') continue
    for (const addr of parseAddresses(value).addresses) {
      if (!isVerified(addr) && !out.includes(addr)) out.push(addr)
    }
  }
  return out
}

// Every recipient piece of this call that is not exactly ONE address
// (recipient-ledger.mjs parseAddresses): two addresses in one array item, an
// address in the display name, two <...> pairs, an object value. The ledger
// cannot vouch for what it cannot read as one address, and the mail tool may
// still deliver to it, so the gate denies on any such piece.
export function unparseableRecipients(toolInput) {
  const out = []
  for (const field of RECIPIENT_FIELDS) {
    const value = toolInput?.[field]
    if (value === undefined || value === null || value === '') continue
    for (const piece of parseAddresses(value).unparseable) {
      if (!out.includes(piece)) out.push(piece)
    }
  }
  return out
}

// Default lookup for the live hook: read the ledger once per invocation. A
// missing or corrupt ledger means nothing is verified, so the gate blocks --
// an evidence store that cannot be read must never open the gate silently.
function ledgerLookup() {
  const ledger = loadLedger()
  return (addr) => isVerifiedIn(ledger, addr)
}

// Pure decision: does this tool call send (or attempt to send) email?
// Returns { deny, kind? }. `kind` selects the deny wording at the hook
// entrypoint: 'draft-required' is the manage_email case (drafting is fine,
// only the actual send is refused), 'send_email' is the direct MCP send tool
// (the only path the thread-reply capability below can narrow),
// 'unverified-recipient' is a draft/manage_email address with no recorded
// source, and everything else is the sub-agent governance block.
export function gateDecision(toolName, toolInput, isVerified = null, cwd = process.cwd()) {
  const name = String(toolName ?? '')
  // Lazy: only build the ledger lookup when a call actually carries addresses,
  // so a read-shaped tool call never pays a file read.
  const verify = isVerified ?? (() => {
    let cached = null
    return (addr) => (cached ??= ledgerLookup())(addr)
  })()
  // Any MCP send_email tool, name-agnostic (gmail or a differently-named
  // server in a customer install -> the matcher + this both key on send_email).
  // Deliberately NOT ledger-checked. This branch is either an unconditional
  // deny, or -- with --allow-thread-reply -- a narrowing that only passes
  // recipients read back from the live thread the reply belongs to. A
  // participant of a thread we can read IS a sourced address in the same sense
  // the ledger means it (the From header of a mail they sent us), so gating it
  // against the file would refuse a legitimately sourced reply. The ledger
  // guards the paths where an address can be typed from memory: drafting and
  // manage_email.
  const mailKind = classifyMailTool(name)
  if (mailKind === 'send_email') return { deny: true, kind: 'send_email' }
  // The claude.ai Gmail connector (mcp__claude_ai_Gmail__*) has no send_email:
  // its sends are send_message / reply / forward. Drafts stay allowed and the
  // reads are not sends, only the three send-shaped tools are denied
  // (GMAILCONNECTOR914 -- before this line a sub-agent could send through the
  // connector with no gate at all).
  // Kind is NOT 'send_email' on purpose: the thread-reply narrowing at the
  // entrypoint reads send_email-shaped fields (threadId/to), which a connector
  // reply does not carry, so the connector stays fully gated for every agent.
  // reply_all is a send on a server of any name, and a tool prefix does not
  // hide any of the four (classifyMailTool).
  if (mailKind === 'connector_send') return { deny: true, kind: 'connector-send' }
  // send_draft sends an existing draft by draftId: a send like any other, and
  // one whose recipients this gate cannot even see. Sub-agents may not send,
  // so it gets the governance deny, with no narrowing.
  if (mailKind === 'send_draft') return { deny: true, kind: 'send-draft' }
  // Draft-creating MCP tools. Drafting is allowed (that is the whole point of
  // the draft-required rule), but the ADDRESS in a draft still has to be
  // verified: the owner presses send on what we typed, so an invented address
  // reaches the outside world through a draft just as surely as through a send.
  if (mailKind === 'draft') {
    // MS365MAIL1024, round 2: the Graph read is decided by the tool NAME, not
    // by the payload's shape -- a non-Graph tool with an object body keeps the
    // flat checks below, and a Graph draft carries its recipients as
    // toRecipients/ccRecipients/bccRecipients items, read the same strict way;
    // an unreadable recipient list fails closed. A Graph-named draft with no
    // Graph material at all (flat to/cc/bcc only) keeps the flat checks too:
    // that is the pinned draft contract, and it errs toward the flat read,
    // which checks every field it can see.
    if (isMs365Tool(name) && ms365HasGraphFields(toolInput)) {
      const r = ms365Recipients(toolInput)
      if (r.reason) return { deny: true, kind: 'unparseable-recipient', addresses: [r.reason] }
      const all = [...r.to, ...r.cc, ...r.bcc]
      const unparseable = unparseableRecipients({ to: all })
      if (unparseable.length) return { deny: true, kind: 'unparseable-recipient', addresses: unparseable }
      const bad = unverifiedRecipients({ to: all }, verify)
      if (bad.length) return { deny: true, kind: 'unverified-recipient', addresses: bad }
      return { deny: false }
    }
    const unparseable = unparseableRecipients(toolInput)
    if (unparseable.length) return { deny: true, kind: 'unparseable-recipient', addresses: unparseable }
    const bad = unverifiedRecipients(toolInput, verify)
    if (bad.length) return { deny: true, kind: 'unverified-recipient', addresses: bad }
    return { deny: false }
  }
  // @aaronsb/google-workspace-mcp multiplexes read, draft and send behind one
  // manage_email tool, so the tool NAME cannot decide this one -- the operation
  // plus the draft flag can. This is what replaces the server's own
  // draft-only-email safety policy, which blocks drafting too: that policy keys
  // on ctx.operation alone and never looks at draft:true, so with it enabled the
  // mailbox is effectively read-only (verified live, 2026-08-10).
  if (mailKind === 'manage_email') {
    const op = String(toolInput?.operation ?? '').toLowerCase()
    if (!MANAGE_EMAIL_SEND_OPS.has(op)) return { deny: false }
    // The address check runs before the draft rule, so it applies to the send
    // AND to the draft the deny message would send us back to write.
    const unparseable = unparseableRecipients(toolInput)
    if (unparseable.length) return { deny: true, kind: 'unparseable-recipient', addresses: unparseable }
    const bad = unverifiedRecipients(toolInput, verify)
    if (bad.length) return { deny: true, kind: 'unverified-recipient', addresses: bad }
    // Fail safe: only an explicit draft request passes. A missing/ambiguous
    // flag is treated as a real send, even though the server would itself
    // force a draft when attachments are present.
    const draft = toolInput?.draft
    if (draft === true || draft === 'true') return { deny: false }
    return { deny: true, kind: 'draft-required' }
  }
  if (name === 'Bash') {
    const cmd = String(toolInput?.command ?? '')
    // SCRIPTSENDGATE1020 first: a recognised sender script is named as such, even where
    // the #718 reader would also deny it as unreadable (both deny; only the wording differs).
    if (scriptRunSends(cmd, cwd)) return { deny: true, kind: 'script-send' }
    if (isSendInvocation(cmd, 0, cwd)) {
      if (ambiguousSendHit(cmd, cwd)) return { deny: true, kind: 'ambiguous-send' }
      if (wrapperDepthHit(cmd)) return { deny: true, kind: 'wrapper-depth' }
      return unreadableScriptHit(cmd, cwd) ? { deny: true, kind: 'unreadable-script' } : { deny: true }
    }
  }
  return { deny: false }
}

// Deny wording for the manage_email case. Unlike the sub-agent governance
// block this is not about who the agent is: drafting stays open, so the fix is
// to re-issue the same call with draft: true and hand the draft to the owner.
export function buildDraftOnlyMsg(ownerName) {
  return (
    'Kimeno email kuldese tiltott (draft-kapu). ' +
    'Ird meg ugyanezt draft: true kapcsoloval, es jelezd a tulajdonosnak ' +
    `(${ownerName}), hogy a Gmail piszkozatok kozott varja a jovahagyasat. ` +
    'Csak VERIFIKALT cimre. A kuldes gombot ember nyomja meg.'
  )
}

// Deny wording for an address the ledger does not know. This is the one deny
// the agent can clear on its own -- by going and finding where the address
// actually comes from. It names the exact command, so the cheap path is the
// correct path and not "write it anyway".
export function buildUnverifiedRecipientMsg(addresses) {
  const list = addresses.join(', ')
  const first = addresses[0] ?? 'cim@pelda.hu'
  return (
    `Nem igazolt cimzett: ${list}. ` +
    'Ez a kapu azert van, mert egy kitalalt cimre meno level neman elveszik ' +
    '(support@connectors.hu, 2026-08-14, 550 User doesn\'t exist). ' +
    'Ne talalgass es ne a support@/info@ szokast hasznald: keresd meg a cimet ' +
    'egy valodi forrasban -- a toluk kapott level From fejleceben ' +
    '(manage_email search: from:<domain> in:anywhere), az elo oldalukon, ' +
    'a Woo rendelesben vagy a Notion lapon. Aztan vedd fel a ledgerbe:\n' +
    `  node ${LEDGER_CLI} add ${first} --source <forras> --note "<honnan>"\n` +
    `  --source: ${SOURCE_HELP}\n` +
    'Ha nem talalsz forrast, a cim NINCS meg: mondd meg a gazdanak, ne kuldj levelet.'
  )
}

// Deny wording for a recipient piece that is not exactly one address. The fix
// is on the caller's side: one clean address per item.
export function buildUnparseableRecipientMsg(pieces) {
  return (
    `Nem ertelmezheto cimzett: ${pieces.join(' | ')}. ` +
    'A cimzett-mezo minden eleme (vesszovel vagy pontosvesszovel elvalasztva) ' +
    'pontosan EGY cim legyen: "cim@pelda.hu" vagy "Nev <cim@pelda.hu>". ' +
    'Ket cim egy elemben, cim a megjelenitett nevben vagy tobb <...> ' +
    'nem ellenorizheto a cimzett-ledgerrel, ezert a kapu tilt (fail-closed). ' +
    'Ird at a mezot tiszta cimekre, aztan kuldd ujra.'
  )
}

// Wording for a call that needs the ledger while the ledger MODULE does not
// load (HOOKDEPLOAD1008). It goes to stderr with exit 2, not as the JSON deny:
// no rewrite of the call helps, the module has to be repaired, and that is the
// main agent's job -- this gate does not run in its session.
export function buildLedgerUnavailableMsg(err) {
  return (
    'EMAIL-KAPU: TILTVA. A kapu fuggosege (scripts/recipient-ledger.mjs) nem toltheto be ' +
    `(${err?.message ?? err}) -- fail-closed: a cimzett nem ellenorizheto, ezert a hivas nem mehet at. ` +
    'A modult a fo ugynok javitja (ez a kapu az o munkameneteben nem fut): jelezd neki.\n'
  )
}

// Pure builder for the deny message, so the brand/owner substitution is
// provable without spawning the hook. With the stock defaults (botName
// 'Marveen', ownerName 'Szabolcs') the wording is byte-identical to before.
export function buildGateMsg(botName, ownerName) {
  return (
    'Email-kuldes sub-agentkent tiltott (governance hard-gate). ' +
    `Kuldd a tervezett emailt (CIMZETT + TARGY + TELJES SZOVEG) ${botName}nek inter-agent uzenetben ` +
    `jovahagyasra; a kimeno emailt ${botName} kuldi. Csak VERIFIKALT cimre (soha nem nevbol talalt cim). ` +
    `Soha ne irj ala ${ownerName} nevevel, es soha ne kerj penzt senki neveben.`
  )
}

// Resolve the brand + owner display names for the deny message. The hook runs
// standalone (no config.ts), so read the install's .env directly, keyed off
// this file's own location (<root>/scripts/email-send-gate.mjs -> <root>/.env).
// Any failure falls back to the stock defaults, so the gate never breaks and a
// bare install keeps the original wording.
export function readBrandEnv(readFile = (p) => readFileSync(p, 'utf-8')) {
  const fallback = { botName: 'Marveen', ownerName: 'Szabolcs' }
  try {
    const envPath = join(dirname(fileURLToPath(import.meta.url)), '..', '.env')
    const raw = readFile(envPath)
    const pick = (key) => {
      const m = raw.match(new RegExp(`^\\s*${key}\\s*=\\s*(.*)$`, 'm'))
      if (!m) return ''
      return m[1].trim().replace(/^["']|["']$/g, '').trim()
    }
    return {
      botName: pick('BOT_NAME') || fallback.botName,
      ownerName: pick('OWNER_NAME') || fallback.ownerName,
    }
  } catch {
    return fallback
  }
}

// --- thread-scoped reply (BONIMAIL910) --------------------------------------
// Owner decision 2026-09-10: ONE named agent may send outbound email, but ONLY
// into an EXISTING Gmail thread and ONLY to addresses that already appear in
// that thread's headers. The capability is granted per agent in code
// (agent-scaffold.ts appends --allow-thread-reply to this hook's command when
// the agent's capability list contains EMAIL_THREAD_REPLY_CAPABILITY); every
// other agent keeps the unconditional deny above.
//
// Why THREAD-MEMBERSHIP and not a topic rule: the 2026-06-25 incident that
// created this gate was a sub-agent mailing a FABRICATED address in the
// owner's name. Membership in an existing thread is checkable in code; "is
// this financial" is not. The narrowing closes exactly the failure shape that
// created the rule: a new address can never be introduced.
//
// Every branch below fails CLOSED: missing threadId, unparseable recipient,
// empty participant list, credential/fetch/HTTP error, timeout -> deny.

// One address token. Deliberately simple: it must match what appears both in
// the tool input and in Gmail's From/To/Cc header values.
const ADDR_TOKEN = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi

// Parse one recipient entry ("addr" or "Name <addr>") to a bare lowercase
// address. Returns '' when the entry is not a single clean address -- the
// caller treats that as deny (an unparseable recipient must never slip past
// the membership check).
export function extractAddress(raw) {
  const s = String(raw ?? '').trim()
  // More than one '@' is ONE clean address only when the display name IS the
  // bracketed address ("jane@example.com" <jane@example.com>, a common client
  // form). "invented@example.org <known@example.com>" would otherwise pass the
  // trailing-bracket test as known@example.com, with a second address in the
  // display name the membership check never looked at.
  if ((s.match(/@/g) ?? []).length > 1) {
    const m = s.match(/^([^<>]*)<([^<>]+)>$/)
    if (!m) return ''
    const display = m[1].trim().replace(/^"(.*)"$/, '$1').trim()
    if (display.toLowerCase() !== m[2].trim().toLowerCase()) return ''
  }

  const angle = s.match(/<([^<>]+)>$/)
  const cand = (angle ? angle[1] : s).trim()
  return /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i.test(cand) ? cand.toLowerCase() : ''
}

// Validate the send_email tool input as a thread-reply request. Pure.
// Returns { ok: true, threadId, recipients } or { ok: false, reason }.
export function threadReplyRequest(toolInput) {
  const threadId = toolInput?.threadId
  if (typeof threadId !== 'string' || !threadId.trim()) {
    return { ok: false, reason: 'threadId hianyzik (uj szal nyitasa tiltott)' }
  }
  const raw = [toolInput?.to, toolInput?.cc, toolInput?.bcc]
    .flatMap((v) => (Array.isArray(v) ? v : v == null ? [] : [v]))
  if (!raw.length) return { ok: false, reason: 'nincs cimzett' }
  const recipients = []
  for (const r of raw) {
    const addr = extractAddress(r)
    if (!addr) return { ok: false, reason: `ertelmezhetetlen cimzett: ${String(r)}` }
    recipients.push(addr)
  }
  return { ok: true, threadId: threadId.trim(), recipients }
}

// The membership decision itself. Pure. Empty participant list denies: a
// thread we could not read participants from authorizes nothing.
export function threadMembershipDecision(recipients, participants) {
  const set = new Set((participants ?? []).map((p) => String(p).toLowerCase()).filter(Boolean))
  if (!set.size) return { allow: false, reason: 'a szal resztvevo-listaja ures (fail-closed)' }
  for (const r of recipients) {
    if (!set.has(r)) return { allow: false, reason: `cimzett nincs a szalban: ${r}` }
  }
  return { allow: true }
}

// Collect every address that appears in the thread's From/To/Cc/Reply-To
// headers. Pure over the Gmail API thread payload (format=metadata).
export function extractParticipants(threadData) {
  const out = new Set()
  for (const msg of threadData?.messages ?? []) {
    for (const h of msg?.payload?.headers ?? []) {
      const n = String(h?.name ?? '').toLowerCase()
      if (n === 'from' || n === 'to' || n === 'cc' || n === 'reply-to') {
        for (const m of String(h?.value ?? '').matchAll(ADDR_TOKEN)) out.add(m[0].toLowerCase())
      }
    }
  }
  return [...out]
}

// Fetch the thread's participant addresses with the same credentials the Gmail
// MCP server uses (same mailbox the send would go through, so the gate and the
// send see the same thread). Path resolution mirrors the MCP server's own:
// GMAIL_CREDENTIALS_PATH / GMAIL_OAUTH_PATH env overrides, else ~/.gmail-mcp.
// Throws on ANY failure; per-request timeouts keep the whole check well under
// the hook's 10s budget (a hook timeout would be a NON-blocking error, i.e.
// fail-open -- the script must always decide first).
export async function fetchThreadParticipants(threadId, opts = {}) {
  const fetchImpl = opts.fetchImpl ?? fetch
  const readFile = opts.readFile ?? ((p) => readFileSync(p, 'utf-8'))
  const now = opts.now ?? (() => Date.now())
  const home = process.env.HOME || homedir()
  const credPath = process.env.GMAIL_CREDENTIALS_PATH || join(home, '.gmail-mcp', 'credentials.json')
  const keysPath = process.env.GMAIL_OAUTH_PATH || join(home, '.gmail-mcp', 'gcp-oauth.keys.json')
  const creds = JSON.parse(readFile(credPath))
  let accessToken = creds.access_token
  const fresh = typeof creds.expiry_date === 'number' && creds.expiry_date > now() + 60_000
  if (!fresh) {
    const keys = JSON.parse(readFile(keysPath))
    const k = keys.installed ?? keys.web
    if (!k?.client_id || !k?.client_secret || !creds.refresh_token) {
      throw new Error('gmail credentials incomplete')
    }
    const res = await fetchImpl(k.token_uri || 'https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id: k.client_id,
        client_secret: k.client_secret,
        refresh_token: creds.refresh_token,
        grant_type: 'refresh_token',
      }).toString(),
      signal: AbortSignal.timeout(3500),
    })
    if (!res.ok) throw new Error(`token refresh failed: ${res.status}`)
    accessToken = (await res.json())?.access_token
  }
  if (!accessToken) throw new Error('no gmail access token')
  const url = 'https://gmail.googleapis.com/gmail/v1/users/me/threads/'
    + encodeURIComponent(threadId)
    + '?format=metadata&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Cc&metadataHeaders=Reply-To'
  const res = await fetchImpl(url, {
    headers: { Authorization: `Bearer ${accessToken}` },
    signal: AbortSignal.timeout(3500),
  })
  if (!res.ok) throw new Error(`thread fetch failed: ${res.status}`)
  return extractParticipants(await res.json())
}

// Deny wording for the thread-reply case: the agent HAS a send right, the
// specific call fell outside it. Says what the right covers and the way out.
export function buildThreadDenyMsg(botName, detail) {
  return (
    'Kimeno email ezzel a jogosultsaggal CSAK meglevo szalba mehet, es csak a ' +
    `szalban mar szereplo cimzettnek (szal-kapu). Elutasitva: ${detail}. ` +
    `Uj cimzetthez vagy uj szalhoz kuldd a tervezett emailt ${botName}nek ` +
    'inter-agent uzenetben jovahagyasra.'
  )
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

// Run as the hook entrypoint only when invoked directly (not when imported by a
// test). Reads the PreToolUse payload from stdin and emits a deny decision for
// email-send tool calls. realpath both sides so a symlinked install path (the
// hook command is an absolute path that may traverse a symlink, e.g. /tmp ->
// /private/tmp on macOS, or a symlinked /home on Linux) still matches -- a raw
// url-vs-argv compare would silently no-op the gate (a security bypass).
function isInvokedDirectly() {
  try {
    const self = realpathSync(fileURLToPath(import.meta.url))
    const entry = process.argv[1] ? realpathSync(process.argv[1]) : ''
    return self === entry
  } catch {
    return false
  }
}
if (isInvokedDirectly()) {
  let payload
  try {
    payload = JSON.parse(readFileSync(0, 'utf-8'))
  } catch {
    allow() // malformed/empty input must never break the agent's tool calls
  }
  let decision
  try {
    const cwd = typeof payload?.cwd === 'string' && isAbsolute(payload.cwd) ? payload.cwd : process.cwd()
    decision = gateDecision(payload?.tool_name, payload?.tool_input, null, cwd)
  } catch (err) {
    // HOOKDEPLOAD1008: the call needed the ledger, and the ledger module did
    // not load. Anything else that throws here is not this branch's to judge.
    if (!LEDGER_LOAD_ERROR) throw err
    process.stderr.write(buildLedgerUnavailableMsg(LEDGER_LOAD_ERROR))
    process.exit(2)
  }
  const { deny: shouldDeny, kind, addresses } = decision
  if (shouldDeny) {
    const { botName, ownerName } = readBrandEnv()
    // An address with no recorded source loses before every other branch,
    // including the thread-reply narrowing below: a verified thread cannot
    // vouch for an unsourced recipient.
    if (kind === 'unparseable-recipient') deny(buildUnparseableRecipientMsg(addresses ?? []))
    if (kind === 'unverified-recipient') deny(buildUnverifiedRecipientMsg(addresses ?? []))
    if (kind === 'draft-required') deny(buildDraftOnlyMsg(ownerName))
    if (kind === 'wrapper-depth') deny(buildWrapperDepthMsg())
    if (kind === 'unreadable-script') deny(buildUnreadableScriptMsg())
    if (kind === 'ambiguous-send') deny(buildAmbiguousSendMsg())
    // Thread-scoped narrowing: only when the scaffold wired this agent's hook
    // command with the flag (capability-driven, regenerated on every spawn),
    // and only for the direct send_email tool. Bash send routes and
    // manage_email stay fully gated even for the capable agent.
    if (kind === 'send_email' && process.argv.includes('--allow-thread-reply')) {
      const req = threadReplyRequest(payload?.tool_input)
      if (!req.ok) deny(buildThreadDenyMsg(botName, req.reason))
      let verdict
      try {
        const participants = await fetchThreadParticipants(req.threadId)
        verdict = threadMembershipDecision(req.recipients, participants)
      } catch {
        verdict = { allow: false, reason: 'a szal resztvevoi nem olvashatok (fail-closed)' }
      }
      if (verdict.allow) allow()
      deny(buildThreadDenyMsg(botName, verdict.reason))
    }
    deny(buildGateMsg(botName, ownerName))
  }
  allow()
}
