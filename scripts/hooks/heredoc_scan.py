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

find_all(cmd) -> (records, certain)

  records   the heredocs above PLUS every here-string (`<<< word`) in shell text, as
            one list sorted by `start`; `kind` tells them apart ('heredoc' /
            'herestring'). A here-string record carries the word's `text` DECODED
            the way bash hands it to the command (quote removal, glued pieces
            joined, backslash escapes, $'..' ANSI-C escapes; $VAR, $( ) and
            backticks kept raw and flagged `dynamic`), because the gates judge
            that text as a program when a shell reads it (#777: `bash <<< $'cd
            /tmp\\nrm ...'`, `bash <<< 'rm'' ...'`, `bash <<< rm\\ ...` all
            ran, and every gate read the word raw or not at all).
  certain   as above; an unterminated here-string word also makes it False.

Only the gates' data/code split depends on this; the mail gates keep their own
scanner (#725).
"""

import re

_META = frozenset(" \t\n;&|()<>")
_SHELLISH = ("c", "$(", "`", "(", "=(")


class Heredoc:
    """start: the `<<`; tail_start: after the tag word (the rest of the opener line
    is ordinary shell text); line_end: the newline of the opener line; body_start,
    body_end: the body; end: after the closing line; tag, quoted (no expansion in
    the body), dash (<<-), closed (a closing line was found)."""
    __slots__ = ("start", "tail_start", "line_end", "tag", "quoted", "dash",
                 "body_start", "body_end", "end", "closed", "kind", "text", "dynamic")

    def __init__(self, **kw):
        self.kind, self.text, self.dynamic = "heredoc", None, False
        for k, v in kw.items():
            setattr(self, k, v)


_ANSI_SIMPLE = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r",
                "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def ansi_c(raw):
    """The text of a $'...' word as bash expands it (ANSI-C escapes). An escape bash
    does not know keeps its backslash. Measured against bash 5.3 (#777)."""
    out, i, n = [], 0, len(raw)
    while i < n:
        c = raw[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        e = raw[i + 1]
        if e in _ANSI_SIMPLE:
            out.append(_ANSI_SIMPLE[e])
            i += 2
        elif e in "01234567":
            m = re.match(r"[0-7]{1,3}", raw[i + 1:])
            out.append(chr(int(m.group(0), 8) & 0xFF))
            i += 1 + len(m.group(0))
        elif e in "xuU":
            width = {"x": 2, "u": 4, "U": 8}[e]
            m = re.match(r"[0-9A-Fa-f]{1,%d}" % width, raw[i + 2:])
            if not m:
                out.append("\\" + e)
                i += 2
                continue
            cp = int(m.group(0), 16)
            out.append(chr(cp) if cp <= 0x10FFFF else "\\" + e + m.group(0))
            i += 2 + len(m.group(0))
        elif e == "c" and i + 2 < n:
            x = raw[i + 2]
            if x == "\\" and i + 3 < n and raw[i + 3] == "\\":
                i += 1             # \c\\ : the backslash itself is the control argument
            out.append(chr(ord(x.upper()) ^ 64) if 63 <= ord(x.upper()) <= 95 else "\\c" + x)
            i += 3
        else:
            out.append("\\" + e)
            i += 2
    return "".join(out)


_WORD_END = frozenset(" \t\n;&|()<>")


def _here_word(cmd, j, heredocs, in_backtick=0):
    """The word after a `<<<` whose text starts at j (blanks skipped): (text, dynamic,
    end). `text` is the word as bash passes it; `dynamic` says the shell still expands
    something in it ($VAR, $( ), backtick, glob), which the gates cannot read. A
    newline inside $( ) or backticks steps over the heredoc bodies the first pass
    found on that line (`bash <<<"$(cat <<EOF ... EOF )"`), like the main scan does.
    None when the word is not terminated (an open quote or substitution).
    `in_backtick`: the `<<<` stands inside a backtick substitution, where the first
    unescaped backtick ends the substitution and with it the word (#777 review 3:
    "echo `bash <<< 'x'`" used to read the closing backtick as a nested one). Its value is
    the backtick LEVEL (#777 review 4): 1 = inside backticks, where a backslash-backtick
    opens a nested substitution (a dynamic part of the word); 2 = inside that nested
    substitution, where the backslash-backtick is the closer and ends the word."""
    n = len(cmd)
    while j < n and cmd[j] in " \t":
        j += 1
    out, dyn, i, depth = [], False, j, 0
    while i < n:
        c = cmd[i]
        if depth == 0 and (c in _WORD_END or (in_backtick and c == "`")):
            break
        if c == "\\":
            if i + 1 >= n:
                return None
            if cmd[i + 1] == "`" and in_backtick:
                if in_backtick >= 2:
                    break                        # the escaped closer of the nested substitution
                k = cmd.find("\\`", i + 2)        # a nested substitution as (part of) the word
                if k < 0:
                    return None
                out.append(cmd[i:k + 2])
                dyn, i = True, k + 2
                continue
            if cmd[i + 1] != "\n":
                out.append(cmd[i + 1])
            i += 2
            continue
        if c == "'":
            e = cmd.find("'", i + 1)
            if e < 0:
                return None
            out.append(cmd[i + 1:e])
            i = e + 1
            continue
        if cmd.startswith("$'", i):
            e = i + 2
            while e < n and cmd[e] != "'":
                e += 2 if cmd[e] == "\\" else 1
            if e >= n:
                return None
            out.append(ansi_c(cmd[i + 2:e]))
            i = e + 1
            continue
        if c == '"' or cmd.startswith('$"', i):
            i += 2 if c == "$" else 1
            while i < n and cmd[i] != '"':
                if cmd[i] == "\\" and i + 1 < n and cmd[i + 1] == "`" and in_backtick:
                    if in_backtick >= 2:
                        return None      # the nested substitution ends inside the open quote
                    k = cmd.find("\\`", i + 2)
                    if k < 0:
                        return None
                    out.append(cmd[i:k + 2])
                    dyn, i = True, k + 2
                    continue
                if cmd[i] == "\\" and i + 1 < n:
                    if cmd[i + 1] in '$`"\\':
                        out.append(cmd[i + 1])
                    elif cmd[i + 1] != "\n":
                        out.append(cmd[i:i + 2])
                    i += 2
                    continue
                if in_backtick and cmd[i] == "`":
                    return None          # the substitution ends inside the open quote
                if cmd.startswith("$(", i) or cmd[i] == "`":
                    k = _sub_end(cmd, i, heredocs)
                    if k is None:
                        return None
                    out.append(cmd[i:k])
                    dyn, i = True, k
                    continue
                if cmd[i] == "$":
                    dyn = True
                out.append(cmd[i])
                i += 1
            if i >= n:
                return None
            i += 1
            continue
        if cmd.startswith("$(", i) or c == "`":
            k = _sub_end(cmd, i, heredocs)
            if k is None:
                return None
            out.append(cmd[i:k])
            dyn, i = True, k
            continue
        if c in "$*?[{":
            dyn = True
        elif c == "~" and not out:
            dyn = True
        out.append(c)
        i += 1
    return "".join(out), dyn, i


def _sub_end(cmd, i, heredocs):
    """The index after the `$( )` or backtick substitution starting at i, read with
    its own quotes; None when it is not closed."""
    n = len(cmd)
    if cmd[i] == "`":
        e = cmd.find("`", i + 1)
        return None if e < 0 else e + 1
    depth, k = 1, i + 2
    while k < n and depth:
        c = cmd[k]
        if c == "\\":
            k += 2
        elif c == "'":
            e = cmd.find("'", k + 1)
            if e < 0:
                return None
            k = e + 1
        elif c == '"':
            k += 1
            while k < n and cmd[k] != '"':
                k += 2 if cmd[k] == "\\" else 1
            k += 1
        elif c == "\n":
            ends = [h.end for h in heredocs if h.kind == "heredoc" and h.line_end == k]
            k = max(ends) + 1 if ends else k + 1
        elif c == "(":
            depth += 1
            k += 1
        elif c == ")":
            depth -= 1
            k += 1
        else:
            k += 1
    return k if depth == 0 else None


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
    """(heredocs, certain): the heredocs only (the gates' data/code split)."""
    heredocs, certain, _hs = _scan(cmd)
    return heredocs, certain


def find_all(cmd):
    """(records, certain): heredocs and here-strings together, sorted by start."""
    heredocs, certain, offsets = _scan(cmd)
    out = list(heredocs)
    for i, in_bt in offsets:
        w = _here_word(cmd, i + 3, heredocs, in_bt)
        if w is None:
            # Unterminated word: still a record, with the RAW rest of the line as its
            # text, so every consumer judges it (over-block at the source, #777 review 3);
            # certain=False stays the signal that this is not what bash would pass.
            certain = False
            e = cmd.find("\n", i + 3)
            e = len(cmd) if e < 0 else e
            raw = cmd[i + 3:e].strip()
            raw = raw[2:] if raw.startswith(("$'", '$"')) else raw[1:] if raw[:1] in "'\"" else raw
            out.append(Heredoc(kind="herestring", start=i, tail_start=e, line_end=e, tag=None,
                               quoted=False, dash=False, body_start=e, body_end=e, end=e,
                               closed=False, text=raw, dynamic=True))   # the open quote itself is dropped
            continue
        text, dyn, k = w
        if "\0" in text:
            text = text[:text.index("\0")]   # bash cuts the word at a NUL (measured, $'\\0; X' ran nothing)
        e = cmd.find("\n", k)
        out.append(Heredoc(kind="herestring", start=i, tail_start=k, line_end=len(cmd) if e < 0 else e,
                           tag=None, quoted=False, dash=False, body_start=k, body_end=k, end=k,
                           closed=True, text=text, dynamic=dyn))
    out.sort(key=lambda h: h.start)
    return out, certain


def _scan(cmd):
    out, pending, stack, depth = [], [], [], []
    herestrings = []
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
            if ch == "\\" and ctx == '"' and cmd.startswith("\\`", i) and any(s in ("`", "\\`") for s in stack):
                # `\`` in double quotes inside a backtick substitution: a nested substitution
                # ("echo `echo \"\`bash <<< 'x'\`\"`" runs the word, measured, #777 review 4)
                i = push("\\`", 2)
                word_start = True
            elif ch == "\\":
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
            elif cmd.startswith("\\`", i) and ctx == "`":
                # `\`` inside a backtick substitution opens a NESTED one (#777 review 4:
                # "echo `echo \`bash <<< 'x'\``" runs the word, measured with bash)
                i, word_start = push("\\`", 2), True
            elif cmd.startswith("\\`", i) and ctx == "\\`":
                pop()
                i, word_start = i + 2, False
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
            if ctx in ("`", "\\`"):
                for b in (cmd.find("`", i), cmd.find("\\`", i)):
                    if b >= 0:
                        e = min(e, b)
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
            if ctx == "\\`":
                # an unescaped backtick ends the OUTER substitution with the nested one open
                certain = False
                while stack and stack[-1] == "\\`":
                    pop()
            if stack and stack[-1] == "`":
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
            herestrings.append((i, 2 if ctx == "\\`" else 1 if ctx == "`" else 0))
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
    return out, certain, herestrings
