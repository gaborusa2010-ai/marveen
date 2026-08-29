#!/usr/bin/env python3
"""
Find auction-closing reminders fast, and only those.

Why a second watcher instead of running the big one more often
--------------------------------------------------------------
The owner asked for exactly one thing on 2026-08-28: "csak szolj mielott lejar
az arveres". Jobalots sends that reminder FIFTEEN minutes before the close. The
half-hourly round therefore cannot keep the promise, and that was measured, not
guessed: on 08-28 the 18:50 mail was relayed at 19:01 and the 19:20 mail at
19:31, both about eleven minutes late, leaving four minutes to act. In the worst
case a mail arrives just after a round and is seen twenty-nine minutes later,
which is longer than the whole warning window -- the auction is already over.

Speeding up the full round would multiply everything else too: calendar,
kanban, every newsletter re-examined. This asks Gmail one narrow question
instead, so it can run every few minutes for almost nothing.

It reports, it does not decide -- unless told to notify
------------------------------------------------------
Output is a JSON line per new closing reminder, with the minutes already
elapsed since the mail arrived. Whether that is still worth sending is the
caller's judgement -- and the caller must check, because "15 minutes" written
at 19:20 is a lie by 19:36.

With --notify it sends the Telegram message ITSELF, through the Bot API, the
way scripts/limit-monitor.sh does. That is the point of the whole file: a
five-minute cadence through the scheduled-task system would be 288 model turns
a day, and the peak measured on 08-28 was already 259 million tokens in a
five-hour window. This path costs nothing until there is something to say.

WHY --notify MAY COMMIT WHAT IT DID NOT SEND, unlike the outbid buffer: there,
holding an item means "send it later". Here, past the window means NEVER -- the
auction is closed and no later message helps. Committing a missed one is
therefore honest, not a silent drop. A send that FAILS is different and is not
committed, so the next run retries.

Its own repeat-protection
-------------------------
Separate state file from the half-hourly round. Sharing one would couple two
watchers with different cadences: the fast one would mark a mail seen and the
slow one would then never look at it, which is how a message disappears while
both halves believe the other handled it.

Usage
-----
  auction-closing-watch.py            # what is new, JSON lines
  auction-closing-watch.py --commit   # mark the ones just printed as seen
  auction-closing-watch.py --window 30

Exit 0 always unless something actually broke (2).
"""
import argparse
import datetime
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "store", "auction-closing-seen.json")
MUHELY = os.path.join(REPO, "scripts", "muhely")

# Jobalots' own subject line. Narrow on purpose: this watcher must stay cheap,
# and every subject it also matches is a mail it has to fetch and judge.
QUERY = 'from:jobalots.com subject:"ending soon" newer_than:1h'

# How long the site's own warning is worth. The mail says fifteen minutes; past
# that the auction is closed and a message would be noise about a done thing.
DEFAULT_WINDOW_MIN = 16


def load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)


CHAT_ID = "8547011582"
TG_ENV = os.path.expanduser("~/.claude/channels/telegram/.env")


def notify(item):
    """Send one closing warning straight through the Bot API. True on success.

    Same token-free path as scripts/limit-monitor.sh: no model turn, so this can
    run every few minutes. The message says WHEN the mail arrived and how old it
    is, because the fifteen minutes it promises are counted from then, not from
    now -- and the reader has to be able to tell how much is left.
    """
    import re
    import subprocess
    try:
        env = open(TG_ENV, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print(f"HIBA: a Telegram token nem olvashato ({e})", file=sys.stderr)
        return False
    m = re.search(r"[0-9]+:[A-Za-z0-9_-]+", env)
    if not m:
        print("HIBA: nincs bot token a channels .env-ben", file=sys.stderr)
        return False
    subject = item["subject"]
    text = (f"Zarulo arveres. Az ertesito {item['sent']}-kor jott, "
            f"tizenot percet igerve, tehat kb. {max(0, 15 - int(item['age_min']))} perced van.\n\n"
            f"{subject}\n\n"
            f"Az idopont a Jobalots szama, nem az en meresem.")
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "15",
             f"https://api.telegram.org/bot{m.group()}/sendMessage",
             "--data-urlencode", f"chat_id={CHAT_ID}",
             "--data-urlencode", f"text={text}",
             "--data-urlencode", "disable_web_page_preview=true"],
            capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"HIBA: a curl nem futott: {e}", file=sys.stderr)
        return False
    # curl exits 0 even when Telegram refuses, so read the answer, not the code.
    # This is the exact trap the repo's inter-agent helper exists to avoid.
    try:
        okflag = json.loads(r.stdout or "{}").get("ok") is True
    except ValueError:
        okflag = False
    if not okflag:
        print(f"HIBA: a Telegram elutasitotta: {(r.stdout or '')[:200]}", file=sys.stderr)
    return okflag


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW_MIN,
                    help="hany percig erdemes meg szolni a levelrol (alap 16)")
    ap.add_argument("--commit", action="store_true",
                    help="a most kiirtakat megjegyzi latottkent")
    ap.add_argument("--notify", action="store_true",
                    help="maga kuldi el a Telegram uzenetet, es utana megjegyzi (cron-hoz)")
    a = ap.parse_args(argv)

    sys.path.insert(0, MUHELY)
    try:
        from google_auth import call
    except ImportError as e:
        print(f"HIBA: a google_auth nem toltheto be: {e}", file=sys.stderr)
        return 2

    d = load()
    now = int(time.time())
    # Seen ids expire after a day. A closing reminder is worthless long before
    # that, so the list stays tiny.
    seen = {i: t for i, t in (d.get("ids") or []) if now - t < 86400}

    try:
        r = call("https://gmail.googleapis.com/gmail/v1/users/me/messages",
                 params={"q": QUERY, "maxResults": "10"})
    except Exception as e:
        print(f"HIBA: a Gmail lekerdezes nem sikerult: {e}", file=sys.stderr)
        return 2

    fresh = []
    for m in r.get("messages", []) or []:
        mid = m["id"]
        if mid in seen:
            continue
        try:
            full = call(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                        params={"format": "metadata", "metadataHeaders": ["Subject"]})
        except Exception as e:
            print(f"HIBA: {mid} nem olvashato: {e}", file=sys.stderr)
            continue
        subj = ""
        for h in full.get("payload", {}).get("headers", []) or []:
            if h.get("name") == "Subject":
                subj = h.get("value", "")
        sent = int(full.get("internalDate", 0)) / 1000
        age_min = round((now - sent) / 60, 1)
        fresh.append({
            "id": mid,
            "subject": subj[:120],
            "sent": datetime.datetime.fromtimestamp(sent).strftime("%H:%M:%S"),
            "age_min": age_min,
            # The judgement the caller needs, stated rather than implied: after
            # the window the auction has closed and there is nothing to do.
            "still_worth_sending": age_min <= a.window,
        })

    for f in sorted(fresh, key=lambda x: x["age_min"]):
        print(json.dumps(f, ensure_ascii=False))
    if not fresh:
        print(json.dumps({"new": 0}, ensure_ascii=False))

    if a.notify:
        for f in fresh:
            if not f["still_worth_sending"]:
                # Too late to act on, and it will never get better. Remember it
                # so the next run does not re-examine a closed auction.
                seen[f["id"]] = now
                print(f"# {f['id']}: {f['age_min']} perces, mar lezart, csak megjegyzem",
                      file=sys.stderr)
                continue
            if notify(f):
                seen[f["id"]] = now
            else:
                # NOT remembered: a failed send must be retried, not buried.
                print(f"# {f['id']}: a kuldes NEM sikerult, kovetkezo korben ujra",
                      file=sys.stderr)
        d["ids"] = [[i, t] for i, t in seen.items()]
        save(d)
        return 0

    if a.commit:
        for f in fresh:
            seen[f["id"]] = now
        d["ids"] = [[i, t] for i, t in seen.items()]
        save(d)
        print(f"# {len(fresh)} megjegyezve, allapot: {len(seen)} id", file=sys.stderr)
    elif fresh:
        print("# NEM jegyeztem meg oket: ha szoltal roluk, futtasd ujra --commit-tal",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
