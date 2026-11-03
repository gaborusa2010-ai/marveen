// JavaScript twin of heredoc_scan.py; keep both in parity via the shared case file.

const META = new Set(' \t\n;&|()<>');

function opener(cmd, i) {
  const n = cmd.length;
  let j = i + 2;
  let dash = false;
  if (j < n && cmd[j] === '-') {
    dash = true;
    j += 1;
  }
  while (j < n && ' \t'.includes(cmd[j])) {
    j += 1;
  }
  let k = j;
  let quoted = false;
  const tag = [];
  while (k < n && !META.has(cmd[k])) {
    const c = cmd[k];
    if (c === "'") {
      const e = cmd.indexOf("'", k + 1);
      if (e < 0) return null;
      tag.push(cmd.slice(k + 1, e));
      quoted = true;
      k = e + 1;
    } else if (c === '"') {
      k += 1;
      while (k < n && cmd[k] !== '"') {
        if (cmd[k] === '\\' && k + 1 < n && '$`"\\'.includes(cmd[k + 1])) {
          k += 1;
        } else if ('$`'.includes(cmd[k])) {
          return null;
        }
        tag.push(cmd[k]);
        k += 1;
      }
      if (k >= n) return null;
      quoted = true;
      k += 1;
    } else if (c === '\\') {
      if (k + 1 >= n || cmd[k + 1] === '\n') return null;
      tag.push(cmd[k + 1]);
      quoted = true;
      k += 2;
    } else if ('$`'.includes(c)) {
      return null;
    } else {
      tag.push(c);
      k += 1;
    }
  }
  const joined = tag.join('');
  if (k === j || tag.length === 0 || !joined) return null;
  return [dash, quoted, joined, k];
}

function bodies(cmd, nl, pending, out) {
  const n = cmd.length;
  let pos = nl + 1;
  let end = n;
  for (const [start, tailStart, dash, quoted, tag] of pending) {
    const bodyStart = Math.min(pos, n);
    let closed = false;
    let bodyEnd;
    while (pos < n) {
      const e = cmd.indexOf('\n', pos);
      const lineEnd = e < 0 ? n : e;
      const line = cmd.slice(pos, lineEnd);
      const compared = dash ? line.replace(/^\t+/, '') : line;
      if (compared === tag) {
        bodyEnd = pos;
        end = lineEnd;
        closed = true;
        break;
      }
      pos = lineEnd + 1;
    }
    if (!closed) {
      bodyEnd = n;
      end = n;
    }
    out.push({
      start,
      tailStart,
      lineEnd: nl,
      tag,
      quoted,
      dash,
      bodyStart,
      bodyEnd,
      end,
      closed,
    });
    pos = Math.min(end + 1, n);
  }
  return end;
}

/** The text of a $'...' word as bash expands it (ANSI-C escapes); an escape bash does
 *  not know keeps its backslash. Twin of heredoc_scan.ansi_c (#777). */
const ANSI_SIMPLE = { a: '\x07', b: '\b', e: '\x1b', E: '\x1b', f: '\f', n: '\n', r: '\r', t: '\t', v: '\v',
  '\\': '\\', "'": "'", '"': '"', '?': '?' };

export function ansiC(raw) {
  let out = '';
  let i = 0;
  const n = raw.length;
  while (i < n) {
    const c = raw[i];
    if (c !== '\\' || i + 1 >= n) { out += c; i += 1; continue; }
    const e = raw[i + 1];
    if (Object.hasOwn(ANSI_SIMPLE, e)) { out += ANSI_SIMPLE[e]; i += 2; continue; }
    if ('01234567'.includes(e)) {
      const m = /^[0-7]{1,3}/.exec(raw.slice(i + 1));
      out += String.fromCharCode(parseInt(m[0], 8) & 0xff);
      i += 1 + m[0].length;
      continue;
    }
    if ('xuU'.includes(e)) {
      const width = { x: 2, u: 4, U: 8 }[e];
      const m = new RegExp('^[0-9A-Fa-f]{1,' + width + '}').exec(raw.slice(i + 2));
      if (!m) { out += '\\' + e; i += 2; continue; }
      const cp = parseInt(m[0], 16);
      out += cp <= 0x10ffff ? String.fromCodePoint(cp) : '\\' + e + m[0];
      i += 2 + m[0].length;
      continue;
    }
    if (e === 'c' && i + 2 < n) {
      let x = raw[i + 2];
      if (x === '\\' && i + 3 < n && raw[i + 3] === '\\') i += 1;   // \c\\ : the backslash is the control argument
      const u = x.toUpperCase().charCodeAt(0);
      out += u >= 63 && u <= 95 ? String.fromCharCode(u ^ 64) : '\\c' + x;
      i += 3;
      continue;
    }
    out += '\\' + e;
    i += 2;
  }
  return out;
}

/** The index after the `$( )` or backtick substitution starting at i, read with its own
 *  quotes; null when it is not closed. A newline steps over the heredoc bodies on that line. */
export function subEnd(cmd, i, heredocs) {
  const n = cmd.length;
  if (cmd[i] === '`') {
    const e = cmd.indexOf('`', i + 1);
    return e < 0 ? null : e + 1;
  }
  let depth = 1;
  let k = i + 2;
  while (k < n && depth) {
    const c = cmd[k];
    if (c === '\\') k += 2;
    else if (c === "'") {
      const e = cmd.indexOf("'", k + 1);
      if (e < 0) return null;
      k = e + 1;
    } else if (c === '"') {
      k += 1;
      while (k < n && cmd[k] !== '"') k += cmd[k] === '\\' ? 2 : 1;
      k += 1;
    } else if (c === '\n') {
      const ends = heredocs.filter((h) => (h.kind ?? 'heredoc') === 'heredoc' && h.lineEnd === k).map((h) => h.end);
      k = ends.length ? Math.max(...ends) + 1 : k + 1;
    } else if (c === '(') { depth += 1; k += 1; }
    else if (c === ')') { depth -= 1; k += 1; }
    else k += 1;
  }
  return depth === 0 ? k : null;
}

/** The word after a `<<<` whose text starts at j (blanks skipped): [text, dynamic, end],
 *  the text as bash passes it; `dynamic` says the shell still expands something in it
 *  ($VAR, $( ), backtick, glob). null when the word is not terminated. Twin of
 *  heredoc_scan._here_word; the same decoder reads a `-c` program word (#777).
 *  `inBacktick`: the `<<<` stands inside a backtick substitution, where the first
 *  unescaped backtick ends the substitution and with it the word (#777 review 3). Its value
 *  is the backtick LEVEL (#777 review 4): 1 = inside `...`, where `\`` opens a nested
 *  substitution (a dynamic part of the word); 2 = inside that nested `\`...\``, where `\``
 *  is the closer and ends the word. */
export function hereWord(cmd, j, heredocs, inBacktick = 0) {
  const n = cmd.length;
  while (j < n && (cmd[j] === ' ' || cmd[j] === '\t')) j += 1;
  let out = '';
  let dyn = false;
  let i = j;
  while (i < n) {
    const c = cmd[i];
    if (META.has(c) || (inBacktick && c === '`')) break;
    if (c === '\\') {
      if (i + 1 >= n) return null;
      if (cmd[i + 1] === '`' && inBacktick) {
        if (inBacktick >= 2) break;                  // the escaped closer of the nested substitution
        const k = cmd.indexOf('\\`', i + 2);        // a nested substitution as (part of) the word
        if (k < 0) return null;
        out += cmd.slice(i, k + 2);
        dyn = true;
        i = k + 2;
        continue;
      }
      if (cmd[i + 1] !== '\n') out += cmd[i + 1];
      i += 2;
      continue;
    }
    if (c === "'") {
      const e = cmd.indexOf("'", i + 1);
      if (e < 0) return null;
      out += cmd.slice(i + 1, e);
      i = e + 1;
      continue;
    }
    if (cmd.startsWith("$'", i)) {
      let e = i + 2;
      while (e < n && cmd[e] !== "'") e += cmd[e] === '\\' ? 2 : 1;
      if (e >= n) return null;
      out += ansiC(cmd.slice(i + 2, e));
      i = e + 1;
      continue;
    }
    if (c === '"' || cmd.startsWith('$"', i)) {
      i += c === '$' ? 2 : 1;
      while (i < n && cmd[i] !== '"') {
        if (cmd[i] === '\\' && i + 1 < n && cmd[i + 1] === '`' && inBacktick) {
          if (inBacktick >= 2) return null;      // the nested substitution ends inside the open quote
          const k = cmd.indexOf('\\`', i + 2);
          if (k < 0) return null;
          out += cmd.slice(i, k + 2);
          dyn = true;
          i = k + 2;
          continue;
        }
        if (cmd[i] === '\\' && i + 1 < n) {
          if ('$`"\\'.includes(cmd[i + 1])) out += cmd[i + 1];
          else if (cmd[i + 1] !== '\n') out += cmd.slice(i, i + 2);
          i += 2;
          continue;
        }
        if (inBacktick && cmd[i] === '`') return null;   // the substitution ends inside the open quote
        if (cmd.startsWith('$(', i) || cmd[i] === '`') {
          const k = subEnd(cmd, i, heredocs);
          if (k === null) return null;
          out += cmd.slice(i, k);
          dyn = true;
          i = k;
          continue;
        }
        if (cmd[i] === '$') dyn = true;
        out += cmd[i];
        i += 1;
      }
      if (i >= n) return null;
      i += 1;
      continue;
    }
    if (cmd.startsWith('$(', i) || c === '`') {
      const k = subEnd(cmd, i, heredocs);
      if (k === null) return null;
      out += cmd.slice(i, k);
      dyn = true;
      i = k;
      continue;
    }
    if ('$*?[{'.includes(c)) dyn = true;
    else if (c === '~' && !out) dyn = true;
    out += c;
    i += 1;
  }
  return [out, dyn, i];
}

/** { heredocs, certain }: the heredocs only (the gates' data/code split). */
export function find(value) {
  const { heredocs, certain } = scan(value);
  return { heredocs, certain };
}

/** { records, certain }: heredocs and here-strings together, sorted by start. A here-string
 *  record has kind 'herestring', start = the `<<<`, tailStart = after the word, text = the
 *  DECODED word, dynamic; its body range is empty. Twin of heredoc_scan.find_all (#777). */
export function findAll(value) {
  const cmd = String(value ?? '');
  let { heredocs, certain, herestrings } = scan(cmd);
  const records = [...heredocs];
  for (const [i, inBt] of herestrings) {
    const w = hereWord(cmd, i + 3, heredocs, inBt);
    if (w === null) {
      // Unterminated word: still a record, with the RAW rest of the line as its text, so
      // every consumer judges it (over-block at the source, #777 review 3); certain=false
      // stays the signal that this is not what bash would pass.
      certain = false;
      let e = cmd.indexOf('\n', i + 3);
      if (e < 0) e = cmd.length;
      let raw = cmd.slice(i + 3, e).trim();
      raw = raw.startsWith("$'") || raw.startsWith('$"') ? raw.slice(2) : "'\"".includes(raw[0] ?? '-') ? raw.slice(1) : raw;
      records.push({ kind: 'herestring', start: i, tailStart: e, lineEnd: e, tag: null, quoted: false,
        dash: false, bodyStart: e, bodyEnd: e, end: e, closed: false, text: raw, dynamic: true });   // the open quote itself is dropped
      continue;
    }
    let [text, dynamic, k] = w;
    const nul = text.indexOf('\0');
    if (nul >= 0) text = text.slice(0, nul);   // bash cuts the word at a NUL (measured)
    const e = cmd.indexOf('\n', k);
    records.push({ kind: 'herestring', start: i, tailStart: k, lineEnd: e < 0 ? cmd.length : e, tag: null,
      quoted: false, dash: false, bodyStart: k, bodyEnd: k, end: k, closed: true, text, dynamic });
  }
  records.sort((a, b) => a.start - b.start);
  return { records, certain };
}

function scan(value) {
  const cmd = String(value ?? '');
  const out = [];
  const herestrings = [];
  let pending = [];
  const stack = [];
  const depth = [];
  let certain = true;
  let wordStart = true;
  let brackets = 0;
  let i = 0;
  const n = cmd.length;

  function push(ctx, width) {
    stack.push(ctx);
    depth.push(0);
    return i + width;
  }

  function pop() {
    stack.pop();
    depth.pop();
  }

  while (i < n) {
    const ch = cmd[i];
    const ctx = stack.length ? stack[stack.length - 1] : 'c';
    if (ctx === "'") {
      const e = cmd.indexOf("'", i);
      if (e < 0) break;
      pop();
      i = e + 1;
      continue;
    }
    if (["$'", '"', '${'].includes(ctx)) {
      if (ch === '\\' && ctx === '"' && cmd.startsWith('\\`', i) && stack.some((s) => s === '`' || s === '\\`')) {
        // `\`` in double quotes inside a backtick substitution: a nested substitution
        // ("echo `echo \"\`bash <<< 'x'\`\"`" runs the word, measured, #777 review 4)
        i = push('\\`', 2);
        wordStart = true;
      } else if (ch === '\\') {
        i += 2;
      } else if ((ctx === "$'" && ch === "'") || (ctx === '"' && ch === '"') || (ctx === '${' && ch === '}')) {
        pop();
        i += 1;
      } else if (ctx === "$'") {
        i += 1;
      } else if (cmd.startsWith('$((', i)) {
        i = push('A', 3);
      } else if (cmd.startsWith('$[', i)) {
        i = push('B', 2);
      } else if (cmd.startsWith('$(', i)) {
        i = push('$(', 2);
        wordStart = true;
      } else if (cmd.startsWith('${', i)) {
        i = push('${', 2);
      } else if (ch === '`') {
        i = push('`', 1);
        wordStart = true;
      } else if (ctx === '${' && "'\"".includes(ch)) {
        i = push(ch, 1);
      } else {
        if (ctx === '${' && cmd.startsWith('<<', i)) certain = false;
        i += 1;
      }
      continue;
    }
    if (ctx === 'A' || ctx === 'B') {
      if ("'\"".includes(ch)) {
        i = push(ch, 1);
      } else if (cmd.startsWith('$((', i)) {
        i = push('A', 3);
      } else if (cmd.startsWith('$(', i)) {
        i = push('$(', 2);
        wordStart = true;
      } else if (cmd.startsWith('${', i)) {
        i = push('${', 2);
      } else if (ch === '`') {
        i = push('`', 1);
        wordStart = true;
      } else if (ch === '(') {
        depth[depth.length - 1] += 1;
        i += 1;
      } else if (ch === ')' && depth[depth.length - 1]) {
        depth[depth.length - 1] -= 1;
        i += 1;
      } else if (ch === ')' && ctx === 'A' && cmd.startsWith('))', i)) {
        pop();
        i += 2;
      } else if (ch === ')') {
        // Bash re-reads an unclosed arithmetic expression as nested parentheses.
        certain = false;
        pop();
        i += 1;
      } else if (ch === ']' && ctx === 'B') {
        pop();
        i += 1;
      } else {
        i += 1;
      }
      continue;
    }
    // Shell text: top level, command substitutions, backticks and subshells.
    if (ch === '\\') {
      if (cmd.startsWith('\\\n', i)) {
        i += 2;
      } else if (cmd.startsWith('\\`', i) && ctx === '`') {
        // `\`` inside a backtick substitution opens a NESTED one (#777 review 4:
        // "echo `echo \`bash <<< 'x'\``" runs the word, measured with bash)
        i = push('\\`', 2);
        wordStart = true;
      } else if (cmd.startsWith('\\`', i) && ctx === '\\`') {
        pop();
        i += 2;
        wordStart = false;
      } else {
        i += 2;
        wordStart = false;
      }
      continue;
    }
    if (ch === '\n') {
      if (pending.length) {
        i = bodies(cmd, i, pending, out);
        pending = [];
      } else {
        i += 1;
      }
      wordStart = true;
      brackets = 0;
      continue;
    }
    if (ch === '#' && wordStart) {
      let e = cmd.indexOf('\n', i);
      e = e < 0 ? n : e;
      if (ctx === '`' || ctx === '\\`') {
        for (const b of [cmd.indexOf('`', i), cmd.indexOf('\\`', i)]) if (b >= 0) e = Math.min(e, b);
      }
      i = e;
      continue;
    }
    if ("'\"".includes(ch)) {
      i = push(ch, 1);
      wordStart = false;
      continue;
    }
    if (cmd.startsWith("$'", i)) {
      i = push("$'", 2);
      wordStart = false;
      continue;
    }
    if (cmd.startsWith('$((', i) || (wordStart && cmd.startsWith('((', i))) {
      i = push('A', ch === '$' ? 3 : 2);
      wordStart = false;
      continue;
    }
    if (cmd.startsWith('$[', i)) {
      i = push('B', 2);
      wordStart = false;
      continue;
    }
    if (cmd.startsWith('$(', i)) {
      i = push('$(', 2);
      wordStart = true;
      continue;
    }
    if (cmd.startsWith('${', i)) {
      i = push('${', 2);
      wordStart = false;
      continue;
    }
    if (ch === '`') {
      if (ctx === '\\`') {
        // an unescaped backtick ends the OUTER substitution with the nested one open
        certain = false;
        while (stack.length && stack[stack.length - 1] === '\\`') pop();
      }
      if (stack.length && stack[stack.length - 1] === '`') {
        pop();
        i += 1;
        wordStart = false;
      } else {
        i = push('`', 1);
        wordStart = true;
      }
      continue;
    }
    if (ch === '(') {
      // Array values and extglobs are one word to Bash.
      const wordParen = !wordStart && i !== 0 && '=@?*+!'.includes(cmd[i - 1]);
      i = push(wordParen ? '=(' : '(', 1);
      wordStart = true;
      brackets = 0;
      continue;
    }
    if (ch === ')') {
      if (['$(', '(', '=('].includes(ctx)) pop();
      else certain = false;
      i += 1;
      wordStart = true;
      brackets = 0;
      continue;
    }
    if (cmd.startsWith('<<<', i)) {
      herestrings.push([i, ctx === '\\`' ? 2 : ctx === '`' ? 1 : 0]);
      i += 3;
      wordStart = true;
      brackets = 0;
      continue;
    }
    if (cmd.startsWith('<<', i)) {
      const op = ctx === '=(' || brackets ? null : opener(cmd, i);
      if (op === null) {
        certain = false;
        i += 2;
        wordStart = true;
        continue;
      }
      const [dash, quoted, tag, k] = op;
      pending.push([i, k, dash, quoted, tag]);
      i = k;
      wordStart = false;
      continue;
    }
    if (ch === '[') {
      brackets += 1;
    } else if (ch === ']' && brackets) {
      brackets -= 1;
    }
    wordStart = ' \t;&|<>'.includes(ch);
    if (wordStart) brackets = 0;
    i += 1;
  }
  if (stack.length) certain = false;
  if (pending.length) bodies(cmd, n, pending, out);
  if (out.some((heredoc) => !heredoc.closed)) certain = false;
  return { heredocs: out, certain, herestrings };
}
