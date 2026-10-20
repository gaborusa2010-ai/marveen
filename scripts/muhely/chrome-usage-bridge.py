#!/usr/bin/env python3
"""
Read the Claude Usage Tracker extension's live usage figures from Chrome.

The owner has the extension on the Windows side of this WSL box. It shows real
percentages, but only on screen and only when he is at the desk. This lifts the
numbers out of the extension's storage so they can reach his phone.

WHERE THE NUMBERS ACTUALLY LIVE
The extension keeps a debug log inside its LevelDB write-ahead log, and each
`updateUsage` entry carries the full structure:

    limits.session.percentage   + resetsAt   (the 5 hour window)
    limits.weekly.percentage    + resetsAt   (the 7 day window)
    subscriptionTier

The compacted .ldb files ALSO contain a `lastPercentage` field, but that is the
badge's historic state and it goes stale. Reading it gave 4% while the extension
was showing 79%. Always parse the newest `updateUsage` entry, never the badge.

Caveats, stated rather than hidden:
  - The value is a CACHE. It is only as fresh as the last time Chrome ran with
    the extension active. Staleness is always reported alongside the number.
  - This scrapes the LevelDB files rather than opening them properly, because
    Chrome holds the lock while running. It reads, never writes.
  - No cookies, no session tokens, no browsing history are touched. Only this
    one extension's own settings directory.

Usage:  chrome-usage-bridge.py [--json]
"""
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

EXT_ID = "knemcdpkggnbhpoaaagmjiigenifejfo"      # Claude Usage Tracker
WIN_USERS = "/mnt/c/Users"
REL = ("AppData/Local/Google/Chrome/User Data/{profile}/"
       "Local Extension Settings/" + EXT_ID)
# macOS keeps Chrome's profiles under the user's own Library (card #583).
MAC_ROOT = os.path.expanduser("~/Library/Application Support/Google/Chrome")
MAC_REL = "{profile}/Local Extension Settings/" + EXT_ID
PROFILES = ["Default", "Profile 1", "Profile 2", "Profile 3"]

# The payload is JSON escaped inside a log string, so quotes arrive as \" and
# newlines as \n. Matching on the escaped form is more robust than unescaping
# 3 MB of log first.
BLOCK = re.compile(
    rb'\\"limits\\":\s*\{(?P<body>.{0,900}?)\\"subscriptionTier\\":\s*\\"(?P<tier>[a-z_]+)\\"',
    re.S)
# Matched against the UNESCAPED body. Inside the log the payload is JSON that
# was itself embedded in a JSON string, so a newline arrives as the two
# characters backslash-n. \s does not match that, which silently turned every
# percentage into None: the pattern matched, the capture group did not.
WINDOW = re.compile(
    r'"(?P<name>session|weekly|sonnetWeekly|opusWeekly|fableWeekly)":\s*'
    r'(?:null|\{\s*"percentage":\s*(?P<pct>\d+)\s*,\s*'
    r'"resetsAt":\s*(?P<reset>\d+))')


def _unescape(text):
    return (text.replace('\\n', '\n').replace('\\"', '"')
                .replace('\\\\', '\\'))
STAMP = re.compile(rb'"timestamp":"(\d\d:\d\d:\d\d\.\d+)"')


def _stores():
    if sys.platform == "darwin":
        for prof in PROFILES:
            p = os.path.join(MAC_ROOT, MAC_REL.format(profile=prof))
            if os.path.isdir(p):
                yield p
        return
    for user in sorted(glob.glob(os.path.join(WIN_USERS, "*"))):
        for prof in PROFILES:
            p = os.path.join(user, REL.format(profile=prof))
            if os.path.isdir(p):
                yield p


def read_latest():
    best = None
    for store in _stores():
        for f in glob.glob(os.path.join(store, "*")):
            if not f.endswith((".log", ".ldb")):
                continue
            try:
                blob = open(f, "rb").read()
                mtime = os.path.getmtime(f)
            except OSError:
                continue
            # Walk backwards to the newest entry that actually carries numbers.
            # The very last entry is often an all-null, claude_free payload from
            # a second tab that is not signed in; taking it blindly reports a
            # logged-out state as if it were the account's usage.
            chosen = None
            for m in reversed(list(BLOCK.finditer(blob))):
                body = _unescape(m.group("body").decode("utf-8", "replace"))
                limits = {}
                for w in WINDOW.finditer(body):
                    if w.group("pct") is None:
                        continue
                    limits[w.group("name")] = {
                        "percent": int(w.group("pct")),
                        "resets_at": int(w.group("reset")) / 1000.0,
                    }
                if limits:
                    chosen = (m, limits)
                    break
            if not chosen:
                continue
            m, limits = chosen
            cand = {
                "limits": limits,
                "tier": m.group("tier").decode(),
                "source_file": os.path.basename(f),
                "file_mtime": mtime,
                "age_seconds": int(time.time() - mtime),
            }
            if best is None or cand["file_mtime"] > best["file_mtime"]:
                best = cand
    return best


def _fmt_reset(ts):
    left = ts - time.time()
    when = datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%H:%M")
    if left <= 0:
        return f"{when} (mar lejart)"
    h, m = int(left // 3600), int((left % 3600) // 60)
    if h >= 24:
        return f"{when}, {h // 24} nap {h % 24} ora mulva"
    return f"{when}, {h} ora {m} perc mulva"


LABEL = {"session": "5 oras ablak", "weekly": "heti",
         "sonnetWeekly": "heti Sonnet", "opusWeekly": "heti Opus",
         "fableWeekly": "heti Fable"}


def main(argv):
    r = read_latest()
    if not r:
        msg = {"available": False,
               "reason": "Nem talaltam ervenyes updateUsage bejegyzest a Claude Usage "
                         "Tracker tarolojaban. Lehet hogy nincs telepitve, mas Chrome "
                         "profilban van, vagy a bovitmeny formatuma valtozott."}
        print(json.dumps(msg, indent=2, ensure_ascii=False) if "--json" in argv
              else msg["reason"])
        return 1
    r["available"] = True
    age_h = r["age_seconds"] / 3600.0
    r["stale"] = age_h > 6
    if "--json" in argv:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0
    print(f"Elofizetes: {r['tier']}")
    for key in ("session", "weekly", "sonnetWeekly", "opusWeekly", "fableWeekly"):
        if key in r["limits"]:
            v = r["limits"][key]
            print(f"  {LABEL[key]:<14} {v['percent']:>3}%   reset: {_fmt_reset(v['resets_at'])}")
    print(f"Frissesseg: {age_h:.2f} ora ({r['source_file']})")
    if r["stale"]:
        print("FIGYELEM: REGI ertek, a bovitmeny azota nem frissult.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
