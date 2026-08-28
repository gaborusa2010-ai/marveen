#!/usr/bin/env python3
"""
Hold outbid notices back and let them out once an hour, together.

Why
---
The owner bids on a lot of small auction lots, and Jobalots sends a mail every
time somebody goes over him -- four or five for a single item. On 2026-08-28 he
was outbid on four different lots in one morning; reported one by one that is
three separate interruptions in ninety minutes, each worth almost nothing on its
own. He asked for hourly instead, and he was right: a watcher that speaks every
round gets switched off, and then the message that mattered is lost with it.

WHAT DOES NOT WAIT: a WON auction (there is money and a collection to arrange)
and a CLOSING one (it closes whether or not we mention it). Only the "You've
Been Outbid" notices collect here. Sorting those two out of the buffer is the
whole point -- an aggregation that also delays the urgent thing is worse than no
aggregation at all.

Two different lists, one file, and they are not the same idea:
    ids               -- I have SEEN this mail. Stops the next round re-reporting it.
    outbid_pending    -- I have seen it and have NOT spoken about it yet.
An outbid mail goes into both: seen, so it is not rediscovered as new; pending,
so it is not lost by having been seen.

Usage
-----
  outbid-buffer.py --add "Estwing hammer" --add "Estwing hammer"   # add and check
  outbid-buffer.py                                                 # check only, no write

Prints MEHET MOST (send the summary it printed, the buffer is now empty) or MEG
VAR (say nothing). Exit 0 either way; exit 1 only on a real failure.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter

STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "store", "folyamatos-ellenorzes-seen.json")
WINDOW = 3600


def load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        # A missing or broken state file must not take the heartbeat down with
        # it: an empty buffer is a correct starting point, and the round that
        # follows will refill it.
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", action="append", default=[], metavar="TETEL",
                    help="egy tullicitalt tetel neve; annyiszor add meg, ahany ertesites jott")
    ap.add_argument("--window", type=int, default=WINDOW,
                    help="masodperc ket jelentes kozott (alap 3600)")
    ap.add_argument("--commit", action="store_true",
                    help="a puffert CSAK ezzel uriti; add meg MIUTAN tenyleg elkuldted")
    a = ap.parse_args(argv)

    d = load()
    now = int(time.time())
    pending = [x for x in (d.get("outbid_pending") or []) if isinstance(x, str)]
    last = d.get("last_outbid_report_at") or 0

    # Adding nothing must not change anything. The heartbeat calls this to look
    # as well as to file, and a read that writes is a read you stop trusting.
    if a.add:
        pending += [s.strip()[:80] for s in a.add if s.strip()]

    elapsed = now - last
    due = bool(pending) and elapsed >= a.window

    ota = f"{elapsed // 60} perc" if last else "meg nem volt jelentes"
    print(f"gyulo tullicit: {len(pending)} | utolso jelentes ota: {ota}")

    if due:
        print("MEHET MOST -- ezt kuldd el egy uzenetben:")
        for item, n in Counter(pending).most_common():
            print(f"  {item}: {n}x" if n > 1 else f"  {item}")
        print(f"  (osszesen {len(pending)} ertesites, "
              f"{len(set(pending))} kulonbozo tetel)")
    elif pending:
        print("MEG VAR -- ne irj rola semmit. A pufferben marad:")
        for item, n in Counter(pending).most_common():
            print(f"  {item}: {n}x" if n > 1 else f"  {item}")
    else:
        print("MEG VAR -- nincs mit jelenteni.")

    # DUE IS NOT SENT. The first version emptied the buffer and stamped the
    # clock in the same breath as saying "MEHET MOST", so the record claimed a
    # message had gone out before the caller had decided to send one. Caught
    # live on 2026-08-28 18:30: the owner had said good night an hour earlier,
    # holding the notice was the right call, and by then the buffer was already
    # cleared and the clock already stamped. A step that records an action it
    # did not take is the exact failure this file was written to avoid.
    #
    # So emptying is now a SEPARATE decision. Check, send, then --commit.
    if due and not a.commit:
        print("  (a puffer NEM urult ki: ha tenyleg elkuldted, futtasd ujra --commit-tal)")

    if a.commit and due:
        d["outbid_pending"] = []
        d["last_outbid_report_at"] = now
    elif a.add:
        d["outbid_pending"] = pending
    else:
        return 0  # pure read, nothing to persist
    try:
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
    except OSError as e:
        # Say it loudly. A buffer that silently fails to empty would repeat the
        # same summary every hour; one that fails to fill would drop notices.
        print(f"HIBA: az allapotfajl irasa nem sikerult: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
