"""Quote- and comment-aware heredoc finder for the Bash gates (card #729).

The gates used to find heredocs with a per-line regex. Measured on main (card #729,
Cody msg 14663, Marveen review 2932): a `<<EOF` inside quotes or a comment opened a
"body" that swallowed the payload lines after it; a non-word, backslash or partly
quoted tag (<<'END-X', <<\\EOF, <<E\\OF, <<E"O"F) was read short or not at all; a
shift in $(( )) or a[ ]= could be read as an opener.

find(cmd) -> (heredocs, certain)

  heredocs  every heredoc whose `<<` stands in shell text (top level, $( ),
            backticks, a subshell), in order.
  certain   False when the command holds a shape this scan does not mirror: an
            unclosed quote, paren or arithmetic, a stray `)` (case patterns), a `<<`
            inside ${ }, an array value a=( ) or an open [ in the word, a tag it
            cannot read, a heredoc with no closing line. bash may read such a
            command differently (a syntax error drops the line and runs the next;
            a heredoc in $( ) ends at the `)`), so a gate must not blank anything
            on an uncertain command: it over-blocks instead of letting a line past.

Only the gates' data/code split depends on this; the mail gates keep their own
scanner (#725).
"""

_META = frozenset(" \t\n;&|()<>")
_SHELLISH = ("c", "$(", "`", "(", "=(")


class Heredoc:
    """start: the `<<`; tail_start: after the tag word (the rest of the opener line
    is ordinary shell text); line_end: the newline of the opener line; body_start,
    body_end: the body; end: after the closing line; tag, quoted (no expansion in
    the body), dash (<<-), closed (a closing line was found)."""
    __slots__ = ("start", "tail_start", "line_end", "tag", "quoted", "dash",
                 "body_start", "body_end", "end", "closed")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _opener(cmd, i):
    """(dash, quoted, tag, index after the tag word) for the `<<` at i, or None."""
    n, j, dash = len(cmd), i + 2, False
    if j < n and cmd[j] == "-":
        dash, j = True, j + 1
    while j < n and cmd[j] in " \t":
        j += 1
    k, quoted, tag = j, False, []
    while k < n and cmd[k] not in _META:
        c = cmd[k]
        if c == "'":
            e = cmd.find("'", k + 1)
            if e < 0:
                return None
            tag.append(cmd[k + 1:e])
            quoted, k = True, e + 1
        elif c == '"':
            k += 1
            while k < n and cmd[k] != '"':
                if cmd[k] == "\\" and k + 1 < n and cmd[k + 1] in '$`"\\':
                    k += 1
                elif cmd[k] in "$`":
                    return None
                tag.append(cmd[k])
                k += 1
            if k >= n:
                return None
            quoted, k = True, k + 1
        elif c == "\\":
            if k + 1 >= n or cmd[k + 1] == "\n":
                return None
            tag.append(cmd[k + 1])
            quoted, k = True, k + 2
        elif c in "$`":
            return None
        else:
            tag.append(c)
            k += 1
    if k == j or not tag or not "".join(tag):
        return None
    return dash, quoted, "".join(tag), k


def _bodies(cmd, nl, pending, out):
    """Read the bodies of the heredocs opened on one line, in order, from the newline
    at nl; returns where the last closing line ends."""
    n, pos = len(cmd), nl + 1
    for start, tail_start, dash, quoted, tag in pending:
        body_start, closed = min(pos, n), False
        while pos < n:
            e = cmd.find("\n", pos)
            line_end = n if e < 0 else e
            line = cmd[pos:line_end]
            if (line.lstrip("\t") if dash else line) == tag:
                body_end, end, closed = pos, line_end, True
                break
            pos = line_end + 1
        if not closed:
            body_end = end = n
        out.append(Heredoc(start=start, tail_start=tail_start, line_end=nl, tag=tag,
                           quoted=quoted, dash=dash, body_start=body_start,
                           body_end=body_end, end=end, closed=closed))
        pos = min(end + 1, n)
    return end


def find(cmd):
    out, pending, stack, depth = [], [], [], []
    certain, word_start, brackets = True, True, 0
    i, n = 0, len(cmd)

    def push(ctx, width):
        stack.append(ctx)
        depth.append(0)
        return i + width

    def pop():
        stack.pop()
        depth.pop()

    while i < n:
        ch, ctx = cmd[i], stack[-1] if stack else "c"
        if ctx == "'":
            e = cmd.find("'", i)
            if e < 0:
                break
            pop()
            i = e + 1
            continue
        if ctx in ("$'", '"', "${"):
            if ch == "\\":
                i += 2
            elif (ctx == "$'" and ch == "'") or (ctx == '"' and ch == '"') or (ctx == "${" and ch == "}"):
                pop()
                i += 1
            elif ctx == "$'":
                i += 1
            elif cmd.startswith("$((", i):
                i = push("A", 3)
            elif cmd.startswith("$[", i):
                i = push("B", 2)
            elif cmd.startswith("$(", i):
                i = push("$(", 2)
                word_start = True
            elif cmd.startswith("${", i):
                i = push("${", 2)
            elif ch == "`":
                i = push("`", 1)
                word_start = True
            elif ctx == "${" and ch in "'\"":
                i = push(ch, 1)
            else:
                if ctx == "${" and cmd.startswith("<<", i):
                    certain = False
                i += 1
            continue
        if ctx in ("A", "B"):
            if ch in "'\"":
                i = push(ch, 1)
            elif cmd.startswith("$((", i):
                i = push("A", 3)
            elif cmd.startswith("$(", i):
                i = push("$(", 2)
                word_start = True
            elif cmd.startswith("${", i):
                i = push("${", 2)
            elif ch == "`":
                i = push("`", 1)
                word_start = True
            elif ch == "(":
                depth[-1] += 1
                i += 1
            elif ch == ")" and depth[-1]:
                depth[-1] -= 1
                i += 1
            elif ch == ")" and ctx == "A" and cmd.startswith("))", i):
                pop()
                i += 2
            elif ch == ")":
                # bash re-reads an (( or $(( that no adjacent )) closes as nested
                # parens; not mirrored
                certain = False
                pop()
                i += 1
            elif ch == "]" and ctx == "B":
                pop()
                i += 1
            else:
                i += 1
            continue
        # shell text: top level, $( ), backticks, ( ), a=( )
        if ch == "\\":
            if cmd.startswith("\\\n", i):
                i += 2
            else:
                i, word_start = i + 2, False
            continue
        if ch == "\n":
            if pending:
                i, pending = _bodies(cmd, i, pending, out), []
            else:
                i += 1
            word_start, brackets = True, 0
            continue
        if ch == "#" and word_start:
            e = cmd.find("\n", i)
            e = n if e < 0 else e
            if ctx == "`":
                b = cmd.find("`", i)
                e = e if b < 0 else min(e, b)
            i = e
            continue
        if ch in "'\"":
            i, word_start = push(ch, 1), False
            continue
        if cmd.startswith("$'", i):
            i, word_start = push("$'", 2), False
            continue
        if cmd.startswith("$((", i) or (word_start and cmd.startswith("((", i)):
            i, word_start = push("A", 3 if ch == "$" else 2), False
            continue
        if cmd.startswith("$[", i):
            i, word_start = push("B", 2), False
            continue
        if cmd.startswith("$(", i):
            i, word_start = push("$(", 2), True
            continue
        if cmd.startswith("${", i):
            i, word_start = push("${", 2), False
            continue
        if ch == "`":
            if ctx == "`":
                pop()
                i, word_start = i + 1, False
            else:
                i, word_start = push("`", 1), True
            continue
        if ch == "(":
            # a=( ) and an extglob @( ) are one word to bash: a << in it opens
            # nothing (a syntax error, bash drops the line)
            word_paren = not word_start and i and cmd[i - 1] in "=@?*+!"
            i, word_start = push("=(" if word_paren else "(", 1), True
            brackets = 0
            continue
        if ch == ")":
            if ctx in ("$(", "(", "=("):
                pop()
            else:
                certain = False
            i, word_start, brackets = i + 1, True, 0
            continue
        if cmd.startswith("<<<", i):
            i, word_start, brackets = i + 3, True, 0
            continue
        if cmd.startswith("<<", i):
            op = None if ctx == "=(" or brackets else _opener(cmd, i)
            if op is None:
                certain = False
                i, word_start = i + 2, True
                continue
            dash, quoted, tag, k = op
            pending.append((i, k, dash, quoted, tag))
            i, word_start = k, False
            continue
        if ch == "[":
            brackets += 1
        elif ch == "]" and brackets:
            brackets -= 1
        word_start = ch in " \t;&|<>"
        if word_start:
            brackets = 0
        i += 1
    if stack:
        certain = False
    if pending:
        _bodies(cmd, n, pending, out)
    if any(not h.closed for h in out):
        certain = False
    return out, certain
