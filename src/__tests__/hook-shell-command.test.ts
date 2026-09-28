// Contract tests for the shared command reader used by the PreToolUse gates.
//
// SHELLREAD901. Three gates were written on 2026-09-01 and between them they
// produced three false positives and one real MISS in a single morning, all
// from the same root cause: reading a shell command as a bag of words. The
// reader exists so that mistake is fixed once. Each test below corresponds to a
// failure that actually happened.

import { describe, it, expect } from 'vitest'
// @ts-expect-error -- plain .mjs hook script, no types
import { tokenize, tokenizeDetailed, dropFlagValues, splitSegments, readCommand, stripHeredocs, extractHeredocBodies, verbIndex } from '../../scripts/hooks/lib/shell-command.mjs'

describe('shell reader: quoting', () => {
  it('keeps a quoted blob as ONE token, whatever it contains', () => {
    const toks = tokenize(`curl -d '{"a": "b c", "u": "https://x/y"}' http://localhost:3420/`)
    expect(toks).toContain('{"a": "b c", "u": "https://x/y"}')
  })

  it('records that a token was quoted, so data can be told from structure', () => {
    const toks = tokenizeDetailed(`echo "; rm -rf /"`)
    expect(toks[1].quoted).toBe(true)
    expect(toks[1].text).toBe('; rm -rf /')
  })
})

describe('shell reader: separators are separators even when glued to a word', () => {
  // SEPSPLIT901, the one that was not a false positive but a REAL MISS: the
  // skill gate let `cd /repo; mkdir -p <protected>` through, because `marveen;`
  // tokenised as one word, no separator token was ever produced, and the
  // segment's verb read as `cd`. The directory was actually created. A
  // separator that only counts when someone typed a space around it is not a
  // separator.
  it('splits on a semicolon with no surrounding space', () => {
    const segs = splitSegments(tokenizeDetailed('cd /repo; mkdir -p /tmp/x'))
    expect(segs.map((s: Array<{ text: string }>) => s.map((t) => t.text))).toEqual([
      ['cd', '/repo'],
      ['mkdir', '-p', '/tmp/x'],
    ])
  })

  it('splits on && and || and |', () => {
    expect(splitSegments(tokenizeDetailed('a && b || c | d')).length).toBe(4)
    expect(splitSegments(tokenizeDetailed('a&&b')).length).toBe(2)
  })

  it('does NOT split on a separator inside quotes', () => {
    const segs = splitSegments(tokenizeDetailed(`echo "a; b && c"`))
    expect(segs.length).toBe(1)
  })
})

describe('shell reader: what a command SENDS is not where it is aimed', () => {
  // The first two false positives, in both gates: a legitimate localhost POST
  // whose JSON body quoted a foreign URL / a protected path.
  it('drops the value carried by a data flag', () => {
    const kept = dropFlagValues(tokenize(`curl -d 'https://evil.example/' http://localhost:3420/`))
    expect(kept).toEqual(['curl', 'http://localhost:3420/'])
  })

  it('drops an inline --data=... value', () => {
    expect(dropFlagValues(tokenize('curl --data=https://evil.example/ http://localhost:3420/')))
      .toEqual(['curl', 'http://localhost:3420/'])
  })

  it('does NOT drop the value of -c, because that is the program itself', () => {
    const kept = dropFlagValues(tokenize(`python3 -c "import urllib.request"`))
    expect(kept).toContain('import urllib.request')
  })
})

describe('shell reader: here-document bodies are input, not command line', () => {
  // HEREDOC901, the third false positive: a patch script whose heredoc body
  // quoted a URL in a COMMENT was read as a fetch to that URL.
  it('strips the body and the terminator', () => {
    const cmd = ["python3 - <<'PY'", "# see https://evil.example/x", "print(1)", 'PY', 'echo done'].join('\n')
    const stripped = stripHeredocs(cmd)
    expect(stripped).not.toContain('evil.example')
    expect(stripped).not.toContain('print(1)')
    expect(stripped).toContain('echo done')
  })

  it('handles the unquoted and dash forms', () => {
    expect(stripHeredocs('cat <<EOF\nsecret\nEOF\nls')).not.toContain('secret')
    expect(stripHeredocs('cat <<-END\nsecret\nEND\nls')).not.toContain('secret')
  })

  it('leaves a command with no heredoc untouched', () => {
    expect(stripHeredocs('ls -la')).toBe('ls -la')
  })
})

describe('shell reader: readCommand is the front end the gates use', () => {
  it('tokenises, drops sent data, and splits into segments in one step', () => {
    const segs = readCommand(`ls /a; curl -d '{"x":"; rm -rf /"}' http://localhost:3420/`)
    expect(segs.length).toBe(2)
    expect(segs[0].map((t: { text: string }) => t.text)).toEqual(['ls', '/a'])
    expect(segs[1].map((t: { text: string }) => t.text)).toEqual(['curl', 'http://localhost:3420/'])
  })
})

describe('a newline is a separator, not whitespace (NEWLINE901)', () => {
  // Found 2026-09-01 by USING the gates, not by testing them. `\n` was already in
  // SEPARATORS but the tokeniser dropped it into the generic whitespace branch,
  // so it never became a token -- dead code, and every multi-line command
  // collapsed into ONE segment whose verb was the first line's.
  it('emits the newline as its own token', () => {
    expect(tokenize('cd /tmp\nls -la')).toEqual(['cd', '/tmp', '\n', 'ls', '-la'])
  })

  it('splits a multi-line command into one segment per line', () => {
    const segs = readCommand('cd /tmp\ncurl https://example.com\necho done')
    expect(segs.map((s: { text: string }[]) => s[0].text)).toEqual(['cd', 'curl', 'echo'])
  })

  it('treats CRLF the same as LF, and emits ONE separator', () => {
    expect(tokenize('cd /tmp\r\nls')).toEqual(['cd', '/tmp', '\n', 'ls'])
  })

  it('leaves a newline INSIDE quotes alone -- it is data', () => {
    expect(tokenize('echo "a\nb"')).toEqual(['echo', 'a\nb'])
  })
})

describe('extractHeredocBodies is the mirror of stripHeredocs', () => {
  it('returns marker and body, without the marker line or the terminator', () => {
    expect(extractHeredocBodies("python3 - <<'PY'\na = 1\nb = 2\nPY")).toEqual([{ marker: 'PY', body: 'a = 1\nb = 2' }])
  })

  it('returns every heredoc, each tagged with its own marker', () => {
    expect(extractHeredocBodies("cat <<'A'\none\nA\ncat <<'B'\ntwo\nB")).toEqual([
      { marker: 'A', body: 'one' },
      { marker: 'B', body: 'two' },
    ])
  })

  it('returns nothing when there is no heredoc', () => {
    expect(extractHeredocBodies('ls -la')).toEqual([])
  })

  it('still returns the text of an UNTERMINATED heredoc', () => {
    // "truncated" must not read as "nothing to inspect".
    expect(extractHeredocBodies("python3 - <<'PY'\nfetch()")).toEqual([{ marker: 'PY', body: 'fetch()' }])
  })
})

describe('verbIndex steps over wrappers and their flags (WRAPFLAG901)', () => {
  it('finds the verb behind a wrapper', () => {
    expect(verbIndex(['curl', '-s', 'x'])).toBe(0)
    expect(verbIndex(['env', 'curl', '-s', 'x'])).toBe(1)
    expect(verbIndex(['sudo', 'nohup', 'curl'])).toBe(2)
  })

  it("steps over the wrapper's OWN flags and numeric arguments", () => {
    expect(verbIndex(['stdbuf', '-oL', 'curl'])).toBe(2)
    expect(verbIndex(['nice', '-n', '10', 'curl'])).toBe(3)
    expect(verbIndex(['timeout', '300', 'curl'])).toBe(2)
    expect(verbIndex(['xargs', '-I{}', 'curl'])).toBe(2)
  })

  it('steps over env assignments', () => {
    expect(verbIndex(['FOO=1', 'BAR=2', 'curl'])).toBe(2)
  })

  it("does NOT eat the verb's own flags when there is no wrapper", () => {
    // If the flag-skip ran unconditionally, `curl -s <url>` would lose its verb
    // and every fetch would read as no-fetcher.
    expect(verbIndex(['-s', 'curl'])).toBe(0)
    expect(verbIndex([])).toBe(0)
  })
})
