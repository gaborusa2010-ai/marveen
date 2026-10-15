#!/usr/bin/env python3
"""Append the VERBATIM title of every card a briefing references.

WHY THIS EXISTS
---------------
The briefing file is hand-written, and a hand-written card line is a paraphrase.
Measured on three consecutive nights of store/night-briefing-anorak.txt.sent-*,
card #238 ("A gazda megbizasa: hangok gyujtese a kollekcioba -- EGYELORE CSAK
ANGOL ES MAGYAR") reached Anorak three different ways, each losing something
different:

  09-21  "A gazda megbizasa: hangok gyujtese a kollekcioba"      -- the whole constraint gone
  09-22  "Hangok gyujtese a kollekcioba (EGYELORE felfuggesztve)" -- and WRONG: it is not suspended
  09-23  "Hangok gyujtese, EGYELORE CSAK ANGOL"                   -- half the mandate gone

Anorak caught it and guessed a fixed-width truncation in the caller's tooling.
That was refuted by measurement (the only generator cuts at [:90]; the title is
81 chars, so it never cuts) -- and the real cause is worse than a tool bug: a
fixed cut would lose the SAME words every night and could be fixed once, while a
paraphrase is re-invented nightly. So the fix cannot be discipline. This appends
the literal title next to whatever the prose said, so the mandate travels even
when the summary does not.

It is ADDITIVE and never authoritative: it does not rewrite, reorder or judge the
briefing text, because a hand-written handover carries context a title cannot.

CONTRACT
--------
  eligazitas-kartyacim-fuggelek.py <briefing-file>   -> augmented text on stdout
Exit 0 with the ORIGINAL text on stdout whenever anything goes wrong (no token,
API down, no card references, nothing new to say). The caller sends what it gets:
a briefing that reaches the agent slightly plainer beats one that does not go out.
"""
import os
import re
import sys
import json
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.environ.get("DASHBOARD_API", "http://localhost:3420")
HEADER = "A HIVATKOZOTT KARTYAK CIME SZO SZERINT (gepi kiegeszites, a fenti szoveget nem irja felul):"


def token():
    p = os.path.join(BASE, "store", ".dashboard-token")
    with open(p, encoding="utf-8") as fh:
        return fh.read().strip()


def cards():
    req = urllib.request.Request(
        API + "/api/kanban", headers={"Authorization": "Bearer " + token()})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    return d if isinstance(d, list) else d.get("cards", d.get("data", []))


def referenced(text):
    """Card numbers the briefing names. '#238' only -- a bare '238' is a number.

    Ordered by first appearance and de-duplicated, so the appendix reads in the
    same order as the prose above it.
    """
    out = []
    for m in re.finditer(r"#(\d{1,5})\b", text):
        n = int(m.group(1))
        if n not in out:
            out.append(n)
    return out


def mask_titles(text, cards_list):
    """Blank out every open card's title that the briefing quotes verbatim.

    A title can itself contain '#N' ("Build and launch utility website #1",
    cards #475-477). Read from the raw text, that '#1' became a reference to
    card #1 and earned a false "not found" line (Cody, msg 9402, 2026-10-13);
    the day a card #1 is open it would get the WRONG title beside it. Masking
    the quoted titles leaves only the numbers the prose itself names. A title
    long enough to be cut by a generator is also matched on its [:90] prefix.
    """
    for c in cards_list:
        t = (c.get("title") or "").strip()
        if len(t) < 8 or "#" not in t:
            continue
        for cand in (t, t[:90]):
            if cand in text:
                text = text.replace(cand, " " * len(cand))
                break
    return text


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: eligazitas-kartyacim-fuggelek.py <briefing-file>\n")
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        sys.stderr.write("nem olvashato: %s\n" % exc)
        return 2

    # Already appended (a retry after a rejected send re-reads the same file):
    # appending twice would make the agent read the block as two separate facts.
    if HEADER in text:
        sys.stdout.write(text)
        return 0

    seqs = referenced(text)
    if not seqs:
        sys.stdout.write(text)
        return 0

    try:
        board = cards()
        by_seq = {c.get("seq"): c for c in board}
    except Exception as exc:                      # noqa: BLE001 -- see CONTRACT
        sys.stderr.write("kartyalista nem jott meg (%s), az eligazitas valtozatlanul megy\n" % exc)
        sys.stdout.write(text)
        return 0

    seqs = referenced(mask_titles(text, board))

    lines = []
    for n in seqs:
        c = by_seq.get(n)
        if not c:
            # Named but absent is worth a line -- and the wording has to say what
            # was actually MEASURED, not what it looks like.
            #
            # Measured 2026-09-23 23:1x: GET /api/kanban returns only cards with
            # archived_at IS NULL (147 of 319 rows in the snapshot DB), it rejects
            # every query parameter with 400, and GET /api/kanban/<id> is 404. So
            # there is NO way over the API to tell an archived, closed card from
            # one that never existed. The first draft of this line said "NINCS
            # ILYEN KARTYA a tablan" and immediately produced a false alarm on
            # #307 -- a real, done card archived that same evening at 20:48.
            # A tool that cries wolf on a legitimate reference gets ignored.
            lines.append("  #%-5d [nem talalhato a NYITOTT kartyak kozott -- "
                         "archivalt (lezart) vagy nem letezo; az API nem tud "
                         "kulonbseget tenni]" % n)
            continue
        lines.append("  #%-5d %s  [%s / %s / %s]" % (
            n, c.get("title") or "(cim nelkul)", c.get("status") or "?",
            c.get("priority") or "?", c.get("assignee") or "nincs felelos"))

    if not lines:
        sys.stdout.write(text)
        return 0

    sep = "" if text.endswith("\n") else "\n"
    sys.stdout.write("%s%s\n%s\n%s\n" % (text, sep, HEADER, "\n".join(lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
