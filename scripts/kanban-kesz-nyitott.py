#!/usr/bin/env python3
"""Report cards whose latest comment says they are done, without closing them.

WHY: work sometimes reaches a clear completion report in a comment while the
kanban status remains open.  The evening timer needs a deterministic, model-free
way to put those mismatches in front of marveen.  This script only reports the
evidence; deciding whether to close a card remains the main agent's job.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://localhost:%s" % os.environ.get("MARVEEN_WEB_PORT", "3420")
STATE = os.path.join(ROOT, "store", "kanban-kesz-nyitott.json")
TOKEN = os.path.join(ROOT, "store", ".dashboard-token")
DONE_WORDS = r"kesz|done|elkeszult|lezarhato|lezarva"
START_WORDS = r"kesz|done|lezart|elkeszult|lezarhato"
QUALIFIERS = (r"nem|majdnem|felig|felkesz|reszben|meg nem|lepes|lepese|step|"
              r"proba|minta|sample|samples|vazlat|terv|elso|masodik|prototipus|"
              r"csomag|resz|fazis|kor|reteg|oldali|fele|v2|v3")
ACCENTS = str.maketrans("áéíóöőúüű", "aeiooouuu")


def _fold(text):
    return str(text or "").lower().translate(ACCENTS)


def _strip_prefix(text):
    """Remove metadata commonly placed before the comment's actual sentence."""
    value = _fold(text).lstrip()
    prefix = re.compile(
        r"^(?:\[[^\]]*\]\s*|"
        r"[a-z][a-z0-9_-]*(?=\s*(?::|\d{4}-\d{2}-\d{2}))\s*|"
        r"\d{4}-\d{2}-\d{2}(?:[t ]\d{1,2}:\d{2})?\s*|"
        r"\d{1,2}:\d(?:\d|x)\s*|--|[-:,])\s*", re.I)
    while True:
        match = prefix.match(value)
        if not match:
            return value
        value = value[match.end():]


def _candidate_allowed(head, match):
    before = head[max(0, match.start() - 30):match.start()]
    after = head[match.end():match.end() + 40]
    if re.search(r"\b(?:%s)\b[^\w]*$" % QUALIFIERS, before):
        return False
    if re.search(r"(?:,\s*de\b|\sde\b|\sbut\b)", after):
        return False
    return True


def is_done_report(text, seq):
    """Return whether the first 160 content characters report this card done."""
    # The [FOLYAMATBAN] marker counts only in the leading metadata. A done
    # report may quote the marker further down ("the [FOLYAMATBAN] line above
    # is superseded"), and scanning the whole text silenced exactly those.
    folded = _fold(text).lstrip()
    rest = _strip_prefix(text)
    if "[folyamatban]" in folded[:len(folded) - len(rest)]:
        return False
    head = rest[:160]
    if re.search(r"\bfolyamatban\b", head):
        return False

    candidates = []
    start = re.match(r"\b(?:%s)\b" % START_WORDS, head)
    if start:
        candidates.append(start)

    card = re.search(r"#%s(?!\d)" % re.escape(str(seq)), head)
    if card:
        nearby = re.search(r"\b(?:%s)\b" % DONE_WORDS,
                           head[card.end():card.end() + 25])
        if nearby:
            nearby_start = card.end() + nearby.start()
            candidates.append(re.match(r"\b(?:%s)\b" % DONE_WORDS,
                                       head[nearby_start:]))
            candidates[-1] = _OffsetMatch(candidates[-1], nearby_start)

    phrases = (r"\bkartya\s+(?:kesz|lezarhato)\b|"
               r"\b(?:a\s+)?feladat\s+kesz\b|\bmunkaja\s+kesz\b|"
               r"\bmunka\s+kesz\b|\blezarhato\b")
    for phrase in re.finditer(phrases, head):
        done = list(re.finditer(r"\b(?:%s)\b" % DONE_WORDS,
                                head[phrase.start():phrase.end()]))[-1]
        candidate = _OffsetMatch(done, phrase.start())
        if candidate.group() == "lezarhato":
            prior = head[max(0, candidate.start() - 30):candidate.start()]
            if re.search(r"\bjegyzet\b.{0,20}$", prior):
                continue
        candidates.append(candidate)
    return any(_candidate_allowed(head, candidate) for candidate in candidates)


class _OffsetMatch:
    """Small match adapter used to retain offsets from sliced searches."""
    def __init__(self, match, offset):
        self.match = match
        self.offset = offset

    def start(self):
        return self.offset + self.match.start()

    def end(self):
        return self.offset + self.match.end()

    def group(self):
        return self.match.group()


def api_get(path):
    with open(TOKEN, encoding="utf-8") as handle:
        token = handle.read().strip()
    request = urllib.request.Request(BASE + path)
    request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode())


def _rows(value, key):
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return value[key]
    raise ValueError("API response has no %s list" % key)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as handle:
            value = json.load(handle)
        reported = value.get("reported", {})
        return reported if isinstance(reported, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def save_state(reported):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    temporary = STATE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump({"reported": reported}, handle, ensure_ascii=False, sort_keys=True)
    os.replace(temporary, STATE)


def findings():
    cards = _rows(api_get("/api/kanban"), "cards")
    result = []
    for card in cards:
        if card.get("status") == "done" or card.get("archived_at"):
            continue
        comments = _rows(api_get("/api/kanban/%s/comments" % card["id"]), "comments")
        if not comments:
            continue
        latest = max(comments, key=lambda item: (int(item.get("created_at", 0)),
                                                  int(item.get("id", 0))))
        if is_done_report(latest.get("content", ""), card.get("seq")):
            result.append((card, latest))
    return result


def build_message(items):
    lines = ["[KESZ-NYITOTT] %d kartya kesznek jelentve, de nincs lezarva "
             "(a szkript nem zart le semmit, a dontes a tied):" % len(items)]
    for card, comment in items:
        status = str(card.get("status") or "-")
        if card.get("wait_kind"):
            status += " (%s)" % card["wait_kind"]
        stamp = time.strftime("%m-%d %H:%M", time.localtime(comment["created_at"]))
        head = _strip_prefix(comment.get("content", ""))[:100]
        lines.append("#%s %s | %s | %s | komment %s %s: %s" % (
            card.get("seq"), str(card.get("title") or "")[:60], status,
            card.get("assignee") or "-", stamp, comment.get("author") or "-", head))
    return "\n".join(lines)


def send(text):
    try:
        result = subprocess.run(
            ["bash", os.path.join(ROOT, "scripts", "agent-msg.sh"),
             "marveen", "marveen", "-"], input=text, text=True,
            capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return "OK id=" in (result.stdout or "")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    try:
        found = findings()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print("FAIL: dashboard API: %s" % error, file=sys.stderr)
        return 1
    reported = load_state()
    new = found if args.all else [item for item in found
                                  if str(item[1]["id"]) not in reported]
    if not new:
        return 0
    text = build_message(new)
    if args.dry_run:
        print(text)
        return 0
    if not send(text):
        return 2
    now = int(time.time())
    cutoff = now - 30 * 24 * 60 * 60
    kept = {str(key): value for key, value in reported.items()
            if isinstance(value, (int, float)) and value >= cutoff}
    for unused_card, comment in new:
        kept[str(comment["id"])] = now
    save_state(kept)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
