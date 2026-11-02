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

export function find(value) {
  const cmd = String(value ?? '');
  const out = [];
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
      if (ch === '\\') {
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
      if (ctx === '`') {
        const b = cmd.indexOf('`', i);
        e = b < 0 ? e : Math.min(e, b);
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
      if (ctx === '`') {
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
  return { heredocs: out, certain };
}
