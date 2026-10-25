#!/usr/bin/env python3
"""Look up what the owner already decided, BEFORE asking him (card #664, 127475eb).

Why this exists (owner, Telegram msg 9199, 2026-10-25): most errors come from
forgetting something already discussed and recorded. Measured the same day:
docs/GAZDA-DONTESEK.md holds five "already discussed" cases, and each time the
decision WAS written down; nobody looked at it in the moment. The rule "look
before you ask" (CLAUDE.md, GAZDA-DONTESEK section) existed for weeks as good
will. This script is the lookup; scripts/hooks/dontes-lookup-gate.py is the
gate that makes an owner-facing question wait for it.

Searches, in this order:
  1. docs/GAZDA-DONTESEK.md   the decision log. An entry starts at a '##' or
                              '###' heading or at a top-level '- YYYY-MM-DD'
                              bullet (all three shapes are in the file), and
                              ends at the next one, an H1 or a '---' rule.
  2. store/lezart-ugyek.json  closed topics: id, what, decision; the topic's
                              own regex is also tried against the query.
  3. store/skill-write-grants.json   standing skill-write grants.
  4. docs/tudas/vedett-fa-es-engedelyek.md   the other standing grants
                              (build+restart, commit+push, quarantine list),
                              searched paragraph by paragraph.

Matching: accent- and case-insensitive substring match per term; every term
must match (AND), or any of them with --any. LIMIT: keyword match only. The
same decision in other words is not found; "NINCS TALALAT" means "no entry
with these words", not "never decided".

Usage: dontes-kereso.py [--any] [--max N] [--agent NAME] "<terms>" [more terms]
Exit: 0 = hits printed, 1 = NINCS TALALAT (a valid run), 2 = bad input.

Every valid run (exit 0 or 1) is recorded in store/dontes-kereso/<agent>.jsonl
(ts, session, terms, hit count), which the gate reads. Agent = --agent, else
env MARVEEN_AGENT, else agents/<name> in the cwd, else agents/<name> in
TELEGRAM_STATE_DIR, else "marveen". The identity is self-asserted, like
everywhere else in the fleet; the session id (CLAUDE_CODE_SESSION_ID) is
recorded too, so the gate can still find the run when the cwd moved.
Stdlib only.
"""
import json
import os
import re
import sys
import time
import unicodedata

ROOT = os.environ.get("DONTES_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISIONS = os.environ.get("DONTES_FILE") or os.path.join(ROOT, "docs", "GAZDA-DONTESEK.md")
TOPICS = os.environ.get("CLOSED_TOPICS_FILE") or os.path.join(ROOT, "store", "lezart-ugyek.json")
GRANTS = os.environ.get("SKILL_GRANTS_FILE") or os.path.join(ROOT, "store", "skill-write-grants.json")
GRANTS_DOC = os.environ.get("GRANTS_DOC_FILE") or os.path.join(ROOT, "docs", "tudas", "vedett-fa-es-engedelyek.md")
STATE_DIR = os.environ.get("DONTES_STATE_DIR") or os.path.join(ROOT, "store", "dontes-kereso")
STATE_KEEP = 200

MAX_QUERY_CHARS = 1000
MAX_TERMS = 20
QUOTE_MAX = 300
FIELD_MAX = 240

AGENT_RE = re.compile(r"/agents/([A-Za-z0-9_-]+)(?:/|$)")
AGENT_OK = re.compile(r"^[a-z0-9_-]{1,40}$")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?")
MSG_RE = re.compile(r"\b(?:msg|tg|message_id)\s*(\d{2,6})\b", re.I)
MSG_ID_RE = re.compile(r"\bmessage_id\s*(\d{2,6})\b", re.I)
ENTRY_START = re.compile(r"^(#{2,3})\s+\S|^-\s+\d{4}-\d{2}-\d{2}\b")


def fold(s: str) -> str:
    """Accent- and case-insensitive form: 'Ő' and 'o' compare equal."""
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def trim(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 3].rstrip() + "..."


def agent_name(cwd: str = "", env=None) -> str:
    env = os.environ if env is None else env
    named = fold(env.get("MARVEEN_AGENT", "")).strip()
    if AGENT_OK.match(named):
        return named
    for source in (cwd or os.getcwd(), env.get("TELEGRAM_STATE_DIR", "")):
        m = AGENT_RE.search(source or "")
        if m and AGENT_OK.match(m.group(1).lower()):
            return m.group(1).lower()
    return "marveen"


def state_path(agent: str) -> str:
    return os.path.join(STATE_DIR, agent + ".jsonl")


def record_run(agent: str, terms: list, hits: int, missing: list) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    path = state_path(agent)
    now = time.time()
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z",
        "t": now,
        "agent": agent,
        "session": os.environ.get("CLAUDE_CODE_SESSION_ID", ""),
        "terms": terms,
        "hits": hits,
    }
    if missing:
        entry["missing"] = missing
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    lines = lines[-(STATE_KEEP - 1):] + [json.dumps(entry, ensure_ascii=False)]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def last_runs(agent: str) -> list:
    """Recorded runs of one agent, oldest first; [] when there are none."""
    path = state_path(agent)
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("t"), (int, float)):
                out.append(rec)
    return out


def all_runs() -> list:
    out = []
    if not os.path.isdir(STATE_DIR):
        return out
    for name in sorted(os.listdir(STATE_DIR)):
        if name.endswith(".jsonl"):
            out.extend(last_runs(name[: -len(".jsonl")]))
    return out


# --- sources -----------------------------------------------------------------

def hit(source, line, date="", msg="", title="", mire="", quote=""):
    return {"source": source, "line": line, "date": date, "msg": msg,
            "title": trim(title, 140), "mire": trim(mire, FIELD_MAX), "quote": trim(quote, QUOTE_MAX)}


def first(regex, text, group=1):
    m = regex.search(text or "")
    return m.group(group) if m else ""


def date_of(text: str) -> str:
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    return m.group(1) + (" " + m.group(2) if m.group(2) else "")


def decision_entries(path: str):
    """(start line, [lines]) per entry of the decision log; template inside a code fence skipped."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start_at = 0
    for i, ln in enumerate(lines):
        if re.match(r"^#\s+DONTESEK\b", ln):
            start_at = i + 1
            break
    entries, cur, cur_start, fence = [], None, 0, False
    for i in range(start_at, len(lines)):
        ln = lines[i]
        if ln.lstrip().startswith("```"):
            fence = not fence
        if not fence and (re.match(r"^#\s", ln) or re.match(r"^-{3,}\s*$", ln)):
            if cur:
                entries.append((cur_start, cur))
            cur = None
            continue
        if not fence and ENTRY_START.match(ln):
            if cur:
                entries.append((cur_start, cur))
            cur, cur_start = [ln], i + 1
            continue
        if cur is not None:
            cur.append(ln)
    if cur:
        entries.append((cur_start, cur))
    return entries


# One-line field shapes ("Szo szerint: ... Mire: ... DONTES: ...", also as "- **Mire:**" bullets).
_NEXT_FIELD = r"(?=\s+-?\s*\**\s*(?:Mire|DONTES|Amit eldont|Amit NEM|Beirta|Gazda szo szerint|Szo szerint)\b|$)"
MIRE_INLINE = re.compile(r"Mire(?:\s+vonatkozik)?\s*:\**\s*(.*?)" + _NEXT_FIELD, re.I | re.S)
QUOTE_INLINE = re.compile(r"Szo\s+szerint\s*:\**\s*(.*?)" + _NEXT_FIELD, re.I | re.S)


def parse_decision(lines: list) -> dict:
    head = lines[0]
    title = re.sub(r"^#{2,3}\s+|^-\s+", "", head)
    if " -- " in title and head.startswith("#"):
        title = title.split(" -- ", 1)[1]
    text = "\n".join(lines)
    top = "\n".join(lines[:3])
    date = date_of(top) or date_of(text)
    msg = first(MSG_ID_RE, text) or first(MSG_RE, top) or first(MSG_RE, text)
    mire, quote = "", ""
    for j, ln in enumerate(lines):
        if re.search(r"Mire vonatkozik\s*:", ln, re.I) and not mire:
            parts = [re.split(r"Mire vonatkozik\s*:\**", ln, maxsplit=1, flags=re.I)[1]]
            for nxt in lines[j + 1:]:
                if not nxt.strip() or nxt.lstrip().startswith(("- ", "**", ">", "#")):
                    break
                parts.append(nxt)
            mire = " ".join(parts)
    quoted = [ln.strip().lstrip(">").strip() for ln in lines if ln.strip().startswith(">")]
    if quoted:
        quote = " ".join(quoted)
    flat = re.sub(r"\s+", " ", text)
    if not mire:
        mire = first(MIRE_INLINE, flat)
    if not quote:
        quote = first(QUOTE_INLINE, flat)
    return {"date": date, "msg": msg, "title": title, "mire": mire, "quote": quote.strip("*_ ")}


def search_decisions(path, match):
    out = []
    for start, lines in decision_entries(path):
        if match(fold("\n".join(lines))):
            p = parse_decision(lines)
            out.append(hit(path, start, **p))
    return out


def line_of(raw_lines: list, needle: str) -> int:
    for i, ln in enumerate(raw_lines):
        if needle in ln:
            return i + 1
    return 0


def search_topics(path, match, raw_query):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    data = json.loads(raw)
    raw_lines = raw.splitlines()
    out = []
    for t in data.get("ugyek", []) if isinstance(data, dict) else []:
        if not isinstance(t, dict):
            continue
        tid, what, dec = str(t.get("id", "")), str(t.get("what", "")), str(t.get("decision", ""))
        by_regex = False
        try:
            by_regex = bool(t.get("pattern")) and bool(re.search(t["pattern"], raw_query, re.I))
        except re.error:
            pass
        if by_regex or match(fold(" ".join((tid, what, dec)))):
            out.append(hit(path, line_of(raw_lines, f'"{tid}"'), date=date_of(dec) or date_of(str(t.get("since_utc", ""))),
                           msg=first(MSG_RE, dec), title=f"LEZART UGY {tid}", mire=what, quote=dec))
    return out


def search_grants(path, match):
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    data = json.loads(raw)
    raw_lines = raw.splitlines()
    out = []
    for g in data.get("grants", []) if isinstance(data, dict) else []:
        if not isinstance(g, dict):
            continue
        agent = str(g.get("agent", ""))
        skills = g.get("skills") if isinstance(g.get("skills"), list) else []
        text = " ".join([agent, " ".join(map(str, skills)), str(g.get("reason", "")),
                         str(g.get("source", "")), str(g.get("approved_at", ""))])
        if match(fold(text)):
            out.append(hit(path, line_of(raw_lines, f'"agent": "{agent}"'), date=str(g.get("approved_at", "")),
                           msg=first(MSG_RE, str(g.get("reason", "")) + " " + str(g.get("source", ""))),
                           title=f"ALLANDO SKILL-IRAS ENGEDELY: {agent}",
                           mire=f"{agent}: " + ", ".join(map(str, skills)), quote=str(g.get("source", ""))))
    return out


def search_grants_doc(path, match):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out, heading, block, block_start = [], "", [], 0

    def flush():
        if block and match(fold("\n".join(block))):
            text = " ".join(block)
            quote = first(re.compile(r'\*"(.+?)"\*'), text) or text
            out.append(hit(path, block_start, date=date_of(text), msg=first(MSG_RE, text),
                           title=heading or os.path.basename(path), mire=heading, quote=quote))

    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            flush()
            heading, block = ln.lstrip("#").strip(), []
        elif not ln.strip():
            flush()
            block = []
        else:
            if not block:
                block_start = i + 1
            block.append(ln)
    flush()
    return out


# --- main --------------------------------------------------------------------

def usage(msg: str) -> int:
    sys.stderr.write(f"HIBA: {msg}\nHasznalat: dontes-kereso.py [--any] [--max N] [--agent NEV] \"<kulcsszavak>\"\n")
    return 2


def parse_args(argv):
    mode_any, max_hits, agent, words = False, 10, None, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--any":
            mode_any = True
        elif a in ("--max", "--agent"):
            if i + 1 >= len(argv):
                raise ValueError(f"{a} utan hianyzik az ertek")
            val = argv[i + 1]
            i += 1
            if a == "--max":
                if not val.isdigit() or not 1 <= int(val) <= 200:
                    raise ValueError("--max 1 es 200 kozotti egesz szam")
                max_hits = int(val)
            else:
                agent = fold(val).strip()
                if not AGENT_OK.match(agent):
                    raise ValueError(f"ervenytelen agens-nev: {val!r}")
        elif a in ("-h", "--help"):
            raise ValueError("sugo")
        elif a.startswith("--"):
            raise ValueError(f"ismeretlen kapcsolo: {a}")
        else:
            words.append(a)
        i += 1
    query = " ".join(words)
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"tul hosszu kereses ({len(query)} karakter, legfeljebb {MAX_QUERY_CHARS})")
    terms = [t for t in (fold(w) for w in query.split()) if len(t) >= 2]
    if not terms:
        raise ValueError("nincs kulcsszo (legalabb egy, 2+ karakteres)")
    if len(terms) > MAX_TERMS:
        raise ValueError(f"tul sok kulcsszo ({len(terms)}, legfeljebb {MAX_TERMS})")
    return mode_any, max_hits, agent, query, list(dict.fromkeys(terms))


def main(argv) -> int:
    try:
        mode_any, max_hits, agent, query, terms = parse_args(argv)
    except ValueError as exc:
        return usage(str(exc))
    agent = agent or agent_name()

    def match(folded: str) -> bool:
        return (any if mode_any else all)(t in folded for t in terms)

    hits, missing = [], []
    searches = [
        (DECISIONS, lambda p: search_decisions(p, match)),
        (TOPICS, lambda p: search_topics(p, match, query)),
        (GRANTS, lambda p: search_grants(p, match)),
        (GRANTS_DOC, lambda p: search_grants_doc(p, match)),
    ]
    for path, fn in searches:
        if not os.path.isfile(path):
            missing.append(path)
            sys.stderr.write(f"FIGYELEM: hianyzo forras, nem kereshettem benne: {path}\n")
            continue
        try:
            hits.extend(fn(path))
        except Exception as exc:
            missing.append(path)
            sys.stderr.write(f"FIGYELEM: olvashatatlan forras ({type(exc).__name__}): {path}\n")

    try:
        record_run(agent, terms, len(hits), missing)
    except OSError as exc:
        sys.stderr.write(f"FIGYELEM: a futas nem rogzult ({exc}); a kapu ezt nem fogja latni\n")

    mode = "BARMELYIK" if mode_any else "MIND"
    if not hits:
        print(f"NINCS TALALAT ({mode}: {' '.join(terms)}). Ez csak annyit jelent, hogy ezekkel a szavakkal "
              "nincs bejegyzes; mas szavakkal is nezd meg, mielott kimondod, hogy nem dontott.")
        return 1
    print(f"{len(hits)} TALALAT ({mode}: {' '.join(terms)})" + (f", az elso {max_hits}:" if len(hits) > max_hits else ":"))
    for h in hits[:max_hits]:
        rel = os.path.relpath(h["source"], ROOT) if h["source"].startswith(ROOT) else h["source"]
        print(f"\n[{rel}:{h['line']}] {h['date'] or '?'} | msg {h['msg'] or '?'} | {h['title']}")
        print(f"  Mire vonatkozik: {h['mire'] or '(nincs megadva)'}")
        print(f"  Szo szerint: {h['quote'] or '(nincs idezet)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
