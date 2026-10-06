#!/usr/bin/env python3
"""Watch Gmail for a Leonardo reply to the custom-pricing Contact Us form (sent 2026-09-27).

Prints one line per NEW matching message (not seen before); prints nothing otherwise.
Known, already-reported threads (refund case, top-up receipt) are excluded via the seen file.
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "muhely"))
from google_auth import call

SEEN = os.path.join(os.path.dirname(__file__), "..", "store", "leonardo-valasz-seen.json")
Q = "in:anywhere leonardo after:2026/09/27"

seen = set()
if os.path.exists(SEEN):
    seen = set(json.load(open(SEEN)))
first_run = not os.path.exists(SEEN)

r = call("https://gmail.googleapis.com/gmail/v1/users/me/messages", params={"q": Q, "maxResults": 50})
ids = [m["id"] for m in r.get("messages", [])]
new = [i for i in ids if i not in seen]
if not first_run:
    for i in new:
        m = call(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{i}",
                 params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        h = {x["name"]: x["value"] for x in m.get("payload", {}).get("headers", [])}
        print(f"{i} | {h.get('Date','')} | {h.get('From','')} | {h.get('Subject','')} | {m.get('snippet','')[:160]}")
json.dump(sorted(seen | set(ids)), open(SEEN, "w"))
if first_run:
    print(f"BASELINE: {len(ids)} existing messages recorded", file=sys.stderr)
