// Shared shell-command reading for PreToolUse gates.
//
// SHELLREAD901. Three gates were written on 2026-09-01 and the first two
// produced the SAME false positive within an hour of going live, for the same
// reason: they read a command as a bag of words. Both times the block was on a
// legitimate command whose DATA merely contained the words being watched for --
// a JSON body quoting a URL, then a comment quoting a path.
//
// The lesson is structural, so the reading lives in one place:
//
//   tokenize()      quote- and escape-aware split. A quoted blob stays ONE
//                   token, so text inside it can never be mistaken for command
//                   structure. `<workdir>/x` is one token, not a redirect.
//   dropFlagValues() removes the arguments carried by value-taking flags
//                   (-d, -H, -o, -w, -X, ...). What a command SENDS is not
//                   where it is aimed.
//   splitSegments() splits a token list on shell separators, so a read in one
//                   half of a `&&` chain cannot convict a write in the other.
//
// None of this is a shell. It expands nothing and resolves nothing. It only has
// to be good enough to tell command structure from payload text -- which is
// exactly the distinction all three gates got wrong on their own.

/** Flags whose NEXT token is a value, not a destination. */
export const VALUE_FLAGS = new Set([
  '-d', '--data', '--data-raw', '--data-binary', '--data-urlencode', '--data-ascii',
  '-H', '--header', '-F', '--form', '--form-string', '--post-data', '--body-data',
  '-o', '--output', '-w', '--write-out', '-X', '--request',
  '-u', '--user', '-A', '--user-agent', '-e', '--referer',
  '-b', '--cookie', '--cookie-jar',
  '--connect-timeout', '-m', '--max-time', '--retry', '--resolve',
  '-T', '--upload-file', '--cacert', '--cert', '--key',
  // NOT '-c': that is curl's cookie-jar, but also `python3 -c` and `sh -c`,
  // where the value IS the program. Dropping it would blind a gate to every
  // inline download. The more dangerous meaning wins.
])

/** Shell separators that end one command and begin another. */
const SEPARATORS = new Set([';', '&&', '||', '|', '&', '\n'])

/**
 * Remove here-document BODIES before anything is read as command structure.
 *
 * HEREDOC901: the third false positive of the day. A `python3 - <<'EOF' ... EOF`
 * patch script had its body split into bare tokens, so a URL quoted in a COMMENT
 * inside it read as a fetch target and the word "urlopen" as a fetcher. A
 * heredoc body is input to a program; it is not the command line.
 *
 * KNOWN GAP, stated rather than hidden: a program actually fed a fetch through a
 * heredoc is therefore NOT seen by these gates. That is the same class as "a
 * script that fetches is not seen" -- these gates raise the cost of an ad-hoc
 * command, they are not a sandbox. Closing it would mean reading program text,
 * and reading program text is what produced every false positive so far.
 */
export function stripHeredocs(cmd) {
  const s = String(cmd ?? '')
  if (!s.includes('<<')) return s
  const lines = s.split('\n')
  const out = []
  let terminator = null
  for (const line of lines) {
    if (terminator !== null) {
      if (line.trim() === terminator) terminator = null
      continue                                   // drop the body AND the terminator
    }
    out.push(line)
    // `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"` -- last one on the line wins.
    const matches = [...line.matchAll(/<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1/g)]
    if (matches.length > 0) terminator = matches[matches.length - 1][2]
  }
  return out.join('\n')
}

/**
 * Command prefixes that are NOT the verb: privilege/env/scheduling wrappers.
 *
 * WRAPFLAG901 (2026-09-01): this used to live twice, once in each gate, with
 * DIFFERENT contents -- the egress gate knew `stdbuf`, the skill gate did not,
 * and neither skipped a wrapper's own FLAGS. Measured consequences, both live:
 * `stdbuf -oL curl <blocked>` walked past the egress gate (the loop stopped on
 * `-oL` and read THAT as the verb), and `env python3 -c "<write to the shared
 * skills folder>"` walked past the skill gate. Two gates, two tables, two
 * different holes -- so the table now lives in ONE place and both call it.
 */
export const PREFIX_WRAPPERS = new Set([
  'sudo', 'command', 'nohup', 'nice', 'stdbuf', 'env', 'time', 'timeout',
  'xargs', 'setsid', 'ionice', 'taskset',
])

/**
 * Index of the real verb: steps over env assignments, wrappers, AND each
 * wrapper's own flags and numeric arguments. `texts` is an array of strings.
 */
/**
 * Does this interpreter read its PROGRAM from stdin?
 *
 * STDINPROG901, and the reason it lives here rather than in one gate: the
 * distinction is `bash <<EOF` / `python3 - <<PY` (the body is code the
 * interpreter runs) versus `bash send.sh <<EOF` (the body is the SCRIPT'S
 * input, and the script is a reviewed repo file). Missing it blocked the
 * fleet's own messaging idiom within a minute -- `bash scripts/agent-msg.sh
 * <to> - <<EOF ...` refused because the MESSAGE TEXT discussed the thing the
 * gate guards. Prose about a write is not a write.
 *
 * The bash-egress-gate learned this on 2026-09-01 and the skill gate did not,
 * so the same false positive came back in the other gate the same evening
 * (Cody isolated it). That is the third time a fix stayed in one gate; hence
 * the shared home.
 *
 * `argTexts` are the RAW tokens after the interpreter. A redirect (`<<EOF`,
 * `>out`) is not an operand -- skipping those is exactly what tells the two
 * shapes apart. A lone `-` IS the stdin operand.
 */
export function readsProgramFromStdin(argTexts) {
  const firstOperand = argTexts.find(
    (t) => !t.startsWith('<') && !t.startsWith('>') && (!t.startsWith('-') || t === '-'),
  )
  return firstOperand === undefined || firstOperand === '-'
}

export function verbIndex(texts) {
  let vi = 0
  while (vi < texts.length) {
    const t = texts[vi]
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(t)) { vi++; continue }
    if (!PREFIX_WRAPPERS.has(t)) break
    vi++
    // `nice -n 10 curl`, `timeout 300 curl`, `xargs -I{} curl`, `stdbuf -oL curl`.
    while (vi < texts.length && (texts[vi].startsWith('-') || /^\d+[smhd]?$/.test(texts[vi]))) vi++
  }
  return vi
}

/**
 * The heredoc BODIES, in order -- the mirror of stripHeredocs().
 *
 * HEREDOCFETCH901 (Forgacs, 2026-09-01): the egress gate could only read an
 * interpreter's program when it came from an inline flag (`-c`, `-e`). A program
 * fed through a heredoc arrives on STDIN, so the gate skipped the segment
 * entirely and an outbound request left the machine with no log line. The house
 * style RECOMMENDS heredocs for shell work, so the blind alternative was the
 * one we were steering everyone towards.
 *
 * Callers must still require an actual fetch call in the body before judging it
 * (CODE_FETCHER_RE). Treating a bare URL in a body as a target is what produced
 * HEREDOC901 -- a URL sitting in a COMMENT read as a fetch destination.
 */
export function extractHeredocBodies(cmd) {
  const s = String(cmd ?? '')
  if (!s.includes('<<')) return []
  const out = []
  let cur = null
  let terminator = null
  for (const line of s.split('\n')) {
    if (terminator !== null) {
      if (line.trim() === terminator) { out.push({ marker: terminator, body: cur.join('\n') }); cur = null; terminator = null; continue }
      cur.push(line)
      continue
    }
    const matches = [...line.matchAll(/<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1/g)]
    if (matches.length > 0) { terminator = matches[matches.length - 1][2]; cur = [] }
  }
  // An UNTERMINATED heredoc still has text, and "truncated" must not read as
  // "nothing to inspect".
  if (cur !== null && cur.length) out.push({ marker: terminator, body: cur.join('\n') })
  return out
}

/**
 * Quote- and escape-aware tokeniser.
 * Returns tokens with their quoting recorded, because a QUOTED token can never
 * be command structure -- it is data, whatever it happens to contain.
 */
export function tokenizeDetailed(cmd) {
  const out = []
  let cur = ''
  let quoted = false
  let started = false
  let quote = null
  const push = () => {
    if (started) out.push({ text: cur, quoted })
    cur = ''
    quoted = false
    started = false
  }
  const s = stripHeredocs(cmd)
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]
    if (quote) {
      // CONTLINE901 (Marveen, 2026-09-06, merve): backslash + ujsor a shellben
      // SORFOLYTATAS -- mindket karakter eltunik --, nem escape-elt ujsor. Ez
      // ketto idezojelen BELUL is igy van. Aposztrofon belul viszont mindketto
      // literalis, ezert az az ag (lentebb, `cur += ch`) valtozatlan marad.
      if (ch === '\\' && quote === '"' && s[i + 1] === '\n') { i++; continue }
      if (ch === '\\' && quote === '"' && s[i + 1] === '\r' && s[i + 2] === '\n') { i += 2; continue }
      if (ch === '\\' && quote === '"' && i + 1 < s.length) { cur += s[++i]; continue }
      if (ch === quote) { quote = null; continue }
      cur += ch
      continue
    }
    if (ch === "'" || ch === '"') { quote = ch; quoted = true; started = true; continue }
    // CONTLINE901: idezojelen KIVUL ugyanez. A regi alak a `\\` utani ujsort
    // BEEMELTE a tokenbe, es a token szovege igy pontosan `\n` lett -- amit a
    // splitSegments SZEPARATORKENT ismer fel. Vagyis egy sorfolytatas kette
    // vagta a parancsot: `curl -s \\` <ujsor> `"<URL>"` ket kulon szegmens lett,
    // az elso fetcher URL nelkul, a masodik verb nelkul. Merve 2026-09-06:
    // az egress-kapu emiatt `no-url-literal`-lal ZART egy teljesen literalis,
    // engedelyezett localhost-hivast. A javitas nem lazitas: a kapu ezutan
    // UGYANAZT latja, mint az egy sorba irt alaknal, es ha a cel tiltott, a
    // dontes `not-allowed` lesz -- ugyanugy blokk, csak a helyes indokkal.
    if (ch === '\\' && s[i + 1] === '\n') { i++; continue }
    if (ch === '\\' && s[i + 1] === '\r' && s[i + 2] === '\n') { i += 2; continue }
    if (ch === '\\' && i + 1 < s.length) { cur += s[++i]; started = true; continue }
    // NEWLINE901 (found 2026-09-01 by USING the gate, not by testing it): a
    // newline ends a command in shell, exactly like `;`. It was falling into the
    // generic whitespace branch and vanishing, so `\n` -- already listed in
    // SEPARATORS -- was DEAD CODE and a multi-line command collapsed into ONE
    // segment whose verb was the FIRST line's. Measured consequences, both live:
    //   `cd /repo\ncurl https://<not-allowlisted>` walked through the egress gate;
    //   `cd /repo\npython3 - <<'PY' ...<protected path>... PY` wrote a shared skill
    //   with no audit line at all.
    // This is the same shape as SEPSPLIT901 above, on the one separator that was
    // written down but never emitted. A separator nobody emits is not a separator.
    if (ch === '\n' || ch === '\r') {
      push()
      if (ch === '\r' && s[i + 1] === '\n') i++
      out.push({ text: '\n', quoted: false })
      started = false
      continue
    }
    if (/\s/.test(ch)) { push(); continue }
    // SEPSPLIT901: separators must become their OWN tokens even when they are
    // glued to a word. Without this, `cd /repo; mkdir -p <protected>` stayed a
    // single segment whose verb read as `cd`, and the skill gate let a real
    // write through -- measured live, the directory was created. A separator
    // that only counts when someone typed a space around it is not a separator.
    if (ch === ';' || ch === '|' || ch === '&') {
      push()
      let op = ch
      if ((ch === '|' || ch === '&') && s[i + 1] === ch) { op += ch; i++ }
      out.push({ text: op, quoted: false })
      started = false
      continue
    }
    cur += ch
    started = true
  }
  push()
  return out
}

/** Plain-string tokens, for callers that do not care about quoting. */
export function tokenize(cmd) {
  return tokenizeDetailed(cmd).map((t) => t.text)
}

/** Remove the arguments consumed by value-taking flags. */
export function dropFlagValues(tokens) {
  const kept = []
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i]
    const text = typeof t === 'string' ? t : t.text
    if (VALUE_FLAGS.has(text)) { i++; continue }        // drop the flag AND its value
    if (/^--[a-z][a-z-]*=/.test(text)) continue          // --data=... : value is inline
    kept.push(t)
  }
  return kept
}

/** Split a token list into command segments on shell separators. */
export function splitSegments(tokens) {
  const segs = [[]]
  for (const t of tokens) {
    const text = typeof t === 'string' ? t : t.text
    const isQuoted = typeof t === 'string' ? false : t.quoted
    if (!isQuoted && SEPARATORS.has(text)) { segs.push([]); continue }
    segs[segs.length - 1].push(t)
  }
  return segs.filter((s) => s.length > 0)
}

/**
 * The usual front-end: tokenise, drop what is being SENT, split into commands.
 * Returns an array of segments, each an array of {text, quoted}.
 */
export function readCommand(cmd) {
  return splitSegments(dropFlagValues(tokenizeDetailed(cmd)))
}
